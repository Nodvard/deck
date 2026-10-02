"""Engine-Client gegen einen echten HTTP-Server auf einem Unix-Socket.

Geprueft wird der echte Client (`nodvard_deck_updater.engine`): Aushandlung der API-Version (1.41 bis 1.54, Podman,
fehlender Kopf), die Allowlist (Endpunkte, Pfadteile, Abfragen, Bodies), Antworten (304, 404, 409, 500, Fehler im
200-Strom, kaputtes JSON, falscher Inhaltstyp, doppelte Schluessel, zu gross, abgebrochen) und Transportfehler
(Socket fehlt, keiner hoert zu, keine Rechte, Zeitlimit).
"""

from __future__ import annotations

import json
import os
import socket
import tempfile

import pytest
from fake_engine import FakeEngine, Response, World
from nodvard_deck_updater import engine as E
from nodvard_deck_updater import policy
from nodvard_deck_updater.policy import Refusal

CID = "a" * 64
NID = "b" * 64
IMAGE_ID = "sha256:" + "c" * 64
DIGEST = "sha256:" + "d" * 64


@pytest.fixture
def fake():
    with FakeEngine(World()) as server:
        yield server
    assert server.violations == []


@pytest.fixture
def client(fake):
    engine = E.Engine(fake.path)
    engine.negotiate()
    return engine


# ---------------------------------------------------------------------------
# Aushandlung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("server", "minimum", "expected"), [
    ("1.54", "1.40", (1, 54)),
    ("1.43", "1.12", (1, 43)),  # Synology Container Manager (Docker 24)
    ("1.41", "1.12", (1, 41)),  # Debian 11 / Raspberry Pi OS bullseye
    ("1.55", "1.40", (1, 54)),  # neuer als getestet: der Helfer bleibt bei 1.54
    ("1.99", "1.44", (1, 54)),
    ("1.52", "1.44", (1, 52)),  # Docker 29.0 bis 29.2
])
def test_negotiation_takes_the_lower_of_server_and_helper(server, minimum, expected):
    world = World(api=server)
    world.version["MinAPIVersion"] = minimum
    with FakeEngine(world) as fake:
        engine = E.Engine(fake.path)
        assert engine.negotiate() == expected
        assert engine.api_version == expected
        engine.list_containers()
        assert fake.calls("GET", "/containers/json")[0].raw_path.startswith("/v{}.{}/".format(*expected))
        assert fake.calls("GET", "/_ping")[0].raw_path == "/_ping"  # ohne Version
        assert fake.calls("GET", "/version")[0].version == "{}.{}".format(*expected)


@pytest.mark.parametrize(("server", "minimum", "code"), [
    ("1.40", "1.12", policy.API_TOO_OLD),   # Engine zu alt
    ("1.24", "1.12", policy.API_TOO_OLD),
    ("1.43", "1.44", policy.API_TOO_OLD),   # MinAPIVersion ueber der eigenen Version
    ("1.60", "1.55", policy.ENGINE_UNSUPPORTED),  # Engine kennt die Versionen des Helfers nicht mehr
])
def test_negotiation_refuses_versions_out_of_range(server, minimum, code):
    world = World(api=server)
    world.version["MinAPIVersion"] = minimum
    with FakeEngine(world) as fake:
        engine = E.Engine(fake.path)
        with pytest.raises(Refusal) as exc:
            engine.negotiate()
        assert exc.value.code == code
        assert engine.api_version is None
        with pytest.raises(E.EngineError) as unnegotiated:
            engine.list_containers()  # ohne ausgehandelte Version geht kein Aufruf hinaus
        assert unnegotiated.value.reason == "not_negotiated" and unnegotiated.value.code == policy.ENGINE_UNREACHABLE
        assert fake.calls("GET", "/containers/json") == []


@pytest.mark.parametrize("header", [None, "", "2.0", "1.x", "1.54 ", "1.5400", "v1.54"])
def test_negotiation_without_a_valid_api_version_header(header):
    world = World()
    with FakeEngine(world) as fake:
        fake.route("GET", "/_ping", Response(body=b"OK", content_type="text/plain",
                                             headers={} if header is None else {"Api-Version": header}))
        with pytest.raises(Refusal) as exc:
            E.Engine(fake.path).negotiate()
    assert exc.value.code == policy.ENGINE_UNSUPPORTED


@pytest.mark.parametrize("version", [
    {"Components": [{"Name": "Podman Engine", "Version": "4.9.3"}], "ApiVersion": "1.41", "MinAPIVersion": "1.24"},
    {"Platform": {"Name": "linux/amd64/fedora-39 Podman Engine"}, "ApiVersion": "1.41", "MinAPIVersion": "1.24"},
])
def test_podman_is_not_supported(version):
    with FakeEngine(World(api="1.41", version=version)) as fake, pytest.raises(Refusal) as exc:
        E.Engine(fake.path).negotiate()
    assert exc.value.code == policy.ENGINE_UNSUPPORTED and exc.value.detail == "podman"


def test_version_without_min_api_version_is_accepted():
    with FakeEngine(World(api="1.41", version={"Version": "20.10.5", "ApiVersion": "1.41"})) as fake:
        assert E.Engine(fake.path).negotiate() == (1, 41)


def test_nothing_goes_out_before_the_first_negotiation(fake):
    engine = E.Engine(fake.path)
    for call in (lambda: engine.list_containers(), lambda: engine.inspect_container(CID),
                 lambda: engine.start_container(CID), lambda: engine.stop_container(CID, 10),
                 lambda: engine.create_container("deck", {"Image": policy.REPOSITORY, "HostConfig": {}}),
                 lambda: engine.pull(DIGEST)):
        with pytest.raises(E.EngineError) as exc:
            call()
        assert exc.value.reason == "not_negotiated" and not isinstance(exc.value, E.NotAllowed)
    assert fake.requests == []


@pytest.mark.parametrize("failure", [
    Response(status=500, body={"message": "boom"}),
    Response(raw=b""),                       # Verbindung ohne Antwort geschlossen (der Docker-Dienst startet neu)
    Response(body=b"OK", content_type="text/plain", delay=0.0, drop=True),
])
def test_failed_renegotiation_is_an_engine_error_and_the_old_version_stays(fake, client, failure):
    # Der Docker-Dienst startet kurz neu: die erneute Aushandlung scheitert am Transport. Danach muessen Aufrufe
    # `EngineError` (nicht `NotAllowed`) werfen bzw. mit der bisherigen Version weiterlaufen.
    assert client.api_version == (1, 54)
    fake.route("GET", "/_ping", failure)
    with pytest.raises(E.EngineError) as exc:
        client.negotiate()
    assert exc.value.code == policy.ENGINE_UNREACHABLE
    assert client.api_version == (1, 54)
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(body={"Id": CID}))
    assert client.inspect_container(CID) == {"Id": CID}  # kein NotAllowed
    assert fake.calls("GET", f"/containers/{CID}/json")[0].version == "1.54"


def test_failed_renegotiation_with_an_unsupported_answer_drops_the_version(fake, client):
    fake.route("GET", "/version", Response(body=b"not json", content_type="text/plain"))
    with pytest.raises(E.EngineError) as exc:
        client.negotiate()
    assert exc.value.code == policy.ENGINE_UNSUPPORTED
    assert client.api_version is None
    with pytest.raises(E.EngineError) as after:
        client.inspect_container(CID)
    assert after.value.reason == "not_negotiated"


def test_renegotiation_refused_afterwards_drops_the_version(fake, client):
    # Die Engine ist jetzt Podman (oder zu alt): die alte Version darf nicht weiter benutzt werden.
    fake.route("GET", "/version", Response(body={"Components": [{"Name": "Podman Engine"}], "ApiVersion": "1.41",
                                                "MinAPIVersion": "1.24"}))
    with pytest.raises(Refusal):
        client.negotiate()
    assert client.api_version is None
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "not_negotiated"


def test_a_successful_renegotiation_switches_the_version(fake, client):
    fake.world.api = "1.43"
    fake.world.version["MinAPIVersion"] = "1.12"
    assert client.negotiate() == (1, 43) == client.api_version


def test_ping_error_status_is_an_engine_error():
    with FakeEngine() as fake:
        fake.route("GET", "/_ping", Response(status=500, body={"message": "boom"}))
        with pytest.raises(E.EngineError) as exc:
            E.Engine(fake.path).negotiate()
    assert exc.value.status == 500 and exc.value.code == policy.ENGINE_UNREACHABLE


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def test_missing_socket_is_unreachable(tmp_path):
    engine = E.Engine(str(tmp_path / "nope.sock"))
    with pytest.raises(E.EngineError) as exc:
        engine.negotiate()
    assert exc.value.reason == "unreachable" and exc.value.code == policy.ENGINE_UNREACHABLE


def test_socket_without_listener_is_unreachable():
    directory = tempfile.mkdtemp(prefix="ndu", dir="/tmp")
    path = os.path.join(directory, "s")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)  # gebunden, aber kein listen(): connect wird verweigert
    try:
        with pytest.raises(E.EngineError) as exc:
            E.Engine(path).negotiate()
        assert exc.value.reason == "unreachable"
    finally:
        sock.close()
        os.unlink(path)
        os.rmdir(directory)


def test_regular_file_instead_of_socket_is_unreachable(tmp_path):
    path = tmp_path / "file"
    path.write_text("x")
    with pytest.raises(E.EngineError) as exc:
        E.Engine(str(path)).negotiate()
    assert exc.value.reason == "unreachable"


@pytest.mark.skipif(os.geteuid() == 0, reason="root darf jeden Socket oeffnen (DAC); als uid 1000 laufen lassen")
def test_socket_without_permission_is_unreachable(fake):
    os.chmod(fake.path, 0o600)
    os.chmod(os.path.dirname(fake.path), 0o700)
    # Ein anderer Nutzer kaeme nicht heran; als Besitzer simulieren wir es ueber einen nicht lesbaren Ordner.
    os.chmod(os.path.dirname(fake.path), 0o000)
    try:
        with pytest.raises(E.EngineError) as exc:
            E.Engine(fake.path).negotiate()
        assert exc.value.reason == "unreachable"
    finally:
        os.chmod(os.path.dirname(fake.path), 0o700)


def test_socket_path_too_long_is_unreachable():
    with pytest.raises(E.EngineError) as exc:
        E.Engine("/tmp/" + "x" * 200).negotiate()
    assert exc.value.reason == "unreachable"


def test_slow_answer_runs_into_the_call_timeout(fake):
    engine = E.Engine(fake.path, call_timeout=0.3)
    engine.negotiate()
    fake.route("GET", "/containers/json", Response(body=[], delay=1.0))
    with pytest.raises(E.EngineError) as exc:
        engine.list_containers()
    assert exc.value.reason == "timeout" and exc.value.code == policy.ENGINE_UNREACHABLE


def test_slowly_dripping_body_runs_into_the_total_deadline(fake):
    engine = E.Engine(fake.path, call_timeout=0.5)
    engine.negotiate()
    chunks = [b"[" + b" " * 10] + [b" " * 10] * 40 + [b"]"]
    fake.route("GET", "/containers/json", Response(chunks=chunks, chunk_delay=0.05))
    with pytest.raises(E.EngineError) as exc:
        engine.list_containers()
    assert exc.value.reason == "timeout"


def test_connection_dropped_mid_body(fake, client):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(body={"Id": CID}, drop=True))
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    assert exc.value.reason in ("disconnected", "bad_response")


def test_connection_closed_without_an_answer_is_disconnected(fake, client):
    fake.route("GET", "/containers/json", Response(raw=b""))
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "disconnected" and exc.value.code == policy.ENGINE_UNREACHABLE


def test_garbage_instead_of_http(fake, client):
    fake.route("GET", "/containers/json", Response(raw=b"NOT HTTP AT ALL\r\n\r\n"))
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "bad_response" and exc.value.code == policy.ENGINE_UNSUPPORTED


def test_overlong_header_line(fake, client):
    fake.route("GET", "/containers/json", Response(raw=b"HTTP/1.1 200 OK\r\nX: " + b"a" * 70000 + b"\r\n\r\n"))
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "bad_response"


# ---------------------------------------------------------------------------
# Antworten
# ---------------------------------------------------------------------------


def test_too_large_answer_by_content_length(fake, client):
    fake.route("GET", "/containers/json", Response(body=b"[" + b" " * (E.MAX_RESPONSE_BYTES + 10) + b"]"))
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "too_large" and exc.value.code == policy.ENGINE_UNSUPPORTED


def test_too_large_answer_without_content_length(fake, client):
    block = b" " * (1024 * 1024)
    fake.route("GET", "/containers/json", Response(chunks=[b"["] + [block] * 17 + [b"]"]))
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "too_large"


def test_answer_just_below_the_limit_is_read(fake, client):
    payload = b"[" + b" " * (E.MAX_RESPONSE_BYTES - 2) + b"]"
    fake.route("GET", "/containers/json", Response(body=payload))
    assert client.list_containers() == []


@pytest.mark.parametrize("body", [b"{not json", b'{"Id": 1, "Id": 2}', b"NaN", b'{"x": 1e999}', b"\xff\xfe"])
def test_broken_json_is_bad_response(fake, client, body):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(body=body))
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    assert exc.value.reason == "bad_response"


def test_json_endpoint_with_other_content_type_is_bad_response(fake, client):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(body=b'{"Id": "x"}', content_type="text/html"))
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    assert exc.value.reason == "bad_response"


@pytest.mark.parametrize("body", [[], "x", 1, None])
def test_inspect_must_be_an_object(fake, client, body):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(body=json.dumps(body).encode()))
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    assert exc.value.reason == "bad_response"


def test_list_must_be_a_list_of_objects(fake, client):
    fake.route("GET", "/containers/json", Response(body=[1, 2]))
    with pytest.raises(E.EngineError) as exc:
        client.list_containers()
    assert exc.value.reason == "bad_response"


def test_not_found_and_conflict(fake, client):
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    assert exc.value.not_found and exc.value.status == 404
    fake.route("POST", r"/containers/[0-9a-f]{64}/rename",
               Response(status=409, body={"message": "Conflict. The name \x1b[31m\"/x\"\x1b[0m is already in use"}))
    with pytest.raises(E.EngineError) as exc:
        client.rename_container(CID, "x-previous")
    assert exc.value.status == 409 and not exc.value.not_found
    assert "\x1b" not in exc.value.message and "\\x1b" in exc.value.message  # ANSI escaped, nur fuers Log


def test_error_message_is_sanitized_and_truncated(fake, client):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json",
               Response(status=500, body={"message": "zeile1\nzeile2 " + "x" * 1000}))
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    message = exc.value.message
    assert len(message) <= E.MESSAGE_MAX_CHARS and "\n" not in message and "\\n" in message
    assert message.isascii()


def test_error_without_json_body(fake, client):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(status=500, body=b"panic\n", content_type=None))
    with pytest.raises(E.EngineError) as exc:
        client.inspect_container(CID)
    assert exc.value.status == 500 and exc.value.message == "panic\\n"


def test_start_and_stop_report_304_as_already_done(fake, client):
    fake.route("POST", r"/containers/[0-9a-f]{64}/start", Response(status=304))
    fake.route("POST", r"/containers/[0-9a-f]{64}/stop", Response(status=204))
    assert client.start_container(CID) is False
    assert client.stop_container(CID, 10) is True
    fake.route("POST", r"/containers/[0-9a-f]{64}/start", Response(status=204))
    fake.route("POST", r"/containers/[0-9a-f]{64}/stop", Response(status=304))
    assert client.start_container(CID) is True
    assert client.stop_container(CID, 10) is False
    assert fake.calls("POST", r"/containers/.*/stop")[0].query == {"t": ["10"]}


def test_stop_waits_longer_than_the_call_timeout(fake):
    engine = E.Engine(fake.path, call_timeout=0.2)
    engine.negotiate()
    fake.route("POST", r"/containers/[0-9a-f]{64}/stop", Response(status=204, delay=0.5))
    assert engine.stop_container(CID, 1) is True  # Zeitlimit t + 30 s, nicht das Aufruf-Limit


def test_create_returns_id_and_warnings(fake, client):
    fake.route("POST", "/containers/create", Response(status=201, body={"Id": CID, "Warnings": ["a", "b"]}))
    body = {"Image": policy.REPOSITORY + ":latest", "HostConfig": {}}
    assert client.create_container("deck", body) == (CID, 2)
    call = fake.calls("POST", "/containers/create")[0]
    assert call.query == {"name": ["deck"]} and call.body == body
    assert call.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("answer", [{"Id": "short"}, {"Warnings": []}, {"Id": CID.upper()}])
def test_create_with_bad_id_in_answer(fake, client, answer):
    fake.route("POST", "/containers/create", Response(status=201, body=answer))
    with pytest.raises(E.EngineError) as exc:
        client.create_container("deck", {"Image": policy.REPOSITORY, "HostConfig": {}})
    assert exc.value.reason == "bad_response"


def test_create_conflict(fake, client):
    fake.route("POST", "/containers/create", Response(status=409, body={"message": "name in use"}))
    with pytest.raises(E.EngineError) as exc:
        client.create_container("deck", {"Image": policy.REPOSITORY, "HostConfig": {}})
    assert exc.value.status == 409


# ---------------------------------------------------------------------------
# Pull-Strom
# ---------------------------------------------------------------------------


def _stream(*objects: dict) -> list[bytes]:
    return [json.dumps(obj).encode() + b"\r\n" for obj in objects]


def test_pull_reads_the_whole_stream(fake, client):
    chunks = _stream({"status": "Pulling from nodvard/deck"}, {"status": "Downloading", "progress": "[=>  ]"},
                     {"status": "Digest: " + DIGEST}, {"status": "Status: Downloaded newer image"})
    fake.route("POST", "/images/create", Response(chunks=chunks))
    assert client.pull(DIGEST) == 4
    call = fake.calls("POST", "/images/create")[0]
    assert call.query == {"fromImage": [policy.REPOSITORY], "tag": [DIGEST]}


def test_pull_stream_split_at_odd_places(fake, client):
    data = b"".join(_stream({"status": "aä"}, {"status": "b"}, {"status": "c"}))
    chunks = [data[i:i + 3] for i in range(0, len(data), 3)]  # auch mitten in einem UTF-8-Zeichen
    fake.route("POST", "/images/create", Response(chunks=chunks))
    assert client.pull(DIGEST) == 3


def test_pull_error_inside_the_200_stream(fake, client):
    chunks = _stream({"status": "Pulling"}, {"errorDetail": {"message": "manifest unknown"},
                                             "error": "manifest unknown\x1b[0m"}, {"status": "never"})
    fake.route("POST", "/images/create", Response(chunks=chunks))
    with pytest.raises(E.EngineError) as exc:
        client.pull(DIGEST)
    assert exc.value.reason == "stream_error" and "\x1b" not in exc.value.message


def test_pull_error_detail_only(fake, client):
    fake.route("POST", "/images/create", Response(chunks=_stream({"errorDetail": {"message": "denied"}})))
    with pytest.raises(E.EngineError) as exc:
        client.pull(DIGEST)
    assert exc.value.reason == "stream_error" and exc.value.message == "denied"


def test_pull_error_before_the_stream(fake, client):
    fake.route("POST", "/images/create", Response(status=500, body={"message": "Get https://ghcr.io: dial tcp"}))
    with pytest.raises(E.EngineError) as exc:
        client.pull(DIGEST)
    assert exc.value.reason == "http" and exc.value.status == 500


@pytest.mark.parametrize("chunks", [
    [b'{"status": "a"}\r\n{"status": '],          # abgeschnitten
    [b'{"status": "a"}\r\nnot json\r\n'],         # kaputte Zeile
    [b'["list"]\r\n'],                            # kein Objekt
    [b'{"a": 1, "a": 2}\r\n'],                    # doppelter Schluessel
])
def test_pull_broken_stream(fake, client, chunks):
    fake.route("POST", "/images/create", Response(chunks=chunks))
    with pytest.raises(E.EngineError) as exc:
        client.pull(DIGEST)
    assert exc.value.reason == "bad_response"


def test_pull_stream_aborted(fake, client):
    fake.route("POST", "/images/create", Response(chunks=_stream({"status": "a"}, {"status": "b"}), drop=True))
    with pytest.raises(E.EngineError) as exc:
        client.pull(DIGEST)
    assert exc.value.reason in ("disconnected", "bad_response")


def test_pull_single_object_too_large(fake, client):
    fake.route("POST", "/images/create", Response(chunks=[b'{"status": "' + b"x" * (E.STREAM_OBJECT_MAX_BYTES + 10)]))
    with pytest.raises(E.EngineError) as exc:
        client.pull(DIGEST)
    assert exc.value.reason == "too_large"


# ---------------------------------------------------------------------------
# Allowlist: jeder Endpunkt, Pfadteile, Abfragen, Bodies
# ---------------------------------------------------------------------------


def test_every_endpoint_of_the_table_reaches_the_engine_as_expected(fake, client):
    fake.route("POST", r".*", Response(status=200, body={}))
    fake.route("DELETE", r".*", Response(status=204))
    fake.route("POST", "/containers/create", Response(status=201, body={"Id": CID}))
    fake.route("POST", r"/containers/.*/(start|stop|rename)", Response(status=204))
    fake.route("POST", r"/images/.*/tag", Response(status=201))
    fake.route("DELETE", r"/images/.*", Response(status=200, body=[]))
    fake.route("POST", "/images/create", Response(chunks=_stream({"status": "ok"})))
    fake.route("GET", r".*", Response(body={}))
    fake.route("GET", "/containers/json", Response(body=[]))
    fake.route("GET", "/version", Response(body={"ApiVersion": "1.54", "MinAPIVersion": "1.40"}))
    fake.route("GET", "/_ping", Response(body=b"OK", headers={"Api-Version": "1.54"}, content_type="text/plain"))
    client.list_containers(["com.docker.compose.project=p"])
    client.inspect_container(CID)
    client.inspect_image(IMAGE_ID)
    client.inspect_image(policy.REPOSITORY + "@" + DIGEST)
    client.inspect_network(NID)
    client.distribution("latest")
    client.distribution("0.7")
    client.create_container("deck", {"Image": policy.REPOSITORY, "HostConfig": {}})
    client.connect_network(NID, CID, {"Aliases": ["deck"]})
    client.start_container(CID)
    client.stop_container(CID, 30)
    client.rename_container(CID, "deck-previous")
    client.set_restart_policy(CID, "no")
    client.remove_container(CID)
    client.tag_image(IMAGE_ID, policy.REPOSITORY, "latest")
    client.tag_image(IMAGE_ID, E.PREVIOUS_REPOSITORY, "0.7.0")
    client.remove_protect_tag("0.7.0")
    client.pull(DIGEST)
    client.negotiate()
    seen = {(r.method, r.path) for r in fake.requests}
    assert seen == {
        ("GET", "/_ping"), ("GET", "/version"), ("GET", "/containers/json"), ("GET", f"/containers/{CID}/json"),
        ("GET", f"/images/{IMAGE_ID}/json"), ("GET", f"/images/{policy.REPOSITORY}@{DIGEST}/json"),
        ("GET", f"/networks/{NID}"), ("GET", f"/distribution/{policy.REPOSITORY}:latest/json"),
        ("GET", f"/distribution/{policy.REPOSITORY}:0.7/json"), ("POST", "/containers/create"),
        ("POST", f"/networks/{NID}/connect"), ("POST", f"/containers/{CID}/start"),
        ("POST", f"/containers/{CID}/stop"), ("POST", f"/containers/{CID}/rename"),
        ("POST", f"/containers/{CID}/update"), ("DELETE", f"/containers/{CID}"),
        ("POST", f"/images/{IMAGE_ID}/tag"), ("DELETE", "/images/nodvard-deck-previous:0.7.0"),
        ("POST", "/images/create"),
    }
    assert len({name for name in E.ENDPOINTS}) == len(E.ENDPOINTS) == 17
    by_path = {(r.method, r.path): r for r in fake.requests}
    assert by_path[("DELETE", f"/containers/{CID}")].query == {"v": ["0"], "force": ["0"]}
    assert by_path[("POST", f"/containers/{CID}/update")].body == {
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}}
    assert by_path[("POST", f"/networks/{NID}/connect")].body == {"Container": CID,
                                                                  "EndpointConfig": {"Aliases": ["deck"]}}
    assert by_path[("GET", "/containers/json")].query == {
        "all": ["1"], "filters": ['{"label":["com.docker.compose.project=p"]}']}
    assert by_path[("DELETE", "/images/nodvard-deck-previous:0.7.0")].query == {}
    # Image-Referenzen im Pfad sind kodiert, `/`, `:` und `@` bleiben (quote(..., safe="/:@")).
    assert by_path[("GET", f"/images/{policy.REPOSITORY}@{DIGEST}/json")].raw_path.endswith(
        f"/images/{policy.REPOSITORY}@{DIGEST}/json")


@pytest.mark.parametrize(("method", "pattern"), [
    ("POST", "/containers/{id}/exec"), ("GET", "/containers/{id}/archive"), ("POST", "/build"),
    ("POST", "/commit"), ("POST", "/images/load"), ("GET", "/volumes"), ("POST", "/swarm/init"),
    ("GET", "/plugins"), ("POST", "/session"), ("GET", "/containers/{id}/logs"), ("GET", "/info"),
    ("POST", "/containers/{id}/kill"), ("POST", "/containers/{id}/json"), ("DELETE", "/containers/{id}/json"),
    ("GET", "/containers/create"), ("PUT", "/containers/{id}/archive"), ("POST", "/containers/prune"),
])
def test_anything_outside_the_table_is_refused_before_sending(fake, client, method, pattern):
    with pytest.raises(E.NotAllowed):
        client._request(method, pattern, params={"id": CID} if "{id}" in pattern else None)
    assert len(fake.requests) == 2  # nur _ping und version aus negotiate


@pytest.mark.parametrize("bad", ["a" * 63, "A" * 64, "a" * 65, "../" + "a" * 61, "a" * 63 + "\n", 12, None,
                                 "sha256:" + "a" * 64, "a" * 32 + "/" + "a" * 31])
def test_container_and_network_ids_are_checked(fake, client, bad):
    for call in (client.inspect_container, client.inspect_network, client.start_container,
                 client.remove_container):
        with pytest.raises(E.NotAllowed):
            call(bad)
    assert len(fake.requests) == 2


@pytest.mark.parametrize("ref", [
    "ghcr.io/nodvard/deck:latest", "ghcr.io/nodvard/deck", "sha256:" + "a" * 63, "SHA256:" + "a" * 64,
    "docker.io/x@" + DIGEST, "ghcr.io/nodvard/deck@sha256:" + "A" * 64, "ghcr.io/nodvard/deck@" + DIGEST + "/x",
    "../" + DIGEST, "ghcr.io/nodvard/deck-evil@" + DIGEST,
])
def test_image_references_are_checked(fake, client, ref):
    with pytest.raises(E.NotAllowed):
        client.inspect_image(ref)


@pytest.mark.parametrize("tag", ["0.7.1", "beta", "", "latest/../x", "0.7\n", "LATEST", "01.7"])
def test_distribution_only_for_floating_tags(fake, client, tag):
    with pytest.raises(E.NotAllowed):
        client.distribution(tag)


def test_repository_comes_from_the_constructor_only(fake):
    engine = E.Engine(fake.path, repository="registry.test/x/deck")
    engine.negotiate()
    fake.route("GET", r"/distribution/.+/json", Response(body={"Descriptor": {}}))
    engine.distribution("latest")
    assert fake.calls("GET", r"/distribution/.+/json")[0].path == "/distribution/registry.test/x/deck:latest/json"
    with pytest.raises(E.NotAllowed):
        engine.inspect_image(policy.REPOSITORY + "@" + DIGEST)


@pytest.mark.parametrize(("call", "args"), [
    ("pull", ("latest",)), ("pull", ("0.7.1",)), ("pull", ("sha256:" + "a" * 63,)), ("pull", ("",)),
    ("tag_image", (IMAGE_ID, "docker.io/evil", "latest")),
    ("tag_image", (IMAGE_ID, policy.REPOSITORY, "beta")),
    ("tag_image", (IMAGE_ID, E.PREVIOUS_REPOSITORY, "latest")),  # Schutz-Tag nur mit Version
    ("tag_image", (IMAGE_ID, E.PREVIOUS_REPOSITORY, "0.7")),
    ("tag_image", (IMAGE_ID, policy.REPOSITORY, "0.6.0")),  # das Repository nur mit beweglichem Tag
    ("tag_image", ("a" * 64, policy.REPOSITORY, "latest")),
    ("remove_protect_tag", ("latest",)), ("remove_protect_tag", ("0.7",)),
    ("rename_container", (CID, "/bad")), ("rename_container", (CID, "")), ("rename_container", (CID, "a" * 200)),
    ("create_container", ("bad name", {"Image": "ghcr.io/nodvard/deck", "HostConfig": {}})),
    ("stop_container", (CID, -1)), ("stop_container", (CID, 99999)), ("stop_container", (CID, "10")),
    ("stop_container", (CID, True)),
    ("set_restart_policy", (CID, "sometimes")), ("set_restart_policy", (CID, "no", -1)),
    ("set_restart_policy", (CID, "no", True)),
])
def test_query_rules(fake, client, call, args):
    with pytest.raises(E.NotAllowed):
        getattr(client, call)(*args)
    assert len(fake.requests) == 2


@pytest.mark.parametrize("query", [
    {"v": "1", "force": "0"}, {"v": "0", "force": "1"}, {"v": "true", "force": "0"}, {"v": "0"}, {},
    {"v": "0", "force": "0", "link": "1"},
])
def test_remove_container_query_is_fixed(fake, client, query):
    with pytest.raises(E.NotAllowed):
        client._request("DELETE", "/containers/{id}", params={"id": CID}, query=query)


@pytest.mark.parametrize("query", [
    {"all": "0"}, {}, {"all": "1", "size": "1"}, {"all": "1", "filters": {"status": ["running"]}},
    {"all": "1", "filters": {"label": ["x"], "name": ["y"]}}, {"all": "1", "filters": {"label": "x=y"}},
    {"all": "1", "filters": {"label": ["Bad=Label"]}}, {"all": "1", "filters": {"label": ["a=b c"]}},
])
def test_list_query_rules(fake, client, query):
    with pytest.raises(E.NotAllowed):
        client._request("GET", "/containers/json", query=query)


@pytest.mark.parametrize("body", [
    {"RestartPolicy": {"Name": "no"}},
    {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}, "Memory": 1},
    {"Memory": 1},
    {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0, "x": 1}},
    {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0.0}},
    None, [],
])
def test_update_only_with_restart_policy(fake, client, body):
    with pytest.raises(E.NotAllowed):
        client._request("POST", "/containers/{id}/update", params={"id": CID}, body=body)


@pytest.mark.parametrize("body", [
    {"Image": policy.REPOSITORY, "HostConfig": {}, "Entrypoint": []},
    {"Image": policy.REPOSITORY, "HostConfig": {}, "Entrypoint": None},
    {"Image": policy.REPOSITORY, "HostConfig": {}, "Entrypoint": ""},
    {"Image": policy.REPOSITORY, "HostConfig": {"AutoRemove": True}},
    {"Image": policy.REPOSITORY},
    {"Image": policy.REPOSITORY + ":0.7.1", "HostConfig": {}},    # fest eingetragen
    {"Image": policy.REPOSITORY + "@" + DIGEST, "HostConfig": {}},  # Digest statt Tag-Text
    {"Image": IMAGE_ID, "HostConfig": {}},
    {"Image": "docker.io/library/alpine", "HostConfig": {}},
    [],
])
def test_create_body_rules(fake, client, body):
    with pytest.raises(E.NotAllowed):
        client.create_container("deck", body)
    assert fake.calls("POST", "/containers/create") == []


def _create_body(**changes):
    """Ein Body, wie `clone.build` ihn liefert (so klein wie moeglich); `changes` ersetzen oder (`_DEL`) entfernen."""
    body = {"Image": policy.REPOSITORY + ":latest", "Cmd": ["serve"], "Env": ["A=1"], "Labels": {"x": "y"},
            "HostConfig": {"Binds": ["data:/app/data"], "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
            "NetworkingConfig": {"EndpointsConfig": {"net": {"Aliases": ["deck"]}}}}
    for key, value in changes.items():
        if value is _DEL:
            body.pop(key, None)
        else:
            body[key] = value
    return body


_DEL = object()


def test_the_create_body_of_the_clone_passes(fake, client):
    body = _create_body(HostConfig={"Binds": [], "MaskedPaths": [], "ReadonlyPaths": [], "Links": ["a:b"]},
                        Healthcheck={"Test": ["CMD", "x"], "Retries": 3}, ExposedPorts={"8080/tcp": {}},
                        Hostname="deck", User="1000")
    client._body_for("create_container", _create_body(NetworkingConfig=_DEL))
    assert client.create_container("deck", body)[0] == "f" * 64
    assert fake.calls("POST", "/containers/create")[0].body == body


@pytest.mark.parametrize("body", [
    # andere Schreibweisen: der Docker-Dienst ordnet JSON-Schluessel ohne Beachtung der Gross-/Kleinschreibung zu,
    # bei Doppelten gewinnt der letzte (`policy.dumps` sortiert Grossbuchstaben vor Kleinbuchstaben)
    _create_body(entrypoint=["/bin/sh", "-c", "id"]),
    _create_body(ENTRYPOINT=["/bin/sh"]),
    _create_body(EntryPoint=[]),
    _create_body(image="evil/other:1"),
    _create_body(IMAGE="evil/other:1"),
    _create_body(hostconfig={"Binds": []}),
    _create_body(HostConfig={"Binds": [], "autoremove": True}),
    _create_body(HostConfig={"Binds": [], "AUTOREMOVE": True}),
    _create_body(HostConfig={"Binds": [], "binds": ["/:/host"]}),
    _create_body(HostConfig={"Binds": [], "privileged": True}),
    _create_body(networkingconfig={"EndpointsConfig": {}}),
    _create_body(NetworkingConfig={"endpointsconfig": {}}),
    _create_body(Healthcheck={"Test": ["CMD", "x"], "test": ["NONE"]}),
    # Schluessel, die `clone.build` nie sendet
    _create_body(AutoRemove=False),
    _create_body(Entrypoint=None),
    _create_body(MacAddress="02:42:ac:11:00:02"),
    _create_body(NetworkDisabled=True),
    _create_body(OnBuild=["x"]),
    _create_body(Shell=["x"]),
    _create_body(HostConfig={"Binds": [], "AutoRemove": False}),
    _create_body(HostConfig={"Binds": [], "ContainerIDFile": ""}),
    _create_body(HostConfig={"Binds": [], "KernelMemory": 0}),
    _create_body(HostConfig={"Binds": [], "Brandneu": 1}),
    _create_body(NetworkingConfig={"EndpointsConfig": {"net": {"MacAddress": "x"}}}),
    _create_body(NetworkingConfig={"EndpointsConfig": {"net": {"aliases": ["x"]}}}),
    _create_body(NetworkingConfig={"EndpointsConfig": {"a": {}, "b": {}}}),
    _create_body(NetworkingConfig={"EndpointsConfig": {"net": []}}),
    _create_body(NetworkingConfig={"EndpointsConfig": []}),
    _create_body(NetworkingConfig={"EndpointsConfig": {}, "Extra": 1}),
    _create_body(NetworkingConfig=[]),
    _create_body(Healthcheck=[]),
    _create_body(Healthcheck={"Test": ["CMD", "x"], "Brandneu": 1}),
    {1: "x", "Image": policy.REPOSITORY, "HostConfig": {}},
])
def test_create_body_only_with_known_keys_in_exact_spelling(fake, client, body):
    with pytest.raises(E.NotAllowed):
        client.create_container("deck", body)
    assert fake.calls("POST", "/containers/create") == []


def test_create_body_key_lists_have_no_two_names_that_differ_only_in_case():
    from nodvard_deck_updater import clone
    for names in (clone.CREATE_CONFIG_KEYS, clone.CREATE_HOST_KEYS, set(clone.ENDPOINT_COPY),
                  set(clone.HEALTH_FIELDS)):
        assert len({name.casefold() for name in names}) == len(names)
    assert not {"Entrypoint", "AutoRemove", "NetworkDisabled"} & (clone.CREATE_CONFIG_KEYS | clone.CREATE_HOST_KEYS)


@pytest.mark.parametrize("endpoint", [
    {"aliases": ["x"]}, {"Aliases": ["x"], "aliases": ["y"]}, {"MacAddress": "x"}, {"NetworkID": "x"}, {"Brandneu": 1},
])
def test_connect_endpoint_config_only_with_known_keys(fake, client, endpoint):
    with pytest.raises(E.NotAllowed):
        client.connect_network(NID, CID, endpoint)
    assert fake.calls("POST", f"/networks/{NID}/connect") == []


@pytest.mark.parametrize("body", [
    {"Container": CID}, {"Container": "short", "EndpointConfig": {}}, {"Container": CID, "EndpointConfig": []},
    {"Container": CID, "EndpointConfig": {}, "Force": True},
])
def test_connect_body_rules(fake, client, body):
    with pytest.raises(E.NotAllowed):
        client._request("POST", "/networks/{id}/connect", params={"id": NID}, body=body)


def test_bodies_are_only_sent_where_allowed(fake, client):
    with pytest.raises(E.NotAllowed):
        client._request("POST", "/containers/{id}/start", params={"id": CID}, body={"x": 1})
    with pytest.raises(E.NotAllowed):
        client._request("GET", "/containers/{id}/json", params={"id": CID}, query={"size": "1"})
    with pytest.raises(E.NotAllowed):
        client._request("GET", "/containers/{id}/json", params={"id": CID, "x": "y"})
    with pytest.raises(E.NotAllowed):
        client._request("GET", "/containers/{id}/json", params={})


def test_engine_docstring_keeps_track_of_the_missing_binding():
    # Die Bindung der aendernden Endpunkte an die IDs und Namen aus dem Journal fehlt noch; sie ist der erste Schritt
    # des Ablaufs. Solange das so ist, steht es im Kopf des Moduls -- der Ablauf ersetzt diesen Test durch die Tests
    # der Bindung, damit der Hinweis nicht verloren geht.
    doc = E.__doc__
    assert "Noch nicht umgesetzt" in doc and "Bindung" in doc
    for rule in ("remove_container", "tag_image", "rename", "connect", "`pull`"):
        assert rule in doc


def test_sanitize():
    assert E.sanitize("a\x1b[31mb\nc d\x00") == "a\\x1b[31mb\\nc\\u2028d\\x00"
    assert E.sanitize(None) == "" and E.sanitize(b"x") == "" and E.sanitize(5) == "5"
    assert len(E.sanitize("\x00" * 1000)) == E.MESSAGE_MAX_CHARS


def test_engine_error_codes():
    assert E.EngineError("unreachable").code == policy.ENGINE_UNREACHABLE
    assert E.EngineError("timeout").code == policy.ENGINE_UNREACHABLE
    assert E.EngineError("disconnected").code == policy.ENGINE_UNREACHABLE
    assert E.EngineError("http", status=500).code == policy.ENGINE_UNREACHABLE
    assert E.EngineError("too_large").code == policy.ENGINE_UNSUPPORTED
    assert E.EngineError("bad_response").code == policy.ENGINE_UNSUPPORTED
    assert E.EngineError("http", status=404).not_found
    assert not E.EngineError("bad_response", status=404).not_found


def test_parse_api_version():
    assert E.parse_api_version("1.41") == (1, 41)
    assert E.parse_api_version("1.100") == (1, 100)
    for bad in ("1.41.0", "2.1", "1.", "1.٤١", " 1.41", None, 1.41, "1.1000"):
        assert E.parse_api_version(bad) is None
