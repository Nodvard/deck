"""Reine Funktionen ohne DB -- Passwort-Hashing, opake Token, JWT.

docs/00-DECISIONS.md D-07.
"""

from __future__ import annotations

import time

import pytest

from nodvard_deck.core import security


def test_password_hash_roundtrip():
    h = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", h)
    assert not security.verify_password("falsches passwort", h)


def test_password_hash_is_argon2():
    h = security.hash_password("x")
    assert h.startswith("$argon2id$")


def test_verify_password_never_raises_on_garbage_hash():
    assert security.verify_password("irrelevant", "kein-echter-hash") is False


def test_opaque_token_hash_is_deterministic_and_one_way():
    token = security.generate_opaque_token()
    h1 = security.hash_opaque_token(token)
    h2 = security.hash_opaque_token(token)
    assert h1 == h2
    assert h1 != token
    assert len(h1) == 64  # SHA-256 Hex


def test_opaque_tokens_are_unique():
    tokens = {security.generate_opaque_token() for _ in range(50)}
    assert len(tokens) == 50


def test_jwt_roundtrip():
    token = security.create_jwt(
        subject="user-1", token_type="access", secret="s3cr3t-langer-testschluessel-32-bytes-plus", ttl_seconds=60
    )
    payload = security.decode_jwt(token, secret="s3cr3t-langer-testschluessel-32-bytes-plus", expected_type="access")
    assert payload["sub"] == "user-1"
    assert payload["typ"] == "access"


def test_jwt_wrong_type_is_rejected():
    """Ein MFA-Token darf niemals als Access-Token durchgehen, selbst mit korrektem
    Schluessel -- der `typ`-Claim ist genau dafuer da."""
    mfa_token = security.create_jwt(
        subject="user-1", token_type="mfa", secret="s3cr3t-langer-testschluessel-32-bytes-plus", ttl_seconds=60
    )
    with pytest.raises(security.TokenError):
        security.decode_jwt(mfa_token, secret="s3cr3t-langer-testschluessel-32-bytes-plus", expected_type="access")


def test_jwt_wrong_secret_is_rejected():
    token = security.create_jwt(
        subject="user-1", token_type="access", secret="richtiger-schluessel-lang-genug-32bytes", ttl_seconds=60
    )
    with pytest.raises(security.TokenError):
        security.decode_jwt(token, secret="falscher-schluessel-lang-genug-32byte", expected_type="access")


def test_jwt_expired_is_rejected():
    token = security.create_jwt(
        subject="user-1", token_type="access", secret="s3cr3t-langer-testschluessel-32-bytes-plus", ttl_seconds=0
    )
    time.sleep(1.2)
    with pytest.raises(security.TokenError):
        security.decode_jwt(token, secret="s3cr3t-langer-testschluessel-32-bytes-plus", expected_type="access")


def test_jwt_garbage_is_rejected():
    with pytest.raises(security.TokenError):
        security.decode_jwt("not.a.jwt", secret="s3cr3t-langer-testschluessel-32-bytes-plus", expected_type="access")
