"""Bausteine fuer Tests mit aendernden Engine-Aufrufen: ein Journal in jedem Schritt, ein Slot, und die Bindung des
Clients zusammen mit dem passenden Waechter der Fake-Engine (`bound`).

Die IDs sind bewusst verschieden und sprechend: `OLD` (das Ziel vor dem Vorgang), `NEW` (der angelegte Container),
`OWN` (der Helfer selbst), `OTHER` (ein beliebiger fremder Container) und entsprechend die Images und Digests.
"""

from __future__ import annotations

from typing import Any

from fake_engine import FakeEngine, JournalGuard
from nodvard_deck_updater import engine as E
from nodvard_deck_updater import policy
from nodvard_deck_updater.state import Journal, NewImage, OldContainer
from updater_support import NOW

OLD = "a" * 64
NEW = "e" * 64
OWN = "9" * 64
OTHER = "7" * 64
NETWORK = "b" * 64
OLD_IMAGE = "sha256:" + "c" * 64
NEW_IMAGE = "sha256:" + "2" * 64
PREV_IMAGE = "sha256:" + "6" * 64
OTHER_IMAGE = "sha256:" + "3" * 64
DIGEST = "sha256:" + "d" * 64
"""Der Digest, auf den das bewegliche Tag in der Registry zeigt (Update)."""
PREV_DIGEST = "sha256:" + "8" * 64
"""Der Digest des Vorgaengers im Slot (Rueckweg nach `image prune -a`)."""
OTHER_DIGEST = "sha256:" + "5" * 64
OLD_DIGEST = "sha256:" + "e" * 64
"""Der Digest des alten Images in der Registry (steht im Journal, fuer den Slot nach dem Update)."""
NAME = "nodvard-deck-nodvard-deck-1"
TAG_TEXT = policy.REPOSITORY + ":latest"
RESTART = ("unless-stopped", 0)
RID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"

UPDATE_VERSIONS = ("0.7.0", "0.7.1")
"""Update: das Ziel laeuft mit 0.7.0, installiert wird 0.7.1."""
ROLLBACK_VERSIONS = ("0.7.1", "0.7.0")
"""Rueckweg: das Ziel laeuft mit 0.7.1, zurueck geht es auf 0.7.0 (das Image aus dem Slot)."""


def journal_at(step: str = "begin", action: str = "update", *, tag_text: str = TAG_TEXT,
               restart: tuple[str, int] = RESTART, repository: str = policy.REPOSITORY, undo: bool = False,
               code: str | None = None) -> Journal:
    """Ein gueltiges Journal im Schritt `step`. Beim Update ist `new` das gezogene Image (mit Digest), beim Rueckweg der
    Vorgaenger aus dem Slot (ohne Digest); ab `created` steht der neue Container darin. `undo`: der Rueckbau ist
    angekuendigt (mit dem Grund `code`)."""
    old_version, new_version = UPDATE_VERSIONS if action == "update" else ROLLBACK_VERSIONS
    old = OldContainer(id=OLD, name=NAME, image_id=OLD_IMAGE, version=old_version, restart_policy=restart,
                       tag_text=tag_text, repo_digest=repository + "@" + OLD_DIGEST)
    new = None
    index = policy.STEPS.index(step)
    if index >= policy.STEPS.index("pulled"):
        new_id = NEW if index >= policy.STEPS.index("created") else None
        if action == "update":
            new = NewImage(image_id=NEW_IMAGE, digest=DIGEST, version=new_version, id=new_id)
        else:
            new = NewImage(image_id=PREV_IMAGE, digest=None, version=new_version, id=new_id)
    journal = Journal(request_id=RID, action=action, step=step, started_at=NOW, deadline=NOW + policy.DEADLINE_S,
                      old=old, new=new, undo=undo, code=code, repository=repository)
    journal.validate()
    return journal


def slot_for(action: str = "update", **changes: Any) -> policy.Slot:
    """Der Slot, der zu einem Vorgang gehoert: beim Update der, den der Commit ersetzt (von 0.6.9 auf das laufende
    0.7.0), beim Rueckweg der, der benutzt wird (von 0.7.0 auf das laufende 0.7.1)."""
    values: dict[str, Any] = {
        "from_version": "0.6.9" if action == "update" else "0.7.0",
        "image_id": PREV_IMAGE, "repo_digest": policy.REPOSITORY + "@" + PREV_DIGEST,
        "installed_container_id": OLD, "installed_image_id": OLD_IMAGE, "until": NOW + policy.SLOT_TTL_S,
    }
    values.update(changes)
    slot = policy.Slot(**values)
    slot.validate()
    return slot


def bound(fake: FakeEngine, engine: E.Engine, journal: Journal | None, *, slot: policy.Slot | None = None,
          own_id: str = OWN, **options: Any) -> E.Binding:
    """Die Bindung des Clients fuer `journal` -- und derselbe Stand als Waechter der Fake-Engine, als staende er so
    auf der Platte."""
    binding = engine.bind(journal, own_id, slot=slot, **options)
    fake.guard = JournalGuard.fixed(own_id, None if journal is None else journal.to_json(),
                                    None if slot is None else slot.to_json(), repository=binding.repository)
    return binding
