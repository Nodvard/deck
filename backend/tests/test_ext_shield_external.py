"""Außenprüfung: Portvergleich, Dienstzuordnung und Zertifikatsbefunde."""

from __future__ import annotations

import asyncio
import ssl
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"


@pytest.fixture
def external(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_shield import external

    yield external
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _host(**kwargs):
    values = dict(id="h1", address="192.0.2.10", name="server", credential_port=2222, metadata={})
    return SimpleNamespace(**(values | kwargs))


def _ctx(host=None, services=()):
    provider = SimpleNamespace(list_services=AsyncMock(return_value=list(services)))
    return SimpleNamespace(
        hosts=SimpleNamespace(get=AsyncMock(return_value=host or _host())),
        capabilities=SimpleNamespace(query=AsyncMock(return_value=[provider])),
        exec=SimpleNamespace(run=AsyncMock(side_effect=AssertionError("Keine Befehle auf dem Server"))),
    )


def _certificate(hostname="app.example.test", days=30, with_key=False):
    key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    certificate = (
        x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    der = certificate.public_bytes(serialization.Encoding.DER)
    return (der, key) if with_key else der


@pytest.mark.asyncio
async def test_first_success_is_baseline_and_next_run_reports_new_port(external, monkeypatch):
    ctx = _ctx()
    scan = AsyncMock(return_value={22: "open", 443: "closed"})
    monkeypatch.setattr(external, "_scan_ports", scan)
    first = await external.check_host(ctx, "h1")
    assert first["status"] == "ok"
    assert first["open_ports"] == [22]
    assert first["new_ports"] == []
    scan.return_value = {22: "open", 443: "open"}
    second = await external.check_host(ctx, "h1", first)
    assert second["status"] == "warning"
    assert second["new_ports"] == [443]
    assert isinstance(second["checked_at"], float)
    ctx.exec.run.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_ports_cannot_replace_successful_baseline(external, monkeypatch):
    previous = {"status": "ok", "open_ports": [22, 443]}
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(return_value={22: "open", 443: "unknown"}))
    result = await external.check_host(_ctx(), "h1", previous)
    assert result["status"] == "unavailable"
    assert result["error"]
    assert result["new_ports"] == []
    assert previous == {"status": "ok", "open_ports": [22, 443]}


@pytest.mark.asyncio
async def test_filtered_ports_on_reachable_host_allow_reachability_comparison(external, monkeypatch):
    scan = AsyncMock(return_value={22: "open", 443: "filtered", 80: "filtered"})
    monkeypatch.setattr(external, "_scan_ports", scan)
    first = await external.check_host(_ctx(), "h1")
    assert first["status"] == "warning"
    scan.return_value = {22: "open", 443: "open", 80: "filtered"}
    second = await external.check_host(_ctx(), "h1", first)
    assert second["new_ports"] == [443]
    scan.return_value = {22: "filtered", 443: "filtered", 80: "filtered"}
    failed = await external.check_host(_ctx(), "h1", second)
    assert failed["status"] == "unavailable"
    assert failed["error"]


@pytest.mark.asyncio
async def test_failed_previous_run_is_not_used_for_comparison(external, monkeypatch):
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(return_value={443: "open"}))
    result = await external.check_host(_ctx(), "h1", {"status": "unavailable", "open_ports": []})
    assert result["new_ports"] == []


@pytest.mark.asyncio
async def test_first_seen_service_port_and_changed_address_are_not_newly_open(external, monkeypatch):
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(return_value={12345: "open"}))
    ctx = _ctx(services=[{"host_id": "h1", "published_ports": [12345]}])
    previous = {"status": "ok", "open_ports": [], "checked_ports": [22, 443], "address": "192.0.2.10"}
    result = await external.check_host(ctx, "h1", previous)
    assert result["new_ports"] == []
    previous.update(checked_ports=[12345], address="192.0.2.11")
    assert (await external.check_host(ctx, "h1", previous))["new_ports"] == []


@pytest.mark.asyncio
async def test_service_targets_keep_custom_hostname_and_ignore_other_hosts(external, monkeypatch):
    host = _host(metadata={"services": [{"url": "https://metadata.example.test:9443/path?token=secret"}]})
    ctx = _ctx(host, [
        {"host_id": "h1", "published_ports": [10443, 12345], "url": "https://app.example.test:10443/"},
        {"host_id": "h2", "host_address": host.address, "published_ports": [45678], "url": "https://other.example.test"},
        {"host_address": host.address, "published_ports": [12346], "url": "https://192.0.2.10:12346"},
    ])
    scan = AsyncMock(return_value={22: "open"})
    certificate = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(external, "_scan_ports", scan)
    monkeypatch.setattr(external, "_certificate", certificate)
    result = await external.check_host(ctx, "h1")
    assert result["status"] == "ok"
    address, ports = scan.call_args.args
    assert address == host.address
    assert {2222, 10443, 12345, 12346} <= set(ports)
    assert 45678 not in ports
    targets = {(call.args[0], call.args[1]) for call in certificate.call_args_list}
    assert targets == {("app.example.test", 10443), ("metadata.example.test", 9443), ("192.0.2.10", 12346)}


@pytest.mark.asyncio
async def test_catalog_failure_is_not_a_clean_empty_service_list(external, monkeypatch):
    ctx = _ctx()
    ctx.capabilities.query.side_effect = RuntimeError("offline")
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(return_value={22: "open"}))
    result = await external.check_host(ctx, "h1")
    assert result["status"] == "unavailable"
    assert any("Dienstkatalog" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_invalid_known_service_url_is_not_a_clean_result(external, monkeypatch):
    ctx = _ctx(services=[{"host_id": "h1", "url": "https://example.test:invalid/"}])
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(return_value={22: "open"}))
    result = await external.check_host(ctx, "h1")
    assert result["status"] == "unavailable"
    assert any("Dienstadresse" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_missing_host_and_dns_failure_are_unavailable(external, monkeypatch):
    ctx = _ctx()
    ctx.hosts.get.return_value = None
    assert (await external.check_host(ctx, "missing"))["status"] == "unavailable"
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(side_effect=OSError("DNS")))
    result = await external.check_host(_ctx(), "h1")
    assert result["status"] == "unavailable"
    assert result["error"]


@pytest.mark.asyncio
async def test_scan_limit_marks_result_incomplete(external, monkeypatch):
    ctx = _ctx(services=[{"host_id": "h1", "published_ports": list(range(20000, 20200))}])
    scan = AsyncMock(return_value={22: "open"})
    monkeypatch.setattr(external, "_scan_ports", scan)
    result = await external.check_host(ctx, "h1")
    assert len(scan.call_args.args[1]) == external.MAX_PORTS
    assert set(external.COMMON_PORTS) <= set(scan.call_args.args[1])
    assert result["status"] == "unavailable"


@pytest.mark.asyncio
async def test_expiry_hostname_and_self_signed_are_separate_findings(external, monkeypatch):
    handshake = AsyncMock(side_effect=[ssl.SSLCertVerificationError("self signed"), (_certificate(days=10), "TLSv1.3")])
    monkeypatch.setattr(external, "_handshake", handshake)
    monkeypatch.setattr(external, "_weak_protocols", AsyncMock(return_value=(["TLS 1.0"], True)))
    result = await external._certificate("wrong.example.test", 443, datetime.now(UTC).timestamp())
    assert result["status"] == "warning"
    assert result["self_signed"] is True
    assert result["hostname_matches"] is False
    assert result["trusted"] is False
    assert result["days_remaining"] == 9
    assert result["expires_at"] > datetime.now(UTC).timestamp()
    assert result["weak_protocols"] == ["TLS 1.0"]
    assert any("14 Tagen" in warning for warning in result["warnings"])
    assert any("Heimnetz" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_unavailable_protocol_probe_is_not_reported_as_safe(external, monkeypatch):
    monkeypatch.setattr(external, "_handshake", AsyncMock(return_value=(_certificate(), "TLSv1.3")))
    monkeypatch.setattr(external, "_weak_protocols", AsyncMock(return_value=([], False)))
    result = await external._certificate("app.example.test", 443, datetime.now(UTC).timestamp())
    assert result["protocol_check_available"] is False
    assert result["status"] == "warning"
    assert any("keine Entwarnung" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_expiry_warning_boundary_is_strictly_less_than_14_days(external, monkeypatch):
    der = _certificate()
    expiry = x509.load_der_x509_certificate(der).not_valid_after_utc.timestamp()
    monkeypatch.setattr(external, "_handshake", AsyncMock(return_value=(der, "TLSv1.3")))
    monkeypatch.setattr(external, "_weak_protocols", AsyncMock(return_value=([], True)))
    boundary = await external._certificate("app.example.test", 443, expiry - 14 * 86400)
    assert not any("14 Tagen" in warning for warning in boundary["warnings"])
    expired = await external._certificate("app.example.test", 443, expiry + 1)
    assert any("abgelaufen" in warning for warning in expired["warnings"])


@pytest.mark.asyncio
async def test_tls_failure_does_not_invent_certificate_details(external, monkeypatch):
    monkeypatch.setattr(external, "_handshake", AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(external, "_weak_protocols", AsyncMock(return_value=([], False)))
    result = await external._certificate("app.example.test", 443, datetime.now(UTC).timestamp())
    assert result["status"] == "unavailable"
    assert result["issuer"] is None
    assert result["hostname_matches"] is None
    assert result["error"]


@pytest.mark.asyncio
async def test_only_explicit_protocol_rejection_is_conclusive(external, monkeypatch):
    rejected = ssl.SSLError("rejected")
    rejected.reason = "TLSV1_ALERT_PROTOCOL_VERSION"
    monkeypatch.setattr(external, "_handshake", AsyncMock(side_effect=rejected))
    monkeypatch.setattr(external.ssl, "HAS_TLSv1", True)
    monkeypatch.setattr(external.ssl, "HAS_TLSv1_1", True)
    assert await external._weak_protocols("app.example.test", 443) == ([], True)
    monkeypatch.setattr(external, "_handshake", AsyncMock(side_effect=ssl.SSLError("no ciphers")))
    assert await external._weak_protocols("app.example.test", 443) == ([], False)


@pytest.mark.asyncio
async def test_connect_scan_uses_real_local_tcp_listener(external):
    connected = asyncio.Event()

    async def connection(reader, writer):
        connected.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await external._connect("127.0.0.1", port) == "open"
        await asyncio.wait_for(connected.wait(), 1)
    assert await external._connect("127.0.0.1", port) == "closed"


@pytest.mark.asyncio
async def test_certificate_check_uses_real_tls_and_sni(external, monkeypatch, tmp_path):
    der, key = _certificate("localhost", with_key=True)
    cert_path = tmp_path / "certificate.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    names = []
    context.sni_callback = lambda connection, name, initial: names.append(name)

    async def connection(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connection, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(external, "_weak_protocols", AsyncMock(return_value=([], True)))
    async with server:
        result = await external._certificate("localhost", port, datetime.now(UTC).timestamp())
    assert result["self_signed"] is True
    assert result["hostname_matches"] is True
    assert result["trusted"] is False
    assert result["error"] is None
    assert "localhost" in names


@pytest.mark.asyncio
async def test_scan_limits_concurrent_connections(external, monkeypatch):
    active = 0
    peak = 0

    async def connect(address, port):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return "closed"

    monkeypatch.setattr(external, "_connect", connect)
    result = await external._scan_ports("127.0.0.1", list(range(20, 40)))
    assert len(result) == 20
    assert peak == external.MAX_PARALLEL


@pytest.mark.asyncio
async def test_cancellation_is_propagated(external, monkeypatch):
    monkeypatch.setattr(external, "_scan_ports", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await external.check_host(_ctx(), "h1")
