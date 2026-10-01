"""`nodvard_deck_ext_scripts.promotion.RecurringFixTracker` -- "KI befoerdert wiederkehrenden
Fix" (docs/02-EXTENSION-API.md Paragraph 6).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "scripts" / "src"))

from nodvard_deck_ext_scripts.promotion import MAX_COUNT, RecurringFixTracker  # noqa: E402


def _observe(tracker: RecurringFixTracker, *, host="h1", command="docker restart nginx", outcome="success"):
    return tracker.observe(
        action_type="shell.exec", host_id=host, outcome=outcome, payload={"command": command}
    )


def test_returns_none_below_threshold():
    tracker = RecurringFixTracker()
    for _ in range(MAX_COUNT - 1):
        assert _observe(tracker) is None


def test_returns_fingerprint_exactly_once_at_threshold():
    tracker = RecurringFixTracker()
    for _ in range(MAX_COUNT - 1):
        assert _observe(tracker) is None
    fp = _observe(tracker)
    assert fp is not None

    # Weitere Wiederholungen DANACH duerfen NICHT erneut befoerdern -- sonst waechst
    # bei jedem weiteren Vorfall ein weiterer, ueberfluessiger Entwurf.
    assert _observe(tracker) is None
    assert _observe(tracker) is None


def test_different_commands_or_hosts_are_tracked_independently():
    tracker = RecurringFixTracker()
    for _ in range(MAX_COUNT - 1):
        assert _observe(tracker, command="docker restart nginx") is None
        assert _observe(tracker, command="docker restart redis") is None
    # Beide erreichen die Schwelle im selben Aufruf-"Takt", aber als eigene fingerprints.
    fp_a = _observe(tracker, command="docker restart nginx")
    fp_b = _observe(tracker, command="docker restart redis")
    assert fp_a is not None
    assert fp_b is not None
    assert fp_a != fp_b


def test_only_shell_exec_is_promotable_not_script_run():
    """script.run darf NIE befoerdert werden -- sonst wuerde ein bereits befoerdertes,
    wiederkehrend laufendes Skript sich selbst immer wieder neu vorschlagen (siehe
    promotion.py-Docstring)."""
    tracker = RecurringFixTracker()
    for _ in range(MAX_COUNT + 2):
        fp = tracker.observe(
            action_type="script.run", host_id="h1", outcome="success", payload={"command": "echo hi"}
        )
        assert fp is None


def test_failed_or_denied_outcomes_are_not_counted():
    tracker = RecurringFixTracker()
    for _ in range(MAX_COUNT + 2):
        assert _observe(tracker, outcome="failure") is None
        assert _observe(tracker, outcome="denied") is None
