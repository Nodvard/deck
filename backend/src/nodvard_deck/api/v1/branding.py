"""Branding aendern -- docs/04-API.md §... (`PUT /branding`), docs/01-ARCHITECTURE.md §6.

Getrennt von `public.router` (dort liegt nur `GET /branding`/`GET /branding/logo`,
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
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from nodvard_sdk import max_body_bytes

from ...branding import (
    LOGO_CONTENT_TYPES, MAX_LOGO_BYTES, Branding, clean_support_url, load_branding, save_branding, save_logo_file,
)
from ...svg_safe import UnsafeSvg, check_svg
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission

router = APIRouter(
    prefix="/branding",
    tags=["branding"],
    dependencies=[Depends(require_permission("branding.write"))],
)


@router.put("", response_model=Branding)
async def put_branding(payload: Branding, session: SessionDep, user: CurrentUser) -> Branding:
    if payload.support_url is not None and payload.support_url.strip():
        cleaned = clean_support_url(payload.support_url)
        if cleaned is None:
            raise HTTPException(
                status_code=422, detail="Der Support-Link muss mit http://, https:// oder mailto: beginnen.",
            )
        payload.support_url = cleaned
    else:
        payload.support_url = None
    await save_branding(session, payload, updated_by_user_id=user.id)
    return await load_branding(session)


@router.post("/logo", response_model=Branding)
@max_body_bytes(MAX_LOGO_BYTES)
async def upload_logo(request: Request, session: SessionDep, settings: SettingsDep, user: CurrentUser) -> Branding:
    """Rohkoerper-Upload wie `POST /files/{source_id}/upload` (api/v1/files.py) --
    dieselbe Konvention statt zusaetzlich `multipart/form-data` einzufuehren. Der
    Content-Type-Header traegt hier (anders als bei Dateien) tatsaechliche Bedeutung:
    er entscheidet ueber die Dateiendung und wird gegen die Allowlist geprueft."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    extension = LOGO_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(status_code=415, detail=f"Nicht unterstützter Bildtyp: {content_type or '(keiner)'}")

    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail=f"Logo zu gross (max. {MAX_LOGO_BYTES // 1024} KB).")
    content = await request.body()
    if len(content) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail=f"Logo zu gross (max. {MAX_LOGO_BYTES // 1024} KB).")

    if extension == "svg":
        try:
            check_svg(content)
        except UnsafeSvg as exc:
            raise HTTPException(status_code=422, detail=f"SVG-Logo abgelehnt: {exc} Nimm ein PNG oder ein bereinigtes SVG.") from exc

    save_logo_file(settings.data_dir, content, extension)

    branding = await load_branding(session)
    branding.logo_url = "/api/v1/branding/logo"
    await save_branding(session, branding, updated_by_user_id=user.id)
    return await load_branding(session)
