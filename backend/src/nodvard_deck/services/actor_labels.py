"""Lesbare Namen für „Vorgeschlagen von“ und „Bestätigt von“ in Aktionslisten.

Aufgelöst wird mit einer Abfrage je Art; wer fremde Benutzernamen sehen darf, regelt `may_see_other_users`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import has_permission
from ..models import Action, User
from .auth import user_permissions
from .extensions import get_records

# Akteure ohne eigene Kennung, die sich nachschlagen ließe.
FIXED_LABELS = {"system": "System", "scheduler": "Zeitplan", "ai": "KI"}

# Feste Kennungen unter `system` mit eigenem Namen, z. B. der Update-Helfer (`services.update_helper`).
SYSTEM_LABELS = {"updater": "Update-Helfer"}


def raw_actor(actor_type: str, actor_id: str) -> str:
    """Der rohe Wert `art/kennung`, auf den ein nicht aufgelöster Akteur zurückfällt."""
    return f"{actor_type}/{actor_id}"


# `GET /actions` verlangt nur `hosts.read`, das auch der Betrachter hat; `GET /users` bleibt ihm verwehrt.
# Fremde Namen bekommt er deshalb auch hier nicht.
def may_see_other_users(viewer: User) -> bool:
    """Darf `viewer` Benutzernamen anderer Nutzer sehen? Der Owner hat `*`.

    Ja mit `users.read` oder `actions.approve:*`: Wer Vorschläge entscheidet, muss den Vorschlagenden erkennen."""
    permissions = user_permissions(viewer)
    return has_permission(permissions, "users.read") or any(
        permission.partition(":")[0] == "actions.approve" for permission in permissions
    )


class ActorLabels:
    """Ergebnis von `load_actor_labels()`: Namen für die Akteure einer Aktionsliste."""

    def __init__(self, users: Mapping[str, str], extensions: Mapping[str, str]) -> None:
        self._users = users
        self._extensions = extensions

    def actor(self, actor_type: str, actor_id: str) -> str:
        if actor_type == "user":
            name = self._users.get(actor_id)
        elif actor_type == "extension":
            name = self._extensions.get(actor_id)
        elif actor_type == "system" and actor_id in SYSTEM_LABELS:
            name = SYSTEM_LABELS[actor_id]
        else:
            name = FIXED_LABELS.get(actor_type)
        return name or raw_actor(actor_type, actor_id)

    def proposed_by(self, action: Action) -> str:
        return self.actor(action.proposed_by_type, action.proposed_by_id)

    def approved_by(self, action: Action) -> str | None:
        """Wer bestätigt, abgelehnt oder verworfen hat (immer ein Nutzer), sonst `None`."""
        if action.approved_by_user_id is None:
            return None
        return self._users.get(action.approved_by_user_id) or action.approved_by_user_id


async def load_actor_labels(
    session: AsyncSession, actions: Iterable[Action], *, viewer: User | None
) -> ActorLabels:
    """Löst die Akteure der `actions` mit je einer Abfrage je Art zu Namen auf.

    Ohne `may_see_other_users()` sieht `viewer` nur den eigenen Namen; `None` löst alles auf (abgesicherte Aufrufer)."""
    user_ids: set[str] = set()
    extension_ids: set[str] = set()
    for action in actions:
        if action.proposed_by_type == "user":
            user_ids.add(action.proposed_by_id)
        elif action.proposed_by_type == "extension":
            extension_ids.add(action.proposed_by_id)
        if action.approved_by_user_id is not None:
            user_ids.add(action.approved_by_user_id)

    users: dict[str, str] = {}
    if viewer is not None:
        users[viewer.id] = viewer.username
        user_ids.discard(viewer.id)
        if not may_see_other_users(viewer):
            user_ids.clear()
    if user_ids:
        # Nur zwei Spalten statt der Entity: `User.roles` wäre `selectin` und kostete
        # je Abfrage eine weitere.
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        users.update({user_id: username for user_id, username in rows.all()})

    extensions: dict[str, str] = {}
    if extension_ids:
        # Die Kennung stammt aus der gespeicherten Aktion und kann nach einer Umbenennung eine alte sein (`legacy_ids`);
        # `get_records` findet die Zeile für beide.
        for ext_id, record in (await get_records(session, extension_ids)).items():
            name = (record.manifest or {}).get("name")
            if isinstance(name, str) and name.strip():
                extensions[ext_id] = name.strip()

    return ActorLabels(users, extensions)


async def load_user_labels(
    session: AsyncSession, user_ids: Iterable[str], *, viewer: User
) -> dict[str, str]:
    """Namen für Benutzer-Kennungen, die nicht aus einer Aktion stammen (z. B. „gemerkt von“ bei Server-Schlüsseln).

    Sichtbarkeit wie bei `load_actor_labels()`; nicht auflösbare Kennungen fehlen, der Aufrufer nimmt `raw_actor()`."""
    wanted = set(user_ids)
    labels = {viewer.id: viewer.username} if viewer.id in wanted else {}
    wanted.discard(viewer.id)
    if wanted and may_see_other_users(viewer):
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(wanted)))
        labels.update({user_id: username for user_id, username in rows.all()})
    return labels
