"""Duenner WebDAV-Client gegen die Nextcloud-API (docs/02-EXTENSION-API.md
Paragraph 4/7 -- Dateimanager-Quellen), analog zu `nodvard_deck_ext_proxmox.connector`:
nutzt ausschliesslich `ctx.http` (nie einen eigenen `httpx`-Client), damit die
`net.outbound`-Pruefung greift.

WebDAV-Pfad-Konvention: `{base_url}/remote.php/dav/files/{username}/{pfad}`
(Nextclouds eigene, nicht generisches WebDAV). Authentifizierung ueber HTTP Basic
mit Benutzername + App-Passwort (kein OAuth in dieser Runde -- ein App-Passwort ist
in Nextcloud selbst erzeugbar, jederzeit widerrufbar, ohne das Hauptpasswort im
Vault zu halten).

Streaming ueber `ctx.http.stream()` (WP-11-Ergaenzung in `ext/context.py` -- fehlte
bis dahin komplett, siehe dortiger Docstring) fuer Downloads; Uploads streamen ueber
`ctx.http.request(..., content=<async iterator>)`, das httpx nativ unterstuetzt.

**`tls_insecure_skip_verify` (Nachtrag, live gegen das echte cloud.home.example
gefunden):** dasselbe Problem wie bei proxmox/backups (WP-8) -- `cloud.home.example`
sitzt hinter einem selbstsignierten/lokalen Zertifikat (Nginx Proxy Manager),
`ctx.http`s Standard-Client verifiziert TLS strikt und lehnt das ab. Identischer
Opt-in ueber `net.outbound.insecure_tls` wie bei `ProxmoxConnector`."""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit

import httpx

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_DAV = "{DAV:}"


class WebDavError(Exception):
    """Fehler beim Zugriff auf Nextcloud (aus, falsche Zugangsdaten, Ordner fehlt): kein Programmfehler,
    der Text wird der Person gezeigt und im Protokoll ohne Traceback vermerkt."""

    readable = True


def redirect_text(status_code: int | None = None) -> str:
    """Der Satz fuer jede Weiterleitung. Der `Location`-Header kommt vom fremden Server und steht
    darum nirgends im Text, auch nicht als Adressvorschlag: ein Angreifer koennte sich sonst eine
    Adresse aussuchen, die die Person dann in die Einstellungen uebernimmt."""
    code = f" (HTTP {status_code})" if status_code else ""
    return (
        f"Nextcloud leitet auf eine andere Adresse um{code}. "
        "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
        "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
        "Trage in den Einstellungen die endgültige Adresse ein."
    )


def _url_is_valid(url: str) -> bool:
    try:
        _ = httpx.URL(url).host  # `.host` entschluesselt Punycode und scheitert an einem kaputten Namen
    except (httpx.InvalidURL, ValueError):
        return False
    return True


# Die Schritte, in denen httpx eine Weiterleitung zusammenbaut (siehe `_came_from_redirect`).
_REDIRECT_STEPS = frozenset(
    {"_build_redirect_request", "_redirect_url", "_redirect_method", "_redirect_headers", "_redirect_stream"}
)


def _came_from_redirect(exc: BaseException) -> bool:
    """Ob `exc` beim Zusammenbauen einer Weiterleitung entstand (erkannt an der Stelle im Traceback,
    ersatzweise am Wort "location" im Text). Waehlt nur zwischen festen Saetzen."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name in _REDIRECT_STEPS:
            return True
        tb = tb.tb_next
    return "location" in str(exc).lower()


def _quotes_the_answer(exc: BaseException) -> bool:
    """Ob der Text von `exc` die fremde Antwort (oder den eigenen Header samt Wert) zitieren kann. Solche
    Ausnahmen haengen nie als Ursache an einer Meldung: wer die Ursachenkette abschreibt, truege ihren Text
    sonst weiter."""
    return isinstance(exc, (httpx.ProtocolError, httpx.InvalidURL, ValueError))


def _transport_problem(method: str, path: str, url: str, exc: BaseException) -> str:
    """Satz zu einem Fehler beim Senden oder Empfangen. httpx baut eine Weiterleitung auch dann, wenn es
    ihr nicht folgt, und scheitert an einem kaputten `Location`-Header mit `InvalidURL` (keine
    `HTTPError`), mit einem Punycode-Fehler (`ValueError`) oder, schon beim Lesen der Kopfzeilen, mit
    einem `ProtocolError`. Deren Text zitiert die fremde Antwort (bzw. den eigenen Header samt Wert) und
    kommt darum nie in den Satz."""
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        # httpx-Zeitueberschreitungen haben keinen Text: ohne diesen Zweig stuende nur "PROPFIND / -> ".
        return "Nextcloud antwortet nicht (Zeitüberschreitung)."
    if isinstance(exc, httpx.ProtocolError):
        if _came_from_redirect(exc):
            return redirect_text()
        if isinstance(exc, httpx.LocalProtocolError):
            return "Die Anfrage an die Nextcloud ließ sich nicht senden. Prüfe die Einstellungen."
        return (
            "Nextcloud hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
            "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
        )
    if isinstance(exc, (httpx.InvalidURL, ValueError)):
        if not _url_is_valid(url):
            return "Die Adresse der Nextcloud ist ungültig. Prüfe sie in den Einstellungen."
        if _came_from_redirect(exc):
            return redirect_text()
        return "Die Anfrage an die Nextcloud ließ sich nicht senden. Prüfe die Einstellungen."
    return f"{method} {path} -> {str(exc).strip() or type(exc).__name__}"


def _status_problem(method: str, path: str, status_code: int) -> str | None:
    """`None` bei 2xx. Eine 3xx-Antwort ist nie ein Erfolg (PUT, MKCOL, MOVE und DELETE wuerden sonst als
    erledigt gelten, obwohl nichts passiert ist), und sie nennt nie ihr Ziel. Feste Saetze ohne den
    Antworttext: der kommt vom Server, und ein Satz wie "Neue Adresse: ... bitte eintragen" darin stuende
    sonst unveraendert im Dateimanager."""
    if 200 <= status_code < 300:
        return None
    if 300 <= status_code < 400:
        return redirect_text(status_code)
    where = path if path.startswith("/") else f"/{path}"
    if status_code == 401:
        return "Nextcloud hat die Anmeldung abgelehnt (HTTP 401). Prüfe Benutzername und App-Passwort in den Einstellungen."
    if status_code == 403:
        return f"Nextcloud verweigert den Zugriff auf {where} (HTTP 403)."
    if status_code == 404:
        return f"Nextcloud findet {where} nicht (HTTP 404). Prüfe auch Adresse und Benutzernamen in den Einstellungen."
    if status_code == 405 and method == "MKCOL":
        return f"Den Ordner {where} gibt es schon (HTTP 405)."
    if status_code == 412 and method == "MOVE":
        return "Am Ziel gibt es schon eine Datei oder einen Ordner mit diesem Namen (HTTP 412)."
    if status_code == 423:
        return f"{where} ist in der Nextcloud gerade gesperrt (HTTP 423). Versuche es später erneut."
    if status_code == 503:
        return "Nextcloud ist gerade nicht bereit (HTTP 503), zum Beispiel im Wartungsmodus. Versuche es später erneut."
    if status_code == 507:
        return "In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507)."
    if status_code >= 500:
        return f"Die Nextcloud meldet einen Fehler (HTTP {status_code}). Versuche es später erneut."
    if status_code >= 400:
        return f"Nextcloud hat die Anfrage abgelehnt ({method} {where}, HTTP {status_code})."
    return f"Nextcloud hat unerwartet geantwortet ({method} {where}, HTTP {status_code})."


class InvalidPathError(WebDavError, FileNotFoundError):
    """Der Pfad wuerde den eigenen Dateibereich verlassen (`..`, Backslash, NUL,
    kodierte Trenner). Es geht keine Anfrage raus. Als `FileNotFoundError` wird das im
    Dateimanager der Kern zu einer 404-Antwort."""


def _clean_segments(path: str) -> list[str]:
    """Zerlegt einen logischen Pfad in seine Teile und lehnt alles ab, womit sich ein
    Pfad aus dem Dateibereich des Benutzers herausschieben liesse: ein `.`- oder
    `..`-Teil (auch kodiert, z. B. `%2e%2e`), Backslash, NUL und kodierte
    Schraegstriche. Leere Teile (`a//b`, fuehrender/abschliessender Schraegstrich)
    fallen weg."""
    if "\x00" in path or "\\" in path:
        raise InvalidPathError("Ungültiger Pfad.")
    segments: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        # Auch mehrfach kodierte Formen pruefen (%2e%2e, %252e%252e, %2F, %5C ...).
        decoded = segment
        for _ in range(5):
            if decoded in (".", "..") or any(c in decoded for c in ("/", "\\", "\x00")):
                raise InvalidPathError("Ungültiger Pfad.")
            again = unquote(decoded)
            if again == decoded:
                break
            decoded = again
        else:
            raise InvalidPathError("Ungültiger Pfad.")
        segments.append(segment)
    return segments


class WebDavEntry:
    def __init__(self, *, name: str, path: str, is_dir: bool, size: int | None, modified_at: datetime | None, mime: str | None) -> None:
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.size = size
        self.modified_at = modified_at
        self.mime = mime


def _parse_propfind(body: bytes, *, skip_href: str | None, strip_prefix: str) -> list[WebDavEntry]:
    """`skip_href`, falls gesetzt, wird uebersprungen -- PROPFIND mit `Depth: 1`
    liefert IMMER zuerst einen Eintrag fuer die angefragte Ressource SELBST, bevor
    die Kind-Eintraege folgen (WebDAV-Standardverhalten, kein Nextcloud-Sonderfall).
    Bei `Depth: 0` gibt es dagegen NUR den Eintrag der Ressource selbst -- den soll
    `stat_one()` gerade NICHT ueberspringen, deshalb `skip_href=None` dort.

    **Live gefunden (WP-11-Boot-Test im echten Browser):** `entry.path` enthielt
    bisher den ROHEN `href` aus der XML-Antwort, also inklusive des WebDAV-Praefixes
    `/remote.php/dav/files/<username>` -- ein Klick auf einen Ordner im Explorer
    schickte diesen (falschen, doppelt-praefigierten) Pfad an `list_dir()` zurueck,
    `_dav_url()` haengte den Praefix ein ZWEITES Mal an, und die Anfrage ging gegen
    einen nicht existierenden, doppelt verschachtelten Pfad (404). `strip_prefix`
    macht `entry.path` zum LOGISCHEN, wurzel-relativen Pfad, den auch `stat()`/
    `list_dir()`/`open_read()` etc. selbst entgegennehmen -- rundtrip-faehig."""
    root = ET.fromstring(body)
    entries: list[WebDavEntry] = []
    for response in root.findall(f"{_DAV}response"):
        href_el = response.find(f"{_DAV}href")
        if href_el is None or href_el.text is None:
            continue
        href = href_el.text.rstrip("/")
        # Der href kommt URL-kodiert (Leerzeichen -> %20, Umlaute -> %C3%A4), skip_href
        # ist unkodiert -- nur fuer den Vergleich dekodieren, sonst erscheint ein Ordner
        # wie "My scripts (Homelab)" in sich selbst.
        if skip_href is not None and _unquote_path_segment(href) == skip_href.rstrip("/"):
            continue

        propstat = response.find(f"{_DAV}propstat")
        prop = propstat.find(f"{_DAV}prop") if propstat is not None else None
        if prop is None:
            continue

        resourcetype = prop.find(f"{_DAV}resourcetype")
        is_dir = resourcetype is not None and resourcetype.find(f"{_DAV}collection") is not None

        size_el = prop.find(f"{_DAV}getcontentlength")
        size = int(size_el.text) if size_el is not None and size_el.text else None

        modified_el = prop.find(f"{_DAV}getlastmodified")
        modified_at = None
        if modified_el is not None and modified_el.text:
            try:
                modified_at = parsedate_to_datetime(modified_el.text)
            except (TypeError, ValueError):
                modified_at = None

        mime_el = prop.find(f"{_DAV}getcontenttype")
        mime = mime_el.text if mime_el is not None else None

        name = href.rsplit("/", 1)[-1]
        logical_path = href[len(strip_prefix):] if href.startswith(strip_prefix) else href
        entries.append(
            WebDavEntry(
                name=_unquote_path_segment(name), path=_unquote_path_segment(logical_path) or "/",
                is_dir=is_dir, size=None if is_dir else size, modified_at=modified_at,
                mime=None if is_dir else mime,
            )
        )
    return entries


def _unquote_path_segment(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)


class Download:
    """Die geoeffnete Datei aus `NextcloudConnector.open_read()`. `aiter_bytes()` uebersetzt Fehler beim
    Lesen (Verbindung weg, kaputter Koerper) in `WebDavError`. Das geschieht hier und nicht im
    Context-Manager: dort liefe die Ausnahme durch `__aexit__`, und die neue Meldung truege die alte (samt
    Teilen der fremden Antwort im Text) als `__context__` mit."""

    def __init__(self, response: Any, path: str, url: str) -> None:
        self._response = response
        self._path = path
        self._url = url
        self.status_code: int = response.status_code
        self.headers = response.headers

    async def aiter_bytes(self, chunk_size: int | None = None) -> AsyncIterator[bytes]:
        chunks = self._response.aiter_bytes(chunk_size)
        problem: str | None = None
        while problem is None:
            try:
                chunk = await chunks.__anext__()
            except StopAsyncIteration:
                return
            except (httpx.HTTPError, TimeoutError) as exc:
                problem = _transport_problem("GET", self._path, self._url, exc)
                if not _quotes_the_answer(exc):
                    raise WebDavError(problem) from exc
            else:
                yield chunk
        raise WebDavError(problem)


class NextcloudConnector:
    def __init__(
        self,
        ctx: "ExtensionContext",
        *,
        base_url: str,
        username: str,
        password: str,
        tls_insecure_skip_verify: bool = False,
    ) -> None:
        self._ctx = ctx
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._auth = (username, password)
        self._tls_insecure_skip_verify = tls_insecure_skip_verify

    @property
    def host(self) -> str:
        return urlsplit(self._base_url).hostname or self._base_url

    def _dav_url(self, path: str) -> str:
        encoded = "/".join(quote(segment, safe="") for segment in _clean_segments(path))
        user_part = f"{self._base_url}/remote.php/dav/files/{quote(self._username, safe='')}/"
        url = f"{user_part}{encoded}"
        # Zweite Absicherung: die fertige Adresse muss im Dateibereich des Benutzers
        # bleiben, so wie der Server sie sieht (einmal dekodiert, `.`/`..` aufgeloest).
        expected = posixpath.normpath(unquote(urlsplit(user_part).path))
        parts = urlsplit(url)
        resolved = posixpath.normpath(unquote(parts.path))
        if (resolved != expected and not resolved.startswith(expected + "/")) or parts.query or parts.fragment:
            raise InvalidPathError("Ungültiger Pfad.")
        return url

    def _dav_prefix(self) -> str:
        """Der WebDAV-Praefix vor jedem LOGISCHEN Pfad -- gebraucht, um ihn aus einem
        `href` wieder herauszurechnen (siehe `_parse_propfind()`s Docstring)."""
        return f"/remote.php/dav/files/{self._username}"

    def _dav_href_path(self, path: str) -> str:
        """Der `href`, den Nextcloud in PROPFIND-Antworten fuer `path` zurueckgibt --
        gebraucht, um den Selbst-Eintrag beim Parsen zu erkennen und zu ueberspringen."""
        clean = "/".join(_clean_segments(path))
        return f"{self._dav_prefix()}/{clean}".rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self._dav_url(path)
        problem: str | None = None
        try:
            response = await self._ctx.http.request(
                method, url, auth=self._auth, insecure_tls=self._tls_insecure_skip_verify, **kwargs
            )
        except WebDavError:
            raise
        except Exception as exc:  # noqa: BLE001 - jeder Netzwerkfehler wird hier vereinheitlicht (wie ProxmoxConnector)
            problem = _transport_problem(method, path, url, exc)
            if not _quotes_the_answer(exc):
                raise WebDavError(problem) from exc
        else:
            problem = _status_problem(method, path, response.status_code)
        if problem is not None:
            # Ausserhalb des `except`-Blocks: sonst hinge die Ausnahme von httpx (mit Teilen der fremden
            # Antwort im Text) als `__context__` an der Meldung.
            raise WebDavError(problem)
        return response

    _PROPFIND_BODY = (
        '<?xml version="1.0"?>'
        '<d:propfind xmlns:d="DAV:">'
        "<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
        "<d:getcontenttype/></d:prop></d:propfind>"
    )

    @staticmethod
    def _parsed(body: bytes, *, skip_href: str | None, strip_prefix: str) -> list[WebDavEntry]:
        try:
            return _parse_propfind(body, skip_href=skip_href, strip_prefix=strip_prefix)
        except (ET.ParseError, ValueError) as exc:
            # Z. B. eine Anmeldeseite (HTML) oder ein Proxy-Fehler mit Status 200 statt der WebDAV-Antwort.
            raise WebDavError(
                "Nextcloud hat keine gültige WebDAV-Antwort geschickt. Prüfe die Adresse in den Einstellungen "
                "(die Adresse der Nextcloud, ohne /remote.php/dav)."
            ) from exc

    async def list_children(self, path: str) -> list[WebDavEntry]:
        response = await self._request("PROPFIND", path, headers={"Depth": "1"}, content=self._PROPFIND_BODY)
        return self._parsed(response.content, skip_href=self._dav_href_path(path), strip_prefix=self._dav_prefix())

    async def stat_one(self, path: str) -> WebDavEntry:
        response = await self._request("PROPFIND", path, headers={"Depth": "0"}, content=self._PROPFIND_BODY)
        parsed = self._parsed(response.content, skip_href=None, strip_prefix=self._dav_prefix())
        if not parsed:
            raise WebDavError(f"'{path}' nicht gefunden.")
        return parsed[0]

    @asynccontextmanager
    async def open_read(self, path: str, *, offset: int = 0) -> AsyncIterator[Download]:
        """Streamt die Datei. Die Antwort wird erst geprueft, dann herausgegeben: eine Fehlerseite oder
        eine Weiterleitung waere sonst der "Inhalt" der Datei. Fehler beim Lesen uebersetzt `Download`."""
        url = self._dav_url(path)
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        problem: str | None = None
        async with AsyncExitStack() as stack:
            try:
                response = await stack.enter_async_context(
                    self._ctx.http.stream(
                        "GET", url, auth=self._auth, headers=headers, insecure_tls=self._tls_insecure_skip_verify
                    )
                )
            except WebDavError:
                raise
            except Exception as exc:  # noqa: BLE001 - wie in `_request`
                problem = _transport_problem("GET", path, url, exc)
                if not _quotes_the_answer(exc):
                    raise WebDavError(problem) from exc
            else:
                problem = _status_problem("GET", path, response.status_code)
            if problem is not None:
                # Ausserhalb des `except`-Blocks, aus demselben Grund wie in `_request`.
                raise WebDavError(problem)
            yield Download(response, path, url)

    async def upload(self, path: str, stream: AsyncIterator[bytes]) -> None:
        await self._request("PUT", path, content=stream)

    async def mkdir(self, path: str) -> None:
        await self._request("MKCOL", path)

    async def remove(self, path: str) -> None:
        await self._request("DELETE", path)

    async def rename(self, src: str, dst: str) -> None:
        await self._request(
            "MOVE", src, headers={"Destination": self._dav_url(dst), "Overwrite": "F"}
        )
