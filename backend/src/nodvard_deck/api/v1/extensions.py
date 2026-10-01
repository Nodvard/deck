"""Extension-Registry -- docs/04-API.md §3.

Nur der WP-3-Ausschnitt (Auflisten, Aktivieren, Deaktivieren) plus WP-7s
`GET .../frontend/index.js` (ESM-Bundle, docs/02 §5). `PUT .../settings`,
`PUT .../permissions`, `POST .../reload`, `DELETE .../{id}` bleiben bewusst NICHT
Teil dieser Runde -- `reload` waere ohnehin nur Zucker fuer disable+enable, was schon
denselben Mechanismus beweist.

**Pfad-Uneinigkeit in der Doku, hier aufgeloest:** docs/02 §5 nennt woertlich
`/api/v1/ext/<id>/frontend/index.js` (den EXTENSION-gemounteten Namensraum), docs/04
§3s Endpunkt-Katalog nennt `/extensions/{id}/frontend/index.js` (diesen, den
Registry-Namensraum). Ein statisches Bundle braucht keinen laufenden
Extension-Router (anders als eine Datenquelle) -- der Registry-Namensraum ist
konsistenter mit `GET /extensions/{id}` (Metadaten unabhaengig vom Ladezustand) und
ist der einzige der beiden, der in docs/04s expliziter Endpunkt-LISTE steht, deshalb
die Wahl hier.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select

from ...ext.runtime import get_extension_runtime
from ...models import ExtensionRecord, User
from ...services import audit as audit_service
from ...services import extension_setup
from ...services import extensions as extensions_service
from ...services.auth import user_has_permission
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission

router = APIRouter(prefix="/extensions", tags=["extensions"])


def _has_settings(ext_id: str) -> bool:
    from .extension_settings import schema_for

    schema = schema_for(ext_id) or {}
    return any(not p.get("x-hidden") for p in (schema.get("properties") or {}).values())


def _effective_state(record: ExtensionRecord) -> str:
    """Der Zustand, den die API meldet. Ein erfolgreiches Laden loescht `last_error`; steht bei
    einer eingeschalteten Erweiterung trotzdem einer, wurde sie beim Start nicht gefunden (siehe
    `services.extensions.enable_extension(..., at_boot=True)`) und ist nicht geladen -- also `error`.
    Es gibt dafuer bewusst keinen neuen Zustandswert, bestehende Clients bleiben unberuehrt."""
    if record.state == "enabled" and record.last_error:
        return "error"
    return record.state


class ExtensionOut(BaseModel):
    id: str
    version: str
    api_version: str
    state: str
    """`enabled`, `disabled`, `error` oder `incompatible`. Eine Erweiterung, die eingeschaltet
    ist, aber gerade fehlt (Ordner oder Paket nicht da), steht in der Registry weiter auf `enabled`
    und meldet hier `error` samt `last_error`; kommt sie zurueck, laedt sie beim naechsten Start wieder."""
    source: str
    name: str | None
    description: str | None
    icon: str | None
    granted_permissions: list[str]
    last_error: str | None
    category: str | None = None
    """Gruppe in der Modul-Auswahl (aus dem Manifest, siehe `ExtensionManifest.category`)."""
    sort_order: int = 100
    has_settings: bool = False
    needs_setup: bool = False
    """Nur bei eingeschalteten Erweiterungen: Pflichtfelder oder Pflicht-Zugangsdaten fehlen,
    oder der letzte Verbindungstest ist fehlgeschlagen (Gruende in `setup_reasons`)."""
    setup_reasons: list[str] = []
    last_test: dict | None = None
    """Ergebnis des letzten Verbindungstests: `{ok, message, at}`; weg nach jeder Aenderung der
    Einstellungen oder Zugangsdaten."""

    @classmethod
    def from_model(cls, record: ExtensionRecord) -> "ExtensionOut":
        manifest = record.manifest or {}
        return cls(
            id=record.id,
            version=record.version,
            api_version=record.api_version,
            state=_effective_state(record),
            source=record.source,
            name=manifest.get("name"),
            description=manifest.get("description"),
            icon=manifest.get("icon"),
            granted_permissions=list(record.granted_permissions or []),
            last_error=record.last_error,
            category=manifest.get("category") or None,
            sort_order=manifest.get("sort_order") if isinstance(manifest.get("sort_order"), int) else 100,
            has_settings=_has_settings(record.id),
        )


async def _with_setup(
    session, records: list[ExtensionRecord], user: User  # noqa: ANN001 - AsyncSession
) -> list[ExtensionOut]:
    """`ExtensionOut` samt „Einrichtung noetig“ -- Geheimnisse und Testergebnisse werden
    einmal fuer alle geladen, nicht je Erweiterung."""
    from .extension_settings import schema_for

    labels = await extension_setup.present_secret_labels(session)
    tests = await extension_setup.load_last_tests(session)
    # Der Text eines Testergebnisses nennt Adressen und Fehlergruende: nur fuer Verwalter.
    can_manage = user_has_permission(user, "extensions.manage")
    out: list[ExtensionOut] = []
    for record in records:
        item = ExtensionOut.from_model(record)
        last = tests.get(record.id)
        item.last_test = last if (last is None or can_manage) else {"ok": last.get("ok"), "at": last.get("at")}
        if item.state == "enabled":
            reasons = extension_setup.setup_reasons(
                schema_for(record.id), dict(record.settings or {}), labels, last, with_test_message=can_manage
            )
            item.setup_reasons = reasons
            item.needs_setup = bool(reasons)
        out.append(item)
    return out


async def _audit_toggle(session, user: User, action: str, ext_id: str, record: ExtensionRecord | None) -> None:  # noqa: ANN001
    """Protokolleintrag fuer das bewusste Ein-/Ausschalten (`state` ist das Ergebnis: eine
    Erweiterung, die beim Laden abstuerzt, steht danach auf `error`)."""
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action=action, outcome="success",
        target_type="extension", target_id=ext_id, detail={"state": record.state if record else None},
    )


@router.get("")
async def list_extensions(session: SessionDep, user: CurrentUser) -> list[ExtensionOut]:
    result = await session.execute(select(ExtensionRecord).order_by(ExtensionRecord.id))
    return await _with_setup(session, list(result.scalars().all()), user)


@router.get("/{ext_id}")
async def get_extension(ext_id: str, session: SessionDep, user: CurrentUser) -> ExtensionOut:
    record = await session.get(ExtensionRecord, ext_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Extension.")
    return (await _with_setup(session, [record], user))[0]


@router.post("/{ext_id}/enable", dependencies=[Depends(require_permission("extensions.manage"))])
async def enable_extension(
    ext_id: str, request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser
) -> ExtensionOut:
    try:
        await extensions_service.enable_extension(request.app, session, settings, ext_id)
    except extensions_service.ExtensionLoadError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    record = await session.get(ExtensionRecord, ext_id)
    # Eine bewusste Entscheidung: der Kern schaltet die Erweiterung nie wieder von selbst ein.
    await extensions_service.record_user_choice(session, ext_id)
    await _audit_toggle(session, user, "extension.enabled", ext_id, record)
    return ExtensionOut.from_model(record)


@router.get("/{ext_id}/frontend/index.js")
async def get_extension_frontend_bundle(ext_id: str, request: Request) -> Response:
    """Oeffentlich wie das Kern-Frontend selbst (main.py`s `StaticFiles`-Mount hat
    auch keine Auth) -- ein Bundle ist nur Code, keine Nutzerdaten. Funktioniert
    unabhaengig vom Ladezustand der Extension (Metadaten-Ansicht, wie `GET
    /extensions/{id}`), nicht nur waehrend sie `enabled` ist."""
    discovered = get_extension_runtime().discovered.get(ext_id)
    if discovered is None or not discovered.ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Extension.")
    manifest = discovered.manifest
    assert manifest is not None  # discovered.ok garantiert das
    if not manifest.frontend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diese Extension liefert kein Frontend-Bundle.")

    bundle_path = (discovered.source_path / manifest.frontend).resolve()
    extension_root = discovered.source_path.resolve()
    if extension_root not in bundle_path.parents or not bundle_path.is_file():
        # `manifest.frontend` ist Freitext aus einer (bundled) extension.toml --
        # ohne diese Pruefung koennte ein Manifest mit "../../../etc/passwd" ausserhalb
        # des Extension-Verzeichnisses lesen.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frontend-Bundle nicht gefunden.")
    # Fester URL ohne Inhaltshash im Namen -- ohne `no-cache` hielt der Browser nach
    # einem Redeploy stundenlang das alte Bundle (live gefunden, siehe
    # main.py::_cache_policy_for). `no-cache` = immer per ETag revalidieren. Anders
    # als `StaticFiles` beantwortet ein aus einer Route zurueckgegebenes
    # `FileResponse` `If-None-Match` NICHT selbst (per Test gefunden) -- ohne den
    # Abgleich hier waere jede Revalidierung ein kompletter Neu-Download.
    response = FileResponse(
        bundle_path, media_type="application/javascript",
        headers={"Cache-Control": "no-cache"}, stat_result=bundle_path.stat(),
    )
    if request.headers.get("if-none-match") == response.headers["etag"]:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": response.headers["etag"], "Cache-Control": "no-cache"},
        )
    return response


@router.post("/{ext_id}/disable", dependencies=[Depends(require_permission("extensions.manage"))])
async def disable_extension(ext_id: str, request: Request, session: SessionDep, user: CurrentUser) -> ExtensionOut:
    existing = await session.get(ExtensionRecord, ext_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Extension.")
    await extensions_service.disable_extension(request.app, session, ext_id)
    # KEIN session.refresh() hier: `existing` ist (Identity Map) dasselbe Objekt, das
    # disable_extension() gerade mutiert hat -- ein refresh() wuerde eine frische
    # SELECT ausfuehren und die noch nicht geflushte Mutation mit dem alten DB-Stand
    # ueberschreiben (live gefunden: genau das lieferte "enabled" statt "disabled"
    # zurueck, siehe Abnahmebericht).
    record = await session.get(ExtensionRecord, ext_id)
    await extensions_service.record_user_choice(session, ext_id)
    await _audit_toggle(session, user, "extension.disabled", ext_id, record)
    return ExtensionOut.from_model(record)
