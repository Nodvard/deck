"""Eigene App-Kacheln im Cockpit ("+ App hinzufuegen") -- Pruefung der Eingaben und Datenbankzugriffe
(Tabelle `custom_apps`, docs/03-DATA-MODEL.md, docs/04-API.md "Eigene Apps").

**Was eine eigene App ist:** ein von Hand angelegter Link (Name, Adresse, Symbol, Gruppe, optional ein
Server dazu). Wer keine Service-Matrix hat -- oder zusaetzlich Router, NAS-Oberflaeche oder Pi-hole im
Cockpit sehen will --, braucht dafuer keinen Code.

**Der Server ruft die Adresse nie selbst ab.** Es gibt keine Statusabfrage (kein "laeuft"/"laeuft nicht"
fuer eigene Apps): damit kann ueber diese Funktion niemand den Server dazu bringen, interne Adressen
anzufragen (SSRF). Die Adresse ist nur ein Link, den der Browser der Person oeffnet, die darauf klickt.
Eine Statusabfrage waere ein eigenes Paket mit eigener Sicherheitsbetrachtung.

**Warum die Pruefung streng ist:** Die Adresse landet als Link auf der Startseite ALLER Nutzer. Erlaubt ist
darum nur `http://` und `https://` -- `javascript:`, `data:`, `file:` & Co. sind als Link ein Angriff auf
den, der klickt. Zugangsdaten in der Adresse (`http://user:pass@...`) werden abgelehnt (stuenden sonst fuer
alle lesbar in der Datenbank und taeuschen mit `https://google.com@boese.example` ein anderes Ziel vor);
ebenso Leer-/Steuerzeichen und Rueckwaertsschraegstriche (Browser lesen `\\` wie `/`).

**Symbol: feste Auswahl oder ein Emoji -- nie eine Bild-Adresse.** Ein frei waehlbares Bild haette den
Browser jedes Nutzers beim Anzeigen der Startseite zu einem fremden Server gefuehrt (Tracking, wer wann das
Dashboard oeffnet), und eine `http://`-Bild-Adresse waere auf einer `https`-Seite ein Mixed-Content-Fehler.
Die Namen stammen aus lucide (das Frontend bundelt sie ohnehin, `frontend/src/lib/appIcons.ts`; ein Test
haelt beide Listen deckungsgleich).
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CustomApp, Host

NAME_MAX = 60
GROUP_MAX = 40
URL_MAX = 1000
EMOJI_MAX_CODEPOINTS = 12
"""Laengste echte Emoji-Folge (Familie mit Hautfarben) liegt darunter."""

SORT_MAX = 100_000
MAX_APPS = 200
"""Mehr Kacheln als das braucht kein Homelab; die Obergrenze haelt die Startseite und `GET /overview` klein."""

APP_ICONS: tuple[str, ...] = (
    "globe", "link", "router", "network", "wifi", "shield-check", "lock", "key-round", "server", "hard-drive",
    "database", "cloud", "cpu", "monitor", "laptop", "smartphone", "tv", "printer", "camera", "cctv",
    "video", "film", "music", "gamepad-2", "book-open", "file-text", "folder", "image", "mail", "calendar",
    "activity", "chart-bar", "thermometer", "lightbulb", "house", "zap", "git-branch", "terminal", "code", "box",
    "container", "package", "wrench", "settings", "bell", "rss", "users", "plug", "bot", "star",
)

_SKIN_TONES = range(0x1F3FB, 0x1F400)
_TEXT_STYLE_EMOJI = {0x203C, 0x2049, 0x2139, *range(0x2194, 0x219A), 0x21A9, 0x21AA, 0x3030, 0x303D}
"""Emoji, die Unicode nicht als "Symbol" fuehrt (Ausrufezeichen-Paar, Info, Pfeile, Welle)."""
_EMOJI_JOINERS = {0x200D, 0xFE0F}  # Zero-Width-Joiner und Emoji-Darstellung
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
_HOST_RE = re.compile(r"[\w.-]+")
_SCHEME_RE = re.compile(r"https?://", re.IGNORECASE)

_ICON_ERROR = "Wähle ein Symbol aus der Liste oder gib ein einzelnes Emoji ein."


class CustomAppError(ValueError):
    """Eine Eingabe ist nicht in Ordnung; der Text ist fuer die Person gedacht. (`ValueError`, damit
    pydantic daraus eine 422-Antwort mit genau diesem Text macht.)"""


class AppServiceError(Exception):
    """Basis der Fehler beim Speichern; die Meldung ist fuer Nutzer gedacht."""


class AppNotFoundError(AppServiceError):
    pass


class AppLimitError(AppServiceError):
    pass


class UnknownHostError(AppServiceError):
    pass


class BadOrderError(AppServiceError):
    pass


# --- Pruefung der Eingaben -----------------------------------------------------------------------------


_ZERO_WIDTH_JOINER = 0x200D
_EMOJI_BLOCKS = range(0x1F000, 0x1FB00)
"""Die Emoji-Bloecke (Mahjong bis "Symbole und Piktogramme, Erweiterung A"). Nur hier gilt ein Zeichen, das diese
Python-Version noch nicht kennt, als (neues) Emoji -- ausserhalb waeren es unsichtbare, nicht vergebene Zeichen."""
_BLANK_LETTERS = {0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800}
"""Fuell- und Leerzeichen, die Unicode als Buchstabe (Hangul-Fueller) bzw. Symbol (Braille-Leerzeichen) fuehrt, die
aber nichts anzeigen: sie zaehlen nicht als sichtbares Zeichen."""


def _is_emoji_block(code: int) -> bool:
    return code in _EMOJI_BLOCKS


def _is_unwanted(ch: str) -> bool:
    """Steuer- und Formatzeichen (Zeilenumbruch, NUL, Rechts-nach-Links-Umschalter, ...), Zeilen- und
    Absatztrenner (U+2028/U+2029) und alle Leerzeichen ausser dem normalen. Ausgenommen der Zero-Width-Joiner (haelt
    Emoji-Folgen wie "Person am Computer" zusammen) und Zeichen, die diese Python-Version noch nicht kennt, die aber
    in einem Emoji-Block liegen (neuere Emoji)."""
    code = ord(ch)
    if code == _ZERO_WIDTH_JOINER:
        return False
    category = unicodedata.category(ch)
    if category == "Cn" and _is_emoji_block(code):
        return False
    if category in ("Zl", "Zp"):
        return True
    if category == "Zs":
        return ch != " "
    return category.startswith("C")


def _has_bad_characters(value: str) -> bool:
    return any(_is_unwanted(ch) for ch in value)


def _is_visible(ch: str) -> bool:
    """Ein Zeichen, das etwas anzeigt: Buchstabe, Ziffer, Symbol oder Satzzeichen (auch ein neues Emoji)."""
    code = ord(ch)
    if code in _BLANK_LETTERS:
        return False
    category = unicodedata.category(ch)
    return category[0] in "LNSP" or (category == "Cn" and _is_emoji_block(code))


def _has_visible_character(value: str) -> bool:
    return any(_is_visible(ch) for ch in value)


def clean_name(value: str) -> str:
    text = value.strip()
    if not text:
        raise CustomAppError("Bitte gib einen Namen an.")
    if len(text) > NAME_MAX:
        raise CustomAppError(f"Der Name ist zu lang (höchstens {NAME_MAX} Zeichen).")
    if _has_bad_characters(text):
        raise CustomAppError("Der Name darf keine Zeilenumbrüche oder Steuerzeichen enthalten.")
    if not _has_visible_character(text):
        # Nur Fuell- oder Verbindungszeichen: fuer die Person ist das kein Name.
        raise CustomAppError("Bitte gib einen Namen an.")
    return text


def clean_group(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > GROUP_MAX:
        raise CustomAppError(f"Die Gruppe ist zu lang (höchstens {GROUP_MAX} Zeichen).")
    if _has_bad_characters(text):
        raise CustomAppError("Die Gruppe darf keine Zeilenumbrüche oder Steuerzeichen enthalten.")
    if not _has_visible_character(text):
        raise CustomAppError("Die Gruppe braucht mindestens ein sichtbares Zeichen.")
    return text


def clean_url(value: str) -> str:
    """Nur `http://`/`https://` mit Rechnername oder IP-Adresse; gibt die getrimmte Adresse unveraendert zurueck."""
    text = value.strip()
    if not text:
        raise CustomAppError("Bitte gib eine Adresse an.")
    if len(text) > URL_MAX:
        raise CustomAppError(f"Die Adresse ist zu lang (höchstens {URL_MAX} Zeichen).")
    if not _SCHEME_RE.match(text):
        raise CustomAppError("Die Adresse muss mit http:// oder https:// beginnen.")
    if "\\" in text or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in text):
        raise CustomAppError("In der Adresse dürfen keine Leerzeichen, Steuerzeichen oder Rückwärts-Schrägstriche stehen.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise CustomAppError("Der Port in der Adresse ist ungültig.") from None
    if "@" in parts.netloc:
        raise CustomAppError("Bitte keinen Benutzernamen und kein Passwort in die Adresse schreiben.")
    host = parts.hostname
    if not host or not _valid_host(host):
        raise CustomAppError("Die Adresse braucht einen Rechnernamen oder eine IP-Adresse, zum Beispiel http://192.168.2.1.")
    if port == 0:
        raise CustomAppError("Der Port in der Adresse ist ungültig.")
    return text


def _valid_host(host: str) -> bool:
    if ":" in host:  # IPv6-Adresse (die Klammern hat urlsplit schon entfernt)
        try:
            ipaddress.IPv6Address(host.split("%", 1)[0])
        except ValueError:
            return False
        return "%" not in host
    return _HOST_RE.fullmatch(host) is not None


def stored_url(value: str | None) -> str | None:
    """Die gespeicherte Adresse fuer die Anzeige: nur, wenn sie die Pruefung besteht -- selbst wenn jemand
    die Datenbank von Hand veraendert hat, kommt nie ein `javascript:`-Link in die Oberflaeche."""
    if not value:
        return None
    try:
        return clean_url(value)
    except CustomAppError:
        return None


def url_origin(url: str) -> str:
    """`https://host:port` ohne Pfad, Parameter und Zugangsdaten -- das, was ins Protokoll darf."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # eine von Hand veraenderte Datenbank: das Protokoll soll daran nicht scheitern
        return "(ungültige Adresse)"
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme.lower()}://{host}{f':{port}' if port else ''}"


def _is_symbol(ch: str) -> bool:
    """Ein Symbol (`So`), auch ein Emoji, das diese Python-Version noch nicht kennt (nur in den Emoji-Bloecken)."""
    if ord(ch) in _BLANK_LETTERS:
        return False
    category = unicodedata.category(ch)
    return (
        category == "So"
        or ord(ch) in _TEXT_STYLE_EMOJI
        or (category == "Cn" and _is_emoji_block(ord(ch)))
    )


def _is_emoji(text: str) -> bool:
    if not 0 < len(text) <= EMOJI_MAX_CODEPOINTS:
        return False
    symbols = 0
    for ch in text:
        code = ord(ch)
        if _is_symbol(ch):
            symbols += 1
        elif code in _EMOJI_JOINERS or code in _SKIN_TONES:
            continue
        else:
            return False
    return symbols > 0 and _is_symbol(text[0])


def clean_icon(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text in APP_ICONS or _is_emoji(text):
        return text
    raise CustomAppError(_ICON_ERROR)


def clean_color(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if not _COLOR_RE.fullmatch(text):
        raise CustomAppError("Die Farbe muss wie #3b82f6 aussehen (ein # und sechs Ziffern oder Buchstaben a–f).")
    return text.lower()


# --- Datenbank --------------------------------------------------------------------------------------------


async def list_apps(session: AsyncSession) -> list[CustomApp]:
    stmt = select(CustomApp).order_by(CustomApp.sort_order, func.lower(CustomApp.name), CustomApp.id)
    return list((await session.execute(stmt)).scalars().all())


async def host_names(session: AsyncSession, host_ids: Iterable[str | None]) -> dict[str, str]:
    """Host-ID -> Anzeigename, nur fuer Server, die es (noch) gibt."""
    ids = {h for h in host_ids if h}
    if not ids:
        return {}
    rows = (await session.execute(select(Host.id, Host.display_name, Host.name).where(Host.id.in_(ids)))).all()
    return {row.id: (row.display_name or row.name) for row in rows}


async def _require_host(session: AsyncSession, host_id: str) -> None:
    if (await session.execute(select(Host.id).where(Host.id == host_id))).first() is None:
        raise UnknownHostError("Der gewählte Server existiert nicht (mehr).")


async def create_app(
    session: AsyncSession,
    *,
    user_id: str | None,
    name: str,
    url: str,
    icon: str | None = None,
    color: str | None = None,
    group: str | None = None,
    open_in_new_tab: bool = True,
    host_id: str | None = None,
    sort_order: int | None = None,
) -> CustomApp:
    """Legt die App an (die Werte sind schon geprueft, siehe `clean_*`). Ohne `sort_order` kommt sie ans Ende."""
    total = (await session.execute(select(func.count()).select_from(CustomApp))).scalar_one()
    if total >= MAX_APPS:
        raise AppLimitError(f"Es gibt schon {MAX_APPS} Apps – das ist die Obergrenze. Lösche zuerst eine, die du nicht mehr brauchst.")
    if host_id:
        await _require_host(session, host_id)
    if sort_order is None:
        highest = (await session.execute(select(func.max(CustomApp.sort_order)))).scalar_one()
        sort_order = 0 if highest is None else highest + 1
    app = CustomApp(
        name=name, url=url, icon=icon, color=color, group_name=group, sort_order=sort_order,
        open_in_new_tab=open_in_new_tab, host_id=host_id or None, created_by_user_id=user_id,
    )
    session.add(app)
    await session.flush()
    return app


async def get_app(session: AsyncSession, app_id: str) -> CustomApp:
    app = await session.get(CustomApp, app_id)
    if app is None:
        raise AppNotFoundError("Diese App gibt es nicht (mehr).")
    return app


async def update_app(session: AsyncSession, app: CustomApp, changes: dict[str, Any]) -> list[str]:
    """Wendet die (schon geprueften) Aenderungen an; Schluessel wie die API-Felder (`group` statt `group_name`).
    Gibt die Namen der Felder zurueck, die sich wirklich geaendert haben."""
    if changes.get("host_id"):
        await _require_host(session, changes["host_id"])
    changed: list[str] = []
    for key, value in changes.items():
        attribute = "group_name" if key == "group" else key
        if key == "host_id":
            value = value or None
        if getattr(app, attribute) != value:
            setattr(app, attribute, value)
            changed.append(key)
    if changed:
        await session.flush()
    return changed


async def delete_app(session: AsyncSession, app: CustomApp) -> None:
    await session.delete(app)
    await session.flush()


async def reorder(session: AsyncSession, ids: list[str]) -> list[CustomApp]:
    """Die genannten Apps stehen danach in dieser Reihenfolge ganz vorn, alle anderen dahinter in ihrer
    bisherigen Reihenfolge; die Positionen werden neu von 0 an durchgezaehlt."""
    if len(set(ids)) != len(ids):
        raise BadOrderError("Jede App darf in der Reihenfolge nur einmal vorkommen.")
    rows = await list_apps(session)
    by_id = {a.id: a for a in rows}
    if any(i not in by_id for i in ids):
        raise AppNotFoundError("Eine der genannten Apps gibt es nicht (mehr).")
    named = set(ids)
    ordered = [by_id[i] for i in ids] + [a for a in rows if a.id not in named]
    for position, app in enumerate(ordered):
        if app.sort_order != position:
            app.sort_order = position
    await session.flush()
    return ordered


async def unlink_host(session: AsyncSession, host_id: str) -> None:
    """Wird ein Server geloescht, bleiben seine Apps stehen, nur ohne Server-Bezug. (Die Datenbank macht das
    mit `ON DELETE SET NULL` ohnehin -- hier ausdruecklich, damit es nicht vom SQLite-Pragma abhaengt.)"""
    await session.execute(update(CustomApp).where(CustomApp.host_id == host_id).values(host_id=None))
