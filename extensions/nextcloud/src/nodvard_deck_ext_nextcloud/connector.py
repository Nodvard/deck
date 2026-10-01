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

import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_DAV = "{DAV:}"


class WebDavError(Exception):
    pass


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
        clean = path.strip("/")
        encoded = quote(clean, safe="/")
        return f"{self._base_url}/remote.php/dav/files/{quote(self._username)}/{encoded}"

    def _dav_prefix(self) -> str:
        """Der WebDAV-Praefix vor jedem LOGISCHEN Pfad -- gebraucht, um ihn aus einem
        `href` wieder herauszurechnen (siehe `_parse_propfind()`s Docstring)."""
        return f"/remote.php/dav/files/{self._username}"

    def _dav_href_path(self, path: str) -> str:
        """Der `href`, den Nextcloud in PROPFIND-Antworten fuer `path` zurueckgibt --
        gebraucht, um den Selbst-Eintrag beim Parsen zu erkennen und zu ueberspringen."""
        clean = path.strip("/")
        return f"{self._dav_prefix()}/{clean}".rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self._dav_url(path)
        try:
            response = await self._ctx.http.request(
                method, url, auth=self._auth, insecure_tls=self._tls_insecure_skip_verify, **kwargs
            )
        except WebDavError:
            raise
        except Exception as exc:  # noqa: BLE001 - jeder Netzwerkfehler wird hier vereinheitlicht (wie ProxmoxConnector)
            raise WebDavError(f"{method} {path} -> {exc}") from exc
        if response.status_code >= 400:
            raise WebDavError(f"{method} {path} -> HTTP {response.status_code}: {response.text[:200]}")
        return response

    _PROPFIND_BODY = (
        '<?xml version="1.0"?>'
        '<d:propfind xmlns:d="DAV:">'
        "<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
        "<d:getcontenttype/></d:prop></d:propfind>"
    )

    async def list_children(self, path: str) -> list[WebDavEntry]:
        response = await self._request("PROPFIND", path, headers={"Depth": "1"}, content=self._PROPFIND_BODY)
        return _parse_propfind(response.content, skip_href=self._dav_href_path(path), strip_prefix=self._dav_prefix())

    async def stat_one(self, path: str) -> WebDavEntry:
        response = await self._request("PROPFIND", path, headers={"Depth": "0"}, content=self._PROPFIND_BODY)
        parsed = _parse_propfind(response.content, skip_href=None, strip_prefix=self._dav_prefix())
        if not parsed:
            raise WebDavError(f"'{path}' nicht gefunden.")
        return parsed[0]

    def open_read(self, path: str, *, offset: int = 0):  # noqa: ANN201 - async Context-Manager von ctx.http.stream()
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        return self._ctx.http.stream(
            "GET", self._dav_url(path), auth=self._auth, headers=headers, insecure_tls=self._tls_insecure_skip_verify
        )

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
