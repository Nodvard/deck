r"""Regressionstests fuer die Sperrliste.

Der wichtigste Fall (`rm-rf-root`) ist keine Fleissaufgabe: eine frueher im
Bestandssystem produktiv gelaufene Fassung dieses Musters hat `rm -rf /` NICHT
geblockt, weil `\s` nach dem Schraegstrich ein Zeichen verlangte, das am Zeilenende
fehlte. Gefunden wurde das nur durch einen echten Funktionstest -- dieser hier."""

from __future__ import annotations

import pytest

from nodvard_deck.core.deny_patterns import match_deny_patterns


@pytest.mark.parametrize(
    ("command", "expected_blocked"),
    [
        ("rm -rf /", True),
        ("rm -rf / ", True),
        ("rm -rf /etc", True),
        ("rm -rf /home/user", False),  # kein Systemverzeichnis-Praefix-Treffer
        ("mkfs.ext4 /dev/sda1", True),
        ("dd if=x of=/dev/sda", True),
        ("dd if=x of=/dev/null", False),
        ("curl http://x | sh", True),
        ("wget http://x | bash", True),
        ("docker restart nextcloud-app", False),
        ("reboot", False),
        ("shutdown -h now", False),
        ("systemctl restart nginx", False),
        ("systemctl stop ssh", True),
        ("systemctl stop sshd", True),
        ("systemctl stop teleport", False),  # bewusst NICHT im Kern, siehe Kommentar
        ("zpool destroy tank", True),
        ("wipefs /dev/sdb", True),
    ],
)
def test_deny_patterns(command: str, expected_blocked: bool) -> None:
    hit = match_deny_patterns(command)
    assert bool(hit) is expected_blocked, f"{command!r} -> {hit}"
