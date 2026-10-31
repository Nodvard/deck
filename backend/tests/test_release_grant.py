"""scripts/release_grant.py: Schlüssel anlegen und eine Version für den Update-Helfer freigeben."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519 as crypto_ed25519
from nodvard_deck.core import updates

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = 1793200000
DIGEST = "sha256:" + "4f" * 32
PASSWORD = b"richtig-langes-passwort"
INDEX = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
INDEX_DIGEST = "sha256:" + hashlib.sha256(INDEX).hexdigest()


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in [n for n in sys.modules if n == "nodvard_deck_updater" or n.startswith("nodvard_deck_updater.")]:
        monkeypatch.delitem(sys.modules, name)
    spec = importlib.util.spec_from_file_location("nodvard_release_grant", REPO_ROOT / "scripts" / "release_grant.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in [n for n in sys.modules if n == "nodvard_deck_updater" or n.startswith("nodvard_deck_updater.")]:
        del sys.modules[name]


@pytest.fixture
def key():
    return crypto_ed25519.Ed25519PrivateKey.generate()


def _trusted(tool, key):
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return (tool.trust.TrustedKey("current", public),)


def test_a_signed_grant_passes_the_helper_and_the_dashboard(tool, key, tmp_path):
    data, sig = tool.sign("v0.12.0", key, digest=DIGEST, now=NOW, keys=_trusted(tool, key))
    grant = json.loads(data)
    assert data == tool.policy.dumps(grant), "genau die Bytes, die der Helfer nachrechnet"
    assert grant == {"fmt": 1, "repo": "ghcr.io/nodvard/deck", "version": "0.12.0", "digest": DIGEST,
                     "key_id": _trusted(tool, key)[0].key_id, "issued_at": NOW, "expires_at": NOW + 365 * 86400}
    grant_path, sig_path = tool.write_files(tmp_path, data, sig)
    found = updates.parse_grant(grant_path.read_bytes(), sig_path.read_bytes(), version="0.12.0", digest=DIGEST)
    assert found == {"grant": grant, "sig": sig}
    raw = {"v": 2, "id": "0f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60", "action": "update", "version": "0.12.0",
           "created_at": NOW + 3600, "digest": DIGEST, "grant": found["grant"], "sig": found["sig"]}
    request = tool.policy.parse_request(tool.policy.dumps(raw), now=NOW + 3600, keys=_trusted(tool, key))
    assert request.grant.digest == DIGEST


def test_a_key_that_is_not_in_trust_signs_nothing(tool, key):
    with pytest.raises(tool.GrantError, match="steht nicht in deploy/updater"):
        tool.sign("v0.12.0", key, digest=DIGEST, now=NOW)
    other = crypto_ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(tool.GrantError, match="steht nicht"):
        tool.sign("v0.12.0", key, digest=DIGEST, now=NOW, keys=_trusted(tool, other))


@pytest.mark.parametrize("tag", ["0.12.0", "v0.12.0-rc1", "v0.12", "v0.12.0\n", "v1.0.0+build"])
def test_only_tags_of_final_versions_get_a_grant(tool, key, tag):
    with pytest.raises(tool.GrantError, match="kein Tag einer fertigen Version"):
        tool.sign(tag, key, digest=DIGEST, now=NOW, keys=_trusted(tool, key))


def test_the_helper_check_stops_a_bad_digest(tool, key):
    with pytest.raises(tool.GrantError, match="bad_request"):
        tool.sign("v0.12.0", key, digest="sha256:kaputt", now=NOW, keys=_trusted(tool, key))


def _fake_registry(monkeypatch, tool, manifest: bytes, header: str | None):
    asked = []

    class Answer:
        def __init__(self, body: bytes, headers: dict[str, str]):
            self.body, self.headers = body, headers

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, limit):
            return self.body[:limit]

    def urlopen(request, timeout):
        asked.append((request.full_url, dict(request.header_items())))
        if request.full_url.startswith("https://ghcr.io/token?"):
            return Answer(b'{"token": "anon"}', {})
        return Answer(manifest, {} if header is None else {"Docker-Content-Digest": header})

    monkeypatch.setattr(tool.urllib.request, "urlopen", urlopen)
    return asked


def test_the_digest_comes_from_ghcr_like_in_the_update_check(tool, monkeypatch):
    manifest = b'{"schemaVersion":2}'
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    asked = _fake_registry(monkeypatch, tool, manifest, digest)
    assert tool.registry_digest("0.12.0") == digest
    assert [url for url, _ in asked] == [
        "https://ghcr.io/token?scope=repository%3Anodvard%2Fdeck%3Apull&service=ghcr.io",
        "https://ghcr.io/v2/nodvard/deck/manifests/0.12.0",
    ]
    assert asked[1][1]["Authorization"] == "Bearer anon" and "image.index" in asked[1][1]["Accept"]


def test_a_digest_header_that_does_not_match_the_manifest_stops(tool, monkeypatch):
    _fake_registry(monkeypatch, tool, b'{"schemaVersion":2}', "sha256:" + "5e" * 32)
    with pytest.raises(tool.GrantError, match="nennt sha256:5e"):
        tool.registry_digest("0.12.0")


def test_keys_are_made_once_encrypted_and_only_for_the_owner(tool, tmp_path):
    lines = tool.make_keys(tmp_path, PASSWORD)
    files = sorted(tmp_path.iterdir())
    assert [f.name for f in files] == ["release-ed25519-current.pem", "release-ed25519-next.pem"]
    for path in files:
        if sys.platform != "win32":  # Windows kennt keine Unix-Rechte.
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b"ENCRYPTED" in path.read_bytes()
        with pytest.raises(tool.GrantError, match="falsches Passwort"):
            tool.load_private_key(path, b"falsch-falsch-falsch")
    keys = [tool.load_private_key(path, PASSWORD) for path in files]
    publics = {tool._raw_public(k).hex() for k in keys}
    assert len(lines) == 2 and all(any(p in line for p in publics) for line in lines)
    assert lines[0].startswith('    TrustedKey("current", bytes.fromhex("')
    with pytest.raises(tool.GrantError, match="nichts überschrieben"):
        tool.make_keys(tmp_path, PASSWORD)
    with pytest.raises(tool.GrantError, match="mindestens 12"):
        tool.make_keys(tmp_path / "neu", b"kurz")


def test_upload_hangs_both_files_on_the_draft(tool, monkeypatch, tmp_path):
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr(tool.subprocess, "run", lambda command, check: calls.append(command) or Done())
    files = (tmp_path / tool.GRANT_NAME, tmp_path / tool.SIG_NAME)
    tool.upload("v0.12.0", files)
    assert calls == [["gh", "release", "upload", "v0.12.0", str(files[0]), str(files[1]), "--clobber"]]


def test_trust_never_holds_a_private_key():
    source = (REPO_ROOT / "deploy" / "updater" / "nodvard_deck_updater" / "trust.py").read_text(encoding="utf-8")
    assert "TRUSTED_KEYS" in source and "-----BEGIN" not in source, "nie ein privater Schlüssel im Repository"


@pytest.mark.parametrize(("value", "trust", "message"), [
    (None, False, "--expect-digest sha256:… angeben"),
    (DIGEST, True, "schließen sich aus"),
    ("sha256:" + "4F" * 32, False, "kein Digest"),
    ("sha256:" + "4f" * 31, False, "kein Digest"),
    ("ghcr.io/nodvard/deck:0.12.0@" + DIGEST, False, "kein Digest"),
    (DIGEST + "\n", False, "kein Digest"),
])
def test_the_expected_digest_is_required_and_strict(tool, value, trust, message):
    with pytest.raises(tool.GrantError, match=message):
        tool.expected_digest(value, trust)


def _sign_cli(tool, monkeypatch, tmp_path, *extra):
    asked = _fake_registry(monkeypatch, tool, INDEX, INDEX_DIGEST)
    tool.make_keys(tmp_path / "keys", PASSWORD)
    pem = tmp_path / "keys" / "release-ed25519-current.pem"
    trusted = _trusted(tool, tool.load_private_key(pem, PASSWORD))
    real_sign = tool.sign
    monkeypatch.setattr(tool, "sign", lambda tag, key, **kw: real_sign(tag, key, keys=trusted, **kw))
    prompts = []
    monkeypatch.setattr(tool, "_password", lambda prompt, **kw: prompts.append(prompt) or PASSWORD)
    out = tmp_path / "out"
    code = tool.main(["sign", "v0.12.0", "--key", str(pem), "--out", str(out), *extra])
    return code, asked, prompts, out


def test_the_grant_is_signed_when_ghcr_matches_the_expected_digest(tool, monkeypatch, tmp_path, capsys):
    code, asked, prompts, out = _sign_cli(tool, monkeypatch, tmp_path, "--expect-digest", INDEX_DIGEST)
    assert code == 0, capsys.readouterr().err
    assert json.loads((out / tool.GRANT_NAME).read_bytes())["digest"] == INDEX_DIGEST
    assert len(asked) == 2 and len(prompts) == 1


def test_a_different_digest_on_ghcr_signs_nothing(tool, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **kw: pytest.fail("nichts hochladen"))
    code, asked, prompts, out = _sign_cli(tool, monkeypatch, tmp_path, "--expect-digest", DIGEST, "--upload")
    err = capsys.readouterr().err
    assert code == 1 and len(asked) == 2
    assert f"liefert für 0.12.0 {INDEX_DIGEST}" in err and f"erwartet war {DIGEST}" in err
    assert not out.exists(), "keine Freigabe geschrieben"
    assert prompts == [], "nach dem Passwort wird gar nicht erst gefragt"


@pytest.mark.parametrize(("extra", "message"), [
    ((), "--expect-digest sha256:… angeben"),
    (("--expect-digest", "sha256:ABC"), "kein Digest"),
    (("--expect-digest", INDEX_DIGEST, "--trust-registry"), "schließen sich aus"),
])
def test_without_a_valid_expected_digest_nothing_is_asked(tool, monkeypatch, tmp_path, capsys, extra, message):
    code, asked, prompts, out = _sign_cli(tool, monkeypatch, tmp_path, *extra)
    assert code == 1 and message in capsys.readouterr().err
    assert asked == [] and prompts == [] and not out.exists()


def test_no_answer_from_ghcr_signs_nothing(tool, monkeypatch, tmp_path, capsys):
    def unreachable(request, timeout):
        raise OSError("Netz weg")

    tool.make_keys(tmp_path / "keys", PASSWORD)
    monkeypatch.setattr(tool.urllib.request, "urlopen", unreachable)
    monkeypatch.setattr(tool, "_password", lambda prompt, **kw: pytest.fail("kein Passwort ohne Digest"))
    code = tool.main(["sign", "v0.12.0", "--key", str(tmp_path / "keys" / "release-ed25519-current.pem"),
                      "--out", str(tmp_path / "out"), "--expect-digest", INDEX_DIGEST])
    assert code == 1 and "antwortet nicht" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_trust_registry_signs_what_ghcr_delivers_with_a_warning(tool, monkeypatch, tmp_path, capsys):
    code, _, _, out = _sign_cli(tool, monkeypatch, tmp_path, "--trust-registry")
    assert code == 0
    assert f"Ohne Vergleich: ghcr.io liefert für 0.12.0 {INDEX_DIGEST}" in capsys.readouterr().err
    assert json.loads((out / tool.GRANT_NAME).read_bytes())["digest"] == INDEX_DIGEST
