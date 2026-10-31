"""Ed25519-Prüfung und vertraute Schlüssel des Helfers."""

from __future__ import annotations

import ast
import inspect
import os

import pytest
from nodvard_deck_updater import ed25519, trust
from test_code_guard import MODULES, _bindings
from updater_support import OTHER_SEED, SEED, public_key, sign

# RFC 8032, Testvektoren für Ed25519: (öffentlicher Schlüssel, Nachricht, Signatur).
RFC_VECTORS = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        (
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        (
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
    ),
    (
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        (
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
    ),
]
L = 2**252 + 27742317777372353535851937790883648493
P = 2**255 - 19

# Punkte kleiner Ordnung (1, 2, 4 und 8), auch mit gesetztem Vorzeichenbit.
SMALL_ORDER = [
    "0100000000000000000000000000000000000000000000000000000000000000",
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000080",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
]


def _vector(index: int) -> tuple[bytes, bytes, bytes]:
    public, message, signature = RFC_VECTORS[index]
    return bytes.fromhex(public), bytes.fromhex(message), bytes.fromhex(signature)


@pytest.mark.parametrize("index", range(len(RFC_VECTORS)))
def test_the_rfc_vectors_verify(index):
    public, message, signature = _vector(index)
    assert ed25519.valid_public_key(public)
    assert ed25519.verify(public, message, signature)
    assert not ed25519.verify(public, message + b"\0", signature)


def test_the_signer_of_the_tests_matches_the_rfc():
    for seed, index in ((SEED, 0), (OTHER_SEED, 1)):
        public, message, signature = _vector(index)
        assert public_key(seed) == public and sign(seed, message) == signature


def test_every_flipped_bit_is_refused():
    public, message, signature = _vector(2)
    for bit in range(len(signature) * 8):
        flipped = bytearray(signature)
        flipped[bit // 8] ^= 1 << (bit % 8)
        assert not ed25519.verify(public, message, bytes(flipped)), bit
    for bit in range(len(public) * 8):
        flipped = bytearray(public)
        flipped[bit // 8] ^= 1 << (bit % 8)
        assert not ed25519.verify(bytes(flipped), message, signature), bit
    for bit in range(len(message) * 8):
        flipped = bytearray(message)
        flipped[bit // 8] ^= 1 << (bit % 8)
        assert not ed25519.verify(public, bytes(flipped), signature), bit


def test_s_must_be_below_the_group_order():
    public, message, signature = _vector(1)
    s = int.from_bytes(signature[32:], "little")
    # Dieselbe Gleichung mit s + L: rechnerisch gleich, aber eine zweite Schreibweise derselben Signatur.
    for other in (s + L, L, L + 1, 2**256 - 1):
        if other < 2**256:
            assert not ed25519.verify(public, message, signature[:32] + other.to_bytes(32, "little")), other


@pytest.mark.parametrize("encoded", SMALL_ORDER)
def test_a_public_key_of_small_order_is_refused(encoded):
    public = bytes.fromhex(encoded)
    assert ed25519._decode(public) is not None, "ein Punkt der Kurve"
    assert not ed25519.valid_public_key(public)
    assert not ed25519.verify(public, b"", bytes(64))


def test_a_public_key_must_be_canonical():
    # y = p kodiert y = 0, y = p + 1 kodiert y = 1: gleiche Punkte, zweite Schreibweise.
    for y in (P, P + 1, 2**255 - 1):
        assert not ed25519.valid_public_key(y.to_bytes(32, "little")), y
    public = bytes.fromhex(RFC_VECTORS[0][0])
    # x = 0 mit gesetztem Vorzeichenbit ist keine gültige Kodierung.
    assert not ed25519.valid_public_key((1 | 1 << 255).to_bytes(32, "little"))
    assert ed25519.valid_public_key(public)


@pytest.mark.parametrize("args", [
    (bytes(31), b"", bytes(64)), (bytes(33), b"", bytes(64)), (b"\x01" * 32, b"", bytes(63)),
    (b"\x01" * 32, b"", bytes(65)), ("d75a", b"", bytes(64)), (bytearray(32), b"", bytes(64)),
    (bytes(32), "text", bytes(64)), (bytes(32), b"", "x" * 64), (None, b"", bytes(64)),
])
def test_wrong_types_and_lengths_are_refused(args):
    assert ed25519.verify(*args) is False


def test_the_same_answer_as_cryptography():
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    exceptions = pytest.importorskip("cryptography.exceptions")
    for round_ in range(150):
        key = ed.Ed25519PrivateKey.generate()
        public_key = key.public_key()
        public = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        message = os.urandom(round_ % 70)
        signature = key.sign(message)
        assert ed25519.verify(public, message, signature)
        tampered = bytearray(signature)
        tampered[round_ % 64] ^= 1 << (round_ % 8)
        try:
            public_key.verify(bytes(tampered), message)
            theirs = True
        except exceptions.InvalidSignature:
            theirs = False
        assert ed25519.verify(public, message, bytes(tampered)) is theirs is False


def test_the_key_id_is_the_start_of_the_sha256():
    public = bytes.fromhex(RFC_VECTORS[0][0])
    key = trust.TrustedKey("current", public)
    assert key.key_id == trust.key_id(public) and len(key.key_id) == 16
    assert trust.find(key.key_id, (key,)) is key
    assert trust.find(key.key_id, ()) is None
    assert trust.find("0" * 16, (key,)) is None


def test_trusted_keys_are_valid_and_distinct():
    roles = [key.role for key in trust.TRUSTED_KEYS]
    assert all(role in trust.ROLES for role in roles) and len(set(roles)) == len(roles)
    assert all(ed25519.valid_public_key(key.public) for key in trust.TRUSTED_KEYS)
    assert len({key.key_id for key in trust.TRUSTED_KEYS}) == len(trust.TRUSTED_KEYS)


def test_the_project_keys_are_trusted():
    assert {key.key_id: key.role for key in trust.TRUSTED_KEYS} == {
        "916fff4eb943eb5d": "current", "939c49421d468701": "next",
    }


def test_trusted_keys_are_set_once_in_trust_and_never_rebound():
    bindings = []
    stores = []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bindings += [(path.name, getattr(node, "lineno", 0)) for node in _bindings(tree, "TRUSTED_KEYS")]
        stores += [
            (path.name, node.lineno) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "TRUSTED_KEYS"
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ]
    assert [name for name, _ in bindings] == ["trust.py"] and stores == []


def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node


KEY_TAKERS = {"parse_request", "read_request", "check_grant", "check_signature", "find", "Helper"}


def test_only_the_selftest_passes_other_keys():
    passed, splats = set(), []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in _functions(tree):
            passed |= {
                (path.name, getattr(func, "name", "lambda"), ast.unparse(node.value)) for node in ast.walk(func)
                if isinstance(node, ast.keyword) and node.arg in {"keys", "trusted_keys"}
            }
        splats += [
            (path.name, node.lineno) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in KEY_TAKERS
            and any(kw.arg is None for kw in node.keywords)
        ]
    assert splats == []
    assert passed == {
        ("__main__.py", "_handle", "self._trusted_keys"),
        ("__main__.py", "_check_signature", "self._trusted_keys"),
        ("__main__.py", "_selftest_signed_request", "keys"),
        ("__main__.py", "_selftest_signed_request", "trusted"),
        ("policy.py", "parse_request", "keys"),
        ("policy.py", "read_request", "keys"),
    }


def test_keys_come_from_trust_or_the_selftest_and_are_looked_up_in_one_place():
    built, finds, stores = [], [], []
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in _functions(tree):
            finds += [
                (path.name, func.name, ast.unparse(node)) for node in ast.walk(func)
                if isinstance(node, ast.Call) and ast.unparse(node.func) in {"trust.find", "find"}
            ]
        built += [
            (path.name, node.lineno) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == "TrustedKey"
        ]
        stores += [
            (path.name, node.lineno) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "_trusted_keys" and isinstance(node.ctx, ast.Store)
        ]
    selftest_key = next(node for node in ast.parse((MODULES[0].parent / "__main__.py").read_text()).body
                        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "_SELFTEST_KEY")
    project = next(node for node in ast.parse((MODULES[0].parent / "trust.py").read_text()).body
                   if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == "TRUSTED_KEYS")
    assert sorted(built) == sorted([("__main__.py", selftest_key.lineno),
                                    *(("trust.py", key.lineno) for key in project.value.elts)])
    assert sorted(finds) == [("policy.py", "check_grant", "trust.find(grant.key_id, keys)"),
                             ("policy.py", "check_signature", "trust.find(grant.key_id, keys)")]
    assert len(stores) == 1 and stores[0][0] == "__main__.py"


def test_the_defaults_are_the_trusted_keys():
    from nodvard_deck_updater import __main__ as main
    from nodvard_deck_updater import policy

    for func, name in ((policy.parse_request, "keys"), (policy.read_request, "keys"), (trust.find, "keys"),
                       (main.Helper.__init__, "trusted_keys")):
        assert inspect.signature(func).parameters[name].default is trust.TRUSTED_KEYS, func.__name__


def test_no_publicly_known_test_key_is_trusted():
    from nodvard_deck_updater import __main__ as main

    known = {public_key(SEED), public_key(OTHER_SEED), main._SELFTEST_KEY.public,
             *(bytes.fromhex(public) for public, _, _ in RFC_VECTORS)}
    assert not known & {key.public for key in trust.TRUSTED_KEYS}
