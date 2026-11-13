"""HTTPS-Ziele beim Holen von Stacks aus Git."""

from __future__ import annotations

import importlib.util
import socket
import ssl
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

URL = "https://git.example.org/team/stacks"


@pytest.fixture(scope="module")
def gf():
    path = Path(__file__).resolve().parents[2] / "extensions/docker/src/nodvard_deck_ext_docker/git_fetch.py"
    spec = importlib.util.spec_from_file_location("git_fetch_network", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[spec.name]


@pytest.fixture
def no_proxy(monkeypatch):
    for name in ("https_proxy", "http_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize(("host", "addresses"), [
    ("192.168.1.10", ["192.168.1.10"]),
    ("10.0.0.5", ["10.0.0.5"]),
    ("172.16.0.1", ["172.16.0.1"]),
    ("git.example.org", ["127.0.0.1"]),
    ("git.example.org", ["0.0.0.0"]),
    ("git.example.org", ["169.254.169.254"]),
    ("git.example.org", ["10.0.0.5"]),
    ("git.example.org", ["100.64.0.1"]),
    ("git.example.org", ["224.0.0.1"]),
    ("git.example.org", ["::1"]),
    ("git.example.org", ["fd00::1"]),
    ("git.example.org", ["fe80::1"]),
    ("git.example.org", ["fec0::1"]),
    ("git.example.org", ["4000::1"]),
    ("git.example.org", ["64:ff9b::a00:1"]),
    ("git.example.org", ["ff02::1"]),
    ("git.example.org", ["::ffff:192.168.1.10"]),
    ("git.example.org", ["93.184.216.34", "192.168.1.10"]),
    ("git.example.org", ["2606:4700:4700::1111", "::1"]),
])
def test_https_fetch_refuses_internal_addresses(tmp_path, monkeypatch, no_proxy, gf, host, addresses):
    connected = []

    def resolve(name, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in addresses]

    def connect(sock, address):
        connected.append(address)
        raise OSError("Testverbindung")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", connect)
    blocked = {"127.0.0.1", "0.0.0.0", "169.254.169.254", "224.0.0.1", "::1", "fe80::1", "fec0::1", "4000::1", "ff02::1"}
    problem = "holt Deck nie" if blocked.intersection(addresses) else "Git-Server im eigenen Netz erlauben"
    with pytest.raises(gf.GitProblem, match=problem):
        gf.fetch(f"https://{host}/team/stacks", "main", "compose.yaml", tmp_path / "store", ("deck", "token"))
    assert connected == []


@pytest.mark.parametrize("proxy_scheme", ["http", "https"])
@pytest.mark.parametrize("host", ["git.example.org", "192.168.1.10", "10.0.0.5", "172.16.0.1"])
def test_https_fetch_refuses_unsafe_proxy_targets(tmp_path, monkeypatch, no_proxy, gf, proxy_scheme, host):
    resolved, connected = [], []

    def resolve(name, port, *args, **kwargs):
        resolved.append(name)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (name, port))]

    monkeypatch.setenv("https_proxy", f"{proxy_scheme}://proxy.example.org:3128")
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: connected.append(address))
    named = host == "git.example.org"
    problem = r"Über den Proxy \(HTTPS_PROXY\).*NO_PROXY" if named else "Git-Server im eigenen Netz erlauben"
    with pytest.raises(gf.GitProblem, match=problem):
        gf.fetch(f"https://{host}/team/stacks", "main", "compose.yaml", tmp_path / "store", ("deck", "token"))
    assert resolved == ([] if named else [host])
    assert connected == []


@pytest.mark.parametrize(("address", "allow_private"), [
    ("93.184.216.34", False), ("2606:4700:4700::1111", False), ("192.168.1.10", True),
])
def test_https_connect_pins_dns_and_keeps_tls_hostname(monkeypatch, no_proxy, gf, address, allow_private):
    import urllib3.connection

    resolved, connected, tls = [], [], []

    def resolve(host, port, *args, **kwargs):
        resolved.append(host)
        ip = (address if resolved.count(host) == 1 else "127.0.0.1") if host == "git.example.org" else host
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    class VerifiedSocket(NamedTuple):
        socket: socket.socket
        is_verified: bool

    def wrap(sock, **kwargs):
        tls.append(kwargs)
        return VerifiedSocket(sock, True)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: connected.append(address))
    monkeypatch.setattr(urllib3.connection, "_ssl_wrap_socket_and_match_hostname", wrap)
    client, _ = gf.http_client(URL, None, gf.Limits(), allow_private=allow_private)
    pool = client.pool_manager._inner.connection_from_url(URL)
    conn = pool._new_conn()
    try:
        conn.connect()
        assert resolved.count("git.example.org") == 1
        assert connected == [(address, 443)]
        assert tls[-1]["server_hostname"] == "git.example.org"
        assert urllib3.util.ssl_.resolve_cert_reqs(tls[-1]["cert_reqs"]) == ssl.CERT_REQUIRED
        assert conn.host == "git.example.org"
    finally:
        conn.close()


@pytest.mark.parametrize("answers", [[], None])
def test_https_fetch_stops_when_dns_has_no_address(tmp_path, monkeypatch, no_proxy, gf, answers):
    connected = []

    def resolve(*args, **kwargs):
        if answers is None:
            raise socket.gaierror("Kein Name")
        return answers

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: connected.append(address))
    with pytest.raises(gf.GitProblem, match="Den Namen des Servers kennt das Netz nicht"):
        gf.fetch(URL, "main", "compose.yaml", tmp_path / "store")
    assert connected == []
