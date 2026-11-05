"""Stacks aus Git: Deck prüft die Ziel-Adresse des Servers und verbindet sich nur mit genau dieser."""

from __future__ import annotations

import asyncio
import datetime
import errno
import functools
import ipaddress
import socket
import ssl
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from wsgiref.simple_server import make_server

import pytest
from docker_stack_fakes import EXT_DIR
from test_ext_docker_git import COMPOSE, _commit, _git_app, _Quiet, _serve, _Threaded


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _gf():
    from nodvard_deck_ext_docker import git_fetch

    return git_fetch


@contextmanager
def _serve_on(app, host, port):
    server = make_server(host, port, app, server_class=_Threaded, handler_class=_Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def no_proxy(monkeypatch):
    for name in ("https_proxy", "http_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def dns(monkeypatch):
    """Namen, die das Netz kennt: `dns["git.lan"] = ["192.168.1.10"]`; eine Liste von Listen gilt Aufruf für Aufruf."""
    names: dict[str, list] = {}
    asked: list[str] = []
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in names:
            return real(host, port, *args, **kwargs)
        asked.append(host)
        answers = names[host]
        if answers and isinstance(answers[0], list):
            answers = answers[min(asked.count(host), len(answers)) - 1]
        if not answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in answers]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    names["asked"] = asked  # type: ignore[assignment]
    return names


@pytest.fixture
def connects(monkeypatch):
    """Zeichnet jeden Verbindungsaufbau auf; das Ziel lehnt immer ab."""
    seen: list[tuple[str, int]] = []
    gf = _gf()

    def create_connection(address, *args, **kwargs):
        seen.append(address)
        raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

    monkeypatch.setattr(gf.urllib3_connection, "create_connection", create_connection)
    return seen


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.8.8", "::1", "0.0.0.0", "0.1.2.3", "169.254.169.254", "169.254.170.2", "fe80::1", "::ffff:127.0.0.1",
    "64:ff9b::7f00:1", "2002:7f00:1::1", "::ffff:169.254.169.254", "100.100.100.200", "168.63.129.16", "fd00:ec2::254",
    "224.0.0.1", "ff02::1", "240.0.0.1", "255.255.255.255", "192.0.0.192", "fec0::1", "::ffff:100.100.100.200", "64:ff9b::a9fe:a9fe",
    "2002:6464:64c8::1",
])
def test_deck_itself_and_metadata_are_never_a_git_server(ip):
    for allow_private in (False, True):
        problem = _gf().address_problem("git.example.org", ipaddress.ip_address(ip), allow_private)
        assert problem and "holt Deck nie" in problem and f"git.example.org ({ip})" in problem


@pytest.mark.parametrize("ip", ["192.168.1.10", "10.0.0.5", "172.16.3.4", "100.64.0.1", "fd12:3456::1", "::ffff:192.168.1.10",
                                "64:ff9b::c0a8:101"])
def test_the_own_network_needs_the_setting(ip):
    gf = _gf()
    problem = gf.address_problem("git.lan", ipaddress.ip_address(ip), False)
    assert problem and gf.PRIVATE_SETTING in problem and "im eigenen Netz" in problem
    assert gf.address_problem("git.lan", ipaddress.ip_address(ip), True) is None


@pytest.mark.parametrize("ip", ["140.82.121.4", "2606:50c0:8000::153", "64:ff9b::8c52:7904", "::ffff:140.82.121.4"])
def test_servers_in_the_internet_are_fine(ip):
    assert _gf().address_problem("github.com", ipaddress.ip_address(ip), False) is None


def test_every_address_of_a_name_must_pass(dns):
    gf = _gf()
    dns["git.example.org"] = ["140.82.121.4", "127.0.0.1"]
    with pytest.raises(gf.GitProblem, match=r"git\.example\.org \(127\.0\.0\.1\) ist Deck selbst"):
        gf.target_addresses("git.example.org", 443, True)
    dns["git.lan"] = ["192.168.1.10", "fd12::7", "192.168.1.10"]
    with pytest.raises(gf.GitProblem, match="liegt im eigenen Netz"):
        gf.target_addresses("git.lan", 443, False)
    assert gf.target_addresses("git.lan", 443, True) == ["192.168.1.10", "fd12::7"]
    dns["nirgends.example.org"] = []
    with pytest.raises(gf.GitProblem, match="kennt das Netz nicht"):
        gf.target_addresses("nirgends.example.org", 443, True)


def test_a_connection_goes_only_to_the_checked_addresses_and_keeps_the_name(connects):
    gf = _gf()
    pool = gf._PinnedManager(["140.82.121.4", "2606:50c0:8000::153"]).connection_from_url("https://git.example.org/team/stacks")
    connection = pool._new_conn()
    with pytest.raises(gf.NewConnectionError, match="Failed to establish a new connection"):
        connection._new_conn()
    assert connects == [("140.82.121.4", 443), ("2606:50c0:8000::153", 443)]
    # SNI, Zertifikat und Host-Kopf nimmt urllib3 aus `host`.
    assert connection.host == "git.example.org"


def test_without_checked_addresses_there_is_no_connection(connects):
    gf = _gf()
    connection = gf._PinnedManager([]).connection_from_url("https://git.example.org/")._new_conn()
    with pytest.raises(gf.GitProblem, match="keine geprüfte Adresse"):
        connection._new_conn()
    assert connects == []


def test_a_server_that_does_not_answer_is_named(dns, monkeypatch, tmp_path, no_proxy):
    gf = _gf()
    dns["git.example.org"] = ["140.82.121.4"]

    def silent(address, *args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(gf.urllib3_connection, "create_connection", silent)
    answer = gf.handle({"url": "https://git.example.org/team/stacks", "ref": "main", "path": "compose.yaml",
                        "store": str(tmp_path / "store"), "credentials": None, "limits": {}})
    assert answer == {"ok": False, "problem": "Der Server antwortet nicht. Prüf die Adresse und ob Deck ihn erreichen kann."}


def test_the_next_address_is_tried_and_a_timeout_stays_one(monkeypatch):
    gf = _gf()
    seen = []
    good = socket.socket()

    def create_connection(address, *args, **kwargs):
        seen.append(address[0])
        if address[0] == "140.82.121.4":
            raise TimeoutError
        return good

    monkeypatch.setattr(gf.urllib3_connection, "create_connection", create_connection)
    connection = gf._PinnedManager(["140.82.121.4", "140.82.121.5"]).connection_from_url("https://git.example.org/")._new_conn()
    with good:
        assert connection._new_conn() is good and seen == ["140.82.121.4", "140.82.121.5"]
    connection = gf._PinnedManager(["140.82.121.4"]).connection_from_url("https://git.example.org/")._new_conn()
    with pytest.raises(gf.ConnectTimeoutError):
        connection._new_conn()


def _tls_files(tmp_path, name):
    """Eine eigene CA und ein Zertifikat nur für `name`, zur Laufzeit erzeugt."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)

    def issue(subject, key, issuer, signer, *extensions):
        builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
                   .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)])).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(hours=1))
                   .not_valid_after(now + datetime.timedelta(hours=1)))
        for extension, critical in extensions:
            builder = builder.add_extension(extension, critical=critical)
        return builder.sign(signer, hashes.SHA256())

    # Python ab 3.13 prüft streng: Schlüsselkennungen und die Verwendung des CA-Schlüssels müssen drinstehen.
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = issue("Test-CA", ca_key, "Test-CA", ca_key, (x509.BasicConstraints(ca=True, path_length=None), True),
               (x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False),
               (x509.KeyUsage(False, False, False, False, False, True, True, False, False), True))
    leaf = issue(name, key, "Test-CA", ca_key, (x509.SubjectAlternativeName([x509.DNSName(name)]), False),
                 (x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False))
    pem = serialization.Encoding.PEM
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(pem))
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(pem) + key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return tmp_path / "ca.pem", tmp_path / "leaf.pem"


def test_tls_still_checks_the_name_not_the_pinned_address(tmp_path):
    gf = _gf()
    ca, leaf = _tls_files(tmp_path, "git.example.org")
    seen: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"] = self.headers["Host"]
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf)
    context.sni_callback = lambda sock, name, ctx: seen.__setitem__("sni", name)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    manager = gf._PinnedManager(["127.0.0.1"], ca_certs=str(ca), cert_reqs="CERT_REQUIRED")
    try:
        assert manager.request("GET", f"https://git.example.org:{port}/", retries=False, timeout=10).status == 200
        assert seen == {"sni": "git.example.org", "host": f"git.example.org:{port}"}
        with pytest.raises(gf.urllib3.exceptions.SSLError, match="match|mismatch"):
            manager.request("GET", f"https://other.example.org:{port}/", retries=False, timeout=10)
    finally:
        server.shutdown()
        server.server_close()


def test_a_name_is_looked_up_once_and_cannot_switch_to_another_server(tmp_path, monkeypatch, dns, no_proxy):
    gf = _gf()
    origin = tmp_path / "origin"
    commit = _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    # Für den Test zählt 127.0.0.1 als erlaubt; danach zeigt der Name auf 127.0.0.2, wo ein anderer Server wartet.
    check = gf.address_problem
    monkeypatch.setattr(gf, "address_problem", lambda host, ip, allow: None if str(ip) == "127.0.0.1" else check(host, ip, allow))
    hosts, elsewhere = [], []
    inner = _git_app(origin)

    def app(environ, start_response):
        hosts.append(environ["HTTP_HOST"])
        return inner(environ, start_response)

    def other(environ, start_response):
        elsewhere.append(environ["PATH_INFO"])
        start_response("404 Not Found", [])
        return [b""]

    with _serve(app) as base, _serve_on(other, "127.0.0.2", int(base.rsplit(":", 1)[1])):
        port = base.rsplit(":", 1)[1]
        dns["git.example.org"] = [["127.0.0.1"], ["127.0.0.2"]]
        url = f"http://git.example.org:{port}/team/stacks"
        fetched = gf.fetch(url, "main", "shop/compose.yaml", tmp_path / "store", None,
                           lambda url, credentials, limits: gf.http_client(url, credentials, limits), gf.Limits())
    assert fetched.commit == commit
    assert dns["asked"] == ["git.example.org"] and elsewhere == []
    assert set(hosts) == {f"git.example.org:{port}"} and len(hosts) >= 2


def test_the_setting_reaches_the_connection(dns, connects, tmp_path, no_proxy):
    gf = _gf()
    dns["git.lan"] = ["192.168.1.10"]
    request = {"url": "https://git.lan/team/stacks", "ref": "main", "path": "compose.yaml", "store": str(tmp_path / "store"),
               "credentials": None, "limits": {}}
    for answer in [gf.handle(request)] + [gf.handle({**request, "allow_private": allowed}) for allowed in (None, "true", False)]:
        assert not answer["ok"] and gf.PRIVATE_SETTING in answer["problem"]
    assert connects == []
    answer = gf.handle({**request, "allow_private": True})
    assert answer == {"ok": False, "problem": "Der Server nimmt keine Verbindung an. Prüf Adresse und Port."}
    assert connects == [("192.168.1.10", 443)]


def test_behind_a_proxy_only_a_checked_address_goes_out(dns, connects, monkeypatch, tmp_path):
    gf = _gf()
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    client_for = functools.partial(gf.http_client, allow_private=True)
    # Ein Name, der Deck eine öffentliche Adresse nennt, könnte dem Proxy eine interne nennen.
    dns["git.example.org"] = ["140.82.121.4"]
    dns["nur-der-proxy.example.org"] = []
    for name in ("git.example.org", "nur-der-proxy.example.org"):
        with pytest.raises(gf.GitProblem, match=f"Über den Proxy \\(HTTPS_PROXY\\) holt Deck nicht von {name}: .* NO_PROXY"):
            gf.fetch(f"https://{name}/team/stacks", "main", "compose.yaml", tmp_path / "store", None, client_for, gf.Limits())
    assert connects == []
    for ip, problem in (("127.0.0.1", "ist Deck selbst"), ("192.168.1.10", "liegt im eigenen Netz")):
        with pytest.raises(gf.GitProblem, match=problem):
            gf.http_client(f"https://{ip}/team/stacks", None, gf.Limits())
    assert connects == []
    with pytest.raises(gf.GitProblem, match="Der Proxy"):
        gf.fetch("https://140.82.121.4/team/stacks", "main", "compose.yaml", tmp_path / "store", None, client_for, gf.Limits())
    assert connects == [("127.0.0.1", 9)]
    monkeypatch.setenv("no_proxy", "git.example.org")
    with pytest.raises(gf.GitProblem):
        gf.fetch("https://git.example.org/team/stacks", "main", "compose.yaml", tmp_path / "store", None, client_for, gf.Limits())
    assert connects[-1] == ("140.82.121.4", 443)


@pytest.mark.asyncio
async def test_the_worker_process_never_asks_deck_itself(tmp_path, no_proxy):
    from nodvard_deck_ext_docker import git_source

    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    asked: list[str] = []
    inner = _git_app(origin)

    def app(environ, start_response):
        asked.append(environ["PATH_INFO"])
        return inner(environ, start_response)

    with _serve(app) as base:
        for allow_private in (False, True):
            request = {"url": f"{base}/team/stacks", "ref": "main", "path": "shop/compose.yaml", "store": str(tmp_path / "store"),
                       "credentials": None, "limits": {}, "allow_private": allow_private}
            with pytest.raises(_gf().GitProblem, match=r"127\.0\.0\.1 ist Deck selbst"):
                git_source.parse_answer(await asyncio.wait_for(git_source.run_worker(request, git_source.LIMITS), 60), git_source.LIMITS)
    assert asked == []
