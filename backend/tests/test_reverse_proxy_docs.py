"""Passwortsperre und VPN-Zugang in der Proxy-Anleitung."""

from pathlib import Path

from nodvard_deck.core import login_limit


def test_passwortsperre_begrenzt_auch_den_vpn_zugang():
    path = Path(__file__).resolve().parents[2] / "docs" / "12-REVERSE-PROXY.md"
    text = " ".join(path.read_text(encoding="utf-8").split())
    assert (
        f"Nach {login_limit.MAX_PASSWORD_FAILURES_PER_NAME} falschen Passwörtern für einen Benutzernamen "
        f"in {login_limit.WINDOW_SECONDS // 60} Minuten"
    ) in text
    assert (
        "aus fremden Netzen vorübergehend gesperrt, auch mit dem richtigen Passwort. Aus dem Heimnetz und über ein "
        "VPN wie [Tailscale](13-TAILSCALE.md) kommst du trotzdem hinein"
    ) in text
