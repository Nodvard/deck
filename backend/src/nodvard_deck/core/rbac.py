"""Berechtigungsauswertung als reine Funktionen ohne I/O (Format `<domäne>.<aktion>[:<scope>]`, z. B. `hosts.read`)."""

from __future__ import annotations

from collections.abc import Iterable


def _split(permission: str) -> tuple[str, str | None]:
    base, _, scope = permission.partition(":")
    return base, (scope or None)


def _scope_matches(granted_scope: str | None, required_scope: str | None) -> bool:
    if granted_scope is None:
        # Ohne Scope gewährt gilt für alle Scopes.
        return True
    if required_scope is None:
        # Verlangt ohne Scope, gewährt nur ein Teilbereich: reicht nicht.
        return False
    if granted_scope == "*":
        return True
    if granted_scope.endswith("*"):
        return required_scope.startswith(granted_scope[:-1])
    return granted_scope == required_scope


def has_permission(granted: Iterable[str], required: str) -> bool:
    """Ob `required` durch mindestens eine der `granted`-Berechtigungen gedeckt ist."""
    req_base, req_scope = _split(required)
    for g in granted:
        g_base, g_scope = _split(g)
        if g_base == "*":
            return True
        if g_base != req_base:
            continue
        if _scope_matches(g_scope, req_scope):
            return True
    return False


def has_all(granted: Iterable[str], required: Iterable[str]) -> bool:
    granted = list(granted)
    return all(has_permission(granted, r) for r in required)


def has_any(granted: Iterable[str], required: Iterable[str]) -> bool:
    granted = list(granted)
    return any(has_permission(granted, r) for r in required)


# Der Präfix eines Event-Namens bestimmt die Berechtigung, die ein WS-Client zum Sehen braucht; gefiltert wird die
# Zustellung, nicht das Abonnieren; dieselbe Tabelle gilt für die themenreinen Kanäle. Präfixe ohne Eintrag gehen
# nicht über WebSocket, außer sie stehen in `PUBLIC_EVENT_PREFIXES`.
EVENT_PREFIX_PERMISSIONS: dict[str, str] = {
    "host": "hosts.read",
    "action": "hosts.read",
    "job": "jobs.read",
    "notification": "notifications.read",
    "audit": "audit.read",
}

# Jeder Angemeldete braucht sie: Die Oberfläche lädt nach `extension.*` Navigation und Kacheln neu.
PUBLIC_EVENT_PREFIXES: frozenset[str] = frozenset({"extension"})


def permission_for_event(event_name: str) -> str | None:
    """Die nötige Berechtigung für das Event laut `EVENT_PREFIX_PERMISSIONS`, sonst `None`."""
    prefix = event_name.split(".", 1)[0]
    return EVENT_PREFIX_PERMISSIONS.get(prefix)


def event_reaches_ws(event_name: str) -> bool:
    """Ob das Event überhaupt über WebSocket verteilt wird."""
    prefix = event_name.split(".", 1)[0]
    return prefix in EVENT_PREFIX_PERMISSIONS or prefix in PUBLIC_EVENT_PREFIXES


def sees_other_users(permissions: Iterable[str]) -> bool:
    """Ob diese Rechte Benutzernamen anderer Konten zeigen: `users.read` oder ein `actions.approve`-Recht."""
    permissions = list(permissions)
    return has_permission(permissions, "users.read") or any(
        permission.partition(":")[0] == "actions.approve" for permission in permissions
    )


# Eingebaute Rollen. `owner` steht nicht hier: Er umgeht RBAC ganz (`User.is_owner`), damit eine Rollenänderung die
# Installation nicht aussperren kann.
#
# `system.read` (Einstellungen, System ansehen) hat nur `admin` über `*`; alles Kritische dort (Sicherung herunterladen,
# Schlüssel, Löschen, Wiederherstellen) verlangt den Owner selbst (`api.deps.require_owner`), auch `*` reicht dafür nie.
SYSTEM_READ = "system.read"

BUILTIN_ROLES: dict[str, tuple[str, ...]] = {
    "admin": ("*",),
    "operator": (
        "hosts.read", "hosts.execute",
        "actions.approve:low", "actions.approve:medium",
        "jobs.read", "jobs.run",
        "files.read", "files.write",
        "audit.read", "notifications.read", "notifications.write",
    ),
    "viewer": ("hosts.read", "jobs.read", "audit.read", "files.read", "notifications.read"),
}
