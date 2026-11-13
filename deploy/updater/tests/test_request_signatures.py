"""Anforderungen und Freigaben vor dem Zugriff auf die Engine prüfen."""

import json
import uuid

import pytest
from nodvard_deck_updater import policy
from test_main import Setup, saved_state
from updater_support import NOW, SEED, fake_owner, public_key


@pytest.fixture
def env(tmp_path, uid):
    setup = Setup(tmp_path, uid)
    setup.lab.release("0.7.1")
    setup.start()
    try:
        yield setup
    finally:
        assert setup.world.violations == []
        setup.close()


def change_request(env, rid, change):
    path = env.root / "requests" / f"{rid}.json"
    doc = json.loads(path.read_bytes())
    change(doc)
    path.write_bytes(policy.dumps(doc))


@pytest.mark.parametrize("field,value", [
    ("request_sig", "0" * 128), ("request_key_id", "0" * 16), ("created_at", NOW + 1),
])
def test_forged_request_changes_nothing(env, field, value):
    rid = env.request()
    change_request(env, rid, lambda doc: doc.update({field: value}))
    env.tick(NOW + 5)
    code = policy.UNKNOWN_KEY if field == "request_key_id" else policy.BAD_SIGNATURE
    assert env.outcome(rid) == ("refused", code)
    assert env.world.mutations == [] and saved_state(env)["actions"] == []


@pytest.mark.parametrize("field", ["request_sig", "request_key_id", "sig", "grant"])
def test_missing_signature_or_grant_changes_nothing(env, field):
    rid = env.request()
    change_request(env, rid, lambda doc: doc.pop(field))
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("refused", policy.BAD_REQUEST)
    assert env.world.mutations == []


def test_a_release_signature_alone_is_not_an_authorized_request(env):
    rid = env.request()

    def downgrade(doc):
        doc["v"] = 2
        del doc["request_key_id"], doc["request_sig"]

    change_request(env, rid, downgrade)
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("refused", policy.SIGNATURE_REQUIRED)
    assert env.world.mutations == []


def test_an_old_signed_request_changes_nothing(env):
    rid = env.request(created_at=NOW - policy.REQUEST_MAX_AGE_S - 1)
    env.tick(NOW)
    assert env.outcome(rid) == ("refused", policy.EXPIRED)
    assert env.world.mutations == []


def test_a_signed_request_cannot_be_given_a_new_id(env):
    rid = env.request()
    path = env.root / "requests" / f"{rid}.json"
    doc = json.loads(path.read_bytes())
    path.unlink()
    doc["id"] = str(uuid.uuid4())
    env.request(rid=doc["id"], raw=policy.dumps(doc))
    env.tick(NOW + 5)
    assert env.outcome(doc["id"]) == ("refused", policy.BAD_SIGNATURE)
    assert env.world.mutations == []


@pytest.mark.parametrize("content", [None, "", "0" * 64, public_key(SEED).hex()])
def test_an_absent_or_wrong_installation_key_changes_nothing(env, content):
    path = env.state_dir / "request-key.pub"
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="ascii")
    rid = env.request()
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("refused", policy.UNKNOWN_KEY)
    assert env.world.mutations == []


def test_a_changed_key_rejects_requests_signed_with_the_old_key(env):
    rid = env.request()
    (env.state_dir / "request-key.pub").write_text(public_key(SEED).hex(), encoding="ascii")
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("refused", policy.UNKNOWN_KEY)
    assert env.world.mutations == []
    fresh = env.signed_request(digest=env.world.tags["latest"], request_seed=SEED)
    env.next_round()
    assert env.outcome(fresh) == ("applied", None)


def test_the_selftest_checks_the_request_signature(monkeypatch):
    from nodvard_deck_updater import __main__ as main

    assert main._selftest_signed_request()

    def refuse(*args):
        raise policy.Refusal(policy.BAD_SIGNATURE)

    monkeypatch.setattr(policy, "check_request_signature", refuse)
    assert main.selftest() == 1


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "mode", "owner"])
def test_an_unsafe_public_key_changes_nothing(env, kind, monkeypatch):
    import os

    path = env.state_dir / "request-key.pub"
    if kind in ("symlink", "hardlink", "fifo"):
        original = env.state_dir / "original.pub"
        path.rename(original)
        if kind == "symlink":
            path.symlink_to(original)
        elif kind == "hardlink":
            os.link(original, path)
        else:
            os.mkfifo(path)
    elif kind == "mode":
        path.chmod(0o666)
    else:
        fake_owner(monkeypatch, {path: env.uid + 1})
    rid = env.request()
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("refused", policy.UNKNOWN_KEY)
    assert env.world.mutations == []


@pytest.mark.parametrize("offset,code", [(-600, None), (-601, policy.EXPIRED), (60, None), (61, policy.BAD_REQUEST)])
def test_signed_requests_keep_the_existing_time_limits(env, offset, code):
    rid = env.request(created_at=NOW + offset)
    env.tick(NOW)
    assert env.outcome(rid) == (("applied", None) if code is None else ("refused", code))
    if code is not None:
        assert env.world.mutations == []


# Rückweg: dieselbe Signatur mit dem Installationsschlüssel, nur ohne Freigabe.


def rollback_ready(env) -> int:
    """Das Update auf 0.7.1 ist eingespielt, der Rückweg-Slot steht, die Grenze lässt die nächste Aktion zu."""
    update = env.request()
    env.tick(NOW + 5)
    assert env.outcome(update) == ("applied", None)
    env.clock.t = saved_state(env)["actions"][-1] + policy.ACTION_INTERVAL_S
    return int(env.clock.t)


def assert_still_on_the_update(env, changes):
    assert len(env.world.mutations) == changes
    assert env.status()["previous"]["version"] == "0.7.0"
    assert saved_state(env)["blocked"] == {}


def test_a_signed_rollback_goes_back(env):
    now = rollback_ready(env)
    rid = env.signed_rollback("0.7.0")
    env.tick(now)
    assert env.outcome(rid) == ("reverted", None)
    assert env.lab.only_deck()["Image"] == env.lab.old_image


@pytest.mark.parametrize("field,value,code", [
    ("request_sig", "0" * 128, policy.BAD_SIGNATURE),
    ("request_key_id", "0" * 16, policy.UNKNOWN_KEY),
    ("created_at", 1, policy.BAD_SIGNATURE),
    ("id", None, policy.BAD_SIGNATURE),
])
def test_a_forged_rollback_changes_nothing(env, field, value, code):
    now = rollback_ready(env)
    changes = len(env.world.mutations)
    rid = env.signed_rollback("0.7.0")
    if field == "id":
        path = env.root / "requests" / f"{rid}.json"
        doc = json.loads(path.read_bytes())
        path.unlink()
        rid = doc["id"] = str(uuid.uuid4())
        env.request(rid=rid, raw=policy.dumps(doc))
    else:
        change_request(env, rid, lambda doc: doc.update({field: doc["created_at"] + value if field == "created_at"
                                                         else value}))
    env.tick(now)
    assert env.outcome(rid) == ("refused", code)
    assert_still_on_the_update(env, changes)


def test_a_rollback_signed_with_another_key_changes_nothing(env):
    now = rollback_ready(env)
    changes = len(env.world.mutations)
    rid = env.signed_rollback("0.7.0", request_seed=SEED)
    env.tick(now)
    assert env.outcome(rid) == ("refused", policy.UNKNOWN_KEY)
    assert_still_on_the_update(env, changes)


@pytest.mark.parametrize("change", ["unsigned", "no_sig", "no_key_id", "with_grant"])
def test_a_rollback_without_its_signature_changes_nothing(env, change):
    now = rollback_ready(env)
    changes = len(env.world.mutations)
    rid = str(uuid.uuid4())
    if change == "unsigned":
        env.request(rid=rid, raw=policy.dumps({"v": 1, "id": rid, "action": "rollback", "version": "0.7.0",
                                               "created_at": now}))
    else:
        env.signed_rollback("0.7.0", rid=rid)
        field = {"no_sig": "request_sig", "no_key_id": "request_key_id"}.get(change)
        change_request(env, rid, (lambda doc: doc.pop(field)) if field else
                       (lambda doc: doc.update(digest="sha256:" + "4f" * 32)))
    env.tick(now)
    code = policy.SIGNATURE_REQUIRED if change == "unsigned" else policy.BAD_REQUEST
    assert env.outcome(rid) == ("refused", code)
    assert_still_on_the_update(env, changes)


def test_a_replayed_rollback_changes_nothing(env):
    now = rollback_ready(env)
    rid = env.signed_rollback("0.7.0")
    raw = (env.root / "requests" / f"{rid}.json").read_bytes()
    env.tick(now)
    first = env.result(rid)
    assert first["outcome"] == "reverted"
    changes = len(env.world.mutations)
    env.clock.t = saved_state(env)["actions"][-1] + policy.ACTION_INTERVAL_S
    env.request(rid=rid, raw=raw)  # dieselbe signierte Datei noch einmal
    env.tick(int(env.clock.t))
    assert env.pending() == []
    assert len(env.world.mutations) == changes
    assert [r for r in env.status()["results"] if r["id"] == rid] == [first]


def test_an_old_signed_rollback_changes_nothing(env):
    now = rollback_ready(env)
    changes = len(env.world.mutations)
    rid = env.signed_rollback("0.7.0", created_at=now - policy.REQUEST_MAX_AGE_S - 1)
    env.tick(now)
    assert env.outcome(rid) == ("refused", policy.EXPIRED)
    assert_still_on_the_update(env, changes)
