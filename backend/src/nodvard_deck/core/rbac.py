"""Berechtigungsauswertung.

Reine Funktionen, kein I/O — damit vollstaendig testbar ohne Datenbank. Das ist Absicht:
Zugriffslogik, die man nur mit laufendem System testen kann, wird nicht getestet.

Format:  <domaene>.<aktion>[:<scope>]
Beispiele:
    hosts.read
    secrets.read:ssh-fleet
    secrets.read:ssh-*          (Suffix-Wildcard im Scope)
    actions.approve:high
"""

from __future__ import annotations

from collections.abc import Iterable


def _split(permission: str) -> tuple[str, str | None]:
    base, _, scope = permission.partition(":")
    return base, (scope or None)


def _scope_matches(granted_scope: str | None, required_scope: str | None) -> bool:
    if granted_scope is None:
        # Ohne Scope gewaehrt = fuer alle Scopes gewaehrt.
        return True
    if required_scope is None:
        # Verlangt wird ohne Scope, gewaehrt ist nur ein Teilbereich -> reicht nicht.
        return False
    if granted_scope == "*":
        return True
    if granted_scope.endswith("*"):
        return required_scope.startswith(granted_scope[:-1])
    return granted_scope == required_scope


def has_permission(granted: Iterable[str], required: str) -> bool:
    """Prueft, ob `required` durch mindestens eine der `granted`-Berechtigungen gedeckt ist."""
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


# --- WS-Kanal-/Event-Berechtigungen (WP-6-Nachtrag) -------------------------
# docs/04-API.md §4 verlangt fuer den `events`-Kanal "gefiltert nach RBAC" -- diese
# Tabelle ist die Umsetzung: der Prefix eines Event-Namens (vor dem ersten Punkt)
# bestimmt, welche Berechtigung ein WS-Client braucht, um das Event zu SEHEN
# (Zustellung wird gefiltert, nicht das Abonnieren des Kanals selbst -- "events" bleibt
# EIN Kanal fuer alle, nur die einzelnen Nachrichten darin sind unterschiedlich
# sichtbar). Dieselbe Tabelle liefert auch die Berechtigung fuer die anderen,
# themenreinen Kanaele (`notifications`, `jobs.{id}`/`runs.{id}`, `actions`, `hosts`) --
# derselbe Domaenen-Name, dieselbe Berechtigung, ob als Event-Praefix oder als
# eigener Kanal. Unbekannte Praefixe (z. B. `ext.<id>.*`, `hello.tick`, `system.*`)
# brauchen NUR Authentifizierung, keine zusaetzliche Berechtigung -- Events tragen
# nie sensible Werte (dieselbe Regel wie im Audit-Log), und Extensions sind
# vertrauenswuerdiger Code (docs/01 §7), keine Sandbox, die zusaetzliche
# Kern-Berechtigungen fuer ihre eigenen Kanaele bräuchte.
EVENT_PREFIX_PERMISSIONS: dict[str, str] = {
    "host": "hosts.read",
    "action": "hosts.read",
    "job": "jobs.read",
    "notification": "notifications.read",
    "audit": "audit.read",
}


def permission_for_event(event_name: str) -> str | None:
    """`None` heisst: jeder authentifizierte WS-Client sieht dieses Event, keine
    weitere Pruefung noetig."""
    prefix = event_name.split(".", 1)[0]
    return EVENT_PREFIX_PERMISSIONS.get(prefix)


# --- Eingebaute Rollen -----------------------------------------------------
# `owner` steht nicht in dieser Tabelle: der Owner umgeht RBAC vollstaendig
# (User.is_owner), damit eine unglueckliche Rollenaenderung die Installation nicht
# aussperren kann.
#
# `system.read` (Einstellungen -> System ansehen: Version, Sicherungen, Updates) hat nur
# `admin` ueber `*`. Alles Kritische dort (Sicherung herunterladen, Schluessel, Loeschen,
# spaeter Wiederherstellen) verlangt den Owner selbst (`api.deps.require_owner`) -- eine
# Berechtigung allein reicht dafuer nie, auch `*` nicht.
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
