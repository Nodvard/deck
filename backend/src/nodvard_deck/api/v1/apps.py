"""Eigene App-Kacheln: `GET/POST /apps`, `PATCH/DELETE /apps/{id}`, `PUT /apps/order`, `GET /apps/suggestions`.

Die Eingaben prüft `services/custom_apps.py`. Der Server ruft die Adressen nie selbst ab (keine Statusabfrage, kein SSRF).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response, status
from nodvard_sdk import AppSuggestionProvider, ServiceCatalog
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import CustomApp, User
from ...services import audit as audit_service
from ...services import custom_apps as apps_service
from ..deps import CurrentUser, SessionDep, require_permission

# Lesen braucht `hosts.read` (wie die erkannten Apps im Cockpit). Schreiben braucht `apps.write`: Ein Link auf der
# Startseite ist für jeden Nutzer ein Klick-Ziel, das passt weder zu `hosts.write` (viel mehr Macht) noch zu
# `settings.write` (ganze Installation). Eingebaut hat es nur der Administrator und der Inhaber.
router = APIRouter(prefix="/apps", tags=["apps"])

_NOT_EMPTY = "Dieses Feld darf nicht leer sein."
_SORT_ERROR = f"Die Position muss eine ganze Zahl zwischen 0 und {apps_service.SORT_MAX} sein."


class CustomAppOut(BaseModel):
    id: str
    name: str
    url: str | None
    """`null` nur, wenn eine von Hand veränderte Datenbank etwas anderes als http/https enthält."""
    icon: str | None
    color: str | None
    group: str | None
    sort_order: int
    open_in_new_tab: bool
    host_id: str | None
    host: str | None
    """Anzeigename des Servers zu `host_id`."""
    created_at: datetime
    updated_at: datetime


def app_out(app: CustomApp, host_names: dict[str, str]) -> CustomAppOut:
    host_id = app.host_id if app.host_id in host_names else None
    return CustomAppOut(
        id=app.id, name=app.name, url=apps_service.stored_url(app.url), icon=app.icon, color=app.color,
        group=app.group_name, sort_order=app.sort_order, open_in_new_tab=app.open_in_new_tab,
        host_id=host_id, host=host_names.get(host_id) if host_id else None,
        created_at=app.created_at, updated_at=app.updated_at,
    )


async def _out(session: AsyncSession, apps: list[CustomApp]) -> list[CustomAppOut]:
    names = await apps_service.host_names(session, (a.host_id for a in apps))
    return [app_out(a, names) for a in apps]


def _check_sort(value: int | None) -> int | None:
    if value is not None and not 0 <= value <= apps_service.SORT_MAX:
        raise ValueError(_SORT_ERROR)
    return value


class AppCreate(BaseModel):
    # Die Längen prüft `services.custom_apps` (mit deutscher Meldung), kein `Field(max_length=...)`.
    name: str
    url: str
    icon: str | None = None
    color: str | None = None
    group: str | None = None
    open_in_new_tab: bool = True
    host_id: str | None = None
    sort_order: int | None = None
    """Ohne Angabe kommt die App ans Ende."""

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return apps_service.clean_name(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return apps_service.clean_url(value)

    @field_validator("icon")
    @classmethod
    def _icon(cls, value: str | None) -> str | None:
        return apps_service.clean_icon(value)

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        return apps_service.clean_color(value)

    @field_validator("group")
    @classmethod
    def _group(cls, value: str | None) -> str | None:
        return apps_service.clean_group(value)

    @field_validator("host_id")
    @classmethod
    def _host_id(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("sort_order")
    @classmethod
    def _sort_order(cls, value: int | None) -> int | None:
        return _check_sort(value)


class AppPatch(BaseModel):
    """Nur die genannten Felder ändern. `icon`, `color`, `group` und `host_id` lassen sich mit `null` leeren."""

    name: str | None = None
    url: str | None = None
    icon: str | None = None
    color: str | None = None
    group: str | None = None
    open_in_new_tab: bool | None = None
    host_id: str | None = None
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else apps_service.clean_name(value)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else apps_service.clean_url(value)

    @field_validator("icon")
    @classmethod
    def _icon(cls, value: str | None) -> str | None:
        return apps_service.clean_icon(value)

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        return apps_service.clean_color(value)

    @field_validator("group")
    @classmethod
    def _group(cls, value: str | None) -> str | None:
        return apps_service.clean_group(value)

    @field_validator("host_id")
    @classmethod
    def _host_id(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("sort_order")
    @classmethod
    def _sort_order(cls, value: int | None) -> int | None:
        return _check_sort(value)

    @model_validator(mode="after")
    def _required_fields_stay_set(self) -> AppPatch:
        for field in ("name", "url", "open_in_new_tab", "sort_order"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(_NOT_EMPTY)
        return self


ORDER_SLACK = 50
"""Platz über `MAX_APPS` hinaus: Das Frontend schickt beim Verschieben alle Apps, und es kann mehr als `MAX_APPS` geben
(Beispieldaten, zwei gleichzeitige Anlegen). Jede genannte ID muss ohnehin eine vorhandene, nur einmal genannte App sein."""


class AppOrder(BaseModel):
    ids: list[str]
    """Die Apps in der gewünschten Reihenfolge; nicht genannte kommen dahinter."""

    @field_validator("ids")
    @classmethod
    def _not_absurdly_long(cls, value: list[str]) -> list[str]:
        if len(value) > apps_service.MAX_APPS + ORDER_SLACK:
            raise ValueError("Die Reihenfolge nennt mehr Apps, als es geben kann.")
        return value


# Protokoll: Name und `https://host:port`, nie Pfad oder Parameter der Adresse (dort könnten Zugangsschlüssel stehen).
async def _audit(
    session: AsyncSession, user: User, action: str, *, target_id: str | None, detail: dict[str, Any],
) -> None:
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action=action, outcome="success",
        target_type="app", target_id=target_id, detail=detail,
    )


def _http(exc: apps_service.AppServiceError) -> HTTPException:
    if isinstance(exc, apps_service.AppNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, apps_service.AppLimitError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.get("", dependencies=[Depends(require_permission("hosts.read"))])
async def list_apps(session: SessionDep) -> list[CustomAppOut]:
    return await _out(session, await apps_service.list_apps(session))


SUGGESTION_TIMEOUT_S = 12.0
"""So lange darf jede Quelle für `GET /apps/suggestions` höchstens brauchen (wie im Cockpit)."""
MAX_SUGGESTIONS = 200


class AppSuggestionOut(BaseModel):
    name: str
    url: str
    source: str
    """Woher der Vorschlag stammt (Name der Quelle oder der Erweiterung, z. B. "Nginx Proxy Manager")."""
    host: str | None = None


class AppSuggestionsOut(BaseModel):
    suggestions: list[AppSuggestionOut]
    errors: list[str] = []
    """Je Quelle, die nicht antwortete, ein Satz (ohne Text aus der Quelle)."""


def url_key(url: str) -> str:
    """Vergleichswert einer Adresse: Schema und Rechner klein, ohne abschließenden Schrägstrich."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url.strip().lower()
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host}{f':{port}' if port else ''}{path}{query}"


def _suggestion(name: Any, url: Any, source: str, host: Any) -> AppSuggestionOut | None:
    """Prüft einen Vorschlag wie eine von Hand angelegte App; ein unbrauchbarer fällt still weg."""
    if not isinstance(name, str) or not isinstance(url, str):
        return None
    try:
        clean_url = apps_service.clean_url(url)
        clean_name = apps_service.clean_name(name.strip()[: apps_service.NAME_MAX])
    except apps_service.CustomAppError:
        return None
    host_text = host.strip()[:120] if isinstance(host, str) and host.strip() else None
    return AppSuggestionOut(name=clean_name, url=clean_url, source=source[:60], host=host_text)


def _source_name(ext_id: str) -> str:
    from ...ext.runtime import get_extension_runtime

    loaded = get_extension_runtime().loaded.get(ext_id)
    name = getattr(getattr(loaded, "manifest", None), "name", None)
    return str(name or ext_id)


async def _from_provider(ext_id: str, provider: Any) -> tuple[list[AppSuggestionOut], str | None]:
    try:
        rows = await asyncio.wait_for(provider.app_suggestions(), timeout=SUGGESTION_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - eine Quelle darf die anderen nicht verdecken; nie ihr Text
        return [], f"Vorschläge von „{_source_name(ext_id)}“ ließen sich gerade nicht lesen."
    out: list[AppSuggestionOut] = []
    for row in rows or []:
        item = _suggestion(getattr(row, "name", None), getattr(row, "url", None), str(getattr(row, "source", "") or _source_name(ext_id)), getattr(row, "host", None))
        if item is not None:
            out.append(item)
    return out, None


async def _from_catalog(ext_id: str, catalog: Any) -> tuple[list[AppSuggestionOut], str | None]:
    """Erkannte Dienste (`ServiceCatalog`) mit Adresse, die gerade laufen; Quelle ist der Name der Erweiterung."""
    try:
        rows = await asyncio.wait_for(catalog.list_services(), timeout=SUGGESTION_TIMEOUT_S)
    except Exception:  # noqa: BLE001
        return [], f"Dienste von „{_source_name(ext_id)}“ ließen sich gerade nicht lesen."
    out: list[AppSuggestionOut] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("unreachable") is True or row.get("state") not in (None, "running"):
            continue
        item = _suggestion(row.get("name"), row.get("url"), _source_name(ext_id), row.get("host"))
        if item is not None:
            out.append(item)
    return out, None


@router.get("/suggestions", dependencies=[Depends(require_permission("apps.write"))])
async def app_suggestions(session: SessionDep) -> AppSuggestionsOut:
    """Vorschläge für neue App-Kacheln: Proxy-Hosts, erkannte Dienste und alles, was eine Erweiterung als
    `AppSuggestionProvider` nennt. Geprüft wie eine von Hand angelegte App; Adressen, die es schon gibt, fallen weg.
    Ruft selbst keine der Adressen ab."""
    from ...ext.runtime import get_extension_runtime

    registry = get_extension_runtime().capabilities
    tasks = [_from_provider(ext_id, p) for ext_id, p in registry.query_named(AppSuggestionProvider)]
    tasks += [_from_catalog(ext_id, c) for ext_id, c in registry.query_named(ServiceCatalog)]
    results = await asyncio.gather(*tasks)
    taken = {url_key(a.url) for a in await apps_service.list_apps(session) if a.url}
    suggestions: list[AppSuggestionOut] = []
    errors: list[str] = []
    for rows, error in results:
        if error:
            errors.append(error)
        for item in rows:
            key = url_key(item.url)
            if key in taken:
                continue
            taken.add(key)
            suggestions.append(item)
    suggestions.sort(key=lambda s: (s.source.lower(), s.name.lower()))
    return AppSuggestionsOut(suggestions=suggestions[:MAX_SUGGESTIONS], errors=errors)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("apps.write"))])
async def create_app(payload: AppCreate, session: SessionDep, user: CurrentUser) -> CustomAppOut:
    try:
        app = await apps_service.create_app(
            session, user_id=user.id, name=payload.name, url=payload.url, icon=payload.icon, color=payload.color,
            group=payload.group, open_in_new_tab=payload.open_in_new_tab, host_id=payload.host_id,
            sort_order=payload.sort_order,
        )
    except apps_service.AppServiceError as exc:
        raise _http(exc) from exc
    await _audit(
        session, user, "app.created", target_id=app.id,
        detail={"name": app.name, "url": apps_service.url_origin(app.url), "group": app.group_name},
    )
    return (await _out(session, [app]))[0]


@router.put("/order", dependencies=[Depends(require_permission("apps.write"))])
async def reorder_apps(payload: AppOrder, session: SessionDep, user: CurrentUser) -> list[CustomAppOut]:
    try:
        ordered = await apps_service.reorder(session, payload.ids)
    except apps_service.AppServiceError as exc:
        raise _http(exc) from exc
    await _audit(session, user, "app.reordered", target_id=None, detail={"count": len(ordered)})
    return await _out(session, ordered)


@router.patch("/{app_id}", dependencies=[Depends(require_permission("apps.write"))])
async def update_app(app_id: str, payload: AppPatch, session: SessionDep, user: CurrentUser) -> CustomAppOut:
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    try:
        app = await apps_service.get_app(session, app_id)
        changed = await apps_service.update_app(session, app, changes)
    except apps_service.AppServiceError as exc:
        raise _http(exc) from exc
    if changed:
        await _audit(session, user, "app.updated", target_id=app.id, detail={"name": app.name, "changed": changed})
    return (await _out(session, [app]))[0]


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission("apps.write"))])
async def delete_app(app_id: str, session: SessionDep, user: CurrentUser) -> Response:
    try:
        app = await apps_service.get_app(session, app_id)
    except apps_service.AppServiceError as exc:
        raise _http(exc) from exc
    detail = {"name": app.name, "url": apps_service.url_origin(app.url)}
    await apps_service.delete_app(session, app)
    await _audit(session, user, "app.deleted", target_id=app_id, detail=detail)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
