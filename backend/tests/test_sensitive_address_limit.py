from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodvard_deck.core import login_limit
from nodvard_deck.db import utcnow
from nodvard_deck.services import auth


@pytest.mark.parametrize("kind", ["password", "setup", "totp", "recovery"])
@pytest.mark.parametrize("network", ["192.0.2.", "2001:db8:1:2::"])
async def test_sensitive_checks_share_address_limit(db_session, test_settings, monkeypatch, kind, network):
    clock = [1000.0]
    monkeypatch.setattr(login_limit, "_now", lambda: clock[0])
    monkeypatch.setattr(auth.security, "verify_password_async", AsyncMock(return_value=False))
    monkeypatch.setattr(auth.vault, "load_keyring", lambda settings: None)
    monkeypatch.setattr(auth.vault, "read_secret_plaintext", AsyncMock(return_value="unused"))
    monkeypatch.setattr(auth, "match_totp_step", lambda *args: None)
    monkeypatch.setattr(auth, "consume_recovery_code", AsyncMock(return_value=False))

    async def check(index):
        user = SimpleNamespace(
            id=f"account-{index % 4}", password_hash="unused", totp_secret_id="unused",
            totp_confirmed_at=None if kind == "setup" else utcnow(),
        )
        ip = network + (str(index + 1) if ":" in network else "1")
        if kind == "password":
            await auth.require_current_password(db_session, user, "wrong", action="test", ip=ip)
        elif kind == "setup":
            await auth.confirm_totp_setup(db_session, test_settings, user, code="000000", ip=ip)
        else:
            await auth.require_current_totp(
                db_session, test_settings, user, "wrong" if kind == "recovery" else "000000",
                action="test", ip=ip, allow_recovery=kind == "recovery",
            )

    wrong = (auth.WrongPassword, auth.InvalidMfaCode, auth.WrongTotp)
    for index in range(login_limit.MAX_FAILURES_PER_IP):
        with pytest.raises(wrong):
            await check(index)
    with pytest.raises(auth.TooManyAttempts) as caught:
        await check(login_limit.MAX_FAILURES_PER_IP)
    assert caught.value.retry_after == login_limit.WINDOW_SECONDS
    clock[0] += login_limit.WINDOW_SECONDS
    with pytest.raises(wrong):
        await check(0)


def test_success_does_not_clear_other_accounts_address_failures():
    for index in range(login_limit.MAX_FAILURES_PER_IP - 1):
        attempt = login_limit.begin(f"konto:{index}", "confirm", address="2001:db8::1")
        login_limit.failed(attempt)
    accepted = login_limit.begin("konto:accepted", "confirm", address="2001:db8::2")
    login_limit.succeeded(accepted)
    failed = login_limit.begin("konto:failed", "confirm", address="2001:db8::3")
    login_limit.failed(failed)
    with pytest.raises(login_limit.Locked):
        login_limit.begin("konto:next", "confirm", address="2001:db8::4")
    login_limit.begin("konto:next", "confirm", address="2001:db8:0:1::1")
