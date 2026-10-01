"""Schutz der Alembic-Revisionen: einmal ausgelieferte Migrationen aendern sich nie (Umbenennung Teil B, PR 6).

Die Python-Pakete der Extensions wurden umbenannt (jetzt `nodvard_deck_ext_<id>`), die Migrationen
liegen aber ausserhalb davon (`extensions/<id>/migrations/versions/`, `backend/migrations/versions/`). Der Test
vergleicht den Stand mit `alembic_revisions.json` (aufgenommen vor der Umbenennung):

* Jede Revision aus dem Schnappschuss gibt es noch, mit derselben Vorgaengerin, denselben Branch-Labels und an
  derselben Stelle. Sonst wuerde eine vorhandene Datenbank (`alembic_version`) auf eine unbekannte Revision zeigen.
* Es kommen keine zusaetzlichen Koepfe dazu: jede Wurzel (Kern, `documents`, `inventory`, `nexus-soc`) hat genau einen Kopf,
  und jeder Kopf des Schnappschusses ist entweder noch Kopf oder wurde durch eine neue Migration fortgesetzt.

**Neue Migrationen brauchen hier keinen Eintrag**: Der Schnappschuss ist eine Untergrenze. Nur wer eine bestehende
Revision aendert (verboten), muss ihn anfassen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from nodvard_deck.migrate import alembic_config

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = json.loads((Path(__file__).resolve().parent / "alembic_revisions.json").read_text(encoding="utf-8"))


def _current() -> tuple[dict[str, dict], set[str], ScriptDirectory]:
    cfg, _ = alembic_config(repo_root=REPO_ROOT)
    script = ScriptDirectory.from_config(cfg)
    revisions: dict[str, dict] = {}
    for rev in script.walk_revisions():
        down = rev.down_revision
        revisions[rev.revision] = {
            "down_revision": sorted(down) if isinstance(down, tuple) else down,
            "branch_labels": sorted(rev.branch_labels),
            "datei": str(Path(rev.path).resolve().relative_to(REPO_ROOT)).replace("\\", "/"),
        }
    return revisions, set(script.get_heads()), script


def _ancestors(revisions: dict[str, dict], start: str) -> set[str]:
    """`start` und alles davor (die Vorgaengerketten, auch bei Zusammenfuehrungen)."""
    seen: set[str] = set()
    todo = [start]
    while todo:
        rev = todo.pop()
        if rev in seen:
            continue
        seen.add(rev)
        down = revisions[rev]["down_revision"]
        todo.extend(down if isinstance(down, list) else [down] if down else [])
    return seen


def test_the_snapshot_is_not_empty_and_has_the_known_branches():
    labels = {label for item in SNAPSHOT["revisionen"].values() for label in item["branch_labels"]}
    assert labels == {"documents", "inventory", "nexus-soc"}
    assert len(SNAPSHOT["heads"]) == 4 and len(SNAPSHOT["revisionen"]) >= 14


@pytest.mark.parametrize("revision", sorted(SNAPSHOT["revisionen"]))
def test_a_shipped_revision_is_unchanged(revision):
    current, _, _ = _current()
    assert revision in current, f"Revision {revision} fehlt (eine Datenbank mit diesem Stand kaeme nicht mehr weiter)"
    assert current[revision] == SNAPSHOT["revisionen"][revision]


def test_the_heads_are_the_same_or_were_continued_by_new_migrations():
    current, heads, _ = _current()
    new_revisions = set(current) - set(SNAPSHOT["revisionen"])
    for old_head in SNAPSHOT["heads"]:
        continued = any(old_head in _ancestors(current, rev) for rev in new_revisions)
        assert old_head in heads or continued, f"Kopf {old_head} ist weder noch Kopf noch fortgesetzt"
    if not new_revisions:
        assert sorted(heads) == SNAPSHOT["heads"], "ohne neue Migration muessen die Koepfe genau die alten sein"


def test_every_branch_has_exactly_one_head():
    current, heads, _ = _current()
    roots_of_heads = []
    for head in heads:
        ancestors = _ancestors(current, head)
        roots_of_heads.append(frozenset(rev for rev in ancestors if not current[rev]["down_revision"]))
    assert len(set(roots_of_heads)) == len(roots_of_heads), "zwei Koepfe auf derselben Wurzel (ein verwaister zweiter Kopf)"
