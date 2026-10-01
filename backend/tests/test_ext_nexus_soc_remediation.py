from __future__ import annotations

import sys
from pathlib import Path

import pytest

NEXUS_SOC_SRC = Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"
if str(NEXUS_SOC_SRC) not in sys.path:
    sys.path.insert(0, str(NEXUS_SOC_SRC))

from nodvard_deck_ext_nexus_soc.remediation import DEFAULT_FORBIDDEN_HOST_KEYWORDS, looks_like_host_not_container  # noqa: E402


def test_non_docker_restart_command_is_never_flagged():
    assert looks_like_host_not_container("uptime") is None
    assert looks_like_host_not_container("systemctl restart nginx") is None


def test_ordinary_container_name_passes():
    assert looks_like_host_not_container("docker restart nextcloud-app") is None


def test_hostname_lookalike_container_name_is_flagged():
    assert looks_like_host_not_container("docker restart power_server") == "power_server"
    assert looks_like_host_not_container("docker restart nas") == "nas"


def test_substring_match_is_intentional_not_word_boundary():
    """Das Vorgaengersystem prueft `kw in c_target.lower()`, kein Wortgrenzen-Match -- "pve1"
    wird ueber "pve" erkannt. Absichtlich woertlich uebernommen, nicht verschaerft."""
    assert looks_like_host_not_container("docker restart pve1-helper") == "pve1-helper"


def test_custom_keyword_list_replaces_default():
    assert looks_like_host_not_container("docker restart power", forbidden_keywords=("only-this",)) is None
    assert looks_like_host_not_container("docker restart only-this-one", forbidden_keywords=("only-this",)) == "only-this-one"


def test_default_list_is_the_generic_starting_value():
    assert set(DEFAULT_FORBIDDEN_HOST_KEYWORDS) == {
        "pve", "proxmox", "host", "server",
        "node", "router", "gateway", "nas",
    }


# ---------------------------------------------------------------------------
# Risiko eines KI-Vorschlags: nur ein schlichter Neustart eines Batch-Containers ist MEDIUM
# ---------------------------------------------------------------------------

from nodvard_deck_ext_nexus_soc.remediation import is_routine_restart, restart_risk_eligible  # noqa: E402

_BATCH = {("h-docker", "nginx-proxy"), ("h-docker", "redis"), ("h-pi", "pihole")}


@pytest.mark.parametrize("command", ["docker restart nginx-proxy", "  docker restart nginx-proxy  ", "docker restart redis"])
def test_plain_restart_of_a_batch_container_on_its_host_is_routine(command):
    assert is_routine_restart(command, "h-docker", _BATCH) is True


@pytest.mark.parametrize(
    "command, host_id",
    [
        ("docker restart nginx-proxy", "h-pi"),  # falscher Host
        ("docker restart pihole", "h-docker"),  # Container gehoert zu einem anderen Host
        ("docker restart other", "h-docker"),  # nicht aus dem Batch
        ("docker restart -t 0 nginx-proxy", "h-docker"),  # Flag
        ("docker restart nginx-proxy redis", "h-docker"),  # zweites Argument
        ("docker restart nginx-proxy; reboot", "h-docker"),
        ("docker restart nginx-proxy && reboot", "h-docker"),
        ("docker restart nginx-proxy | tee x", "h-docker"),
        ("docker restart $(echo redis)", "h-docker"),  # Subshell
        ("docker restart `echo redis`", "h-docker"),
        ("docker restart $NAME", "h-docker"),
        ("docker restart nginx-proxy\nreboot", "h-docker"),
        ("docker  restart nginx-proxy", "h-docker"),  # doppeltes Leerzeichen
        ("Docker restart nginx-proxy", "h-docker"),  # Grossschreibung
        ("DOCKER RESTART nginx-proxy", "h-docker"),
        ("docker rm nginx-proxy", "h-docker"),  # anderer Befehl
        ("docker stop nginx-proxy", "h-docker"),
        ("sudo docker restart nginx-proxy", "h-docker"),
        ("docker restart", "h-docker"),
        ("", "h-docker"),
    ],
)
def test_anything_else_is_not_routine(command, host_id):
    assert is_routine_restart(command, host_id, _BATCH) is False


def test_container_name_must_match_the_strict_pattern_even_if_in_the_batch():
    batch = {("h", "-rf"), ("h", "a b"), ("h", "x;y")}
    assert is_routine_restart("docker restart -rf", "h", batch) is False
    assert is_routine_restart("docker restart x;y", "h", batch) is False


def _fact(exit_code: int, oom: bool = False, running: bool = False) -> dict:
    return {"oom_killed": oom, "exit_code": exit_code, "running": running, "finished_at": "", "error": ""}


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(fact=_fact(1)), True),  # echter Absturz
        (dict(fact=_fact(2)), True),
        (dict(fact=_fact(137, oom=True)), True),  # OOM
        (dict(fact=_fact(139, oom=True)), True),
        (dict(fact=_fact(137)), False),  # docker kill
        (dict(fact=_fact(139)), False),  # SIGSEGV: echter Absturz, aber bewusst konservativ (Signal ohne OOM)
        (dict(fact=_fact(129)), False),  # kill -s HUP
        (dict(fact=_fact(130)), False),  # kill -s INT
        (dict(fact=_fact(0)), False),  # sauber beendet / pausiert
        (dict(fact=_fact(143)), False),  # SIGTERM
        (dict(fact=_fact(1, running=True)), False),  # laeuft inzwischen wieder
        (dict(fact=None), False),  # ohne Fakten im Zweifel HOCH
        (dict(is_crash=False, fact=_fact(1)), False),  # manueller Stopp/Pause
        (dict(fact=_fact(1), resumed=True), False),  # nach Neustart
        (dict(fact=_fact(1), attempts=1), False),  # Wiederholung
    ],
)
def test_only_real_crashes_are_eligible_for_medium_risk(kwargs, expected):
    base = dict(is_crash=True, resumed=False, attempts=0, fact=None)
    assert restart_risk_eligible(**{**base, **kwargs}) is expected


# ---------------------------------------------------------------------------
# Feste Einordnung der Ursache (vom Code, nicht von der KI)
# ---------------------------------------------------------------------------

from nodvard_deck_ext_nexus_soc.remediation import (  # noqa: E402
    NO_FACTS_CAUSE,
    cause_notice,
    cause_prompt_line,
    cause_tag_for_command,
    classify_exit_cause,
)

_CRASH = {"status": "Exited (1) 2s ago", "state": "exited", "is_crash": True}


@pytest.mark.parametrize(
    "details, fact, label, tag",
    [
        (_CRASH, _fact(1), "echter Absturz (Exit-Code 1)", "echter Absturz"),
        (_CRASH, _fact(2), "echter Absturz (Exit-Code 2)", "echter Absturz"),
        (_CRASH, _fact(137, oom=True), "Speichermangel (OOMKilled)", "Speichermangel"),
        (_CRASH, _fact(137), "von außen beendet (Signal/Kill)", "von außen beendet"),
        (
            {"status": "Exited (143) 2s ago", "state": "exited", "is_crash": False},
            _fact(143), "manuell gestoppt", "manuell gestoppt",
        ),
        (
            {"status": "Exited (0) 2s ago", "state": "exited", "is_crash": False},
            _fact(0), "manuell gestoppt", "manuell gestoppt",
        ),
        (
            {"status": "Up 3 minutes (Paused)", "state": "paused", "is_crash": False},
            _fact(0, running=True), "pausiert", "pausiert",
        ),
        ({"status": "Up 3 minutes (Paused)", "is_crash": False}, None, "pausiert", "pausiert"),  # ohne Fakten
        (_CRASH, None, "Ursache unklar (keine Beendigungs-Fakten)", "Ursache unklar"),
    ],
)
def test_cause_is_fixed_by_the_facts(details, fact, label, tag):
    cause = classify_exit_cause(details, fact)
    assert cause.label == label and cause.tag == tag
    assert cause.sentence.endswith(".")


def test_cause_sentences_do_not_contradict_the_facts():
    assert "OOMKilled=true" in classify_exit_cause(_CRASH, _fact(137, oom=True)).sentence
    kill = classify_exit_cause(_CRASH, _fact(137)).sentence
    assert "Signal 9" in kill and "nicht wegen Speichermangel" in kill
    crash = classify_exit_cause(_CRASH, _fact(1)).sentence
    assert "von außen" not in crash and "Signal" in crash  # "kein Signal"
    assert classify_exit_cause(_CRASH, None) is NO_FACTS_CAUSE


def test_cause_prompt_line_and_notice():
    cause = classify_exit_cause(_CRASH, _fact(137, oom=True))
    line = cause_prompt_line("db", "docker", cause)
    assert line.startswith("FESTSTEHENDE EINORDNUNG (vom System ermittelt, nicht ändern): db auf docker: Speichermangel (OOMKilled).")
    one = {("h1", "db"): ("db", "docker", cause)}
    assert cause_notice(one) == "Ursache laut System: Speichermangel (OOMKilled)"
    two = {**one, ("h1", "web"): ("web", "docker", classify_exit_cause(_CRASH, _fact(1)))}
    assert cause_notice(two).splitlines() == [
        "Ursache laut System (db @ docker): Speichermangel (OOMKilled)",
        "Ursache laut System (web @ docker): echter Absturz (Exit-Code 1)",
    ]
    assert cause_notice({}) == ""
    assert cause_tag_for_command("docker restart web", "h1", two) == "echter Absturz"
    assert cause_tag_for_command("docker restart db", "h1", two) == "Speichermangel"
    assert cause_tag_for_command("docker restart db", "andere", two) == "Speichermangel / echter Absturz"
    assert cause_tag_for_command("docker restart db", "h1", {}) is None


@pytest.mark.parametrize("code", [132, 134, 135, 136, 139])
def test_fehler_signale_gelten_als_absturz_nicht_als_eingriff_von_aussen(code):
    cause = classify_exit_cause(_CRASH, _fact(code))
    assert cause.label.startswith("echter Absturz")
    assert "von außen" not in cause.label


@pytest.mark.parametrize("code", [129, 130, 137, 131])
def test_andere_signale_bleiben_von_aussen_beendet(code):
    assert classify_exit_cause(_CRASH, _fact(code)).label.startswith("von außen beendet")
