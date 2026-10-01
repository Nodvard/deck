"""Eigene App-Kacheln ("+ App hinzufuegen") -- `GET/POST /apps`, `PATCH/DELETE /apps/{id}`,
`PUT /apps/order` (docs/04-API.md "Eigene Apps"). Pruefung der Eingaben und Begruendung der
Begrenzungen: `services/custom_apps.py`.

**Rechte:**

* Lesen (`GET /apps`): `hosts.read` -- wie die erkannten Apps im Cockpit (`GET /overview`).
* Schreiben (anlegen, aendern, loeschen, Reihenfolge): neu `apps.write`. Die Kacheln sehen alle Nutzer
  auf der Startseite; ein Link, den jemand dort hinlegt, ist fuer jeden anderen Nutzer (auch den
  Administrator) ein Klick-Ziel. Das gehoert nicht zu `hosts.write` (Server und SSH-Zugaenge verwalten --
  viel mehr Macht) und nicht zu `settings.write` (Einstellungen der ganzen Installation). Eingebaut hat es
  deshalb nur der Administrator (ueber `*`) und der Inhaber; Bediener und Betrachter bekommen es nicht von
  selbst. Es ist rein additiv: keine bestehende Rolle verliert oder gewinnt dadurch etwas.

Jede Aenderung steht im Protokoll (`app.created`, `app.updated`, `app.deleted`, `app.reordered`) -- mit
Name und `https://host:port`, nie mit Pfad oder Parametern der Adresse (dort koennten Zugangsschluessel stehen).

Der Server ruft die Adressen nie selbst ab (keine Statusabfrage, kein SSRF).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import CustomApp, User
from ...services import audit as audit_service
from ...services import custom_apps as apps_service
from ..deps import CurrentUser, SessionDep, require_permission

router = APIRouter(prefix="/apps", tags=["apps"])

_NOT_EMPTY = "Dieses Feld darf nicht leer sein."
_SORT_ERROR = f"Die Position muss eine ganze Zahl zwischen 0 und {apps_service.SORT_MAX} sein."


class CustomAppOut(BaseModel):
    id: str
    name: str
    url: str | None
    """`null` nur, wenn eine von Hand veraenderte Datenbank etwas anderes als http/https enthaelt."""
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
    # Die Laengen prueft `services.custom_apps` (mit deutscher Meldung), kein `Field(max_length=...)`.
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
    """Nur die genannten Felder aendern. `icon`, `color`, `group` und `host_id` lassen sich mit `null` leeren."""

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
"""Platz ueber `MAX_APPS` hinaus: Das Frontend schickt beim Verschieben immer ALLE Apps. Mehr als `MAX_APPS` kann es
geben (die Beispieldaten, zwei gleichzeitige Anlegen); dann soll das Verschieben nicht an der Obergrenze scheitern.
Die Liste ist nur nach oben begrenzt, damit niemand eine riesige Liste schickt -- jede genannte ID muss ohnehin eine
vorhandene, nur einmal genannte App sein (sonst 404/422)."""


class AppOrder(BaseModel):
    ids: list[str]
    """Die Apps in der gewuenschten Reihenfolge; nicht genannte kommen dahinter."""

    @field_validator("ids")
    @classmethod
    def _not_absurdly_long(cls, value: list[str]) -> list[str]:
        if len(value) > apps_service.MAX_APPS + ORDER_SLACK:
            raise ValueError("Die Reihenfolge nennt mehr Apps, als es geben kann.")
        return value


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
