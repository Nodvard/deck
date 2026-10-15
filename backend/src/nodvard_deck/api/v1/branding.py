"""Branding aendern -- docs/04-API.md §... (`PUT /branding`), docs/01-ARCHITECTURE.md §6.

Getrennt von `public.router` (dort liegen nur `GET /branding`, `GET /branding/logo` und `GET /branding/favicon`,
bewusst ohne Authentifizierung, weil die Login-Seite es braucht): Schreiben ist eine
administrative Handlung und braucht eine eigene Berechtigung, `branding.write` --
NICHT `settings.write`, `branding.py`s eigener Modul-Docstring und
`api/v1/settings.py`s Gap-Fill-Begruendung nennen das ausdruecklich als eigenen,
bewusst nicht mitgebauten Endpunkt. WP-13 baut ihn jetzt, weil der
Erstinbetriebnahme-Assistent ihn braucht (Schritt 2: Produktname/Farben setzen).

`POST /branding/logo`: war lange
bewusst nicht Teil des Umfangs ("logo_url nimmt bereits eine beliebige URL entgegen") --
jetzt gebaut, weil eine gehostete Logo-URL in der Praxis eine Huerde ist, die die
meisten Installationen nicht selbst loesen koennen/wollen.

`POST /branding/favicon` und die Regel fuer `logo_url`/`favicon_url` in `PUT /branding`: Die Seite soll Bilder nur
noch von diesem Server laden (`img-src 'self' data: blob:`, vorerst als `main.SPA_CSP_REPORT_ONLY` nur gemeldet). Neue
Werte muessen deshalb ein eigener Pfad oder `data:image/…` sein; ein schon gespeicherter fremder Wert bleibt gespeichert,
bis jemand ihn ersetzt.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from nodvard_sdk import max_body_bytes
from pydantic import BaseModel, Field

from ...branding import (
    FAVICON_CONTENT_TYPES,
    FAVICON_PATH,
    LOGO_CONTENT_TYPES,
    LOGO_PATH,
    MAX_FAVICON_BYTES,
    MAX_LOGO_BYTES,
    Branding,
    clean_support_url,
    image_matches_type,
    is_local_image_url,
    load_branding,
    remove_image_file,
    save_branding,
    save_favicon_file,
    save_logo_file,
)
from ...custom_css import (
    MAX_CSS_BYTES,
    UnsafeCss,
    check_css,
    load_custom_css,
    save_custom_css,
)
from ...services import audit as audit_service
from ...svg_safe import UnsafeSvg, check_svg
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission

router = APIRouter(
    prefix="/branding",
    tags=["branding"],
    dependencies=[Depends(require_permission("branding.write"))],
)


@router.put("", response_model=Branding)
async def put_branding(payload: Branding, session: SessionDep, settings: SettingsDep, user: CurrentUser) -> Branding:
    if payload.support_url is not None and payload.support_url.strip():
        cleaned = clean_support_url(payload.support_url)
        if cleaned is None:
            raise HTTPException(
                status_code=422, detail="Der Support-Link muss mit http://, https:// oder mailto: beginnen.",
            )
        payload.support_url = cleaned
    else:
        payload.support_url = None
    current = await load_branding(session)
    for field, label in (("logo_url", "Das Logo"), ("favicon_url", "Das Favicon")):
        value = (getattr(payload, field) or "").strip() or None
        # Ein schon gespeicherter fremder Wert bleibt erlaubt, damit das Speichern anderer Felder nicht scheitert.
        if value is not None and value != getattr(current, field) and not is_local_image_url(value):
            raise HTTPException(
                status_code=422,
                detail=f"{label} muss auf diesem Server liegen (Hochladen) oder als data:image/… eingebettet sein, "
                       "fremde Adressen lädt die Seite nicht.",
            )
        setattr(payload, field, value)
    await save_branding(session, payload, updated_by_user_id=user.id)
    # „Entfernen“ (oder ein anderes Bild): Die hochgeladene Datei wäre sonst weiter öffentlich abrufbar.
    for value, path, stem in ((payload.logo_url, LOGO_PATH, "logo"), (payload.favicon_url, FAVICON_PATH, "favicon")):
        if value != path:
            remove_image_file(settings.data_dir, stem)
    return await load_branding(session)


async def _read_image(request: Request, types: dict[str, str], max_bytes: int, label: str) -> tuple[bytes, str]:
    """Rohkoerper-Upload wie `POST /files/{source_id}/upload` (api/v1/files.py) --
    dieselbe Konvention statt zusaetzlich `multipart/form-data` einzufuehren. Der
    Content-Type-Header traegt hier (anders als bei Dateien) tatsaechliche Bedeutung:
    er entscheidet ueber die Dateiendung und wird gegen die Allowlist geprueft."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    extension = types.get(content_type)
    if extension is None:
        raise HTTPException(status_code=415, detail=f"Nicht unterstützter Bildtyp: {content_type or '(keiner)'}")

    too_big = f"{label} zu groß (max. {max_bytes // 1024} KB)."
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > max_bytes:
        raise HTTPException(status_code=413, detail=too_big)
    content = await request.body()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=too_big)
    if not content:
        raise HTTPException(status_code=422, detail=f"{label}: Die Datei ist leer.")
    if not image_matches_type(content, extension):
        raise HTTPException(
            status_code=422, detail=f"{label}: Der Inhalt der Datei passt nicht zum Typ {content_type}.",
        )

    if extension == "svg":
        try:
            check_svg(content)
        except UnsafeSvg as exc:
            raise HTTPException(
                status_code=422, detail=f"SVG-{label} abgelehnt: {exc} Nimm ein PNG oder ein bereinigtes SVG.",
            ) from exc
    return content, extension


@router.post("/logo", response_model=Branding)
@max_body_bytes(MAX_LOGO_BYTES)
async def upload_logo(request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser) -> Branding:
    content, extension = await _read_image(request, LOGO_CONTENT_TYPES, MAX_LOGO_BYTES, "Logo")
    save_logo_file(settings.data_dir, content, extension)

    branding = await load_branding(session)
    branding.logo_url = LOGO_PATH
    await save_branding(session, branding, updated_by_user_id=user.id)
    return await load_branding(session)


@router.post("/favicon", response_model=Branding)
async def upload_favicon(request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser) -> Branding:
    """Symbol im Browser-Tab (PNG, ICO oder SVG, max. 512 KB). Ersetzt die frühere freie Adresse, weil die Seite
    Bilder nur noch von diesem Server lädt. Kein eigenes `max_body_bytes`: Die allgemeine Grenze (1 MiB) liegt
    darüber, die 512 KB prüft `_read_image`."""
    content, extension = await _read_image(request, FAVICON_CONTENT_TYPES, MAX_FAVICON_BYTES, "Favicon")
    save_favicon_file(settings.data_dir, content, extension)

    branding = await load_branding(session)
    branding.favicon_url = FAVICON_PATH
    await save_branding(session, branding, updated_by_user_id=user.id)
    return await load_branding(session)


class CustomCss(BaseModel):
    css: str = Field(default="", max_length=MAX_CSS_BYTES)
    """Eigenes CSS für alle Nutzer. Erlaubt sind eingebettete Bilder (`data:`) und Pfade auf diesem Server, kein `@import`."""


@router.get("/custom-css", response_model=CustomCss)
async def get_custom_css(session: SessionDep) -> CustomCss:
    return CustomCss(css=await load_custom_css(session))


@router.put("/custom-css", response_model=CustomCss)
async def put_custom_css(payload: CustomCss, session: SessionDep, user: CurrentUser) -> CustomCss:
    """Speichert das eigene CSS; 422 mit einem Satz, wenn es etwas von außen laden würde oder größer als 64 KB ist."""
    try:
        check_css(payload.css)
    except UnsafeCss as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await save_custom_css(session, payload.css, updated_by_user_id=user.id)
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="branding.custom_css.saved", outcome="success",
        detail={"bytes": len(payload.css.encode("utf-8"))},
    )
    return CustomCss(css=payload.css)
