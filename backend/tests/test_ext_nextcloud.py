"""nextcloud-Extension -- die zweite echte Vendor-Extension nach proxmox.
Getestet gegen einen echten, lokal laufenden
WebDAV-Mock (echtes HTTP auf einem echten TCP-Port, kein `respx`/Monkeypatch von
`httpx`) -- dasselbe Prinzip wie `test_ext_proxmox.py`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import sys
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_USERNAME = "alice"
_PASSWORD = "app-password-123"
_EXPECTED_AUTH = "Basic " + __import__("base64").b64encode(f"{_USERNAME}:{_PASSWORD}".encode()).decode()


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _norm(path: str) -> str:
    return "/" + path.strip("/")


def _build_mock_webdav_app() -> tuple[FastAPI, dict]:
    """Ein In-Memory-Dateisystem hinter einer minimalen, aber echten WebDAV-API --
    echtes PROPFIND-Multistatus-XML, echte Basic-Auth-Pruefung, echter Zustand
    (PUT/MKCOL/DELETE/MOVE aendern wirklich das In-Memory-Dateisystem)."""
    state: dict = {
        "fs": {
            "/": {"is_dir": True},
            "/docs": {"is_dir": True},
            "/docs/notes.txt": {"is_dir": False, "content": b"hello world", "mime": "text/plain"},
            # Nur fuer den Such-Test gebraucht (search.py): ein tieferer
            # Baum, damit eine ECHTE Breitensuche (mehr als eine Ebene) etwas zu tun
            # hat -- die anderen Tests in dieser Datei pruefen "/docs" per Teilmenge/
            # `next(...)`, nicht per exaktem Vergleich der Wurzel, bleiben also
            # unberuehrt.
            "/photos": {"is_dir": True},
            "/photos/vacation": {"is_dir": True},
            "/photos/vacation/beach.jpg": {"is_dir": False, "content": b"jpegbytes", "mime": "image/jpeg"},
            # Nur fuer den Volltextsuche-Test gebraucht: "quetzalcoatl"
            # steht NUR im Inhalt, nie im Dateinamen -- ein Treffer hier beweist, dass
            # search() tatsaechlich Dateiinhalte liest, nicht nur Namen vergleicht.
            "/photos/journal.log": {"is_dir": False, "content": b"Sah heute eine quetzalcoatl-Statue.", "mime": "text/plain"},
            # Dasselbe Wort auch im Inhalt einer BINAEREN Datei -- darf NICHT gefunden
            # werden (_looks_like_text() muss image/pdf ausschliessen, sonst wuerde
            # dieser Test das Gegenteil beweisen).
            "/photos/receipt.pdf": {"is_dir": False, "content": b"pdfbytes quetzalcoatl", "mime": "application/pdf"},
        },
        "calls": [],
    }
    app = FastAPI()

    def _check_auth(authorization: str | None = Header(default=None)) -> None:
        if authorization != _EXPECTED_AUTH:
            raise HTTPException(status_code=401, detail="Ungueltige Basic-Auth.")

    def _prefix(path: str) -> str:
        return f"/remote.php/dav/files/{_USERNAME}{path}"

    def _direct_children(path: str) -> list[str]:
        base = path.rstrip("/")
        out = []
        for p in state["fs"]:
            if p == path:
                continue
            parent = p.rsplit("/", 1)[0] or "/"
            if parent == (base or "/"):
                out.append(p)
        return out

    def _entry_xml(path: str) -> str:
        entry = state["fs"][path]
        href = _prefix(path) + ("/" if entry["is_dir"] and path != "/" else "")
        if entry["is_dir"]:
            return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        size = len(entry["content"])
        mime = entry["mime"]
        return (
            f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype/>"
            f"<d:getcontentlength>{size}</d:getcontentlength><d:getcontenttype>{mime}</d:getcontenttype>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        )

    @app.api_route("/remote.php/dav/files/{username}/{path:path}", methods=["PROPFIND"])
    async def propfind(username: str, path: str, depth: str = Header(default="1", alias="depth"), _: None = Depends_check(_check_auth)) -> Response:
        p = _norm(path)
        state["calls"].append(("PROPFIND", p, depth))
        if p not in state["fs"]:
            raise HTTPException(status_code=404, detail="Nicht gefunden.")
        parts = [_entry_xml(p)]
        if depth == "1" and state["fs"][p]["is_dir"]:
            for child in _direct_children(p):
                parts.append(_entry_xml(child))
        body = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>"
        return RawResponse(content=body, media_type="application/xml", status_code=207)

    @app.get("/remote.php/dav/files/{username}/{path:path}")
    async def get_file(username: str, path: str, _: None = Depends_check(_check_auth)) -> Response:
        p = _norm(path)
        entry = state["fs"].get(p)
        if entry is None or entry["is_dir"]:
            raise HTTPException(status_code=404, detail="Nicht gefunden.")
        return RawResponse(content=entry["content"], media_type=entry["mime"])

    @app.put("/remote.php/dav/files/{username}/{path:path}")
    async def put_file(username: str, path: str, request: Request, _: None = Depends_check(_check_auth)) -> Response:
        p = _norm(path)
        content = await request.body()
        state["fs"][p] = {"is_dir": False, "content": content, "mime": "application/octet-stream"}
        state["calls"].append(("PUT", p))
        return RawResponse(status_code=201)

    @app.api_route("/remote.php/dav/files/{username}/{path:path}", methods=["MKCOL"])
    async def mkcol(username: str, path: str, _: None = Depends_check(_check_auth)) -> Response:
        p = _norm(path)
        state["fs"][p] = {"is_dir": True}
        state["calls"].append(("MKCOL", p))
        return RawResponse(status_code=201)

    @app.delete("/remote.php/dav/files/{username}/{path:path}")
    async def delete_entry(username: str, path: str, _: None = Depends_check(_check_auth)) -> Response:
        p = _norm(path)
        for key in [k for k in state["fs"] if k == p or k.startswith(p.rstrip("/") + "/")]:
            del state["fs"][key]
        state["calls"].append(("DELETE", p))
        return RawResponse(status_code=204)

    @app.api_route("/remote.php/dav/files/{username}/{path:path}", methods=["MOVE"])
    async def move_entry(
        username: str, path: str, destination: str = Header(alias="destination"), _: None = Depends_check(_check_auth)
    ) -> Response:
        src = _norm(path)
        dst_path = destination.split(f"/remote.php/dav/files/{username}", 1)[-1]
        dst = _norm(dst_path)
        state["fs"][dst] = state["fs"].pop(src)
        state["calls"].append(("MOVE", src, dst))
        return RawResponse(status_code=201)

    return app, state


def Depends_check(fn):  # noqa: N802 - liest sich wie Depends(...) am Aufrufort
    from fastapi import Depends

    return Depends(fn)


@pytest_asyncio.fixture
async def mock_webdav():
    app, state = _build_mock_webdav_app()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)

    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        server.should_exit = True
        await serve_task


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup_nextcloud(
    client, db_session, test_settings, base_url: str, *, extra_settings: dict | None = None
) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/nextcloud/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text

    record = await db_session.get(ExtensionRecord, "nextcloud")
    record.settings = {"base_url": base_url, "username": _USERNAME, **(extra_settings or {})}
    await db_session.flush()

    token_resp = await client.post(
        "/api/v1/ext/nextcloud/token", json={"value": _PASSWORD}, headers=_auth_header(token)
    )
    assert token_resp.status_code == 204, token_resp.text
    return token


@pytest.mark.asyncio
async def test_nextcloud_appears_as_a_file_source_over_the_real_files_api(client, db_session, test_settings, mock_webdav):
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)

    sources = await client.get("/api/v1/files/sources", headers=_auth_header(token))
    assert sources.status_code == 200, sources.text
    by_id = {s["source_id"]: s for s in sources.json()}
    assert "nextcloud" in by_id
    assert by_id["nextcloud"]["caps"]["write"] is True


@pytest.mark.asyncio
async def test_nextcloud_list_stat_download_over_the_real_files_api(client, db_session, test_settings, mock_webdav):
    base_url, state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    listing = await client.get("/api/v1/files/nextcloud/list?path=/docs", headers=headers)
    assert listing.status_code == 200, listing.text
    names = {item["name"] for item in listing.json()["items"]}
    assert names == {"notes.txt"}

    stat = await client.get("/api/v1/files/nextcloud/stat?path=/docs/notes.txt", headers=headers)
    assert stat.status_code == 200, stat.text
    assert stat.json()["size"] == len(b"hello world")

    download = await client.get("/api/v1/files/nextcloud/download?path=/docs/notes.txt", headers=headers)
    assert download.status_code == 200
    assert download.content == b"hello world"


@pytest.mark.asyncio
async def test_nextcloud_paths_leaving_the_file_area_are_rejected_over_the_real_files_api(
    client, db_session, test_settings, mock_webdav
):
    base_url, state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    calls_before = list(state["calls"])

    for bad in ("../../calendars/alice", "/a/../../x", "%2e%2e/%2e%2e/trashbin"):
        listing = await client.get("/api/v1/files/nextcloud/list", params={"path": bad}, headers=headers)
        assert listing.status_code == 404, (bad, listing.text)
        download = await client.get("/api/v1/files/nextcloud/download", params={"path": bad}, headers=headers)
        assert download.status_code == 404, (bad, download.text)
        removed = await client.post("/api/v1/files/nextcloud/remove", json={"path": bad}, headers=headers)
        assert removed.status_code == 404, (bad, removed.text)
        moved = await client.post(
            "/api/v1/files/nextcloud/rename", json={"src": "/docs/notes.txt", "dst": bad}, headers=headers
        )
        assert moved.status_code == 404, (bad, moved.text)

    assert state["calls"] == calls_before
    assert "/docs/notes.txt" in state["fs"]


@pytest.mark.asyncio
async def test_nextcloud_listed_entry_path_is_directly_usable_for_a_follow_up_call(
    client, db_session, test_settings, mock_webdav
):
    """Live gefunden im Boot-Test (echter Browser): ein Klick auf einen im Explorer gelisteten Ordner
    schickt GENAU den `path`, den `GET .../list` fuer diesen Eintrag zurueckgegeben
    hat, an einen erneuten `GET .../list`-Aufruf zurueck. Vor dem Fix (roher `href`
    inklusive WebDAV-Praefix statt eines logischen, wurzel-relativen Pfads) fuehrte
    das zu einem doppelt verschachtelten, nicht existierenden Pfad und einem 404 --
    dieser Test bildet exakt diese Klickfolge nach, ueber die echte HTTP-API gegen
    den echten Mock, nicht nur gegen die reine Parse-Funktion (siehe
    test_ext_nextcloud_connector.py fuer den isolierten Unit-Test)."""
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    root_listing = await client.get("/api/v1/files/nextcloud/list?path=/", headers=headers)
    assert root_listing.status_code == 200, root_listing.text
    docs_entry = next(i for i in root_listing.json()["items"] if i["name"] == "docs")
    assert docs_entry["path"] == "/docs"  # NICHT "/remote.php/dav/files/alice/docs"

    docs_listing = await client.get(
        f"/api/v1/files/nextcloud/list?path={docs_entry['path']}", headers=headers
    )
    assert docs_listing.status_code == 200, docs_listing.text
    assert {i["name"] for i in docs_listing.json()["items"]} == {"notes.txt"}


@pytest.mark.asyncio
async def test_nextcloud_upload_mkdir_rename_remove_over_the_real_files_api(client, db_session, test_settings, mock_webdav):
    base_url, state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    uploaded = await client.post(
        "/api/v1/files/nextcloud/upload?path=/docs/new.txt", headers=headers, content=b"neuer inhalt"
    )
    assert uploaded.status_code == 200, uploaded.text
    assert state["fs"]["/docs/new.txt"]["content"] == b"neuer inhalt"

    made = await client.post("/api/v1/files/nextcloud/mkdir", json={"path": "/docs/sub"}, headers=headers)
    assert made.status_code == 200, made.text
    assert state["fs"]["/docs/sub"]["is_dir"] is True

    renamed = await client.post(
        "/api/v1/files/nextcloud/rename", json={"src": "/docs/new.txt", "dst": "/docs/renamed.txt"}, headers=headers
    )
    assert renamed.status_code == 200, renamed.text
    assert "/docs/renamed.txt" in state["fs"]
    assert "/docs/new.txt" not in state["fs"]

    removed = await client.post("/api/v1/files/nextcloud/remove", json={"path": "/docs/renamed.txt"}, headers=headers)
    assert removed.status_code == 200, removed.text
    assert "/docs/renamed.txt" not in state["fs"]


@pytest.mark.asyncio
async def test_nextcloud_transfer_from_itself_streams_over_the_real_files_api(client, db_session, test_settings, mock_webdav):
    """`/files/transfer` (docs/04 Paragraph 3): serverseitiges `open_read(A) ->
    open_write(B)` -- hier A=B=nextcloud (einfachster echter Fall ohne eine zweite
    Quelle), beweist trotzdem den vollen Pfad inklusive Job/Run + WS-Fortschritt."""
    import asyncio as _asyncio

    base_url, state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    transfer = await client.post(
        "/api/v1/files/transfer",
        json={"from": {"source": "nextcloud", "path": "/docs/notes.txt"}, "to": {"source": "nextcloud", "path": "/docs/copy.txt"}},
        headers=headers,
    )
    assert transfer.status_code == 200, transfer.text
    run_id = transfer.json()["run_id"]

    for _ in range(100):
        if "/docs/copy.txt" in state["fs"]:
            break
        await _asyncio.sleep(0.02)
    assert state["fs"]["/docs/copy.txt"]["content"] == b"hello world"

    from nodvard_deck.models import JobRun

    run = await db_session.get(JobRun, run_id)
    for _ in range(100):
        await db_session.refresh(run)
        if run.status != "running":
            break
        await _asyncio.sleep(0.02)
    assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_nextcloud_search_finds_a_file_two_levels_deep_over_the_real_files_api(
    client, db_session, test_settings, mock_webdav
):
    """`caps.search` war bisher `False` -- die generische Fan-out-Route
    `GET /files/search` (docs/04-API.md) existierte schon, aber keine Quelle
    beantwortete sie. Beweist die echte Breitensuche: `beach.jpg` liegt unter
    `/photos/vacation/`, zwei Ebenen unter der Wurzel -- ein Treffer hier beweist,
    dass `search()` tatsaechlich rekursiv ueber `list_children()` wandert, nicht
    nur die Wurzel selbst durchsucht."""
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    res = await client.get("/api/v1/files/search?q=beach", headers=headers)
    assert res.status_code == 200, res.text
    hits = res.json()
    assert len(hits) == 1
    assert hits[0]["source_id"] == "nextcloud"
    assert hits[0]["entry"]["path"] == "/photos/vacation/beach.jpg"


@pytest.mark.asyncio
async def test_nextcloud_search_is_case_insensitive_and_matches_substrings(
    client, db_session, test_settings, mock_webdav
):
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    res = await client.get("/api/v1/files/search?q=BEACH", headers=headers)
    assert res.status_code == 200, res.text
    assert {h["entry"]["name"] for h in res.json()} == {"beach.jpg"}

    # Teilstring, nicht nur exakter Name -- "not" matcht "notes.txt" UND "Nextcloud"
    # selbst kennt keinen Ordner "not", also nur die eine Datei.
    res2 = await client.get("/api/v1/files/search?q=not", headers=headers)
    assert res2.status_code == 200, res2.text
    assert {h["entry"]["name"] for h in res2.json()} == {"notes.txt"}


@pytest.mark.asyncio
async def test_nextcloud_search_finds_a_word_that_exists_only_in_file_content(
    client, db_session, test_settings, mock_webdav
):
    """search() las bisher
    NUR Dateinamen. "quetzalcoatl" steht ausschliesslich im Inhalt von
    journal.log -- ein Treffer beweist, dass jetzt tatsaechlich Inhalte
    gelesen werden."""
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    res = await client.get("/api/v1/files/search?q=quetzalcoatl", headers=headers)
    assert res.status_code == 200, res.text
    hits = res.json()
    assert len(hits) == 1
    assert hits[0]["entry"]["path"] == "/photos/journal.log"


@pytest.mark.asyncio
async def test_nextcloud_content_search_skips_binary_files(client, db_session, test_settings, mock_webdav):
    """receipt.pdf enthaelt "quetzalcoatl" ebenfalls im Inhalt -- muss trotzdem NICHT
    gefunden werden, weil `_looks_like_text()` application/pdf ausschliesst. Ohne
    diesen Test wuerde ein Bug, der jede Datei liest statt nur text-artige, beim
    vorigen Test unbemerkt bleiben (der findet die .log-Datei so oder so)."""
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    res = await client.get("/api/v1/files/search?q=quetzalcoatl", headers=headers)
    assert res.status_code == 200, res.text
    paths = {h["entry"]["path"] for h in res.json()}
    assert "/photos/receipt.pdf" not in paths


@pytest.mark.asyncio
async def test_nextcloud_search_returns_empty_list_not_an_error_when_nothing_matches(
    client, db_session, test_settings, mock_webdav
):
    base_url, _state = mock_webdav
    token = await _setup_nextcloud(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    res = await client.get("/api/v1/files/search?q=xyz-nichts-passt-hier", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == []


def _write_self_signed_cert(tmp_path: Path) -> tuple[str, str]:
    """Ein ECHTES selbstsigniertes Zertifikat fuer `127.0.0.1` -- derselbe Ansatz wie
    `test_ext_proxmox.py::_write_self_signed_cert()`, hier fuer den WebDAV-Mock
    dupliziert statt importiert (dieselbe bewusste Unabhaengigkeit wie bei
    proxmox/backups: jede Extension bringt ihre eigene TLS-Verifikation mit)."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "nextcloud-mock-selfsigned.crt"
    key_path = tmp_path / "nextcloud-mock-selfsigned.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return str(cert_path), str(key_path)


@pytest_asyncio.fixture
async def mock_webdav_https(tmp_path: Path):
    """Wie `mock_webdav`, aber echtes TLS mit einem echten selbstsignierten
    Zertifikat -- siehe `_write_self_signed_cert()`."""
    app, state = _build_mock_webdav_app()
    cert_path, key_path = _write_self_signed_cert(tmp_path)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
        ssl_certfile=cert_path, ssl_keyfile=key_path,
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)

    try:
        yield f"https://127.0.0.1:{port}", state
    finally:
        server.should_exit = True
        await serve_task


@pytest.mark.asyncio
async def test_nextcloud_https_self_signed_cert_fails_by_default(client, db_session, test_settings, mock_webdav_https):
    """Live gefunden gegen das echte cloud.home.example (Nginx Proxy Manager mit
    selbstsigniertem/lokalem Zertifikat): OHNE `tls_insecure_skip_verify` schlaegt
    JEDE Verbindung fehl -- derselbe Fehler wie bei proxmox/backups, hier fuer
    nextcloud bewiesen. Kein 500er, ein sauberes `healthy=False`."""
    base_url, _state = mock_webdav_https
    await _setup_nextcloud(client, db_session, test_settings, base_url)

    runtime = get_extension_runtime()
    loaded = runtime.loaded["nextcloud"]
    report = await loaded.instance.health(loaded.ctx)
    assert report.healthy is False


@pytest.mark.asyncio
async def test_nextcloud_https_self_signed_cert_succeeds_with_insecure_tls_setting(
    client, db_session, test_settings, mock_webdav_https
):
    """Mit `tls_insecure_skip_verify=true` gesetzt: derselbe echte TLS-Handshake
    gegen dasselbe echte selbstsignierte Zertifikat gelingt jetzt."""
    base_url, _state = mock_webdav_https
    await _setup_nextcloud(client, db_session, test_settings, base_url, extra_settings={"tls_insecure_skip_verify": True})

    runtime = get_extension_runtime()
    loaded = runtime.loaded["nextcloud"]
    report = await loaded.instance.health(loaded.ctx)
    assert report.healthy is True
