"""`nodvard_deck_ext_nextcloud.connector` -- reines PROPFIND-XML-Parsing und
Pfad-Aufbau, kein Netzwerk."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nextcloud" / "src"))

from nodvard_deck_ext_nextcloud.connector import NextcloudConnector, _parse_propfind  # noqa: E402

_LIST_RESPONSE = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/docs/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Mon, 01 Jan 2024 00:00:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/docs/notes.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>42</d:getcontentlength>
        <d:getlastmodified>Tue, 02 Jan 2024 00:00:00 GMT</d:getlastmodified>
        <d:getcontenttype>text/plain</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/docs/sub%20dir/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""

_STAT_RESPONSE = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/docs/notes.txt</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>42</d:getcontentlength>
        <d:getcontenttype>text/plain</d:getcontenttype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


_PREFIX = "/remote.php/dav/files/alice"


def test_parse_propfind_skips_the_self_entry_for_depth_one_listings():
    entries = _parse_propfind(_LIST_RESPONSE, skip_href=f"{_PREFIX}/docs", strip_prefix=_PREFIX)
    assert {e.name for e in entries} == {"notes.txt", "sub dir"}


def test_parse_propfind_marks_collections_as_directories_without_size():
    entries = _parse_propfind(_LIST_RESPONSE, skip_href=f"{_PREFIX}/docs", strip_prefix=_PREFIX)
    by_name = {e.name: e for e in entries}
    assert by_name["sub dir"].is_dir is True
    assert by_name["sub dir"].size is None
    assert by_name["notes.txt"].is_dir is False
    assert by_name["notes.txt"].size == 42
    assert by_name["notes.txt"].mime == "text/plain"


def test_parse_propfind_url_decodes_percent_encoded_names():
    """Live-relevant: ein Ordnername mit Leerzeichen kommt von Nextcloud
    prozent-kodiert zurueck ('sub%20dir') -- ohne Dekodierung waere `entry.name`
    fuer die UI und fuer nachfolgende Pfad-Operationen falsch."""
    entries = _parse_propfind(_LIST_RESPONSE, skip_href=f"{_PREFIX}/docs", strip_prefix=_PREFIX)
    names = {e.name for e in entries}
    assert "sub dir" in names
    assert "sub%20dir" not in names


def test_parse_propfind_with_skip_href_none_keeps_the_self_entry():
    """`stat()` (Depth: 0) hat NUR den Selbst-Eintrag -- der darf nicht uebersprungen
    werden, sonst waere `stat()` fuer JEDEN Pfad immer leer."""
    entries = _parse_propfind(_STAT_RESPONSE, skip_href=None, strip_prefix=_PREFIX)
    assert len(entries) == 1
    assert entries[0].name == "notes.txt"
    assert entries[0].size == 42


def test_parse_propfind_strips_the_webdav_prefix_so_paths_are_rundtrip_able():
    """Live gefunden im Boot-Test (echter Browser): `entry.path` enthielt vor
    diesem Fix den ROHEN `href` inklusive `/remote.php/dav/files/<user>`-Praefix. Ein
    Klick auf einen Ordner im Explorer schickte diesen (falschen) Pfad an
    `list_dir()` zurueck, `_dav_url()` haengte den Praefix ein ZWEITES Mal an, und
    die Anfrage ging gegen einen nicht existierenden, doppelt verschachtelten Pfad
    (404 im echten Mock). `entry.path` muss deshalb der
    LOGISCHE, wurzel-relative Pfad sein, den `list_dir()`/`stat()` selbst
    entgegennehmen."""
    entries = _parse_propfind(_LIST_RESPONSE, skip_href=f"{_PREFIX}/docs", strip_prefix=_PREFIX)
    by_name = {e.name: e for e in entries}
    assert by_name["notes.txt"].path == "/docs/notes.txt"
    assert by_name["sub dir"].path == "/docs/sub dir"
    assert _PREFIX not in by_name["notes.txt"].path


def test_dav_url_encodes_path_and_username():
    connector = NextcloudConnector(ctx=None, base_url="https://cloud.example.com", username="ali ce", password="x")
    assert connector._dav_url("docs/sub dir/notes.txt") == (
        "https://cloud.example.com/remote.php/dav/files/ali%20ce/docs/sub%20dir/notes.txt"
    )


def test_dav_url_strips_leading_and_trailing_slashes_from_path():
    connector = NextcloudConnector(ctx=None, base_url="https://cloud.example.com/", username="alice", password="x")
    assert connector._dav_url("/docs/") == "https://cloud.example.com/remote.php/dav/files/alice/docs"


def _folder_response(self_href: str, child_href: str) -> bytes:
    return f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>{self_href}</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>{child_href}</d:href>
    <d:propstat><d:prop><d:resourcetype/><d:getcontentlength>7</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>
""".encode()


class _FakeHttp:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.urls: list[str] = []

    async def request(self, method, url, **kwargs):
        self.urls.append(url)

        class _Resp:
            status_code = 207
            content = self.body
            text = ""

        return _Resp()


class _FakeCtx:
    def __init__(self, body: bytes) -> None:
        self.http = _FakeHttp(body)



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("folder", "encoded"),
    [
        ("/My scripts (Homelab)", "My%20scripts%20(Homelab)"),
        ("/Rechnungen/März", "Rechnungen/M%C3%A4rz"),
    ],
)
async def test_list_children_skips_the_url_encoded_self_entry(folder, encoded):
    """Nextcloud liefert den `href` URL-kodiert (Leerzeichen -> %20,
    Umlaute -> %C3%A4). Der Vergleich mit dem unkodierten Selbst-Pfad schlug fehl,
    der Ordner erschien in sich selbst und die Suche lief im Kreis."""
    body = _folder_response(f"{_PREFIX}/{encoded}/", f"{_PREFIX}/{encoded}/notiz.txt")
    connector = NextcloudConnector(ctx=_FakeCtx(body), base_url="https://cloud.example.com", username="alice", password="x")

    entries = await connector.list_children(folder)

    assert [(e.name, e.path, e.is_dir) for e in entries] == [("notiz.txt", f"{folder}/notiz.txt", False)]
