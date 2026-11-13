"""Schlüssel und Signaturen der Update-Anforderungen."""

import json
import os

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nodvard_deck.core import updater_client, updater_signing
from nodvard_deck.services import update_helper
from updater_helpers import REQUEST_ID, helper_module, vectors


def test_the_installation_key_is_created_once(tmp_path):
    public = updater_signing.prepare_key(tmp_path)
    assert len(public) == 32
    assert updater_signing.prepare_key(tmp_path) == public
    assert updater_signing.public_key(updater_signing.load_key(tmp_path)) == public
    if os.name == "posix":
        assert (tmp_path / updater_signing.KEY_NAME).stat().st_mode & 0o777 == 0o600


def test_an_interrupted_write_leaves_no_key_behind(tmp_path, monkeypatch):
    def broken(fd, data):
        raise OSError("Platte voll")

    monkeypatch.setattr(updater_signing.os, "write", broken)
    with pytest.raises(OSError):
        updater_signing.prepare_key(tmp_path)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.undo()
    assert len(updater_signing.prepare_key(tmp_path)) == 32
    assert [p.name for p in tmp_path.iterdir()] == [updater_signing.KEY_NAME]


@pytest.mark.skipif(os.name != "posix", reason="Hardlinks unter POSIX")
def test_a_crash_between_link_and_unlink_heals_on_the_next_setup(tmp_path):
    public = updater_signing.prepare_key(tmp_path)
    key = tmp_path / updater_signing.KEY_NAME
    stale = tmp_path / f".{updater_signing.KEY_NAME}.0123456789abcdef.tmp"
    os.link(key, stale)
    with pytest.raises(ValueError):
        updater_signing.load_key(tmp_path)
    assert updater_signing.prepare_key(tmp_path) == public
    assert not stale.exists()


@pytest.mark.skipif(os.name != "posix", reason="Hardlinks unter POSIX")
def test_a_foreign_second_name_is_never_removed(tmp_path):
    updater_signing.prepare_key(tmp_path)
    foreign = tmp_path / "backup-of-key"
    os.link(tmp_path / updater_signing.KEY_NAME, foreign)
    with pytest.raises(ValueError):
        updater_signing.prepare_key(tmp_path)
    assert foreign.exists()


def test_existing_invalid_keys_are_never_replaced(tmp_path):
    path = tmp_path / updater_signing.KEY_NAME
    path.write_bytes(b"broken")
    with pytest.raises(ValueError):
        updater_signing.prepare_key(tmp_path)
    assert path.read_bytes() == b"broken"


@pytest.mark.skipif(os.name != "posix", reason="Dateirechte unter POSIX")
@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666])
def test_a_key_readable_by_other_users_is_refused(tmp_path, mode):
    updater_signing.prepare_key(tmp_path)
    (tmp_path / updater_signing.KEY_NAME).chmod(mode)
    with pytest.raises(ValueError):
        updater_signing.load_key(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="Dateitypen unter POSIX")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_a_key_must_be_one_regular_file(tmp_path, kind):
    original = tmp_path / "original"
    original.write_bytes(bytes(32))
    original.chmod(0o600)
    path = tmp_path / updater_signing.KEY_NAME
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, path)
    else:
        os.mkfifo(path)
    with pytest.raises((OSError, ValueError)):
        updater_signing.load_key(tmp_path)


def signed_request(key):
    data = vectors("requests")
    case = next(case for case in data["cases"] if case["name"] == "signiertes Update")
    expected = case["expect"]
    raw = updater_client.encode_request(request_id=REQUEST_ID, action="update", version=expected["version"],
                                        created_at=expected["created_at"], digest=expected["digest"],
                                        grant={"grant": expected["grant"], "sig": expected["sig"]}, signing_key=key)
    return data, raw


def test_the_helper_verifies_the_request_and_the_release_separately():
    key = Ed25519PrivateKey.generate()
    data, raw = signed_request(key)
    policy, trust = helper_module("policy"), helper_module("trust")
    keys = tuple(trust.TrustedKey(entry["role"], bytes.fromhex(entry["public"])) for entry in data["keys"])
    request = policy.parse_request(raw, now=data["now"], file_id=REQUEST_ID, keys=keys)
    assert request.v == updater_client.AUTHENTICATED_REQUEST_VERSION == 3
    assert policy.check_request_signature(request, updater_signing.public_key(key)) is None
    assert updater_client.REQUEST_DOMAIN == policy.REQUEST_DOMAIN
    with pytest.raises(policy.Refusal, match="unknown_key"):
        policy.check_request_signature(request, updater_signing.public_key(Ed25519PrivateKey.generate()))
    with pytest.raises(policy.Refusal, match="unknown_key"):
        policy.parse_request(raw, now=data["now"], keys=())


def test_the_signature_binds_every_request_field():
    key = Ed25519PrivateKey.generate()
    _data, raw = signed_request(key)
    doc = json.loads(raw)
    signature = bytes.fromhex(doc.pop("request_sig"))
    def canonical(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")

    key.public_key().verify(signature, updater_client.REQUEST_DOMAIN + canonical(doc))
    for field in doc:
        changed = {**doc, field: None}
        with pytest.raises(InvalidSignature):
            key.public_key().verify(signature, updater_client.REQUEST_DOMAIN + canonical(changed))
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, canonical(doc))


def test_the_signer_requires_a_release_grant():
    with pytest.raises(ValueError, match="Freigabe"):
        updater_client.encode_request(request_id=REQUEST_ID, action="update", version="1.0.0", created_at=1,
                                      signing_key=Ed25519PrivateKey.generate())
    with pytest.raises(ValueError, match="Freigabe"):
        updater_client.encode_request(request_id=REQUEST_ID, action="rollback", version="1.0.0", created_at=1,
                                      grant={"grant": {}, "sig": ""}, signing_key=Ed25519PrivateKey.generate())


def test_the_helper_accepts_the_signed_rollback_of_the_dashboard():
    key = Ed25519PrivateKey.generate()
    data = vectors("requests")
    raw = updater_client.encode_request(request_id=REQUEST_ID, action="rollback", version="0.6.0",
                                        created_at=data["now"] - 5, signing_key=key)
    policy = helper_module("policy")
    request = policy.parse_request(raw, now=data["now"], file_id=REQUEST_ID, keys=())
    assert (request.v, request.action, request.grant) == (3, "rollback", None)
    assert policy.require_authenticated(request) is None
    assert policy.check_request_signature(request, updater_signing.public_key(key)) is None
    with pytest.raises(policy.Refusal, match="unknown_key"):
        policy.check_request_signature(request, updater_signing.public_key(Ed25519PrivateKey.generate()))
    forged = policy.parse_request(raw.replace(b'"0.6.0"', b'"0.6.1"'), now=data["now"], keys=())
    with pytest.raises(policy.Refusal, match="bad_signature"):
        policy.check_request_signature(forged, updater_signing.public_key(key))


def test_the_command_prints_only_the_public_key(tmp_path, monkeypatch, capsys):
    from nodvard_deck import config

    settings = config.Settings(data_dir=tmp_path)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    updater_signing.main()
    assert capsys.readouterr().out == updater_signing.public_key(updater_signing.load_key(tmp_path)).hex() + "\n"


def test_a_missing_private_key_writes_no_request(test_settings, monkeypatch):
    monkeypatch.setattr(updater_client, "write_request", lambda *a, **kw: pytest.fail("Anforderung ohne Schlüssel"))
    with pytest.raises(update_helper.HelperRefused) as error:
        update_helper._write(test_settings, action="update", version="1.0.0")
    assert error.value.code == "signature_required"


@pytest.mark.parametrize("action", ["update", "rollback"])
@pytest.mark.parametrize("error", [ValueError("ungültige Anforderung"), OSError("Schreiben fehlgeschlagen")])
def test_request_errors_are_not_key_errors(test_settings, monkeypatch, action, error):
    updater_signing.prepare_key(test_settings.data_dir)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(updater_client, "write_request", fail)
    with pytest.raises(type(error)) as caught:
        update_helper._write(test_settings, action=action, version="1.0.0")
    assert caught.value is error


@pytest.mark.parametrize("action", ["update", "rollback"])
def test_invalid_request_fields_are_not_key_errors(test_settings, monkeypatch, action):
    updater_signing.prepare_key(test_settings.data_dir)
    monkeypatch.setattr(updater_client, "_SUPPORTED", True)
    with pytest.raises(ValueError, match="ungueltige Anforderung"):
        update_helper._write(test_settings, action=action, version="invalid")
