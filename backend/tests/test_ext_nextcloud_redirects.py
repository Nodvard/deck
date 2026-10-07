"""nextcloud-Extension: Weiterleitungen (3xx) und unbrauchbare Antworten.

Eine 3xx-Antwort ist nie ein Erfolg (sonst gelten Anlegen, Hochladen, Umbenennen und Loeschen als
erledigt, obwohl der Server nichts getan hat), und der `Location`-Header des fremden Servers steht
nirgends in einem Text. `ctx.http` folgt Weiterleitungen nie."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import httpx
import pytest
from raw_http_helpers import BROKEN_ANSWERS, REDIRECT_ANSWERS, raw_http_server, whole_chain
from test_ext_nextcloud import _auth_header, _cleanup_sys_path, _setup_nextcloud  # noqa: F401 -- autouse-Fixture

from nodvard_deck.ext.runtime import get_extension_runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nextcloud" / "src"))

from nodvard_deck_ext_nextcloud.capabilities import NextcloudFileSource  # noqa: E402
from nodvard_deck_ext_nextcloud.connector import NextcloudConnector, WebDavError  # noqa: E402

EVIL_LOCATION = "https://angreifer.example/login?x=geheim"
BASE = "https://cloud.example.com"

MULTISTATUS = (
    '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response>'
    "<d:href>/remote.php/dav/files/alice/docs/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>"
    "</d:prop></d:propstat></d:response></d:multistatus>"
)


class FakeHttp:
    """Steht fuer `ctx.http`: `request()` und `stream()` mit `insecure_tls`, dahinter ein `MockTransport`."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        self._client = httpx.AsyncClient(transport=httpx.MockTransport(_record))

    async def request(self, method, url, *, insecure_tls=False, **kwargs):
        return await self._client.request(method, url, **kwargs)

    def stream(self, method, url, *, insecure_tls=False, **kwargs):
        return self._client.stream(method, url, **kwargs)


class Ctx:
    def __init__(self, handler) -> None:
        self.http = FakeHttp(handler)


def _connector(handler, base_url: str = BASE) -> tuple[NextcloudConnector, FakeHttp]:
    ctx = Ctx(handler)
    return NextcloudConnector(ctx, base_url=base_url, username="alice", password="x"), ctx.http


def _redirect(code: int = 302, location: str | None = EVIL_LOCATION):
    headers = {"Location": location} if location is not None else {}
    return lambda request: httpx.Response(code, headers=headers, text="<html>weiter</html>")


def _chain(exc: BaseException) -> str:
    parts, seen, current = [], set(), exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)


async def _drain(connector: NextcloudConnector, path: str = "/docs/a.txt") -> None:
    async with connector.open_read(path) as response:
        async for _ in response.aiter_bytes():
            pass


async def _upload(connector: NextcloudConnector) -> None:
    async def body():
        yield b"abc"

    await connector.upload("/docs/a.txt", body())


CALLS = {
    "list": lambda c: c.list_children("/docs"),
    "stat": lambda c: c.stat_one("/docs/a.txt"),
    "download": lambda c: _drain(c),
    "upload": _upload,
    "mkdir": lambda c: c.mkdir("/docs/neu"),
    "remove": lambda c: c.remove("/docs/a.txt"),
    "rename": lambda c: c.rename("/docs/a.txt", "/docs/b.txt"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("call", list(CALLS))
@pytest.mark.parametrize("code", [301, 302, 307, 308])
async def test_redirect_is_an_error_for_every_method_and_never_names_the_target(call, code):
    connector, http = _connector(_redirect(code))

    with pytest.raises(WebDavError) as exc:
        await CALLS[call](connector)

    text = str(exc.value)
    assert text.startswith(f"Nextcloud leitet auf eine andere Adresse um (HTTP {code}).")
    assert "endgültige Adresse" in text
    chain = _chain(exc.value)
    assert "angreifer" not in chain.lower() and "geheim" not in chain and "weiter</html>" not in chain
    assert all(r.url.host == "cloud.example.com" for r in http.requests), "der Weiterleitung wurde nicht gefolgt"


@pytest.mark.asyncio
@pytest.mark.parametrize("call", list(CALLS))
@pytest.mark.parametrize(
    "location",
    ["javascript:alert(1)", "http://cloud.example.com:angreifer-text/", "http://xn--angreifer-ey9f.example/login"],
)
async def test_unusable_location_header_is_no_crash_and_never_in_the_text(call, location):
    connector, _ = _connector(_redirect(302, location))

    with pytest.raises(WebDavError) as exc:
        await CALLS[call](connector)

    assert str(exc.value).startswith("Nextcloud leitet auf eine andere Adresse um")
    chain = _chain(exc.value)
    assert "angreifer" not in chain.lower() and "alert" not in chain


@pytest.mark.asyncio
async def test_redirect_without_location_header_gives_the_same_sentence():
    connector, _ = _connector(_redirect(301, None))
    with pytest.raises(WebDavError, match=r"leitet auf eine andere Adresse um \(HTTP 301\)"):
        await connector.remove("/docs/a.txt")


@pytest.mark.asyncio
async def test_own_address_that_httpx_cannot_use_is_named_as_such():
    connector, http = _connector(_redirect(), base_url="https://cloud.example.com:abc")
    with pytest.raises(WebDavError, match="Adresse der Nextcloud ist ungültig"):
        await connector.stat_one("/")
    with pytest.raises(WebDavError, match="Adresse der Nextcloud ist ungültig"):
        await _drain(connector)
    assert http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["list", "stat"])
async def test_a_login_page_instead_of_webdav_xml_is_a_clear_error(call):
    connector, _ = _connector(lambda request: httpx.Response(200, text="<html><body>Anmelden"))
    with pytest.raises(WebDavError, match="keine gültige WebDAV-Antwort"):
        await CALLS[call](connector)


@pytest.mark.asyncio
async def test_a_non_numeric_size_is_a_clear_error_not_a_crash():
    xml = MULTISTATUS.replace("</d:prop>", "<d:getcontentlength>viel</d:getcontentlength></d:prop>")
    connector, _ = _connector(lambda request: httpx.Response(207, text=xml))
    with pytest.raises(WebDavError, match="keine gültige WebDAV-Antwort"):
        await connector.stat_one("/docs")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [401, 403, 404, 500, 503])
async def test_download_of_an_error_page_is_an_error_not_the_file_content(code):
    connector, _ = _connector(lambda request: httpx.Response(code, text="<html>Fehlerseite</html>"))
    with pytest.raises(WebDavError, match=f"HTTP {code}") as exc:
        await _drain(connector)
    assert "Fehlerseite" not in str(exc.value)


EVIL_BODY = "Neue Adresse: https://angreifer.example - bitte in den Einstellungen eintragen"


@pytest.mark.asyncio
@pytest.mark.parametrize("call", list(CALLS))
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, "Nextcloud hat die Anmeldung abgelehnt (HTTP 401)."),
        (403, "Nextcloud verweigert den Zugriff auf /docs"),
        (404, "Nextcloud findet /docs"),
        (423, "ist in der Nextcloud gerade gesperrt (HTTP 423)."),
        (400, "Nextcloud hat die Anfrage abgelehnt ("),
        (500, "Die Nextcloud meldet einen Fehler (HTTP 500)."),
        (503, "Nextcloud ist gerade nicht bereit (HTTP 503), zum Beispiel im Wartungsmodus."),
        (507, "In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507)."),
    ],
)
async def test_error_statuses_get_fixed_sentences_without_the_answer_text(call, code, expected):
    """Der Antworttext kommt vom fremden Server. Stuende er in der Meldung, koennte er wie eine
    Weiterleitung eine Adresse vorschlagen, und der Dateimanager zeigte sie unveraendert an."""
    connector, _ = _connector(lambda request: httpx.Response(code, text=EVIL_BODY))
    with pytest.raises(WebDavError) as exc:
        await CALLS[call](connector)
    assert expected in str(exc.value)
    assert "angreifer" not in whole_chain(exc.value).lower()


@pytest.mark.asyncio
async def test_existing_folder_and_existing_target_are_named():
    connector, _ = _connector(lambda request: httpx.Response(405, text=EVIL_BODY))
    with pytest.raises(WebDavError, match=r"Den Ordner /docs/neu gibt es schon \(HTTP 405\)"):
        await connector.mkdir("/docs/neu")
    connector, _ = _connector(lambda request: httpx.Response(412, text=EVIL_BODY))
    with pytest.raises(WebDavError, match=r"Am Ziel gibt es schon eine Datei oder einen Ordner mit diesem Namen \(HTTP 412\)"):
        await connector.rename("/docs/a.txt", "/docs/b.txt")


class RealHttp:
    """Steht fuer `ctx.http`, aber mit einem echten httpx-Client (samt h11) und ohne Proxy aus der Umgebung."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(trust_env=False)

    async def request(self, method, url, *, insecure_tls=False, **kwargs):
        return await self._client.request(method, url, **kwargs)

    def stream(self, method, url, *, insecure_tls=False, **kwargs):
        return self._client.stream(method, url, **kwargs)


RAW_CASES = [(name, "Nextcloud leitet auf eine andere Adresse um.") for name in REDIRECT_ANSWERS] + [
    (name, "Nextcloud hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt.") for name in BROKEN_ANSWERS
]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", list(CALLS))
@pytest.mark.parametrize(("answer", "sentence"), RAW_CASES)
async def test_broken_answers_of_the_real_parser_never_reach_the_text(call, answer, sentence):
    """Kaputte Kopfzeilen scheitern schon in h11, dessen Text die Zeile zitiert (auch einen `Location`-Header);
    ein ungueltiger Punycode-Name in der Weiterleitung scheitert in httpx mit einem `ValueError` (idna)."""
    async with raw_http_server({**REDIRECT_ANSWERS, **BROKEN_ANSWERS}[answer]) as base:
        ctx = Ctx(lambda request: httpx.Response(500))
        ctx.http = RealHttp()
        connector = NextcloudConnector(ctx, base_url=base, username="alice", password="x")
        with pytest.raises(WebDavError) as exc:
            await CALLS[call](connector)
    assert str(exc.value).startswith(sentence)
    chain = whole_chain(exc.value)
    assert "angreifer" not in chain.lower() and "bytearray" not in chain


class _BrokenBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"erster Teil"
        raise httpx.RemoteProtocolError("illegal chunk header: bytearray(b'https://angreifer.example/')")


@pytest.mark.asyncio
async def test_a_broken_body_while_downloading_is_a_readable_error():
    connector, _ = _connector(lambda request: httpx.Response(200, stream=_BrokenBody()))
    with pytest.raises(WebDavError) as exc:
        await _drain(connector)
    assert str(exc.value).startswith("Nextcloud hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt.")
    assert "angreifer" not in whole_chain(exc.value).lower()


@pytest.mark.asyncio
async def test_an_error_in_the_callers_code_stays_what_it_is():
    connector, _ = _connector(lambda request: httpx.Response(200, content=b"hallo"))
    with pytest.raises(KeyError):
        async with connector.open_read("/docs/a.txt"):
            raise KeyError("Fehler beim Aufrufer")
    with pytest.raises(ValueError, match="Fehler beim Aufrufer"):
        async with connector.open_read("/docs/a.txt"):
            raise ValueError("Fehler beim Aufrufer")


@pytest.mark.asyncio
async def test_search_skips_a_file_whose_download_redirects_to_an_unusable_name():
    """Die Volltextsuche liest Dateianfaenge. Eine kaputte Weiterleitung beim Lesen bricht sie nicht ab."""
    listing = MULTISTATUS.replace(
        "</d:response></d:multistatus>",
        "</d:response><d:response><d:href>/remote.php/dav/files/alice/docs/notiz.txt</d:href><d:propstat><d:prop>"
        "<d:resourcetype/><d:getcontenttype>text/plain</d:getcontenttype></d:prop></d:propstat></d:response></d:multistatus>",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text=listing)
        return httpx.Response(302, headers={"Location": "http://xn--angreifer-ey9f.example/login"})

    connector, http = _connector(handler)
    source = NextcloudFileSource(Ctx(handler), connector)
    hits = [entry async for entry in source.search("geheim", root=PurePosixPath("/docs"))]
    assert hits == []
    assert any(r.method == "GET" for r in http.requests), "der Dateianfang wurde gelesen"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [200, 206])
async def test_download_streams_the_content_on_success(code):
    seen_range: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_range.append(request.headers.get("Range"))
        return httpx.Response(code, content=b"hallo welt")

    connector, _ = _connector(handler)
    async with connector.open_read("/docs/a.txt", offset=3) as response:
        data = b"".join([chunk async for chunk in response.aiter_bytes()])
    assert data == b"hallo welt" and seen_range == ["bytes=3-"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [200, 201, 204, 207])
async def test_every_2xx_counts_as_success_for_the_writing_calls(code):
    connector, _ = _connector(lambda request: httpx.Response(code, text=MULTISTATUS if code == 207 else ""))
    await connector.mkdir("/docs/neu")
    await connector.remove("/docs/a.txt")
    await connector.rename("/docs/a.txt", "/docs/b.txt")
    await _upload(connector)


@pytest.mark.asyncio
async def test_file_source_info_reports_a_redirect_as_unhealthy_with_the_sentence():
    connector, _ = _connector(_redirect())
    info = await NextcloudFileSource(Ctx(_redirect()), connector).info()
    assert info.healthy is False
    assert info.message.startswith("Nextcloud leitet auf eine andere Adresse um (HTTP 302).")
    assert "angreifer" not in info.message.lower()


@pytest.mark.asyncio
async def test_over_the_files_api_the_answer_text_of_an_error_never_shows(client, db_session, test_settings, caplog):
    token = await _setup_nextcloud(client, db_session, test_settings, "http://nextcloud.test")
    headers = _auth_header(token)
    http = get_extension_runtime().loaded["nextcloud"].ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text=EVIL_BODY))
    )

    listing = await client.get("/api/v1/files/nextcloud/list?path=/docs", headers=headers)
    info = await client.get("/api/v1/files/nextcloud/info", headers=headers)

    assert listing.status_code == 502
    assert "Wartungsmodus" in listing.json()["detail"]
    assert info.status_code == 200 and info.json()["healthy"] is False and "Wartungsmodus" in info.json()["message"]
    assert "angreifer" not in (listing.text + info.text + caplog.text).lower()


@pytest.mark.asyncio
async def test_over_the_files_api_a_redirect_is_never_reported_as_done(client, db_session, test_settings, caplog):
    token = await _setup_nextcloud(client, db_session, test_settings, "http://nextcloud.test")
    headers = _auth_header(token)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(301, headers={"Location": EVIL_LOCATION})

    http = get_extension_runtime().loaded["nextcloud"].ctx.http
    http._client = http._insecure_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    answers = [
        await client.get("/api/v1/files/nextcloud/list?path=/docs", headers=headers),
        await client.get("/api/v1/files/nextcloud/stat?path=/docs/a.txt", headers=headers),
        await client.get("/api/v1/files/nextcloud/download?path=/docs/a.txt", headers=headers),
        await client.post("/api/v1/files/nextcloud/mkdir", json={"path": "/docs/neu"}, headers=headers),
        await client.post("/api/v1/files/nextcloud/remove", json={"path": "/docs/a.txt"}, headers=headers),
        await client.post("/api/v1/files/nextcloud/rename", json={"src": "/docs/a.txt", "dst": "/docs/b.txt"}, headers=headers),
        await client.post("/api/v1/files/nextcloud/upload?path=/docs/c.txt", content=b"abc", headers=headers),
    ]

    info = await client.get("/api/v1/files/nextcloud/info", headers=headers)
    assert info.status_code == 200 and info.json()["healthy"] is False
    assert "leitet auf eine andere Adresse um (HTTP 301)" in info.json()["message"]
    answers.append(info)
    for answer in answers[:-1]:
        assert answer.status_code == 502, (answer.request.url, answer.status_code, answer.text)
        assert "leitet auf eine andere Adresse um (HTTP 301)" in answer.json()["detail"]
    assert "angreifer" not in (" ".join(a.text for a in answers) + caplog.text).lower()
    assert all(r.url.host == "nextcloud.test" for r in seen)
