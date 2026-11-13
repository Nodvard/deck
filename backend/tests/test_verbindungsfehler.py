"""Verbindungsfehler: Terminal und Dateien zu einem Server, der nicht antwortet,
zeigten gar keinen Grund (`str(TimeoutError())` ist leer), und im Protokoll landeten Tracebacks.

Jetzt: ein deutscher Satz als Grund -- und im Protokoll nur eine Zeile (`deploy_pi.sh` wertet
"Traceback" in den letzten Minuten als gescheiterten Start).
"""

from __future__ import annotations

import asyncio
import errno
import logging
import socket
import sys
from pathlib import Path, PurePosixPath

import asyncssh
import httpx
import pytest
from httpx import AsyncClient
from nodvard_sdk import FileEntry, FileSourceCaps, SourceInfo
from nodvard_sdk.capabilities import FileSourceProvider, MetricsProvider
from nodvard_sdk.errors import HostUnreachable

from nodvard_deck.core import ssh
from nodvard_deck.core.error_text import describe_connection_error, is_expected_connection_error
from nodvard_deck.core.gate import describe_error
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup():
    before = list(sys.path)
    reset_extension_runtime()
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _tracebacks(caplog) -> list[logging.LogRecord]:  # noqa: ANN001
    return [r for r in caplog.records if r.exc_info]


async def _silent_server():
    """Nimmt Verbindungen an und sagt nie etwas -- so antwortet ein Server, der aus ist, aus Sicht
    des Dashboards (der SSH-Handshake laeuft in die Zeitueberschreitung)."""
    server = await asyncio.start_server(lambda r, w: asyncio.sleep(60), "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _closed_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _target(port: int) -> ssh.ConnectionTarget:
    return ssh.ConnectionTarget(
        host_id="host-1", address="127.0.0.1", port=port, username="x", kind="ssh_password", secret_value="pw",
    )


# --- der Text ------------------------------------------------------------------------------------


def test_zeitueberschreitung_hat_einen_grund_obwohl_die_ausnahme_keinen_text_hat():
    assert str(TimeoutError()) == ""
    text = describe_connection_error(TimeoutError(), address="192.168.2.10", port=22)
    assert text.startswith("Server antwortet nicht (Zeitüberschreitung bei 192.168.2.10:22)")
    assert describe_connection_error(asyncio.TimeoutError()).startswith("Server antwortet nicht (Zeitüberschreitung)")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ConnectionRefusedError(), "Der Server lehnt die Verbindung ab (192.168.2.10:22)"),
        (socket.gaierror(-2, "Name or service not known"), "Den Namen 192.168.2.10 kennt das Netz nicht."),
        (ConnectionResetError("Connection reset by peer"), "Die Verbindung zum Server ist abgebrochen. Fehlerkennung:"),
        (ConnectionResetError(), "Die Verbindung zum Server ist abgebrochen."),
        (asyncssh.ConnectionLost("Connection lost"), "Die Verbindung zum Server ist abgebrochen. Fehlerkennung:"),
        (OSError(errno.EHOSTUNREACH, "No route to host"), "Kein Weg zum Server (192.168.2.10:22)"),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), "Kein Weg zum Server (192.168.2.10:22)"),
        (OSError(errno.EHOSTDOWN, "Host is down"), "Kein Weg zum Server (192.168.2.10:22)"),
    ],
)
def test_verbindungsfehler_auf_deutsch(exc, expected):
    assert describe_connection_error(exc, address="192.168.2.10", port=22).startswith(expected)


def test_fremde_fehler_zeigen_nur_eine_fehlerkennung():
    import re

    for exc in (RuntimeError("kaputt"), RuntimeError()):
        assert re.fullmatch(r"Die Anfrage ist fehlgeschlagen\. Fehlerkennung: [0-9a-f]{32}\.", describe_connection_error(exc))
    # Kein Netzfehler: nicht als "Verbindung" ausgeben, und der Text mit dem Pfad des Servers bleibt weg.
    for error in (PermissionError(errno.EACCES, "Permission denied", "/app/data/lattice.db"), OSError(errno.ENOSPC, "No space left on device")):
        assert re.fullmatch(r"Die Anfrage ist fehlgeschlagen\. Fehlerkennung: [0-9a-f]{32}\.", describe_connection_error(error))


def test_unbekannter_oserror_beim_verbindungsaufbau_zeigt_nur_eine_fehlerkennung():
    import re

    exc = OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
    for error in (exc, OSError()):
        text = describe_connection_error(error, address="192.168.2.10", port=22, network=True)
        assert re.fullmatch(r"Keine Verbindung zum Server\. Fehlerkennung: [0-9a-f]{32}\.", text)
    assert re.fullmatch(r"Die Anfrage ist fehlgeschlagen\. Fehlerkennung: [0-9a-f]{32}\.", describe_connection_error(exc))


def test_fertige_deutsche_saetze_kommen_unveraendert_durch():
    assert describe_connection_error(ssh.SshError("Zugang abgelehnt.")) == "Zugang abgelehnt."
    assert describe_connection_error(ssh.SshTimeout("Server antwortet nicht.")) == "Server antwortet nicht."


def test_erwartbare_verbindungsfehler_erkennen():
    erwartbar = (
        TimeoutError(),
        asyncio.TimeoutError(),
        ConnectionRefusedError(),
        ConnectionResetError(),
        socket.gaierror(-2, "Name or service not known"),
        OSError(errno.EHOSTUNREACH, "No route to host"),
        OSError(errno.ENETUNREACH, "Network is unreachable"),
        ssh.SshError("x"),
        ssh.SshUnreachable("x"),
        asyncssh.DisconnectError(1, "x"),
        HostUnreachable("x"),
    )
    for exc in erwartbar:
        assert is_expected_connection_error(exc), exc
    for exc in (RuntimeError("x"), KeyError("x"), ValueError("x")):
        assert not is_expected_connection_error(exc), exc


def test_dateifehler_sind_kein_verbindungsfehler():
    """Ein `OSError` ohne Netzbezug ist ein echter Fehler: Traceback im Protokoll, nie als "Server nicht erreichbar"."""
    for exc in (
        PermissionError(errno.EACCES, "Permission denied", "/app/data/lattice.db"),
        FileNotFoundError(errno.ENOENT, "No such file or directory", "/app/data/x"),
        IsADirectoryError(errno.EISDIR, "Is a directory"),
        OSError(errno.ENOSPC, "No space left on device"),
        OSError("ohne Nummer"),
    ):
        assert not is_expected_connection_error(exc), exc


def test_aktions_protokoll_nennt_den_deutschen_grund_statt_befehl_zu_lange():
    assert describe_error(ssh.SshTimeout("Server antwortet nicht (Zeitüberschreitung bei x:22).")) == (
        "Server antwortet nicht (Zeitüberschreitung bei x:22)."
    )
    # Unveraendert: ein roher Timeout beim Befehl.
    assert describe_error(TimeoutError()).startswith("Zeitüberschreitung")


# --- core.ssh.connect ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_zeitueberschreitung_ist_ein_ssh_fehler_mit_grund_und_bleibt_ein_timeout(db_session):
    server, port = await _silent_server()
    try:
        with pytest.raises(ssh.SshTimeout) as caught:
            await ssh.connect(db_session, _target(port), connect_timeout_s=0.3)
    finally:
        server.close()
    exc = caught.value
    assert str(exc).startswith(f"Server antwortet nicht (Zeitüberschreitung bei 127.0.0.1:{port})")
    # Wer bisher auf TimeoutError/OSError/SshError gehorcht hat, faengt es weiter.
    assert isinstance(exc, (TimeoutError, OSError, ssh.SshError, ssh.SshUnreachable))
    assert isinstance(exc.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_connect_abgelehnt_ist_ein_ssh_fehler_mit_grund(db_session):
    port = _closed_port()
    with pytest.raises(ssh.SshUnreachable) as caught:
        await ssh.connect(db_session, _target(port), connect_timeout_s=2.0)
    assert not isinstance(caught.value, TimeoutError)
    assert isinstance(caught.value, OSError)
    assert str(caught.value).startswith(f"Der Server lehnt die Verbindung ab (127.0.0.1:{port})")


@pytest.mark.asyncio
async def test_connect_mit_mehreren_gescheiterten_adressen_nennt_den_grund(db_session, monkeypatch):
    """Bei mehreren gescheiterten Adressen bleiben rohe Verbindungsdetails verborgen."""
    import re

    text = "Multiple exceptions: [Errno 111] Connect call failed ('192.168.2.10', 22), [Errno 101] Network is unreachable"

    async def _fails(**kwargs):
        raise OSError(text)

    monkeypatch.setattr(ssh.asyncssh, "connect", _fails)
    with pytest.raises(ssh.SshUnreachable) as caught:
        await ssh.connect(db_session, _target(22), connect_timeout_s=1.0)
    assert re.fullmatch(r"Keine Verbindung zum Server\. Fehlerkennung: [0-9a-f]{32}\.", str(caught.value))
    assert text not in str(caught.value)
    assert is_expected_connection_error(caught.value)


# --- Dateien: eine Quelle, die nicht antwortet -----------------------------------------------------


class _SleepingFileSource:
    source_id = "schlaeft"
    label = "Schlafender Server"
    icon = "server"
    caps = FileSourceCaps(write=True, rename=True, remove=True, mkdir=True, search=True, range_read=True)

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def stat(self, path: PurePosixPath) -> FileEntry:
        raise self._error

    async def list_dir(self, path: PurePosixPath, *, cursor: str | None = None):
        raise self._error

    async def info(self) -> SourceInfo:
        raise self._error

    async def search(self, query: str, *, root: PurePosixPath):
        raise self._error
        yield  # pragma: no cover - macht daraus einen Generator

    async def open_read(self, path: PurePosixPath, *, offset: int = 0):
        raise self._error
        yield  # pragma: no cover

    async def open_write(self, path, stream, *, size=None):  # noqa: ANN001
        raise self._error

    async def mkdir(self, path):  # noqa: ANN001
        raise self._error

    async def remove(self, path, *, recursive=False):  # noqa: ANN001
        raise self._error

    async def rename(self, src, dst):  # noqa: ANN001
        raise self._error


class _Provider:
    def __init__(self, *sources) -> None:
        self._sources = list(sources)

    async def file_sources(self) -> list:
        return self._sources


async def _owner_token(client: AsyncClient) -> str:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["list?path=/", "stat?path=/x", "info"])
async def test_dateien_zeitueberschreitung_nennt_den_grund_ohne_traceback(client, caplog, path):
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_SleepingFileSource(TimeoutError())))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get(f"/api/v1/files/schlaeft/{path}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    detail = res.json()["detail"]
    # Kein Vorspann, der von einer "Antwort" spricht, wenn der Grund gerade sagt, dass keine kam.
    assert detail.startswith("Zugriff auf die Quelle fehlgeschlagen: Server antwortet nicht (Zeitüberschreitung")
    assert "antwortete" not in detail
    assert not detail.endswith(": ")
    assert _tracebacks(caplog) == []
    assert any(r.getMessage().startswith("file_source_failed") for r in caplog.records)


class _QuellFehler(Exception):
    """Ein Fehler einer Quelle, der weder OSError noch SSH-Fehler ist (so wie WebDavError bei Nextcloud)."""


def _webdav_error() -> BaseException:
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "nextcloud" / "src"))
    from nodvard_deck_ext_nextcloud.connector import WebDavError

    return WebDavError("PROPFIND / -> HTTP 401: Unauthorized")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["list?path=/", "stat?path=/x", "info"])
@pytest.mark.parametrize("make_error", [lambda: _QuellFehler("PROPFIND / -> [Errno 111] Connection refused"), _webdav_error])
async def test_dateien_fehler_der_quelle_ohne_traceback_im_protokoll(client, caplog, path, make_error):
    """Quellfehler bleiben ohne Traceback im Protokoll."""
    import re

    error = make_error()
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_SleepingFileSource(error)))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get(f"/api/v1/files/schlaeft/{path}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert detail.startswith("Zugriff auf die Quelle fehlgeschlagen: ")
    reason = detail.removeprefix("Zugriff auf die Quelle fehlgeschlagen: ")
    if isinstance(error, _QuellFehler):
        match = re.fullmatch(r"Die Anfrage ist fehlgeschlagen\. Fehlerkennung: ([0-9a-f]{32})\.", reason)
        assert match, detail
        assert f"request_failed id={match[1]} type=_QuellFehler " in caplog.text
        for hidden in ("PROPFIND", "[Errno 111]", "Connection refused"):
            assert hidden not in res.text
            assert hidden not in caplog.text
    else:
        assert reason == str(error)
    assert _tracebacks(caplog) == []
    records = [r for r in caplog.records if r.getMessage().startswith("file_source_failed")]
    assert len(records) == 1
    assert records[0].getMessage() == f"file_source_failed type={type(error).__name__} reason={reason}"


class _FailingHttp:
    """`ctx.http` einer Erweiterung, dessen Anfrage mit `error` scheitert."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def request(self, method, url, **kwargs):  # noqa: ANN001, ANN201
        raise self._error


class _FailingCtx:
    def __init__(self, error: BaseException) -> None:
        self.http = _FailingHttp(error)


def _nextcloud_connector(error: BaseException):  # noqa: ANN202
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "nextcloud" / "src"))
    from nodvard_deck_ext_nextcloud.connector import NextcloudConnector

    return NextcloudConnector(ctx=_FailingCtx(error), base_url="https://cloud.example.com", username="alice", password="x")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout(""), httpx.ConnectTimeout(""), httpx.PoolTimeout(""), TimeoutError()],
    ids=["read", "connect", "pool", "timeout-error"],
)
async def test_nextcloud_zeitueberschreitung_hat_einen_grund_obwohl_der_text_leer_ist(error):
    assert str(error) == ""
    connector = _nextcloud_connector(error)
    with pytest.raises(Exception) as caught:
        await connector.list_children("/")
    assert str(caught.value) == "Nextcloud antwortet nicht (Zeitüberschreitung)."
    assert is_expected_connection_error(caught.value)
    assert caught.value.__cause__ is error


@pytest.mark.asyncio
async def test_nextcloud_anderer_fehler_ohne_text_nennt_wenigstens_den_namen():
    connector = _nextcloud_connector(httpx.ConnectError(""))
    with pytest.raises(Exception) as caught:
        await connector.stat_one("/x")
    assert str(caught.value) == "PROPFIND /x -> ConnectError"
    # Mit Text bleibt der Text.
    connector = _nextcloud_connector(httpx.ConnectError("All connection attempts failed"))
    with pytest.raises(Exception) as caught:
        await connector.stat_one("/x")
    assert str(caught.value) == "PROPFIND /x -> All connection attempts failed"


@pytest.mark.asyncio
async def test_dateien_nextcloud_zeitueberschreitung_kommt_als_satz_im_dateimanager_an(client, caplog):
    """Gesamtweg: httpx-Zeitueberschreitung -> Nextcloud-Verbindung -> Dateimanager-Antwort ohne leeren Grund."""
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "nextcloud" / "src"))
    connector = _nextcloud_connector(httpx.ReadTimeout(""))

    class _Quelle(_SleepingFileSource):
        async def list_dir(self, path, *, cursor=None):  # noqa: ANN001, ANN201
            return await connector.list_children("/")

    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_Quelle(RuntimeError("unbenutzt"))))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get("/api/v1/files/schlaeft/list?path=/", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    assert res.json()["detail"] == "Zugriff auf die Quelle fehlgeschlagen: Nextcloud antwortet nicht (Zeitüberschreitung)."
    assert _tracebacks(caplog) == []


@pytest.mark.asyncio
async def test_dateien_dateifehler_der_quelle_verbirgt_text_und_pfad(client, caplog):
    import re

    error = PermissionError(errno.EACCES, "Permission denied", "/app/data/lattice.db")
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_SleepingFileSource(error)))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get("/api/v1/files/schlaeft/list?path=/", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    detail = res.json()["detail"]
    match = re.fullmatch(
        r"Zugriff auf die Quelle fehlgeschlagen: Die Anfrage ist fehlgeschlagen\. Fehlerkennung: ([0-9a-f]{32})\.",
        detail,
    )
    assert match, detail
    assert f"request_failed id={match[1]} type=PermissionError " in caplog.text
    records = [r for r in caplog.records if r.getMessage().startswith("file_source_failed")]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    reason = detail.removeprefix("Zugriff auf die Quelle fehlgeschlagen: ")
    assert records[0].getMessage() == f"file_source_failed type=PermissionError reason={reason}"
    assert _tracebacks(caplog) == []
    for hidden in ("Permission denied", "/app/data/lattice.db"):
        assert hidden not in res.text
        assert hidden not in caplog.text


@pytest.mark.asyncio
async def test_dateien_suche_mit_webdav_fehler_ohne_traceback(client, caplog):
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_SleepingFileSource(_webdav_error())))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get("/api/v1/files/search?q=x", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == []
    assert _tracebacks(caplog) == []


@pytest.mark.asyncio
async def test_dateien_suche_mit_ausgeschaltetem_server_loggt_nur_eine_zeile(client, caplog):
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_SleepingFileSource(TimeoutError())))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get("/api/v1/files/search?q=x", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == []
    assert _tracebacks(caplog) == []
    assert any(r.getMessage().startswith("file_source_search_failed") for r in caplog.records)


@pytest.mark.asyncio
async def test_dateien_echter_fehler_bleibt_mit_traceback_im_protokoll(client, caplog):
    """Nur erwartbare Verbindungsfehler werden zur Zeile -- ein Programmfehler bleibt auffindbar."""
    get_extension_runtime().capabilities.provide("fake-ext", FileSourceProvider, _Provider(_SleepingFileSource(RuntimeError("kaputt"))))
    token = await _owner_token(client)
    with caplog.at_level(logging.DEBUG):
        res = await client.get("/api/v1/files/search?q=x", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert _tracebacks(caplog) == []
    assert "Fehlerkennung:" in caplog.text
    assert "kaputt" not in caplog.text


# --- Messwerte: Erweiterung, die per SSH misst -------------------------------------------------------


class _UnreachableMetrics:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def sample(self, host):  # noqa: ANN001, ANN201
        raise self._error

    async def metric_names(self) -> list[str]:
        return []


async def _host_with_metrics_provider(client, db_session) -> tuple[str, str]:
    token = await _owner_token(client)
    host = (await client.post("/api/v1/hosts", json={"name": "a", "address": "192.168.2.10"}, headers={"Authorization": f"Bearer {token}"})).json()
    from nodvard_deck.models import Host as HostModel

    db_host = await db_session.get(HostModel, host["id"])
    db_host.provider_ext_id = "fake-metrics"
    db_host.provider_ref = "x"
    await db_session.flush()
    return token, host["id"]


@pytest.mark.asyncio
async def test_messwerte_von_ausgeschaltetem_server_sind_502_mit_grund_und_ohne_traceback(client, db_session, caplog):
    token, host_id = await _host_with_metrics_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(TimeoutError()))
    with caplog.at_level(logging.DEBUG):
        res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    assert res.json()["detail"].startswith("Server antwortet nicht (Zeitüberschreitung bei 192.168.2.10)")
    assert _tracebacks(caplog) == []


@pytest.mark.asyncio
async def test_messwerte_dateifehler_zeigen_den_pfad_nicht_und_bleiben_ein_serverfehler(client, db_session, caplog):
    """Ein `PermissionError` ist ein `OSError`, aber kein Netzfehler: kein 502 mit "[Errno 13] ... /app/data/...",
    sondern wie jeder Programmfehler ein 500 (mit Traceback im Protokoll)."""
    token, host_id = await _host_with_metrics_provider(client, db_session)
    error = PermissionError(errno.EACCES, "Permission denied", "/app/data/lattice.db")
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(error))
    with caplog.at_level(logging.DEBUG):
        res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 500
    assert "/app/data" not in res.text
    assert "Permission denied" not in res.text


@pytest.mark.asyncio
async def test_messwerte_programmfehler_bleiben_ein_serverfehler(client, db_session):
    token, host_id = await _host_with_metrics_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(RuntimeError("kaputt")))
    res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 500


# --- jede andere Anfrage: der Auffang in main.py -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ssh.SshTimeout("Server antwortet nicht (Zeitüberschreitung bei x:22). Ist er eingeschaltet und im Netz?"),
        asyncssh.ConnectionLost("Connection lost"),
        HostUnreachable("Für „Bastel-Pi“ ist noch kein SSH-Zugang eingerichtet."),
    ],
    ids=["ssh-timeout", "connection-lost", "host-unreachable"],
)
async def test_unbehandelter_verbindungsfehler_wird_502_statt_500_mit_traceback(client, caplog, error):
    from fastapi.routing import APIRoute

    from nodvard_deck.main import app

    async def _probe() -> dict:
        raise error

    route = APIRoute("/api/v1/_probe/unreachable", _probe, methods=["GET"])
    app.router.routes.insert(0, route)
    try:
        with caplog.at_level(logging.DEBUG):
            res = await client.get("/api/v1/_probe/unreachable")
    finally:
        app.router.routes.remove(route)
    assert res.status_code == 502
    assert res.json()["detail"]
    if isinstance(error, (asyncssh.ConnectionLost, HostUnreachable)):
        import re

        message = "Die Verbindung zum Server ist abgebrochen." if isinstance(error, asyncssh.ConnectionLost) else "Die Anfrage ist fehlgeschlagen."
        match = re.fullmatch(rf"{re.escape(message)} Fehlerkennung: ([0-9a-f]{{32}})\.", res.json()["detail"])
        assert match is not None
        records = [r for r in caplog.records if r.name == "nodvard_deck.errors"]
        assert len(records) == 1
        assert records[0].getMessage().startswith(f"request_failed id={match[1]} type={type(error).__name__} frames=")
        assert str(error) not in res.text
    else:
        assert res.json()["detail"] == describe_connection_error(error)
    assert _tracebacks(caplog) == []


# --- Terminal -----------------------------------------------------------------------------------------


async def _terminal_ticket(running_app, db_session, test_settings, *, port: int):
    from nodvard_deck.main import app

    http_base, ws_base = running_app
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR, "ssh_connect_timeout_s": 0.5})
    host = await hosts_service.create_host(db_session, name="stumm", address="127.0.0.1")
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password", username="x", port=port, secret_value="pw",
    )
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "terminal")
    async with AsyncClient(base_url=http_base) as ac:
        await ac.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
        login = await ac.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
        created = await ac.post(
            "/api/v1/terminal/sessions", json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
    assert created.status_code == 200, created.text
    return f"{ws_base}/api/v1/ws/terminal/{created.json()['session_id']}"


async def _read_until_closed(ws_url: str) -> tuple[list[object], int]:
    import websockets
    from websockets.exceptions import ConnectionClosed

    messages: list[object] = []
    async with websockets.connect(ws_url) as ws:
        try:
            while True:
                messages.append(await asyncio.wait_for(ws.recv(), timeout=15))
        except ConnectionClosed as exc:
            return messages, exc.rcvd.code if exc.rcvd else -1


@pytest.mark.asyncio
async def test_terminal_zu_stummem_server_sagt_warum_und_loggt_nur_eine_zeile(running_app, db_session, test_settings, caplog):
    import json

    server, port = await _silent_server()
    try:
        ws_url = await _terminal_ticket(running_app, db_session, test_settings, port=port)
        with caplog.at_level(logging.DEBUG):
            messages, code = await _read_until_closed(ws_url)
    finally:
        server.close()
    assert code == 4500
    errors = [json.loads(m) for m in messages if isinstance(m, str)]
    assert len(errors) == 1 and errors[0]["type"] == "error"
    assert errors[0]["message"].startswith(f"Server antwortet nicht (Zeitüberschreitung bei 127.0.0.1:{port})")
    assert _tracebacks(caplog) == []
    assert [r for r in caplog.records if r.getMessage().startswith("terminal_open_failed")]


@pytest.mark.asyncio
async def test_terminal_zu_geschlossenem_port_sagt_warum(running_app, db_session, test_settings, caplog):
    import json

    port = _closed_port()
    ws_url = await _terminal_ticket(running_app, db_session, test_settings, port=port)
    with caplog.at_level(logging.DEBUG):
        messages, code = await _read_until_closed(ws_url)
    assert code == 4500
    error = json.loads(next(m for m in messages if isinstance(m, str)))
    assert error["message"].startswith(f"Der Server lehnt die Verbindung ab (127.0.0.1:{port})")
    assert _tracebacks(caplog) == []


async def test_terminal_open_error_hides_values_in_message_and_log(running_app, db_session, test_settings, monkeypatch, caplog):
    import json
    import re
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from nodvard_deck.api.v1 import terminal

    target = SimpleNamespace(open=AsyncMock(side_effect=RuntimeError("vertraulich-terminal /srv/private/key")))
    monkeypatch.setattr(terminal, "_find_terminal_target", AsyncMock(return_value=target))
    ws_url = await _terminal_ticket(running_app, db_session, test_settings, port=22)
    messages, code = await _read_until_closed(ws_url)
    assert code == 4500
    error = json.loads(next(m for m in messages if isinstance(m, str)))
    match = re.search(r"Fehlerkennung: ([0-9a-f]{32})\.", error["message"])
    assert match and match[1] in caplog.text
    assert "vertraulich-terminal" not in error["message"] + caplog.text
    assert "/srv/private" not in error["message"]
    assert _tracebacks(caplog) == []
