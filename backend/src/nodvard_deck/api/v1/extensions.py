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
from ...version import __version__
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission

router = APIRouter(prefix="/extensions", tags=["extensions"])


async def refuse_stale_twin_address(session, ext_id: str) -> None:
    """409, wenn unter der alten Kennung `ext_id` noch ein verwaister Zwilling liegt
    (`services.extensions.is_stale_twin_address`): Wer diese Adresse benutzt, meint womoeglich den Stand des
    Zwillings. Ueber sie darf deshalb nichts geaendert werden, weder am Zwilling noch an der Erweiterung."""
    if await extensions_service.is_stale_twin_address(session, ext_id):
        canonical = get_extension_runtime().canonical(ext_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"„{ext_id}“ ist die alte Kennung von „{canonical}“, und darunter liegt noch ein älterer Stand, "
                f"den die Erweiterung nicht nutzt. Damit nichts verwechselt wird, geht das nur über „{canonical}“."
            ),
        )


async def resolve_extension(
    session, ext_id: str, *, change: bool = False, not_found: str = "Unbekannte Extension."
) -> tuple[str, ExtensionRecord]:
    """Die Kennung aus der Adresse `/extensions/{ext_id}/...` -> (heutige Kennung, Registry-Zeile).

    Eine alte Kennung (`legacy_ids` einer umbenannten Erweiterung) gilt wie die heutige; gelesen und
    geschrieben wird die Zeile der Speicher-Kennung (`services.extensions.get_record`). 404, wenn es keine
    Zeile gibt. `change=True` fuer alles, was etwas aendert: dann zusaetzlich `refuse_stale_twin_address`.
    Ohne `legacy_ids` ist die heutige Kennung immer die aus der Adresse."""
    canonical = get_extension_runtime().canonical(ext_id)
    record = await extensions_service.get_record(session, canonical)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found)
    if change:
        await refuse_stale_twin_address(session, ext_id)
    return canonical, record


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
    bundled: bool = False
    """`true` bei Erweiterungen, die mit Nodvard Deck ausgeliefert werden (`source == "bundled"`). Ihre eigene
    `version` wird bis zur 1.0 nicht gepflegt und sagt nichts; massgeblich ist die Programmversion."""
    display_version: str | None = None
    """Die Version, die Menschen angezeigt wird: bei mitgelieferten Erweiterungen die Programmversion, bei
    nachinstallierten (pip) die eigene `version`."""
    legacy_ids: list[str] = []
    """Fruehere Kennungen einer umbenannten Erweiterung (`legacy_ids` im Manifest). Adressen mit einer alten
    Kennung gelten weiter (`/api/v1/extensions/<alt>/...`, `/api/v1/ext/<alt>/...`, als veraltet markiert) und
    meinen diese Erweiterung; `id` ist immer die heutige Kennung. Liegt unter einer alten Kennung noch ein
    verwaister Zwilling, aendern die Kern-Adressen ueber sie nichts (409). Ohne Umbenennung leer."""

    @classmethod
    def from_model(cls, record: ExtensionRecord) -> "ExtensionOut":
        """`id` ist die heutige Kennung: nach einer Umbenennung liegt der Stand womoeglich noch in der Zeile
        einer alten Kennung (Speicher-Kennung), angezeigt und angesprochen wird die Erweiterung trotzdem
        unter ihrer heutigen."""
        runtime = get_extension_runtime()
        ext_id = runtime.canonical(record.id)
        manifest = record.manifest or {}
        return cls(
            id=ext_id,
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
            has_settings=_has_settings(ext_id),
            bundled=record.source == "bundled",
            display_version=__version__ if record.source == "bundled" else record.version,
            legacy_ids=runtime.legacy_ids_of(ext_id),
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
                schema_for(item.id), dict(record.settings or {}), labels, last, with_test_message=can_manage
            )
            item.setup_reasons = reasons
            item.needs_setup = bool(reasons)
        out.append(item)
    return out


async def _audit_toggle(session, user: User, action: str, ext_id: str, record: ExtensionRecord | None) -> None:  # noqa: ANN001
    """Protokolleintrag fuer das bewusste Ein-/Ausschalten (`state` ist das Ergebnis: eine
    Erweiterung, die beim Laden abstuerzt, steht danach auf `error`)."""
    detail: dict = {"state": record.state if record else None}
    # Bietet die Erweiterung Adressen ohne Anmeldung an (`public=True`, Berechtigung
    # `api.public`), steht das im Protokoll -- wer sie einschaltet, soll das nachlesen koennen.
    public_routes = extensions_service.public_route_prefixes(ext_id)
    if public_routes:
        detail["public_routes"] = public_routes
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action=action, outcome="success",
        target_type="extension", target_id=ext_id, detail=detail,
    )


@router.get("")
async def list_extensions(session: SessionDep, user: CurrentUser) -> list[ExtensionOut]:
    """Alle Erweiterungen der Registry, je einmal unter ihrer heutigen Kennung. Ein verwaister Zwilling
    (Zeile einer alten Kennung, die die umbenannte Erweiterung nicht nutzt) fehlt."""
    runtime = get_extension_runtime()
    result = await session.execute(select(ExtensionRecord).order_by(ExtensionRecord.id))
    records = [r for r in result.scalars().all() if not runtime.is_stale_twin(r.id)]
    items = await _with_setup(session, records, user)
    return sorted(items, key=lambda item: item.id)


@router.get("/{ext_id}")
async def get_extension(ext_id: str, session: SessionDep, user: CurrentUser) -> ExtensionOut:
    """Auch ueber eine alte Kennung (`legacy_ids`); die Antwort nennt die heutige in `id`."""
    _, record = await resolve_extension(session, ext_id)
    return (await _with_setup(session, [record], user))[0]


@router.post("/{ext_id}/enable", dependencies=[Depends(require_permission("extensions.manage"))])
async def enable_extension(
    ext_id: str, request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser
) -> ExtensionOut:
    # Fehlt die Zeile, meldet das Einschalten selbst den Grund (404 mit Text aus dem Dienst).
    await refuse_stale_twin_address(session, ext_id)
    canonical = get_extension_runtime().canonical(ext_id)
    try:
        await extensions_service.enable_extension(request.app, session, settings, canonical)
    except extensions_service.ExtensionLoadError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    record = await extensions_service.get_record(session, canonical)
    # Eine bewusste Entscheidung: der Kern schaltet die Erweiterung nie wieder von selbst ein.
    await extensions_service.record_user_choice(session, canonical)
    await _audit_toggle(session, user, "extension.enabled", canonical, record)
    return ExtensionOut.from_model(record)


@router.get("/{ext_id}/frontend/index.js")
async def get_extension_frontend_bundle(ext_id: str, request: Request) -> Response:
    """Oeffentlich wie das Kern-Frontend selbst (main.py`s `StaticFiles`-Mount hat
    auch keine Auth) -- ein Bundle ist nur Code, keine Nutzerdaten. Funktioniert
    unabhaengig vom Ladezustand der Extension (Metadaten-Ansicht, wie `GET
    /extensions/{id}`), nicht nur waehrend sie `enabled` ist. Auch ueber eine alte Kennung
    (`legacy_ids`): offene Tabs mit einem alten Stand laden so weiter das heutige Bundle."""
    runtime = get_extension_runtime()
    discovered = runtime.discovered.get(runtime.canonical(ext_id))
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
    canonical, _ = await resolve_extension(session, ext_id, change=True)
    await extensions_service.disable_extension(request.app, session, canonical)
    # KEIN session.refresh() hier: die Zeile ist (Identity Map) dasselbe Objekt, das
    # disable_extension() gerade mutiert hat -- ein refresh() wuerde eine frische
    # SELECT ausfuehren und die noch nicht geflushte Mutation mit dem alten DB-Stand
    # ueberschreiben (live gefunden: genau das lieferte "enabled" statt "disabled"
    # zurueck, siehe Abnahmebericht).
    record = await extensions_service.get_record(session, canonical)
    await extensions_service.record_user_choice(session, canonical)
    await _audit_toggle(session, user, "extension.disabled", canonical, record)
    return ExtensionOut.from_model(record)
