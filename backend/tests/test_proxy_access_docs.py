"""Die Proxy-Anleitungen schließen direkten Zugriff aus dem LAN aus."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDES = [
    (
        "docs/12-REVERSE-PROXY.md",
        "Proxy auf einem anderen Rechner im LAN",
        "in der Firewall nur die einzelne Adresse des Proxys erlauben",
        "direkter Zugriff nur im LAN",
        "Gateway oder ein ganzes Docker-Netz",
        "gilt diese LAN-Variante nicht",
    ),
    (
        "docs/en/REVERSE-PROXY.md",
        "Proxy on another machine in the LAN",
        "allow only the proxy's single address in the firewall",
        "direct access only from the LAN",
        "gateway or a whole Docker network",
        "do not use this LAN option",
    ),
]


@pytest.mark.parametrize("guide", GUIDES, ids=["deutsch", "englisch"])
def test_lan_proxy_requires_firewall_to_allow_only_the_proxy(guide):
    path, marker, firewall_rule, direct_access, _, _ = guide
    text = (ROOT / path).read_text(encoding="utf-8")
    lan = " ".join(text.split(f"  - {marker}", 1)[1].split("\n- **", 1)[0].split())
    assert firewall_rule in lan
    assert direct_access not in lan


@pytest.mark.parametrize("guide", GUIDES, ids=["deutsch", "englisch"])
def test_lan_proxy_is_excluded_when_a_gateway_or_docker_network_is_trusted(guide):
    path, marker, _, _, trusted_network, exclusion = guide
    text = (ROOT / path).read_text(encoding="utf-8")
    lan = " ".join(text.split(f"  - {marker}", 1)[1].split("\n- **", 1)[0].split())
    assert trusted_network in lan
    assert exclusion in lan
