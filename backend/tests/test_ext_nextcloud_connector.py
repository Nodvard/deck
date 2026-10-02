"""`nodvard_deck_ext_nextcloud.connector` -- reines PROPFIND-XML-Parsing und
Pfad-Aufbau, kein Netzwerk."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nextcloud" / "src"))

from nodvard_deck_ext_nextcloud.capabilities import NextcloudFileSource  # noqa: E402
from nodvard_deck_ext_nextcloud.connector import InvalidPathError, NextcloudConnector, WebDavError, _parse_propfind  # noqa: E402

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


class _RecordingHttp:
    """Merkt sich jede Anfrage; antwortet auf alles mit 207/leerem Inhalt."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))

        class _Resp:
            status_code = 207
            content = b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"/>'
            text = ""

        return _Resp()

    def stream(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        raise AssertionError("stream darf hier nicht erreicht werden")


class _RecordingCtx:
    def __init__(self) -> None:
        self.http = _RecordingHttp()


_BAD_PATHS = [
    "..",
    "../calendars/alice",
    "/../../trashbin/alice/trash",
    "a/../../x",
    "%2e%2e/calendars",
    "docs/%2E%2E/%2e%2e/addressbooks",
    "%252e%252e/x",
    "docs%2F..%2F..%2Fcalendars",
    "a/%5C../b",
    "a\\..\\b",
    "docs/a\x00b",
]


def _source() -> tuple[NextcloudFileSource, _RecordingHttp]:
    ctx = _RecordingCtx()
    connector = NextcloudConnector(ctx=ctx, base_url="https://cloud.example.com", username="alice", password="x")
    return NextcloudFileSource(ctx, connector), ctx.http


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", _BAD_PATHS)
async def test_every_source_method_rejects_paths_leaving_the_file_area_without_a_request(bad):
    from pathlib import PurePosixPath

    source, http = _source()
    p = PurePosixPath(bad)
    ok = PurePosixPath("/docs/a.txt")

    async def _stream():
        yield b"x"

    async def _drain():
        async for _ in source.open_read(p):
            pass

    calls = [
        lambda: source.list_dir(p),
        lambda: source.stat(p),
        _drain,
        lambda: source.open_write(p, _stream()),
        lambda: source.mkdir(p),
        lambda: source.remove(p),
        lambda: source.rename(p, ok),
        lambda: source.rename(ok, p),
    ]
    for call in calls:
        with pytest.raises(FileNotFoundError):
            await call()
    assert http.requests == []


@pytest.mark.asyncio
async def test_search_never_requests_outside_the_file_area():
    from pathlib import PurePosixPath

    source, http = _source()
    hits = [h async for h in source.search("x", root=PurePosixPath("../calendars"))]
    assert hits == []
    assert http.requests == []


def test_invalid_path_error_is_a_webdav_error_and_a_file_not_found_error():
    assert issubclass(InvalidPathError, WebDavError)
    assert issubclass(InvalidPathError, FileNotFoundError)


@pytest.mark.parametrize(
    ("path", "tail"),
    [
        ("docs/Rechnung #1?.txt", "docs/Rechnung%20%231%3F.txt"),
        ("/Rechnungen/März/Übersicht.pdf", "Rechnungen/M%C3%A4rz/%C3%9Cbersicht.pdf"),
        ("a b/50%.txt", "a%20b/50%25.txt"),
        ("docs//a.txt/", "docs/a.txt"),
        ("...", "..."),
        ("/", ""),
    ],
)
def test_dav_url_encodes_each_segment_so_special_characters_stay_in_the_path(path, tail):
    connector = NextcloudConnector(ctx=None, base_url="https://cloud.example.com/sub", username="al/ice", password="x")
    assert connector._dav_url(path) == f"https://cloud.example.com/sub/remote.php/dav/files/al%2Fice/{tail}"


def test_dav_url_with_a_normal_path_stays_below_the_users_file_area():
    from urllib.parse import urlsplit

    connector = NextcloudConnector(ctx=None, base_url="https://cloud.example.com", username="alice", password="x")
    parts = urlsplit(connector._dav_url("/docs/x?y#z"))
    assert parts.path.startswith("/remote.php/dav/files/alice/")
    assert parts.query == "" and parts.fragment == ""


@pytest.mark.parametrize("bad", ["a/./b", "./a", "a/."])
def test_dav_url_rejects_dot_segments(bad):
    connector = NextcloudConnector(ctx=None, base_url="https://cloud.example.com", username="alice", password="x")
    with pytest.raises(InvalidPathError):
        connector._dav_url(bad)


@pytest.mark.parametrize("segments", [[".."], ["docs", "..", ".."], ["%2e%2e"], ["a", "%2E%2E", "%2e%2e"]])
def test_dav_url_second_guard_catches_dot_segments_on_its_own(monkeypatch, segments):
    """Die Praefix-Pruefung der fertigen Adresse muss auch dann greifen, wenn die
    Pruefung der einzelnen Teile etwas durchliesse."""
    module_globals = NextcloudConnector._dav_url.__globals__
    monkeypatch.setitem(module_globals, "_clean_segments", lambda path: segments)
    monkeypatch.setitem(module_globals, "quote", lambda value, safe="": value)
    connector = NextcloudConnector(ctx=None, base_url="https://cloud.example.com", username="alice", password="x")
    with pytest.raises(InvalidPathError):
        connector._dav_url("egal")
