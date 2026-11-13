"""Passwortgrenze über alle Adressen."""

import pytest
from nodvard_deck.core import login_limit


@pytest.fixture(autouse=True)
def reset_limit(monkeypatch):
    login_limit.reset()
    now = [1000.0]
    monkeypatch.setattr(login_limit, "_now", lambda: now[0])
    yield now
    login_limit.reset()


@pytest.mark.parametrize("network", ["2001:db8:50:{:x}::1", "192.0.2.{}"])
def test_password_limit_blocks_new_networks(network, reset_limit):
    for i in range(30):
        ip = network.format(i + 1)
        attempt = login_limit.begin(ip, "demo", brake_name=login_limit.is_foreign(ip), password_login=True)
        scopes = login_limit.failed(attempt)
        reset_limit[0] += 1
    for ip in ("2001:db8:51::1", "198.51.100.9"):
        with pytest.raises(login_limit.Locked) as err:
            login_limit.begin(ip, "demo", brake_name=login_limit.is_foreign(ip), password_login=True)
        assert err.value.retry_after == 270
    assert scopes == ["password"]
    # Heimnetz, VPN und das Gerät selbst sperrt die Grenze nicht.
    for ip in ("127.0.0.1", "10.42.0.99", "100.64.0.9", "fd00::9", "::ffff:192.168.1.9", None):
        assert not login_limit.begin(ip, "demo", password_login=True).password_login
    reset_limit[0] = 1300.0
    login_limit.begin("2001:db8:51::1", "demo", brake_name=True, password_login=True)


def test_password_limit_counts_pending_attempts():
    attempts = [
        login_limit.begin(f"2001:db8:45:{i:x}::1", "demo", password_login=True)
        for i in range(30)
    ]
    with pytest.raises(login_limit.Locked) as err:
        login_limit.begin("192.0.2.1", "demo", password_login=True)
    assert err.value.retry_after == 300
    login_limit.release(attempts[-1])
    last = login_limit.begin("192.0.2.1", "demo", password_login=True)
    assert login_limit.failed(last) == ["password"]
    assert login_limit.failed(attempts[0]) == []


@pytest.mark.parametrize("complete", [login_limit.release, login_limit.succeeded])
def test_password_limit_only_takes_back_the_current_attempt(complete, reset_limit):
    for i in range(29):
        login_limit.failed(login_limit.begin(f"2001:db8:47:{i:x}::1", "demo", password_login=True))
        reset_limit[0] += 1
    for _ in range(3):
        complete(login_limit.begin("192.0.2.1", "demo", password_login=True))
    login_limit.failed(login_limit.begin("192.0.2.1", "demo", password_login=True))
    with pytest.raises(login_limit.Locked):
        login_limit.begin("198.51.100.1", "demo", password_login=True)
    reset_limit[0] = 1300.0
    login_limit.begin("198.51.100.1", "demo", password_login=True)


def test_password_limit_does_not_count_other_auth_steps():
    for i in range(30):
        login_limit.failed(login_limit.begin(f"2001:db8:48:{i:x}::1", "demo"))
    for i in range(30):
        login_limit.failed(login_limit.begin(f"2001:db8:49:{i:x}::1", "demo", password_login=True))
    login_limit.begin("192.0.2.1", "demo")
    with pytest.raises(login_limit.Locked):
        login_limit.begin("198.51.100.1", "demo", password_login=True)


def test_password_limit_ignores_local_networks():
    for i in range(60):
        login_limit.failed(login_limit.begin(f"10.42.0.{i + 1}", "demo", password_login=True))
    assert login_limit.begin("192.0.2.1", "demo", password_login=True).password_login
