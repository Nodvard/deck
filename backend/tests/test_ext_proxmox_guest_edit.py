"""Gast-Hardware aendern (vm.config_set): Whitelist, Grenzen, digest, Ballon-Regel,
Startreihenfolge im Proxmox-Format, und der Weg durchs Gate bis zum PUT."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "proxmox" / "src"))

from nodvard_deck_ext_proxmox.guest_edit import (  # noqa: E402
    ConfigEditError,
    build_update,
    current_values,
    describe,
    pending_keys,
)

QEMU = {"cores": 2, "sockets": 1, "memory": "4096", "balloon": 2048, "onboot": 1, "startup": "order=3,up=30", "digest": "abc123"}
LXC = {"cores": 1, "memory": 512, "swap": 512, "digest": "d1"}


def test_current_values_fill_proxmox_defaults():
    assert current_values("qemu", QEMU) == {
        "cores": 2, "sockets": 1, "memory": 4096, "balloon": 2048, "onboot": True, "startup_order": 3,
    }
    # Kein balloon-Schluessel -> Minimum = RAM; kein onboot/startup -> aus/keine.
    assert current_values("qemu", {"memory": 2048}) == {
        "cores": 1, "sockets": 1, "memory": 2048, "balloon": 2048, "onboot": False, "startup_order": None,
    }
    assert current_values("lxc", LXC) == {"cores": 1, "memory": 512, "swap": 512, "onboot": False, "startup_order": None}


def test_only_real_changes_are_sent_with_digest():
    params, diff = build_update("qemu", QEMU, {"cores": 4, "memory": 4096, "onboot": True})
    assert params == {"cores": 4, "digest": "abc123"}
    assert diff == [{"key": "cores", "label": "Kerne", "old": 2, "new": 4}]


def test_startup_order_keeps_up_and_down_delays():
    params, _ = build_update("qemu", QEMU, {"startup_order": 7})
    assert params["startup"] == "order=7,up=30"
    params, _ = build_update("qemu", QEMU, {"startup_order": None})
    assert params["startup"] == "up=30"
    params, _ = build_update("qemu", {**QEMU, "startup": "order=3"}, {"startup_order": None})
    assert params["delete"] == "startup" and "startup" not in params


def test_balloon_rules():
    with pytest.raises(ConfigEditError, match="Ballon-Minimum darf nicht größer"):
        build_update("qemu", QEMU, {"memory": 1024})  # Ballon 2048 > neuer RAM
    params, _ = build_update("qemu", QEMU, {"memory": 1024, "balloon": 512})
    assert params == {"memory": 1024, "balloon": 512, "digest": "abc123"}
    # Minimum = RAM ist der Proxmox-Standard -> Schluessel entfernen statt doppeln.
    params, _ = build_update("qemu", QEMU, {"balloon": 4096})
    assert params == {"delete": "balloon", "digest": "abc123"}


@pytest.mark.parametrize("new_memory", [4096, 16384])
def test_memory_change_without_balloon_key_lets_the_minimum_follow(new_memory):
    """Ohne "balloon"-Schluessel (Proxmox-Standard) ist das Minimum = RAM.
    RAM verkleinern wurde vorher mit "Ballon-Minimum darf nicht größer ..." abgelehnt,
    obwohl niemand das Ballon-Feld angefasst hatte."""
    params, diff = build_update("qemu", {"memory": "8192", "digest": "d9"}, {"memory": new_memory})
    assert params == {"memory": new_memory, "digest": "d9"}
    assert [d["key"] for d in diff] == ["memory"]
    # Wer das Minimum selbst mitsetzt, wird weiter geprueft.
    with pytest.raises(ConfigEditError, match="Ballon-Minimum darf nicht größer"):
        build_update("qemu", {"memory": "8192"}, {"memory": 4096, "balloon": 6000})


@pytest.mark.parametrize(
    ("kind", "changes", "message"),
    [
        ("qemu", {}, "Keine Änderung angegeben"),
        ("qemu", {"cores": 2}, "bereits so gesetzt"),
        ("qemu", {"net0": "virtio,bridge=vmbr1"}, "Nicht änderbar über Nodvard Deck: net0"),
        ("qemu", {"cipassword": "x"}, "Nicht änderbar"),
        ("lxc", {"sockets": 2}, "Nicht änderbar über Nodvard Deck: sockets"),
        ("qemu", {"cores": 0}, "erlaubt sind 1 bis"),
        ("qemu", {"cores": "4; rm -rf /"}, "ganze Zahl erwartet"),
        ("qemu", {"cores": True}, "ganze Zahl erwartet"),
        ("qemu", {"memory": 8}, "erlaubt sind 16 bis"),
        ("qemu", {"onboot": "yes"}, "an oder aus"),
        ("node", {"cores": 2}, "Nur VMs und Container"),
    ],
)
def test_rejects_everything_outside_the_whitelist(kind, changes, message):
    with pytest.raises(ConfigEditError, match=message):
        build_update(kind, QEMU if kind != "lxc" else LXC, changes)


def test_lxc_swap_and_memory():
    params, diff = build_update("lxc", LXC, {"memory": 1024, "swap": 0, "onboot": True})
    assert params == {"memory": 1024, "swap": 0, "onboot": 1, "digest": "d1"}
    assert [d["key"] for d in diff] == ["memory", "swap", "onboot"]


def test_describe_and_pending():
    _, diff = build_update("qemu", QEMU, {"cores": 4, "memory": 8192})
    rows = [
        {"key": "cores", "value": 2, "pending": 4},
        {"key": "memory", "value": "4096", "pending": 8192},
        {"key": "name", "value": "docker"},
        {"key": "startup", "value": "order=3", "delete": 1},
    ]
    assert pending_keys(rows) == ["cores", "memory", "startup_order"]
    assert describe(diff, ["memory"]) == (
        "Geändert: Kerne 2 → 4, RAM 4096 MB → 8192 MB. Wirksam erst nach einem Neustart des Gasts: RAM."
    )
