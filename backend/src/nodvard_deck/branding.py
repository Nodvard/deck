"""Branding als Konfigurationswert — docs/01-ARCHITECTURE.md §6.

**Dies ist der einzige Ort im Kern, an dem ein Produktname/eine Farbe als Literal stehen
darf** (siehe `scripts/check_core_purity.py`). Alles hier ist nur der *Seed* fuer die
Erstinbetriebnahme — sobald die Tabelle `settings` einen Wert hat, gewinnt der.

Aendern nach dem ersten Start: `PUT /api/v1/branding`, nicht diese Datei.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SETTINGS_KEY_PREFIX = "branding."

LOGO_CONTENT_TYPES = {
    "image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp",
}
"""`logo_url` akzeptierte bisher
nur eine bereits gehostete URL -- kein Weg, ohne eigenen Webserver ein Logo zu
hinterlegen. `POST /branding/logo` (api/v1/branding.py) speichert die Datei jetzt
selbst unter `<data_dir>/branding/`, `GET /branding/logo` (api/v1/public.py, oeffentlich
wie `GET /branding` selbst -- die Login-Seite braucht das Logo vor jeder
Authentifizierung) liefert sie aus. Absichtlich eine kleine, feste Allowlist statt
beliebiger Content-Types -- ein Logo ist ein Bild, kein generischer Datei-Upload."""

MAX_LOGO_BYTES = 2 * 1024 * 1024

UNTRUSTED_FILE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}
"""Header für alles, was Nutzer hochladen und das im selben Origin wie das Dashboard
ausgeliefert wird. Öffnet jemand die Adresse direkt, läuft kein Skript: `sandbox` gibt
dem Dokument einen eigenen, leeren Origin, `nosniff` verbietet dem Browser, den Typ zu
erraten. Ein `<img src>` ist davon nicht betroffen, die Regel gilt nur für die Antwort
selbst, nicht für die Seite, die sie einbettet."""

SUPPORT_URL_SCHEMES = ("http", "https", "mailto")


def clean_support_url(value: str | None) -> str | None:
    """Gibt die Adresse zurück, wenn sie mit `http:`, `https:` oder `mailto:` beginnt,
    sonst `None`. Leerzeichen am Rand werden entfernt; Steuerzeichen und Leerraum mitten
    in der Adresse (`java\tscript:`) machen sie ungültig."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    scheme, sep, rest = text.partition(":")
    if not sep or scheme.lower() not in SUPPORT_URL_SCHEMES or not rest:
        return None
    return text


def logo_dir(data_dir: Path) -> Path:
    return data_dir / "branding"


def find_logo_file(data_dir: Path) -> Path | None:
    """Es kann nur EIN aktuelles Logo geben -- bei jedem Upload werden zuerst alle
    vorhandenen `logo.*`-Dateien entfernt (save_logo_file), damit hier nie mehrere
    unterschiedlicher Endung gleichzeitig existieren."""
    d = logo_dir(data_dir)
    if not d.is_dir():
        return None
    matches = sorted(d.glob("logo.*"))
    return matches[0] if matches else None


def save_logo_file(data_dir: Path, content: bytes, extension: str) -> Path:
    d = logo_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("logo.*"):
        old.unlink()
    path = d / f"logo.{extension}"
    path.write_bytes(content)
    return path


class BrandingColors(BaseModel):
    accent: str = "#0ea5e9"
    accent_strong: str = "#0369a1"
    background: str = "#0b1220"
    surface: str = "#111a2b"
    text: str = "#e6ebf5"


class Branding(BaseModel):
    """Antwortform von `GET /api/v1/branding` — ohne Auth, die Login-Seite braucht es."""

    product_name: str
    short_name: str
    logo_url: str | None = None
    favicon_url: str | None = None
    login_subtitle: str | None = None
    support_url: str | None = None
    colors: BrandingColors = BrandingColors()


DEFAULT_BRANDING = Branding(
    product_name="Nodvard Deck",
    short_name="Nodvard Deck",
    login_subtitle="Homelab-Kommandozentrale",
    colors=BrandingColors(),
)
"""Arbeitsstand-Branding dieser Installation. Ein Kaeufer des
generischen Kerns sieht stattdessen seinen eigenen Wert aus `settings` — dieser Default
greift nur, solange niemand ihn ueberschrieben hat."""


_FIELD_NAMES = tuple(Branding.model_fields.keys())


async def load_branding(session: AsyncSession) -> Branding:
    """Liest `settings` (scope='global', key='branding.<feld>') und fuellt Luecken aus
    `DEFAULT_BRANDING`. Muss lokal importieren, um einen Zirkelimport mit models zu
    vermeiden (Setting-Modell importiert seinerseits nichts aus branding.py)."""
    from .models import Setting

    rows = await session.execute(
        select(Setting).where(
            Setting.scope == "global",
            Setting.key.like(f"{SETTINGS_KEY_PREFIX}%"),
        )
    )
    overrides: dict[str, Any] = {}
    for row in rows.scalars():
        field = row.key.removeprefix(SETTINGS_KEY_PREFIX)
        if field in _FIELD_NAMES:
            overrides[field] = row.value

    data = DEFAULT_BRANDING.model_dump()
    data.update(overrides)
    # Ein früher gespeicherter, ungültiger Wert (z. B. `javascript:`) wird nicht als Link angezeigt.
    data["support_url"] = clean_support_url(data.get("support_url"))
    return Branding.model_validate(data)


async def save_branding(session: AsyncSession, branding: Branding, *, updated_by_user_id: str | None = None) -> None:
    """Schreibt jedes Feld als eigene `settings`-Zeile (upsert). Wird von
    `PUT /api/v1/branding` in einem spaeteren Arbeitspaket aufgerufen."""
    from .db import utcnow
    from .models import Setting

    payload = branding.model_dump()
    for field, value in payload.items():
        key = f"{SETTINGS_KEY_PREFIX}{field}"
        existing = await session.get(Setting, {"key": key, "scope": "global", "user_id": ""})
        if existing is None:
            session.add(
                Setting(
                    key=key,
                    scope="global",
                    user_id="",
                    value=value,
                    updated_at=utcnow(),
                    updated_by_user_id=updated_by_user_id,
                )
            )
        else:
            existing.value = value
            existing.updated_at = utcnow()
            existing.updated_by_user_id = updated_by_user_id
