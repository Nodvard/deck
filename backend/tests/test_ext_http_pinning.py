"""`ctx.http` mit gepinntem Zertifikat (`pinned_sha256`) und `peer_certificate()`.

Gegen echte TLS-Server im Test (echter Handshake, echtes selbstsigniertes Zertifikat), kein Monkeypatch der
TLS-Schicht: Der Beweis, dass bei einem falschen Zertifikat nichts gesendet wird, ist ein Server, der jedes Byte
nach dem Handshake mitschreibt.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import ipaddress
import ssl
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from nodvard_sdk import CertificateMismatch, PeerCertificate, sha256_fingerprint
from nodvard_sdk.errors import PermissionDenied

from nodvard_deck.ext.context import HttpHandle, _PermissionChecker

PERMS = ["net.outbound", "net.outbound.insecure_tls"]


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _cert(subject_cn: str, key, *, issuer_cn: str | None = None, issuer_key=None, ca: bool = False) -> x509.Certificate:
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn or subject_cn)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
    )
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("pve1.example"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
    return builder.sign(issuer_key or key, hashes.SHA256())


def _write(tmp_path: Path, name: str, cert: x509.Certificate, key) -> tuple[Path, Path]:
    cert_path, key_path = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path


def _fingerprint(cert: x509.Certificate) -> str:
    return ":".join(f"{b:02X}" for b in hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest())


class _RecordingTlsServer:
    """TLS-Server, der alles mitschreibt, was nach dem Handshake ankommt, und auf eine Anfrage mit einer kleinen
    JSON-Antwort antwortet (oder als WebSocket-Gegenstelle nur mitliest)."""

    def __init__(
        self, cert_path: Path, key_path: Path, *, extra_headers: bytes = b"", status_line: bytes = b"200 OK",
    ) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(cert_path), str(key_path))
        self.received = bytearray()
        self.requests: list[bytes] = []
        self.connections = 0
        self.extra_headers = extra_headers
        self.status_line = status_line
        self._server: asyncio.base_events.Server | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            data = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            self.received.extend(data)
            if data.startswith((b"GET", b"POST")):
                self.requests.append(bytes(data))
                body = b'{"data":{"ok":1}}'
                writer.write(
                    b"HTTP/1.1 " + self.status_line + b"\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n" + self.extra_headers + b"Connection: close\r\n\r\n" + body
                )
                await writer.drain()
        except (TimeoutError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    async def __aenter__(self) -> str:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.context)
        port = self._server.sockets[0].getsockname()[1]
        return f"127.0.0.1:{port}"

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()


def _find_in_chain(exc: BaseException, kind: type) -> BaseException | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, kind):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


@pytest.fixture
def self_signed(tmp_path: Path):
    key = _key()
    cert = _cert("pve1.example", key)
    cert_path, key_path = _write(tmp_path, "server", cert, key)
    return cert, cert_path, key_path


@pytest.mark.asyncio
async def test_peer_certificate_reads_a_self_signed_certificate_without_sending_anything(self_signed):
    cert, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    try:
        async with _RecordingTlsServer(cert_path, key_path) as target:
            info = await handle.peer_certificate(f"https://{target}")
    finally:
        await handle.aclose()
    assert isinstance(info, PeerCertificate)
    assert info.trusted is False
    assert info.sha256 == _fingerprint(cert)
    assert "pve1.example" in (info.subject or "")
    assert "pve1.example" in info.alt_names and "127.0.0.1" in info.alt_names
    assert info.not_after and info.not_after.startswith(str(cert.not_valid_after_utc.year))


@pytest.mark.asyncio
async def test_peer_certificate_sends_no_request_bytes(self_signed):
    _cert_obj, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    recorder = _RecordingTlsServer(cert_path, key_path)
    try:
        async with recorder as target:
            await handle.peer_certificate(f"https://{target}")
            await asyncio.sleep(0.05)
    finally:
        await handle.aclose()
    assert recorder.connections >= 1
    assert bytes(recorder.received) == b""


@pytest.mark.asyncio
async def test_peer_certificate_is_trusted_when_a_known_authority_signed_it(tmp_path: Path, monkeypatch):
    ca_key = _key()
    ca = _cert("Test-CA", ca_key, ca=True)
    ca_path, _ = _write(tmp_path, "ca", ca, ca_key)
    key = _key()
    cert = _cert("pve1.example", key, issuer_cn="Test-CA", issuer_key=ca_key)
    cert_path, key_path = _write(tmp_path, "leaf", cert, key)
    # Dieselbe Quelle, der auch der normale Client von ctx.http vertraut (httpx liest SSL_CERT_FILE).
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
    handle = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    try:
        async with _RecordingTlsServer(cert_path, key_path) as target:
            info = await handle.peer_certificate(f"https://{target}")
    finally:
        await handle.aclose()
    assert info.trusted is True
    assert info.sha256 == _fingerprint(cert)


@pytest.mark.asyncio
async def test_peer_certificate_checks_the_target_and_refuses_plain_http():
    limited = HttpHandle(_PermissionChecker("ext-1", ["net.outbound:10.0.0.0/8"]), ["net.outbound:10.0.0.0/8"])
    try:
        with pytest.raises(PermissionDenied):
            await limited.peer_certificate("https://127.0.0.1:8006")
        with pytest.raises(ValueError):
            await limited.peer_certificate("http://10.1.2.3:8006")
    finally:
        await limited.aclose()


@pytest.mark.asyncio
async def test_pinned_request_goes_through_with_the_confirmed_certificate(self_signed):
    cert, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path)
    try:
        async with recorder as target:
            # Auch in Kleinbuchstaben ohne Doppelpunkte.
            pin = _fingerprint(cert).replace(":", "").lower()
            response = await handle.post(f"https://{target}/api2/json/x", pinned_sha256=pin, data={"a": "b"})
    finally:
        await handle.aclose()
    assert response.status_code == 200
    assert response.json() == {"data": {"ok": 1}}
    assert bytes(recorder.received).startswith(b"POST /api2/json/x")


@pytest.mark.asyncio
async def test_pinned_requests_never_keep_a_cookie_the_server_set(self_signed):
    """Setzt der Server nach einer Anmeldung eine Sitzung (`Set-Cookie`), darf die naechste Anfrage mit demselben
    Fingerabdruck sie nicht mittragen -- etwa die Probe eines neuen Tokens, die sonst ueber die Sitzung statt ueber den
    Token bestuende."""
    cert, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path, extra_headers=b"Set-Cookie: PVEAuthCookie=ROOT-TICKET; Path=/\r\n")
    try:
        async with recorder as target:
            pin = _fingerprint(cert)
            first = await handle.post(f"https://{target}/api2/json/access/ticket", pinned_sha256=pin, data={"a": "b"})
            assert first.headers["set-cookie"].startswith("PVEAuthCookie=")
            await handle.get(f"https://{target}/api2/json/nodes", pinned_sha256=pin)
            # Ein Cookie, das der Aufruf selbst setzt, geht weiter mit.
            await handle.get(f"https://{target}/api2/json/version", pinned_sha256=pin, headers={"Cookie": "eigen=1"})
    finally:
        await handle.aclose()
    assert len(recorder.requests) == 3
    assert b"ROOT-TICKET" not in recorder.requests[1]
    assert b"ROOT-TICKET" not in recorder.requests[2] and b"eigen=1" in recorder.requests[2]


@pytest.mark.asyncio
async def test_pinned_request_with_another_certificate_sends_nothing(self_signed):
    _cert_obj, cert_path, key_path = self_signed
    other = _cert("pve1.example", _key())  # gleicher Name, anderer Schluessel: ein Zertifikat, das sich dazwischen schiebt
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path)
    try:
        async with recorder as target:
            with pytest.raises(httpx.ConnectError) as caught:
                await handle.post(
                    f"https://{target}/api2/json/access/ticket", pinned_sha256=_fingerprint(other),
                    data={"username": "root@pam", "password": "GEHEIM-pw"},
                )
            await asyncio.sleep(0.05)
    finally:
        await handle.aclose()
    mismatch = _find_in_chain(caught.value, CertificateMismatch)
    assert isinstance(mismatch, CertificateMismatch)
    assert mismatch.expected == _fingerprint(other)
    # Der Client bricht nach dem Handshake ab, bevor er seine letzte Handshake-Nachricht schickt: Beim Server kommt
    # die Verbindung gar nicht erst als fertig an, geschweige denn ein Byte der Anfrage.
    assert bytes(recorder.received) == b"", "nach einem falschen Zertifikat darf kein Byte der Anfrage beim Server ankommen"
    assert "GEHEIM" not in str(caught.value)


@pytest.mark.asyncio
async def test_pinning_needs_the_insecure_tls_permission_and_an_https_address(self_signed):
    cert, _cert_path, _key_path = self_signed
    pin = _fingerprint(cert)
    only_outbound = HttpHandle(_PermissionChecker("ext-1", ["net.outbound"]), ["net.outbound"])
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    try:
        with pytest.raises(PermissionDenied):
            await only_outbound.get("https://127.0.0.1:1/", pinned_sha256=pin)
        with pytest.raises(ValueError):
            await handle.get("http://127.0.0.1:1/", pinned_sha256=pin)
        with pytest.raises(ValueError):
            await handle.get("https://127.0.0.1:1/", pinned_sha256=pin, insecure_tls=True)
        with pytest.raises(ValueError):
            await handle.get("https://127.0.0.1:1/", pinned_sha256="kein-fingerabdruck")
        with pytest.raises(ValueError):
            handle.websocket("ws://127.0.0.1:1/", pinned_sha256=pin)
        with pytest.raises(PermissionDenied):
            only_outbound.websocket("wss://127.0.0.1:1/", pinned_sha256=pin)
    finally:
        await only_outbound.aclose()
        await handle.aclose()


@pytest.mark.asyncio
async def test_pinned_clients_are_kept_per_fingerprint_and_bounded():
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    try:
        pins = [f"{i:02X}" * 32 for i in range(12)]
        first = handle._client_for(insecure_tls=False, pinned_sha256=pins[0], url="https://a.example/")
        assert handle._client_for(insecure_tls=False, pinned_sha256=pins[0].lower(), url="https://a.example/") is first
        assert handle._client_for(insecure_tls=False, url="https://a.example/") is not first
        for pin in pins[1:]:
            handle._client_for(insecure_tls=False, pinned_sha256=pin, url="https://a.example/")
        assert len(handle._pinned_clients) == 8
    finally:
        await handle.aclose()
    assert len(handle._pinned_clients) == 0


@pytest.mark.asyncio
async def test_pinned_websocket_with_another_certificate_sends_nothing(self_signed):
    _cert_obj, cert_path, key_path = self_signed
    other = _cert("pve1.example", _key())
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path)
    try:
        async with recorder as target:
            with pytest.raises(Exception) as caught:  # noqa: PT011 - websockets reicht den Handshake-Fehler unterschiedlich ein
                async with handle.websocket(
                    f"wss://{target}/vnc", headers={"Authorization": "PVEAPIToken=geheim"}, pinned_sha256=_fingerprint(other),
                ):
                    pass
            await asyncio.sleep(0.05)
    finally:
        await handle.aclose()
    assert _find_in_chain(caught.value, CertificateMismatch) is not None
    assert bytes(recorder.received) == b""


@pytest.mark.asyncio
async def test_pinned_websocket_reaches_the_server_with_the_confirmed_certificate(self_signed):
    cert, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path)
    try:
        async with recorder as target:
            with pytest.raises(Exception):  # noqa: B017, PT011 - die Gegenstelle spricht kein WebSocket; es geht nur ums Ankommen
                async with handle.websocket(f"wss://{target}/vnc", pinned_sha256=_fingerprint(cert), open_timeout_s=2.0):
                    pass
            await asyncio.sleep(0.05)
    finally:
        await handle.aclose()
    assert bytes(recorder.received).startswith(b"GET /vnc")


class _CountingServer:
    """Einfacher TCP-Server, der nur zaehlt, wie oft sich jemand verbindet (als "Proxy" bzw. Ziel einer Umleitung)."""

    def __init__(self) -> None:
        self.connections = 0
        self._server: asyncio.base_events.Server | None = None

    async def _handle(self, _reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        writer.close()

    async def __aenter__(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()


def _clear_proxy_environment(monkeypatch) -> None:
    """Jede `*_proxy`-Variable weg (auch `NO_PROXY`, das oft `127.0.0.1` ausnimmt -- dann liefe der Test-Server nie
    ueber den Proxy, und der Test bewiese nichts)."""
    import os

    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("variable", ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"])
async def test_a_pinned_request_never_goes_through_a_proxy_from_the_environment(self_signed, monkeypatch, variable):
    """Ein Proxy aus der Umgebung saehe Ziel und Kopfzeilen (bei `https` zumindest das Ziel), und ein TLS-Proxy mit
    eigenem Zertifikat stuende zwischen Erweiterung und Server. Der gepinnte Aufruf geht deshalb immer direkt -- und
    ginge er doch ueber einen Proxy, muesste der Handshake am Fingerabdruck scheitern, nicht still durchlaufen."""
    cert, cert_path, key_path = self_signed
    _clear_proxy_environment(monkeypatch)
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path)
    proxy = _CountingServer()
    try:
        async with proxy as proxy_port, recorder as target:
            monkeypatch.setenv(variable, f"http://127.0.0.1:{proxy_port}")
            response = await handle.post(
                f"https://{target}/api2/json/access/ticket", pinned_sha256=_fingerprint(cert), data={"password": "GEHEIM"},
            )
    finally:
        await handle.aclose()
    assert response.status_code == 200
    assert proxy.connections == 0, "der gepinnte Aufruf ging an den Proxy aus der Umgebung"
    assert bytes(recorder.received).startswith(b"POST /api2/json/access/ticket")


@pytest.mark.asyncio
async def test_a_pinned_request_with_follow_redirects_is_refused_before_anything_is_sent(self_signed):
    cert, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    recorder = _RecordingTlsServer(cert_path, key_path)
    pin = _fingerprint(cert)
    try:
        async with recorder as target:
            url = f"https://{target}/api2/json/access/ticket"
            for call in (
                lambda: handle.post(url, pinned_sha256=pin, follow_redirects=True, data={"password": "GEHEIM"}),
                lambda: handle.get(url, pinned_sha256=pin, follow_redirects=True),
                lambda: handle.request("POST", url, pinned_sha256=pin, follow_redirects=True, data={"password": "GEHEIM"}),
            ):
                with pytest.raises(ValueError, match="Weiterleitungen"):
                    await call()
            with pytest.raises(ValueError, match="Weiterleitungen"):
                handle.stream("POST", url, pinned_sha256=pin, follow_redirects=True)
            await asyncio.sleep(0.05)
    finally:
        await handle.aclose()
    assert recorder.connections == 0
    assert bytes(recorder.received) == b""


@pytest.mark.asyncio
async def test_a_pinned_request_returns_a_redirect_instead_of_following_it(self_signed):
    """307 heisst "dieselbe Anfrage samt Koerper an die neue Adresse": folgte der Client, ginge ein Passwort an ein
    Ziel, dessen Zertifikat niemand bestaetigt hat. Die 3xx-Antwort kommt unveraendert zurueck."""
    cert, cert_path, key_path = self_signed
    handle = HttpHandle(_PermissionChecker("ext-1", PERMS), PERMS)
    elsewhere = _CountingServer()
    try:
        async with elsewhere as other_port:
            recorder = _RecordingTlsServer(
                cert_path, key_path, status_line=b"307 Temporary Redirect",
                extra_headers=f"Location: https://127.0.0.1:{other_port}/api2/json/access/ticket\r\n".encode(),
            )
            async with recorder as target:
                response = await handle.post(
                    f"https://{target}/api2/json/access/ticket", pinned_sha256=_fingerprint(cert),
                    data={"password": "GEHEIM"},
                )
                await asyncio.sleep(0.05)
    finally:
        await handle.aclose()
    assert response.status_code == 307
    assert len(recorder.requests) == 1
    assert elsewhere.connections == 0


def test_sha256_fingerprint_accepts_the_usual_spellings():
    canonical = ":".join(["AB"] * 32)
    for spelling in ("ab" * 32, canonical.lower(), "SHA256 Fingerprint=" + canonical, " " + " ".join(["ab"] * 32) + " "):
        assert sha256_fingerprint(spelling) == canonical
    for bad in ("", "AB:CD", "ZZ" * 32, "ab" * 33):
        with pytest.raises(ValueError):
            sha256_fingerprint(bad)
