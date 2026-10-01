"""Endpunkte ohne Authentifizierung — docs/04-API.md §2.

`/capabilities` ist absichtlich hier und nicht auth-pflichtig: die Login-Seite selbst
(Web) und die Android-App vor dem ersten Login muessen wissen, was der Server kann
(docs/04 §2: "eine APK, viele unterschiedlich bestueckte Server"). Es verraet nur
Struktur (welche Extensions aktiviert sind, welche View-Typen existieren), nie Daten.
"""

from __future__ import annotations

import io
import time
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from nodvard_sdk import API_VERSION
from nodvard_sdk.widgets import (
    ActionsView,
    ChartView,
    GaugeView,
    ListView,
    LogView,
    MarkdownView,
    StatView,
    StatusGridView,
    TableView,
)

_VIEW_CLASSES = (
    StatView, ListView, TableView, ChartView, StatusGridView, GaugeView,
    MarkdownView, ActionsView, LogView,
)

from ...branding import LOGO_CONTENT_TYPES, Branding, find_logo_file, load_branding
from ...ext.runtime import get_extension_runtime
from ...version import __version__
from ..deps import SessionDep, SettingsDep

router = APIRouter(tags=["public"])


@router.get("/health")
async def health(request: Request) -> dict:
    started_at: float = request.app.state.started_at
    return {
        "status": "ok",
        "version": __version__,
        "uptime_s": round(time.monotonic() - started_at, 1),
    }


@router.get("/branding", response_model=Branding)
async def get_branding(session: SessionDep) -> Branding:
    return await load_branding(session)


_EXT_TO_CONTENT_TYPE = {ext: content_type for content_type, ext in LOGO_CONTENT_TYPES.items()}


@router.get("/branding/logo")
async def get_branding_logo(settings: SettingsDep) -> FileResponse:
    """Oeffentlich wie `GET /branding` selbst -- Login-/Setup-Seite zeigen das Logo vor
    jeder Authentifizierung."""
    path = find_logo_file(settings.data_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="Kein Logo hinterlegt.")
    content_type = _EXT_TO_CONTENT_TYPE.get(path.suffix.removeprefix("."), "application/octet-stream")
    return FileResponse(path, media_type=content_type, headers={"Cache-Control": "no-store"})


# --- Als App aufs Handy (PWA) -----------------------------------------------------
# Name, Farben und Symbol kommen aus dem Branding -- "Zum Startbildschirm hinzufuegen"
# zeigt so den eigenen Namen und das eigene Logo statt "Nodvard Deck". Bewusst OHNE
# Service Worker: kein Offline-Cache, der nach einem Update alte Seiten ausliefert.

_ICON_SIZES = (180, 192, 512)


@router.get("/app/manifest.webmanifest")
async def app_manifest(session: SessionDep) -> JSONResponse:
    b = await load_branding(session)
    icons = [
        {"src": f"/api/v1/app/icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"}
        for size in (192, 512)
    ]
    icons.append({"src": "/api/v1/app/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"})
    manifest = {
        "id": "/",
        "name": b.product_name,
        "short_name": b.short_name or b.product_name[:12],
        "description": b.login_subtitle or "Homelab-Cockpit",
        "lang": "de",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": b.colors.background,
        "theme_color": b.colors.background,
        "icons": icons,
        "shortcuts": [
            {"name": "Meldungen", "url": "/notifications"},
            {"name": "Aktionen", "url": "/actions"},
        ],
    }
    return JSONResponse(manifest, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/app/icon-{name}.png")
async def app_icon(name: str, session: SessionDep, settings: SettingsDep) -> Response:
    size_text, _, variant = name.partition("-")
    if not size_text.isdigit() or int(size_text) not in _ICON_SIZES or variant not in ("", "maskable"):
        raise HTTPException(status_code=404, detail="Unbekanntes Symbol.")
    b = await load_branding(session)
    logo = find_logo_file(settings.data_dir)
    logo_key = (str(logo), logo.stat().st_mtime) if logo is not None else None
    png = _render_icon(int(size_text), variant == "maskable", b.product_name[:1].upper() or "N",
                       b.colors.accent, b.colors.accent_strong, b.colors.background, logo_key)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


def _hex(color: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return fallback


@lru_cache(maxsize=32)
def _render_icon(size: int, maskable: bool, letter: str, accent: str, accent_strong: str, background: str,
                 logo_key: tuple[str, float] | None) -> bytes:
    """Symbol mit Pillow: hochgeladenes Logo auf Hintergrund, sonst Farbverlauf der
    Akzentfarben mit dem Anfangsbuchstaben -- wie das Logo oben links im Menue."""
    from PIL import Image, ImageDraw, ImageFont

    a, s = _hex(accent, (14, 165, 233)), _hex(accent_strong, (3, 105, 161))
    bg = _hex(background, (11, 18, 32))
    img = Image.new("RGBA", (size, size), (*bg, 255) if maskable else (0, 0, 0, 0))

    # Diagonaler Verlauf a -> s
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = (round(a[0] + (s[0] - a[0]) * t), round(a[1] + (s[1] - a[1]) * t), round(a[2] + (s[2] - a[2]) * t), 255)

    mask = Image.new("L", (size, size), 0)
    if maskable:
        mask.paste(255, (0, 0, size, size))  # Vollflaeche -- das System schneidet selbst zu
    else:
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255)

    logo_img = None
    if logo_key is not None:
        try:
            logo_img = Image.open(logo_key[0]).convert("RGBA")
        except Exception:  # noqa: BLE001 - SVG o. ae.: dann eben der Buchstabe
            logo_img = None

    if logo_img is not None:
        plate = Image.new("RGBA", (size, size), (*bg, 255))
        img.paste(plate, (0, 0), mask)
        inner = round(size * (0.58 if maskable else 0.72))
        logo_img.thumbnail((inner, inner), Image.LANCZOS)
        img.alpha_composite(logo_img, ((size - logo_img.width) // 2, (size - logo_img.height) // 2))
    else:
        img.paste(grad, (0, 0), mask)
        draw = ImageDraw.Draw(img)
        font_size = round(size * (0.42 if maskable else 0.52))
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:  # sehr altes Pillow ohne Groessenangabe
            font = ImageFont.load_default()
        stroke = max(1, round(size * 0.022))  # die mitgelieferte Schrift ist duenn -- Kontur macht sie fett
        box = draw.textbbox((0, 0), letter, font=font, stroke_width=stroke)
        w, h = box[2] - box[0], box[3] - box[1]
        draw.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), letter, font=font, fill=(255, 255, 255, 255),
                  stroke_width=stroke, stroke_fill=(255, 255, 255, 255))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


class ExtensionSummary(BaseModel):
    id: str
    name: str
    version: str
    icon: str | None = None


class Capabilities(BaseModel):
    api_version: str
    view_types: list[str]
    extensions: list[ExtensionSummary]
    feature_flags: dict[str, bool] = {}


@router.get("/capabilities", response_model=Capabilities)
async def get_capabilities(settings: SettingsDep) -> Capabilities:
    """`feature_flags` war seit seiner Einfuehrung immer `{}` -- kein Aufrufer
    brauchte es. `demo_mode` ist der
    erste echte: die Login-/Setup-Seite kann jetzt sichtbar machen, dass eine
    Installation eine Demo mit Fake-Daten ist, nicht echte Infrastruktur."""
    runtime = get_extension_runtime()
    return Capabilities(
        api_version=API_VERSION,
        view_types=sorted(cls.model_fields["kind"].default for cls in _VIEW_CLASSES),
        extensions=[
            ExtensionSummary(
                id=loaded.manifest.id, name=loaded.manifest.name,
                version=loaded.manifest.version, icon=loaded.manifest.icon,
            )
            for loaded in runtime.loaded.values()
        ],
        feature_flags={"demo_mode": settings.demo_mode},
    )
