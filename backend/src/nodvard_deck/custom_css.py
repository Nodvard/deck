"""Eigenes CSS der Admins: prüfen, speichern, laden; ausgeliefert wird es als eigene Datei vom selben Server."""

from __future__ import annotations

import re
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

SETTINGS_KEY = "ui.custom_css"
MAX_CSS_BYTES = 64 * 1024

# Ziel in `url()` nach dem Auflösen der Escapes: eingebettete Daten oder ein Pfad auf diesem Server. Alles andere
# (fremde Rechner, `//`, `\`, Leer- und Steuerzeichen) wird abgelehnt, nicht umgeschrieben.
_OWN_PATH_RE = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]*")
# Funktionen, deren Zeichenketten der Browser als Adresse lädt (`image-set("a.png" 1x)`), auch mit Herstellerpräfix.
_LOADING_FUNCTIONS = frozenset({"image", "image-set", "cross-fade", "src"})
_VENDOR_PREFIX_RE = re.compile(r"^-[a-z0-9]+-")
_WHITESPACE = frozenset(" \t\n")
_HEX = frozenset("0123456789abcdefABCDEF")
# Zeichen, die in einer Adresse ohne Anführungszeichen den Browser zu einem ungültigen `url()` bringen.
_BAD_IN_URL = frozenset("\"'(") | {chr(c) for c in (*range(0x09), 0x0B, *range(0x0E, 0x20), 0x7F)}


class UnsafeCss(ValueError):
    """Das CSS würde etwas von außen nachladen oder ist zu groß; die Meldung ist ein deutscher Satz für die Oberfläche."""


def _name_start(c: str) -> bool:
    return c.isascii() and (c.isalpha() or c == "_") or not c.isascii()


def _name_char(c: str) -> bool:
    return _name_start(c) or c.isdigit() and c.isascii() or c == "-"


def is_local_url(target: str) -> bool:
    """`data:` oder ein Pfad auf diesem Server (nicht `//`, kein `\\`, keine Leer- und Steuerzeichen). Gilt auch für
    Logo und Favicon (`branding.py`), weil die App-Seite Bilder nur von dort laden soll (`img-src 'self' data: blob:`)."""
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in target):
        return False
    if target[:5].lower() == "data:":
        return True
    return _OWN_PATH_RE.fullmatch(target) is not None and not target.startswith("//")


def _check_url(target: str) -> None:
    if not is_local_url(target):
        raise UnsafeCss(
            "Im eigenen CSS sind nur eingebettete Bilder (data:) und Pfade auf diesem Server erlaubt, "
            "keine fremden Adressen."
        )


class _Scanner:
    """Liest das CSS genau einmal von vorn nach hinten wie der Browser (CSS Syntax Level 3, Tokenizer), ohne
    reguläre Ausdrücke mit Rückschritten: Die Laufzeit wächst nur linear mit der Länge."""

    def __init__(self, css: str) -> None:
        self.s = css
        self.n = len(css)
        self.i = 0
        # Offene Klammern: Name der Funktion (klein) oder "" für eine einfache Klammer. Wie viele davon `url(` sind,
        # steht extra, damit die Prüfung einer Zeichenkette nicht den ganzen Stapel durchsucht.
        self.open: list[str] = []
        self.open_urls = 0

    def at(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < self.n else ""

    def valid_escape(self, k: int = 0) -> bool:
        return self.at(k) == "\\" and self.at(k + 1) not in ("", "\n")

    def starts_ident(self, k: int = 0) -> bool:
        c = self.at(k)
        if c == "-":
            nxt = self.at(k + 1)
            return nxt != "" and (_name_start(nxt) or nxt == "-" or self.valid_escape(k + 1))
        if c == "\\":
            return self.valid_escape(k)
        return c != "" and _name_start(c)

    def escaped(self) -> str:
        """Nach dem Backslash: ein Zeichen oder bis zu sechs Hex-Ziffern (plus ein Leerzeichen danach)."""
        if self.i >= self.n:
            return "\ufffd"
        c = self.s[self.i]
        if c not in _HEX:
            self.i += 1
            return c
        j = self.i
        while j < self.n and j - self.i < 6 and self.s[j] in _HEX:
            j += 1
        code = int(self.s[self.i:j], 16)
        self.i = j
        if self.i < self.n and self.s[self.i] in _WHITESPACE:
            self.i += 1
        if code == 0 or 0xD800 <= code <= 0xDFFF or code > 0x10FFFF:
            return "\ufffd"
        return chr(code)

    def name(self) -> str:
        out: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if _name_char(c):
                out.append(c)
                self.i += 1
            elif self.valid_escape():
                self.i += 1
                out.append(self.escaped())
            else:
                break
        return "".join(out)

    def string(self, quote: str) -> str:
        self.i += 1
        out: list[str] = []
        while True:
            if self.i >= self.n:
                raise UnsafeCss("Eine Zeichenkette im eigenen CSS ist nicht abgeschlossen.")
            c = self.s[self.i]
            if c == quote:
                self.i += 1
                return "".join(out)
            if c == "\n":
                # Der Browser beendet die Zeichenkette hier und liest danach weiter – das wäre ein Schlupfloch.
                raise UnsafeCss("Eine Zeichenkette im eigenen CSS ist nicht abgeschlossen.")
            if c == "\\":
                if self.at(1) == "":
                    self.i += 1
                elif self.at(1) == "\n":
                    self.i += 2
                else:
                    self.i += 1
                    out.append(self.escaped())
                continue
            out.append(c)
            self.i += 1

    def unquoted_url(self) -> str:
        while self.at() in _WHITESPACE and self.at() != "":
            self.i += 1
        out: list[str] = []
        while True:
            c = self.at()
            if c == "":
                raise UnsafeCss("Eine Adresse in „url()“ ist nicht richtig abgeschlossen.")
            if c == ")":
                self.i += 1
                return "".join(out)
            if c in _WHITESPACE:
                while self.at() in _WHITESPACE and self.at() != "":
                    self.i += 1
                if self.at() == ")":
                    self.i += 1
                    return "".join(out)
                raise UnsafeCss("Eine Adresse in „url()“ ist nicht richtig abgeschlossen.")
            if c in _BAD_IN_URL:
                raise UnsafeCss("Eine Adresse in „url()“ ist nicht richtig abgeschlossen.")
            if c == "\\":
                if not self.valid_escape():
                    raise UnsafeCss("Eine Adresse in „url()“ ist nicht richtig abgeschlossen.")
                self.i += 1
                out.append(self.escaped())
                continue
            out.append(c)
            self.i += 1

    def ident_like(self) -> None:
        name = self.name().lower()
        if self.at() != "(":
            return
        self.i += 1
        if name == "url":
            j = self.i
            while j < self.n and self.s[j] in _WHITESPACE:
                j += 1
            if j < self.n and self.s[j] in "\"'":
                # `url("…")` ist eine Funktion; die Zeichenkette darin prüft `run()`.
                self.open.append("url")
                self.open_urls += 1
                return
            _check_url(self.unquoted_url())
            return
        if _VENDOR_PREFIX_RE.sub("", name, count=1) in _LOADING_FUNCTIONS:
            raise UnsafeCss(f"„{name}()“ ist im eigenen CSS nicht erlaubt.")
        self.open.append(name)

    def run(self) -> None:
        while self.i < self.n:
            c = self.s[self.i]
            if c == "/" and self.at(1) == "*":
                end = self.s.find("*/", self.i + 2)
                self.i = self.n if end < 0 else end + 2
            elif c in _WHITESPACE:
                self.i += 1
            elif c in "\"'":
                text = self.string(c)
                if self.open_urls:
                    _check_url(text)
            elif self.s.startswith("<!--", self.i) or self.s.startswith("-->", self.i):
                # CDO/CDC aus HTML-Zeiten: im Stylesheet bedeutungslos, aber vor `--x` zu lesen (sonst wäre es ein Name).
                raise UnsafeCss("„<!--“ und „-->“ gehören nicht in eigenes CSS.")
            elif c == "@" and self.starts_ident(1):
                self.i += 1
                if self.name().lower() == "import":
                    raise UnsafeCss(
                        "„@import“ ist im eigenen CSS nicht erlaubt, es darf nichts von anderen Adressen nachladen."
                    )
            elif c == "#" and (self.at(1) != "" and _name_char(self.at(1)) or self.valid_escape(1)):
                self.i += 1
                self.name()
            elif self.starts_ident():
                self.ident_like()
            elif c == "(":
                self.open.append("")
                self.i += 1
            elif c == ")":
                if self.open and self.open.pop() == "url":
                    self.open_urls -= 1
                self.i += 1
            else:
                self.i += 1


def check_css(css: str) -> None:
    """Wirft `UnsafeCss`, wenn das CSS etwas von einer fremden Adresse laden würde oder größer als 64 KB ist.

    Geprüft wird mit einer Erlaubnisliste wie im Browser: Zeilenenden erst vereinheitlichen (CR LF, CR und Seitenvorschub
    werden LF), dann in einem Durchgang Kommentare, Zeichenketten, Escapes und `url()` lesen."""
    try:
        size = len(css.encode("utf-8"))
    except UnicodeEncodeError:
        raise UnsafeCss("Das eigene CSS enthält ungültige Zeichen.") from None
    if size > MAX_CSS_BYTES:
        raise UnsafeCss(f"Das eigene CSS ist größer als {MAX_CSS_BYTES // 1024} KB.")
    if "\x00" in css:
        raise UnsafeCss("Das eigene CSS enthält ein Nullzeichen.")
    _Scanner(css.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")).run()


@lru_cache(maxsize=8)
def servable_css(css: str) -> str:
    """Was `GET /branding/custom.css` ausliefert: das gespeicherte CSS, wenn es die Prüfung besteht, sonst nichts.
    Zwischengespeichert, weil die Route ohne Anmeldung erreichbar ist und sonst jede Anfrage bis zu 64 KB neu prüfte."""
    try:
        check_css(css)
    except UnsafeCss:
        return ""
    return css


async def load_custom_css(session: AsyncSession) -> str:
    from .services import settings as settings_service

    value = await settings_service.get_global(session, SETTINGS_KEY, "")
    return value if isinstance(value, str) else ""


async def save_custom_css(session: AsyncSession, css: str, *, updated_by_user_id: str) -> None:
    from .services import settings as settings_service

    await settings_service.set_global(session, SETTINGS_KEY, css, updated_by_user_id=updated_by_user_id)
