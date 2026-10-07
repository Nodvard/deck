"""Titel von Container-Vorfaellen ohne relative Zeit, feste Fakten im Lagebericht und der
Abgleich des Modelltextes mit den Fakten (reine Funktionen, kein Server)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SHIELD_SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"
if str(SHIELD_SRC) not in sys.path:
    sys.path.insert(0, str(SHIELD_SRC))

from nodvard_deck_ext_shield.incident_text import (  # noqa: E402
    clean_incident_message,
    describe_transition,
    strip_relative_time,
)
from nodvard_deck_ext_shield.remediation import (  # noqa: E402
    drop_contradictions,
    facts_line,
    facts_notice,
)
from nodvard_deck_ext_shield.watcher import (  # noqa: E402
    DockerWatcher,
    parse_exit_facts,
)


@pytest.mark.parametrize(
    "status, expected",
    [
        ("Exited (137) 4 seconds ago", "Exited (137)"),
        ("Exited (1) About an hour ago", "Exited (1)"),
        ("Exited (0) Less than a second ago", "Exited (0)"),
        ("Exited (2) 2 days ago", "Exited (2)"),
        ("Restarting (1) 3 seconds ago", "Restarting (1)"),
        ("Up 3 hours (Paused)", "Up (Paused)"),
        ("Up About an hour", "Up"),
        ("Dead", "Dead"),
    ],
)
def test_relative_time_is_removed_from_docker_status(status, expected):
    assert strip_relative_time(status) == expected


@pytest.mark.parametrize(
    "kind, state, status, expected",
    [
        ("crash", "exited", "Exited (137) 4 seconds ago", "Container abgestürzt (Exit-Code 137)"),
        ("crash", "restarting", "Restarting (1) 5 seconds ago", "Container startet immer wieder neu (Exit-Code 1)"),
        ("crash", "dead", "Dead", "Container abgestürzt (Docker-Zustand: dead)"),
        ("stopped", "exited", "Exited (0) 2 minutes ago", "Container manuell gestoppt (Exit-Code 0)"),
        ("paused", "paused", "Up 3 hours (Paused)", "Container pausiert"),
    ],
)
def test_new_titles_are_plain_german_without_time(kind, state, status, expected):
    assert describe_transition(kind=kind, state=state, status=status) == expected


@pytest.mark.parametrize(
    "old, expected",
    [
        ("Container CRASH (Exited (137) 4 seconds ago)", "Container abgestürzt (Exit-Code 137)"),
        ("Container CRASH (Exited (1))", "Container abgestürzt (Exit-Code 1)"),
        ("Container CRASH", "Container abgestürzt"),
        ("Container CRASH (Restarting (1) 3 seconds ago)", "Container startet immer wieder neu (Exit-Code 1)"),
        ("Container MANUELLER STOP (Exited (143) 1 minute ago)", "Container manuell gestoppt (Exit-Code 143)"),
        ("Container PAUSIERT (Up 3 hours (Paused))", "Container pausiert"),
        (
            "Container CRASH (Absturzschleife: 2x neu gestartet seit der letzten Prüfung, Up 5 seconds)",
            "Container abgestürzt (Absturzschleife: 2x neu gestartet seit der letzten Prüfung)",
        ),
        ("Container abgestürzt (Exit-Code 1)", "Container abgestürzt (Exit-Code 1)"),
        ("Etwas ganz anderes", "Etwas ganz anderes"),
        ("Dienst seit 4 seconds ago kaputt", "Dienst seit kaputt"),
    ],
)
def test_stored_titles_are_cleaned_on_output(old, expected):
    assert clean_incident_message(old) == expected


@pytest.mark.asyncio
async def test_watcher_titles_and_details_carry_no_relative_time():
    from test_ext_shield_watcher import (
        _FakeCtx,
        _FakeExecResult,
        _FakeHost,
        _ps_line,
    )

    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"}, hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("nginx", "running", "Up 2 hours"))},
    )
    seen = []

    async def capture(t):
        seen.append(t)

    watcher = DockerWatcher(ctx, on_transition=capture)
    await watcher.tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps_line("nginx", "exited", "Exited (137) 4 seconds ago"))
    await watcher.tick()
    assert [t.message for t in seen] == ["Container abgestürzt (Exit-Code 137)"]
    assert seen[0].details["status"] == "Exited (137)"


def test_parse_exit_facts_reads_restart_count_and_image_and_still_accepts_the_short_form():
    facts = parse_exit_facts(
        "/db|false|1|false|2026-09-30T10:00:01.5Z|3|postgres:16|boom | wrapper\n"
        "/old|true|137|false|2026-09-30T10:00:00Z|\n"
    )
    assert facts["db"]["restart_count"] == 3 and facts["db"]["image"] == "postgres:16"
    assert facts["db"]["error"] == "boom | wrapper"
    assert "restart_count" not in facts["old"] and facts["old"]["oom_killed"] is True


def test_facts_line_is_written_by_the_code():
    fact = {
        "oom_killed": False, "exit_code": 1, "restart_count": 1, "image": "nginx:1.27",
        "finished_at": "2026-09-30T10:00:01.123456789Z",
    }
    assert facts_line(fact) == "Exit-Code 1 · OOMKilled=false · 1 Neustart · Image nginx:1.27 · beendet 2026-09-30 10:00:01 UTC"
    assert facts_line({"oom_killed": True, "exit_code": 137}) == "Exit-Code 137 · OOMKilled=true"


def test_facts_notice_one_line_per_container_and_empty_without_facts():
    cause = object()
    causes = {("h", "db"): ("db", "pve1", cause), ("h", "web"): ("web", "pve1", cause)}
    assert facts_notice(causes, {}) == ""
    one = facts_notice(causes, {("h", "db"): {"oom_killed": False, "exit_code": 1}})
    assert one == "Fakten (docker inspect): Exit-Code 1 · OOMKilled=false"
    both = facts_notice(causes, {("h", "db"): {"oom_killed": False, "exit_code": 1}, ("h", "web"): {"oom_killed": True, "exit_code": 137}})
    assert both.splitlines() == [
        "Fakten zu db @ pve1: Exit-Code 1 · OOMKilled=false",
        "Fakten zu web @ pve1: Exit-Code 137 · OOMKilled=true",
    ]


_CRASH = [{"oom_killed": False, "exit_code": 1}]
_OOM = [{"oom_killed": True, "exit_code": 137}]


@pytest.mark.parametrize(
    "text, facts, tags, expected_text, expected_dropped",
    [
        # Selbstwiderspruch aus dem Live-Fund
        ("Lagebericht: OOMKilled=true, da OOMKilled=false.", _CRASH, {"echter Absturz"}, "", 1),
        # falscher OOMKilled-Wert
        ("Lagebericht: Der Container hatte OOMKilled=true. Er wurde neu gestartet.", _CRASH, {"echter Absturz"},
         "Lagebericht: Er wurde neu gestartet.", 1),
        # falscher Exit-Code
        ("Exit-Code 137 ist aufgetreten. Ursache ist ein Fehler im Programm.", _CRASH, {"echter Absturz"},
         "Ursache ist ein Fehler im Programm.", 1),
        # Speichermangel trotz OOMKilled=false
        ("Der Container hatte Speichermangel.", _CRASH, {"echter Absturz"}, "", 1),
        # "von aussen beendet" bei einem echten Absturz
        ("Der Container wurde von aussen beendet.", _CRASH, {"echter Absturz"}, "", 1),
        # ... auch mit "ß" (so steht es in der festen Einordnung, das Modell schreibt es oft nach)
        ("Der Container wurde von außen beendet.", _CRASH, {"echter Absturz"}, "", 1),
        # englische Verneinung bleibt stehen
        ("The container was not OOM killed.", _CRASH, {"echter Absturz"}, "The container was not OOM killed.", 0),
        # Verneinung bleibt stehen
        ("Es liegt kein Speichermangel vor (OOMKilled=false).", _CRASH, {"echter Absturz"},
         "Es liegt kein Speichermangel vor (OOMKilled=false).", 0),
        # Richtige Aussagen bleiben
        ("Lagebericht: Exit-Code 1 mit OOMKilled=false, der Prozess ist selbst gestorben.", _CRASH, {"echter Absturz"},
         "Lagebericht: Exit-Code 1 mit OOMKilled=false, der Prozess ist selbst gestorben.", 0),
        ("Der Speicher reichte nicht (OOMKilled=true, Exit-Code 137).", _OOM, {"Speichermangel"},
         "Der Speicher reichte nicht (OOMKilled=true, Exit-Code 137).", 0),
        ("Es war kein Speichermangel.", _OOM, {"Speichermangel"}, "", 1),
        # mehrere Zeilen und Leerzeilen bleiben erhalten
        ("Lagebericht: Alles klar.\n\nZweite Zeile.", _CRASH, {"echter Absturz"}, "Lagebericht: Alles klar.\n\nZweite Zeile.", 0),
    ],
)
def test_sentences_contradicting_the_facts_are_dropped(text, facts, tags, expected_text, expected_dropped):
    assert drop_contradictions(text, facts, expected=1, tags=tags) == (expected_text, expected_dropped)


def test_without_facts_only_self_contradictions_are_dropped():
    assert drop_contradictions("OOMKilled=true, da OOMKilled=false.", [], expected=1, tags=set()) == ("", 1)
    assert drop_contradictions("Der Container hatte OOMKilled=true.", [], expected=1, tags=set()) == (
        "Der Container hatte OOMKilled=true.", 0,
    )


def test_incomplete_facts_do_not_check_codes_of_other_containers():
    # Fakten nur fuer einen von zwei Containern: Exit-Code des zweiten darf stehen bleiben
    assert drop_contradictions("web: Exit-Code 2.", _CRASH, expected=2, tags={"echter Absturz"}) == ("web: Exit-Code 2.", 0)


def test_mixed_facts_allow_both_oom_values_in_one_sentence():
    mixed = _CRASH + _OOM
    text = "db hatte OOMKilled=false, web hatte OOMKilled=true."
    assert drop_contradictions(text, mixed, expected=2, tags={"echter Absturz", "Speichermangel"}) == (text, 0)
