"""RBAC ist eine reine Funktion, ohne DB testbar (docs/03-DATA-MODEL.md §1)."""

from __future__ import annotations

from nodvard_deck.core.rbac import BUILTIN_ROLES, has_permission, permission_for_event


def test_exact_match():
    assert has_permission(["hosts.read"], "hosts.read")
    assert not has_permission(["hosts.read"], "hosts.write")


def test_scope_wildcard_suffix():
    assert has_permission(["secrets.read:ssh-*"], "secrets.read:ssh-fleet")
    assert not has_permission(["secrets.read:ssh-*"], "secrets.read:api-token")


def test_unscoped_grant_covers_any_scope():
    assert has_permission(["secrets.read"], "secrets.read:beliebig")


def test_scoped_grant_does_not_cover_unscoped_requirement():
    assert not has_permission(["secrets.read:ssh-fleet"], "secrets.read")


def test_admin_wildcard():
    assert has_permission(BUILTIN_ROLES["admin"], "irgendwas.exotisch")


def test_viewer_cannot_execute():
    assert not has_permission(BUILTIN_ROLES["viewer"], "hosts.execute")


def test_operator_risk_ceiling():
    assert has_permission(BUILTIN_ROLES["operator"], "actions.approve:low")
    assert not has_permission(BUILTIN_ROLES["operator"], "actions.approve:high")


def test_permission_for_event_known_prefixes():
    assert permission_for_event("host.down") == "hosts.read"
    assert permission_for_event("host.up") == "hosts.read"
    assert permission_for_event("action.denied") == "hosts.read"
    assert permission_for_event("job.finished") == "jobs.read"
    assert permission_for_event("notification.created") == "notifications.read"
    assert permission_for_event("audit.something") == "audit.read"


def test_permission_for_event_unknown_prefix_needs_no_extra_permission():
    assert permission_for_event("hello.tick") is None
    assert permission_for_event("system.started") is None
    assert permission_for_event("ext.ntfy.custom") is None
    assert permission_for_event("no-dot-at-all") is None


def test_apps_write_is_only_for_admin_by_default():
    """`apps.write` (eigene App-Kacheln anlegen/aendern/loeschen) ist additiv: Admin hat es ueber `*`,
    Bediener und Betrachter bekommen es nicht von selbst -- die Links stehen allen auf der Startseite."""
    assert has_permission(BUILTIN_ROLES["admin"], "apps.write")
    assert not has_permission(BUILTIN_ROLES["operator"], "apps.write")
    assert not has_permission(BUILTIN_ROLES["viewer"], "apps.write")
    assert not any("apps" in p for p in BUILTIN_ROLES["operator"] + BUILTIN_ROLES["viewer"])
    # Lesen: wie die erkannten Apps im Cockpit.
    assert has_permission(BUILTIN_ROLES["viewer"], "hosts.read")
