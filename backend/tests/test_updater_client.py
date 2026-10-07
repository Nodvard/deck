"""Kanal zum Update-Helfer (`core.updater_client`): Status sicher lesen, Anforderungen sicher schreiben, und der
Gleichlauf mit dem Helfer selbst (Konstanten, gemeinsame Vektoren, echter Kanal des Helfers)."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
import time

import pytest
from nodvard_deck.core import updater_client as client
from nodvard_deck.core import updates
from updater_helpers import (
    OTHER_ID,
    REQUEST_ID,
    helper_module,
    make_channel,
    result,
    status_doc,
    vectors,
    write_status,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Kanal nur unter POSIX (dir_fd, O_NOFOLLOW)")


@pytest.fixture(autouse=True)
def _own_uid(monkeypatch):
    """Ohne root gehoert der Kanal dem, der den Test ausfuehrt."""
    monkeypatch.setattr(client, "EXPECTED_UID", os.getuid())


@pytest.fixture
def channel(tmp_path):
    return make_channel(tmp_path / "updater")


# ---------------------------------------------------------------------------
# Status lesen
# ---------------------------------------------------------------------------


def test_missing_channel_or_status_means_no_helper(tmp_path, channel):
    assert client.read_status(tmp_path / "gibt-es-nicht") == client.HelperStatus(False, "missing", None)
    assert client.read_status(channel) == client.HelperStatus(False, "missing", None)


def test_a_fresh_valid_status_is_present(channel):
    doc = status_doc(previous={"version": "0.6.9", "until": int(time.time()) + 3600}, results=[result()])
    write_status(channel, doc)
    status = client.read_status(channel)
    assert status.present and status.reason is None
    assert status.doc["target"] == doc["target"] and status.doc["previous"] == doc["previous"]
    assert status.doc["results"] == doc["results"]


@pytest.mark.parametrize(("offset", "present"), [(-89, True), (-91, False), (299, True), (301, False)])
def test_heartbeat_age_and_future(channel, offset, present):
    now = time.time()
    write_status(channel, status_doc(heartbeat_at=int(now) + offset))
    status = client.read_status(channel, now=now)
    assert status.present is present
    if not present:
        assert status.reason == "stale" and status.doc is not None, "die Ergebnisse eines alten Status gelten weiter"


def test_root_as_symlink_is_unsafe(tmp_path, channel):
    link = tmp_path / "link"
    link.symlink_to(channel)
    write_status(channel, status_doc())
    assert client.read_status(link).reason == "unsafe"


def test_status_as_symlink_is_never_followed(tmp_path, channel):
    elsewhere = tmp_path / "fremd.json"
    elsewhere.write_text(json.dumps(status_doc()))
    os.chmod(elsewhere, 0o644)
    (channel / "status.json").symlink_to(elsewhere)
    assert client.read_status(channel).reason == "unsafe"


def test_root_writable_for_others_or_foreign_owner_is_unsafe(channel, monkeypatch):
    write_status(channel, status_doc())
    os.chmod(channel, 0o777)
    assert client.read_status(channel).reason == "unsafe"
    os.chmod(channel, 0o755)
    assert client.read_status(channel).present
    monkeypatch.setattr(client, "EXPECTED_UID", os.getuid() + 1)
    assert client.read_status(channel).reason == "unsafe", "der Kanal muss root gehoeren"


@pytest.mark.skipif(os.geteuid() != 0, reason="nur root kann den Besitzer aendern")
def test_status_of_another_owner_is_unsafe(channel):
    path = write_status(channel, status_doc())
    os.chown(path, 1000, 1000)
    assert client.read_status(channel).reason == "unsafe"


def test_status_writable_for_others_hardlinked_too_big_or_fifo_is_unsafe(tmp_path, channel):
    write_status(channel, status_doc(), mode=0o666)
    assert client.read_status(channel).reason == "unsafe"

    write_status(channel, status_doc())
    os.link(channel / "status.json", tmp_path / "zweiter-name")
    assert client.read_status(channel).reason == "unsafe"
    os.unlink(tmp_path / "zweiter-name")

    write_status(channel, None, raw=b" " * (client.STATUS_MAX_BYTES + 1))
    assert client.read_status(channel).reason == "unsafe"

    os.unlink(channel / "status.json")
    os.mkfifo(channel / "status.json", 0o644)
    done: list = []
    reader = threading.Thread(target=lambda: done.append(client.read_status(channel)), daemon=True)
    reader.start()
    reader.join(5)
    assert done and done[0].reason == "unsafe", "ein FIFO blockiert nicht"


def test_sparse_file_reports_its_size_and_is_unsafe(channel):
    path = channel / "status.json"
    with open(path, "wb") as fh:
        fh.truncate(1024 * 1024 * 1024)
    os.chmod(path, 0o644)
    assert client.read_status(channel).reason == "unsafe"


@pytest.mark.parametrize(("raw", "reason"), [
    (b'{"proto": 1, "proto": 1}', "invalid"),
    (b"[1, 2", "invalid"),
    (b'{"proto": NaN}', "invalid"),
    (b'{"x": "\xe4"}', "invalid"),
    (b"[" * 8000 + b"]" * 8000, "invalid"),
], ids=["doppelt", "abgeschnitten", "nan", "kein-utf8", "verschachtelt"])
def test_broken_json_is_invalid(channel, raw, reason):
    write_status(channel, None, raw=raw)
    assert client.read_status(channel).reason == reason


def test_other_protocol_is_reported(channel):
    write_status(channel, status_doc(proto=2))
    assert client.read_status(channel).reason == "proto"
    write_status(channel, status_doc(request_versions=[2]))
    assert client.read_status(channel).reason == "proto", "der Helfer versteht unsere Anforderungen nicht"


def test_unknown_keys_fall_away_and_free_text_never_passes(channel):
    doc = status_doc(message="X=GEHEIM123", results=[result(), {**result(OTHER_ID), "log": "X=GEHEIM123"}])
    doc["target"]["extra"] = "X=GEHEIM123"
    write_status(channel, doc)
    status = client.read_status(channel)
    assert status.present
    assert "GEHEIM123" not in json.dumps(status.doc)
    assert [r["id"] for r in status.doc["results"]] == [REQUEST_ID, OTHER_ID]

    write_status(channel, status_doc(reason="Fehler: X=GEHEIM123"))
    assert client.read_status(channel).reason == "invalid"
    write_status(channel, status_doc(results=[result(code="pull failed X=GEHEIM123"), result(OTHER_ID)]))
    status = client.read_status(channel)
    assert [r["id"] for r in status.doc["results"]] == [OTHER_ID], "nur der kaputte Eintrag faellt weg"


# ---------------------------------------------------------------------------
# Anforderung schreiben
# ---------------------------------------------------------------------------


def test_request_is_written_atomically_with_0644_even_under_umask_077(channel):
    old = os.umask(0o077)
    try:
        written = client.write_request(channel, action="update", version="0.7.1", now=1790812395)
    finally:
        os.umask(old)
    files = os.listdir(channel / "requests")
    assert files == [f"{written['id']}.json"], "keine Zwischendatei bleibt liegen"
    path = channel / "requests" / files[0]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    body = json.loads(path.read_bytes())
    assert body == {"v": 1, "id": written["id"], "action": "update", "version": "0.7.1", "created_at": 1790812395}
    assert sorted(body) == list(client.REQUEST_KEYS)
    assert client.pending_requests(channel) == [written["id"]]


@pytest.mark.parametrize("mode", [0o777, 0o755, 0o1770])
def test_requests_dir_with_wrong_mode_is_refused(channel, mode):
    os.chmod(channel / "requests", mode)
    with pytest.raises(client.ChannelError) as exc:
        client.write_request(channel, action="update", version="0.7.1")
    assert exc.value.code == "unsafe"
    os.chmod(channel / "requests", 0o700)
    assert os.listdir(channel / "requests") == []


def test_requests_as_symlink_or_missing_is_refused(tmp_path, channel):
    os.rmdir(channel / "requests")
    with pytest.raises(client.ChannelError) as exc:
        client.write_request(channel, action="update", version="0.7.1")
    assert exc.value.code == "missing"
    target = tmp_path / "anderswo"
    target.mkdir()
    os.chmod(target, 0o1777)
    (channel / "requests").symlink_to(target)
    with pytest.raises(client.ChannelError) as exc:
        client.write_request(channel, action="update", version="0.7.1")
    assert exc.value.code == "unsafe"
    assert os.listdir(target) == []
    with pytest.raises(client.ChannelError):
        client.pending_requests(channel)


def test_unsafe_root_is_refused(channel, monkeypatch):
    os.chmod(channel, 0o775)
    with pytest.raises(client.ChannelError):
        client.write_request(channel, action="update", version="0.7.1")
    os.chmod(channel, 0o755)
    monkeypatch.setattr(client, "EXPECTED_UID", os.getuid() + 1)
    with pytest.raises(client.ChannelError):
        client.write_request(channel, action="update", version="0.7.1")


def test_a_prepared_tmp_symlink_is_never_followed(tmp_path, channel):
    victim = tmp_path / "opfer"
    victim.write_text("unveraendert")
    (channel / "requests" / f".{REQUEST_ID}.tmp").symlink_to(victim)
    with pytest.raises(client.ChannelError):
        client.write_request(channel, action="update", version="0.7.1", request_id=REQUEST_ID)
    assert victim.read_text() == "unveraendert"
    assert not (channel / "requests" / f"{REQUEST_ID}.json").exists()


@pytest.mark.parametrize(("action", "version"), [
    ("update", "0.7.1-rc1"), ("update", "07.1.0"), ("update", "0.7.١"), ("install", "0.7.1"),
    ("update", "0.7.1\n"), ("update", "../0.7.1"),
])
def test_invalid_requests_are_never_written(channel, action, version):
    with pytest.raises(ValueError):
        client.write_request(channel, action=action, version=version)
    assert os.listdir(channel / "requests") == []


# ---------------------------------------------------------------------------
# Gleichlauf mit dem Helfer
# ---------------------------------------------------------------------------


def test_constants_match_the_helper_and_the_shared_vectors():
    proto = vectors("protocol")
    policy = helper_module("policy")
    channel = helper_module("channel")
    assert client.PROTOCOL == proto["proto"] == policy.PROTOCOL
    assert [client.REQUEST_VERSION] == proto["request_versions"] == list(policy.REQUEST_VERSIONS)
    assert list(client.REQUEST_KEYS) == proto["request_keys"] == sorted(policy.REQUEST_KEYS)
    assert list(client.ACTIONS) == proto["actions"] == list(policy.ACTIONS)
    assert list(client.STATES) == proto["states"] == list(policy.STATES)
    assert list(client.OUTCOMES) == proto["outcomes"] == list(policy.OUTCOMES)
    assert list(client.STEPS) == proto["steps"] == list(policy.STEPS)
    assert client.CODE_RE.pattern == proto["code_pattern"] == policy.CODE_RE.pattern
    assert all(client.is_code(code) for group in proto["codes"].values() for code in group)
    files = proto["files"]
    assert client.STATUS_NAME == files["status"] == channel.STATUS_NAME
    assert client.REQUESTS_DIR == files["requests_dir"] == channel.REQUESTS_DIR
    assert client.REQUEST_NAME_RE.pattern == channel.REQUEST_NAME_RE.pattern
    assert client.TMP_NAME_RE.pattern == channel.DASHBOARD_TMP_RE.pattern
    for name in (f"{REQUEST_ID}.json", f"{REQUEST_ID.upper()}.json", f"{REQUEST_ID}.json.tmp", f".{REQUEST_ID}.tmp",
                 f".{REQUEST_ID}.tmp.x", f"x{REQUEST_ID}.json"):
        for mine, pattern in ((client.REQUEST_NAME_RE, "request_name_pattern"), (client.TMP_NAME_RE, "dashboard_tmp_pattern")):
            expected = re.fullmatch(files[pattern], name, re.ASCII) is not None
            assert (mine.fullmatch(name) is not None) is expected, (pattern, name)
    assert client.TMP_NAME_RE.fullmatch(f".{REQUEST_ID}.tmp"), "so heisst die Zwischendatei in write_request"
    assert client.REQUESTS_MODE == int(files["requests_mode"], 8) == channel.REQUESTS_MODE
    assert client.REQUEST_MODE == int(files["request_mode"], 8) == channel.REQUEST_MODE
    limits = proto["limits"]
    assert client.REQUEST_MAX_BYTES == limits["request_max_bytes"] == policy.REQUEST_MAX_BYTES
    assert client.REQUEST_MAX_AGE_S == limits["request_max_age_s"] == policy.REQUEST_MAX_AGE_S
    assert client.STATUS_MAX_BYTES == limits["status_max_bytes"] == policy.STATUS_MAX_BYTES
    assert client.RESULTS_MAX == limits["results_max"] == policy.RESULTS_MAX
    assert client.UUID4_RE.pattern == policy.UUID4_RE.pattern
    assert client.VERSION_RE.pattern == policy.VERSION_RE.pattern
    assert proto["repository"] == policy.REPOSITORY == updates.OFFICIAL_IMAGE


def test_version_vectors_agree():
    data = vectors("versions")
    policy = helper_module("policy")
    for text in data["valid"]:
        assert client.is_version(text) and policy.is_version(text), text
    for text in data["invalid"] + data["not_a_string"]:
        assert not client.is_version(text) and not policy.is_version(text), text
    for candidate, current, newer in data["newer"]:
        assert policy.is_newer(candidate, current) is newer
        if client.is_version(candidate) and client.is_version(current):
            assert updates.is_newer(candidate, current) is newer, (candidate, current)
    for tag in ("latest", "0.7", "0.8", "1.0", "beta"):
        for version in ("0.7.0", "0.7.12", "0.8.0", "1.0.0"):
            assert client.tag_fits_version(tag, version) is policy.tag_fits_version(tag, version), (tag, version)


def test_status_vectors_valid_are_read_the_same():
    data = vectors("status")
    validate = helper_module("channel").validate_status
    for case in data["valid"]:
        validate(case["doc"])
        doc = client.clean_status(case["doc"])
        for key in ("state", "ready", "reason", "target", "busy", "previous", "results", "heartbeat_at"):
            assert doc[key] == case["doc"][key], (case["name"], key)
        assert doc["helper_version"] == case["doc"]["helper_version"]


STATUS_TOLERATED = {
    # Was der Helfer nie schreibt, das Dashboard aber annimmt: unbekannte Felder fallen weg, unbekannte Codes, die
    # wie Codes aussehen, und Schritte gehen durch (ein neuerer Helfer darf neue dazunehmen), eine kaputte Nebenangabe
    # wird `None`, ein kaputtes Ergebnis faellt einzeln weg.
    "Zusatzfeld", "unbekannter Code", "seq negativ", "helper_version kaputt", "busy Schritt unbekannt",
    "Ergebnis outcome unbekannt", "Ergebnis mit Freitext", "Ergebnis Zusatzfeld",
}
STATUS_REJECTED = {
    "Freitext als Grund", "proto 2", "proto true", "heartbeat_at Kommazahl", "state unbekannt", "ready als Zahl",
    "request_versions true", "target fehlt Feld", "target Tag fremd", "busy id gross", "previous Version kaputt",
    "elf Ergebnisse", "Liste statt Objekt",
}


def test_status_vectors_invalid_are_rejected_or_harmless():
    data = vectors("status")
    names = {case["name"] for case in data["invalid"]}
    assert names == STATUS_TOLERATED | STATUS_REJECTED, "neuer Vektor: hier bewusst einordnen"
    for case in data["invalid"]:
        if case["name"] in STATUS_REJECTED:
            with pytest.raises(client._Invalid):
                client.clean_status(case["doc"])
            continue
        doc = client.clean_status(case["doc"])
        text = json.dumps(doc)
        for needle in ("message", "Fehler", "pull failed", '"log"', '"..."', '"ok"'):
            assert needle not in text, (case["name"], needle)
        assert doc["seq"] is None or doc["seq"] >= 0
        assert doc["helper_version"] is None or client.is_version(doc["helper_version"])


def test_a_request_from_the_dashboard_is_accepted_by_the_real_helper_channel(channel):
    helper_channel = helper_module("channel")
    policy = helper_module("policy")
    now = time.time()
    written = client.write_request(channel, action="rollback", version="0.7.0", now=now)
    with helper_channel.Channel(channel, expected_uid=os.getuid()) as reader:
        reader.setup()
        polled = reader.poll(now=now)
    assert [item.id for item in polled.items] == [written["id"]]
    request = policy.parse_request(polled.items[0].raw, now=now, file_id=written["id"])
    assert (request.action, request.version, request.created_at) == ("rollback", "0.7.0", int(now))
    assert os.listdir(channel / "requests") == [], "der Helfer hat sie abgeholt"


def test_the_status_written_by_the_real_helper_channel_is_read_back(channel):
    helper_channel = helper_module("channel")
    doc = status_doc(results=[result()], previous={"version": "0.7.0", "until": int(time.time()) + 600})
    with helper_channel.Channel(channel, expected_uid=os.getuid()) as writer:
        writer.setup()
        writer.write_status(doc)
    status = client.read_status(channel)
    assert status.present and status.doc["results"] == doc["results"]
    assert stat.S_IMODE(os.stat(channel / "status.json").st_mode) == 0o644


# ---------------------------------------------------------------------------
# Gegenpruefung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["heartbeat_at", "previous", "busy", "results"])
def test_gigantic_numbers_make_the_status_invalid_instead_of_crashing(channel, field):
    huge = 10 ** 400  # passt in 16 KiB, ist aber als Zeit unbrauchbar (Ueberlauf beim Rechnen mit Gleitkommazahlen)
    changes = {
        "heartbeat_at": {"heartbeat_at": huge},
        "previous": {"previous": {"version": "0.6.9", "until": huge}},
        "busy": {"state": "busy", "ready": False, "target": None,
                 "busy": {"id": REQUEST_ID, "action": "update", "step": "started", "since": huge}},
        "results": {"results": [result(finished_at=huge)]},
    }[field]
    write_status(channel, status_doc(**changes))
    status = client.read_status(channel)
    if field == "results":
        assert status.present and status.doc["results"] == [], "nur der Eintrag faellt weg"
    else:
        assert status == client.HelperStatus(False, "invalid", None)
