"""Lesbare Namen für „Vorgeschlagen von“ / „Bestätigt von“.

In der `actions`-Tabelle stehen nur Art und Kennung des Akteurs (`user` + UUID,
`extension` + Erweiterungs-ID, `ai` + Modellname ...). Die Oberfläche soll daraus
einen Namen machen -- der Kern löst ihn hier auf, in EINER Abfrage je Art für alle
gelisteten Aktionen, statt dass jede Zeile nachfragt.

Preisgegeben wird nur der Anzeigename (Benutzername bzw. Name der Erweiterung), nie
weitere Nutzerfelder wie E-Mail oder Rollen. Aber: Benutzernamen anderer Nutzer standen
vorher NICHT auf der Seite (nur die UUID), und `GET /actions` verlangt nur `hosts.read`,
das auch der Betrachter (viewer) hat -- `GET /users` bleibt ihm dagegen verwehrt. Darum
gilt:

- den eigenen Namen sieht jeder,
- die Namen anderer Nutzer sehen nur, wer `users.read` hat oder Aktionen entscheiden darf
  (`actions.approve:<risiko>`, z. B. Bediener) -- wer freigibt, muss sehen, von wem der
  Vorschlag kommt, sonst ist der Name für ihn wertlos,
- alle anderen (Betrachter) sehen wie bisher den rohen Wert `user/<uuid>`.

Namen von Erweiterungen und die festen Bezeichnungen (System, Zeitplan, KI) sind keine
Nutzerdaten und stehen jedem offen. Ein Akteur, der sich nicht mehr auflösen lässt
(gelöschter Nutzer, entfernte Erweiterung), fällt auf den rohen Wert `art/kennung` zurück.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.rbac import has_permission
from ..models import Action, ExtensionRecord, User
from .auth import user_permissions

# Akteure ohne eigene Kennung, die sich nachschlagen liesse.
FIXED_LABELS = {"system": "System", "scheduler": "Zeitplan", "ai": "KI"}


def raw_actor(actor_type: str, actor_id: str) -> str:
    """Der ungeschönte Wert, wie ihn die Oberfläche früher gezeigt hat."""
    return f"{actor_type}/{actor_id}"


def may_see_other_users(viewer: User) -> bool:
    """Darf `viewer` Benutzernamen ANDERER Nutzer sehen? Ja mit `users.read` (kennt sie aus
    der Nutzerverwaltung ohnehin) oder mit einer beliebigen `actions.approve:*`-Berechtigung
    (wer Vorschläge entscheidet, muss den Vorschlagenden erkennen). Der Owner hat `*`."""
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
        else:
            name = FIXED_LABELS.get(actor_type)
        return name or raw_actor(actor_type, actor_id)

    def proposed_by(self, action: Action) -> str:
        return self.actor(action.proposed_by_type, action.proposed_by_id)

    def approved_by(self, action: Action) -> str | None:
        """Wer bestätigt/abgelehnt/verworfen hat -- immer ein Nutzer, sonst `None`."""
        if action.approved_by_user_id is None:
            return None
        return self._users.get(action.approved_by_user_id) or action.approved_by_user_id


async def load_actor_labels(
    session: AsyncSession, actions: Iterable[Action], *, viewer: User | None
) -> ActorLabels:
    """Sammelt die Nutzer- und Erweiterungs-Kennungen aller `actions` und schlägt sie mit
    je einer Abfrage nach (keine Abfrage, wenn es keine Kennung der Art gibt).

    `viewer` ist, wer die Namen zu sehen bekommt: ohne `may_see_other_users()` werden nur
    er selbst (ohne Abfrage) und keine anderen Nutzer aufgelöst. `None` heißt "kein
    Betrachter" und löst alles auf -- nur für Aufrufer, die selbst schon abgesichert sind
    (`ctx.actions.proposer_labels()` hinter der Route der Erweiterung)."""
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
        rows = await session.execute(
            select(ExtensionRecord.id, ExtensionRecord.manifest).where(ExtensionRecord.id.in_(extension_ids))
        )
        for ext_id, manifest in rows.all():
            name = (manifest or {}).get("name")
            if isinstance(name, str) and name.strip():
                extensions[ext_id] = name.strip()

    return ActorLabels(users, extensions)


async def load_user_labels(
    session: AsyncSession, user_ids: Iterable[str], *, viewer: User
) -> dict[str, str]:
    """Namen fuer Benutzer-Kennungen, die NICHT aus einer Aktion stammen (z. B. "gemerkt
    von" bei einem Server-Schluessel) -- mit derselben Sichtbarkeitsregel wie
    `load_actor_labels()`: den eigenen Namen sieht jeder, fremde nur, wer
    `may_see_other_users()`. Nicht aufloesbare Kennungen fehlen im Ergebnis; der Aufrufer
    faellt dann auf `raw_actor("user", id)` zurueck."""
    wanted = set(user_ids)
    labels = {viewer.id: viewer.username} if viewer.id in wanted else {}
    wanted.discard(viewer.id)
    if wanted and may_see_other_users(viewer):
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(wanted)))
        labels.update({user_id: username for user_id, username in rows.all()})
    return labels
