"""Pruefung nach der Aktion (after_check.py): Laeuft ein Container nach "starten"/"neu starten"/"Stack neu starten"
wirklich -- und bleibt er laufen?"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "docker" / "src"))

from nodvard_deck_ext_docker import after_check as ac
from nodvard_deck_ext_docker import capabilities as caps
from nodvard_deck_ext_docker import image_apply as ia
from nodvard_sdk import ActionRequest, Actor

ID_A = "a" * 64
ID_B = "b" * 64


def _line(name: str, status: str = "running", *, code: int = 0, health: str | None = None, restarts: int = 0,
          started: str = "2026-10-08T01:00:00Z") -> str:
    return json.dumps({"name": f"/{name}", "image_id": "sha256:" + "c" * 64, "status": status, "exit_code": code,
                       "health": health, "restarts": restarts, "started": started}) + "\n"


def _snap(*lines: str) -> ia.Snapshot:
    return ia.parse_verify("".join(lines))


def _after_check_modules() -> list:
    """`capabilities` importiert `after_check` erst beim Aufruf. Haben andere Tests die Erweiterung zwischendurch neu
    geladen (`sys.modules`), ist das ein anderes Modul als `ac` -- die Konstanten muessen dann auch dort gesetzt werden,
    sonst wartet die Pruefung echte 15 s (und liest sechsmal statt zweimal)."""
    current = importlib.import_module("nodvard_deck_ext_docker.after_check")
    return [ac] if current is ac else [ac, current]


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    for module in _after_check_modules():
        monkeypatch.setattr(module, "CHECK_STABLE_S", 0)
        monkeypatch.setattr(module, "CHECK_INTERVAL_S", 0)


@pytest.mark.parametrize(("lines", "final", "ok", "fragment"), [
    ([_line("web")], False, True, "Container läuft stabil."),
    ([_line("web", health="healthy")], False, True, "Container läuft und ist gesund."),
    ([_line("web", "exited", code=1)], False, False, "Container läuft nicht (beendet, Exit-Code 1)"),
    ([_line("web", "exited", code=0)], False, True, "Bei einem Container für einmalige Aufgaben ist das normal"),
    ([_line("web", "dead", code=0)], False, False, "Container läuft nicht (beendet, Exit-Code 0)"),
    ([_line("web", health="unhealthy")], True, False, "meldet „ungesund“"),
    ([_line("web", "restarting")], True, False, ia.CRASH_LOOP_TEXT),
    ([_line("web", health="starting")], True, True, "meldet aber noch „startet“"),
    ([_line("web", "paused")], True, False, "Zustand: paused"),
    ([], True, False, "Container nicht mehr vorhanden."),
])
def test_judge_running(lines, final, ok, fragment):
    verdict = ac.judge_running(_snap(*lines), final, expected=1)
    assert verdict is not None and verdict.ok is ok and fragment in verdict.text


def test_judge_running_waits_while_it_is_still_unclear():
    assert ac.judge_running(_snap(_line("web", "restarting")), False, expected=1) is None
    assert ac.judge_running(_snap(_line("web", health="starting")), False, expected=1) is None
    assert ac.judge_running(_snap(), False, expected=1) is None
    # "ungesund" kann sich erholen (Healthcheck ohne Anlaufzeit): erst zum Ende ein Urteil.
    assert ac.judge_running(_snap(_line("web", health="unhealthy")), False, expected=1) is None


def test_judge_running_names_the_member_of_a_stack():
    verdict = ac.judge_running(_snap(_line("app"), _line("db", "exited", code=3)), False, expected=2)
    assert verdict is not None and not verdict.ok and verdict.text.startswith("db: Container läuft nicht")
    assert ac.judge_running(_snap(_line("app"), _line("db")), False, expected=2).text == "Alle 2 Container laufen stabil."
    gone = ac.judge_running(_snap(_line("app")), True, expected=2)
    assert gone is not None and gone.text == "1 von 2 Containern nicht mehr vorhanden."
    # Ein Absturz wird nicht erst nach Fristende gemeldet, weil ein anderer Container fehlt.
    crashed = ac.judge_running(_snap(_line("db", "exited", code=2)), False, expected=2)
    assert crashed is not None and crashed.text.startswith("db: Container läuft nicht")
    job = ac.judge_running(_snap(_line("app"), _line("migrate", "exited", code=0)), False, expected=2)
    assert job is not None and job.ok and job.text == "1 von 2 Containern laufen stabil; ohne Fehler beendet (Exit-Code 0): migrate."
    looping = ac.judge_running(_snap(_line("app"), _line("db", "restarting")), True, expected=2)
    assert looping is not None and looping.text == "db: " + ia.CRASH_LOOP_TEXT


def _reader(*answers):
    """Liest nacheinander die Antworten (eine Exception wird geworfen); die letzte bleibt stehen."""
    queue = list(answers)

    async def read():
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return read


async def _settle(read, *, timeout_s=0.2):
    return await ac.wait_settled(read, lambda s, final: ac.judge_running(s, final, expected=1),
                                 timeout_s=timeout_s, interval_s=0, stable_s=0)


@pytest.mark.asyncio
async def test_two_quiet_reads_are_a_success():
    verdict, snap = await _settle(_reader(_snap(_line("web"))))
    assert verdict.ok and snap is not None


@pytest.mark.asyncio
async def test_a_new_start_between_the_reads_is_a_crash_loop():
    verdict, _ = await _settle(_reader(_snap(_line("web")), _snap(_line("web", started="2026-10-08T01:00:09Z"))))
    assert not verdict.ok and verdict.crashed and verdict.text == ia.CRASH_LOOP_TEXT
    verdict, _ = await _settle(_reader(_snap(_line("web")), _snap(_line("web", restarts=1))))
    assert verdict.text == ia.CRASH_LOOP_TEXT


@pytest.mark.asyncio
async def test_a_short_outage_does_not_count_but_silence_until_the_end_does():
    verdict, _ = await _settle(_reader(OSError("weg"), _snap(_line("web"))))
    assert verdict.ok
    verdict, snap = await _settle(_reader(OSError("weg")), timeout_s=0.05)
    assert not verdict.ok and snap is None and "nicht prüfbar" in verdict.text


@pytest.mark.asyncio
async def test_a_crash_after_the_first_good_read_is_reported():
    verdict, _ = await _settle(_reader(_snap(_line("web")), _snap(_line("web", "exited", code=137))))
    assert not verdict.ok and "Exit-Code 137" in verdict.text


def test_state_commands_only_take_names_and_ids():
    assert ac.cmd_state(["web", ID_A]).endswith(f" web {ID_A}")
    for bad in (["x; reboot"], ["-f"], [], ["a b"], ["web\n"]):
        with pytest.raises(ValueError):
            ac.cmd_state(bad)
    with pytest.raises(ValueError):
        ac.cmd_stack_running("x; reboot")


class _Exec:
    def __init__(self, answers: dict[str, list[tuple[int, str, str]]]) -> None:
        self.answers = answers
        self.commands: list[str] = []

    async def run(self, host, command, *, timeout_s=60):
        self.commands.append(command)
        for prefix, queue in self.answers.items():
            if command.startswith(prefix):
                code, out, err = queue.pop(0) if len(queue) > 1 else queue[0]
                return SimpleNamespace(exit_code=code, stdout=out, stderr=err, duration_ms=7)
        raise AssertionError(f"unerwarteter Befehl: {command}")


def _ctx(exec_handle: _Exec) -> SimpleNamespace:
    host = SimpleNamespace(id="h1", name="docker", display_name=None)

    async def get(host_id):
        return host if host_id == "h1" else None

    return SimpleNamespace(exec=exec_handle, hosts=SimpleNamespace(get=get))


def _req(action_type: str, payload: dict) -> ActionRequest:
    return ActionRequest(action_type=action_type, payload=payload, host_ref="h1",
                         proposed_by=Actor.user("u1", "nico"), reason="Test")


@pytest.mark.asyncio
async def test_a_restart_that_crashes_right_after_is_a_failure_with_the_reason():
    exec_handle = _Exec({
        "docker restart web": [(0, "web\n", "")],
        "docker container inspect": [(0, _line("web"), ""), (0, _line("web", "exited", code=1), "")],
    })
    result = await caps.DockerContainerExecutor(_ctx(exec_handle)).execute(_req("container.restart", {"container": "web"}))
    assert result.success is False
    assert result.output == "Container 'web' neu gestartet."
    assert result.error == "Prüfung danach: Container läuft nicht (beendet, Exit-Code 1). Logs über den Knopf „Logs“."
    assert result.detail["check"]["ok"] is False


@pytest.mark.asyncio
async def test_a_start_that_stays_up_says_so():
    exec_handle = _Exec({"docker start web": [(0, "web\n", "")], "docker container inspect": [(0, _line("web"), "")]})
    result = await caps.DockerContainerExecutor(_ctx(exec_handle)).execute(_req("container.start", {"container": "web"}))
    assert result.success is True
    assert result.output == "Container 'web' gestartet.\nPrüfung danach: Container läuft stabil."
    assert sum(c.startswith("docker container inspect") for c in exec_handle.commands) == 2


@pytest.mark.asyncio
async def test_stop_and_a_failed_start_are_not_checked():
    exec_handle = _Exec({"docker start web": [(1, "", "Error: port is already allocated")], "docker stop web": [(0, "web\n", "")],
                         "docker inspect": [(0, "f" * 64 + "\n", "")]})
    executor = caps.DockerContainerExecutor(_ctx(exec_handle))
    failed = await executor.execute(_req("container.start", {"container": "web"}))
    assert failed.success is False and failed.error == "Error: port is already allocated"
    assert (await executor.execute(_req("container.stop", {"container": "web"}))).success
    assert not any(c.startswith("docker container inspect") for c in exec_handle.commands)


@pytest.mark.asyncio
async def test_a_stack_restart_checks_only_what_ran_before():
    exec_handle = _Exec({
        "docker ps -q --no-trunc": [(0, f"{ID_A}\n{ID_B}\n", "")],
        "docker compose -p immich restart": [(0, "", "")],
        "docker container inspect": [(0, _line("immich-server") + _line("immich-db"), "")],
    })
    result = await caps.DockerMaintenanceExecutor(_ctx(exec_handle)).execute(
        _req("docker.stack_restart", {"project": "immich"}))
    assert result.success is True
    assert result.output == "Stack 'immich' neu gestartet.\nPrüfung danach: Alle 2 Container laufen stabil."
    inspect = next(c for c in exec_handle.commands if c.startswith("docker container inspect"))
    assert inspect.endswith(f" {ID_A} {ID_B}")


@pytest.mark.asyncio
async def test_a_stack_member_that_dies_after_the_restart_fails_the_action():
    exec_handle = _Exec({
        "docker ps -q --no-trunc": [(0, f"{ID_A}\n{ID_B}\n", "")],
        "docker compose -p immich restart": [(0, "", "")],
        "docker container inspect": [(0, _line("immich-server") + _line("immich-db", "exited", code=1), "")],
    })
    result = await caps.DockerMaintenanceExecutor(_ctx(exec_handle)).execute(
        _req("docker.stack_restart", {"project": "immich"}))
    assert result.success is False and result.error.startswith("Prüfung danach: immich-db: Container läuft nicht")


@pytest.mark.asyncio
async def test_a_stack_that_was_stopped_is_not_checked_and_says_so():
    exec_handle = _Exec({"docker ps -q --no-trunc": [(0, "", "")], "docker compose -p immich restart": [(0, "", "")]})
    result = await caps.DockerMaintenanceExecutor(_ctx(exec_handle)).execute(
        _req("docker.stack_restart", {"project": "immich"}))
    assert result.success is True
    assert result.output == "Stack 'immich' neu gestartet.\nPrüfung danach übersprungen: vorher lief kein Container des Stacks."
    exec_handle = _Exec({"docker ps -q --no-trunc": [(1, "", "Cannot connect")], "docker compose -p immich restart": [(0, "", "")]})
    result = await caps.DockerMaintenanceExecutor(_ctx(exec_handle)).execute(
        _req("docker.stack_restart", {"project": "immich"}))
    assert result.output.endswith("Prüfung danach nicht möglich: die laufenden Container ließen sich vorher nicht lesen.")


def test_the_stack_list_leaves_out_compose_run_containers():
    assert ac.cmd_stack_running("immich") == (
        "docker ps -q --no-trunc --filter label=com.docker.compose.project=immich "
        "--filter label=com.docker.compose.oneoff=False"
    )


@pytest.mark.asyncio
async def test_one_restart_after_the_start_is_tolerated_and_named():
    exec_handle = _Exec({
        "docker restart web": [(0, "web\n", "")],
        "docker container inspect": [(0, _line("web"), ""), (0, _line("web", started="2026-10-08T01:00:05Z", restarts=1), "")],
    })
    result = await caps.DockerContainerExecutor(_ctx(exec_handle)).execute(_req("container.restart", {"container": "web"}))
    assert result.success is True
    assert result.output == ("Container 'web' neu gestartet.\nPrüfung danach: Container läuft stabil." + ac.ONE_RESTART_NOTE)


@pytest.mark.asyncio
async def test_a_second_restart_is_a_crash_loop_and_names_the_container_of_a_stack():
    exec_handle = _Exec({
        "docker ps -q --no-trunc": [(0, f"{ID_A}\n{ID_B}\n", "")],
        "docker compose -p immich restart": [(0, "", "")],
        "docker container inspect": [
            (0, _line("immich-server") + _line("immich-db"), ""),
            (0, _line("immich-server") + _line("immich-db", started="2026-10-08T01:00:05Z", restarts=1), ""),
            (0, _line("immich-server") + _line("immich-db", started="2026-10-08T01:00:09Z", restarts=2), ""),
        ],
    })
    result = await caps.DockerMaintenanceExecutor(_ctx(exec_handle)).execute(
        _req("docker.stack_restart", {"project": "immich"}))
    assert result.success is False
    assert result.error == f"Prüfung danach: immich-db: {ia.CRASH_LOOP_TEXT}"


@pytest.mark.asyncio
async def test_a_healthcheck_that_turns_healthy_after_unhealthy_is_a_success():
    exec_handle = _Exec({
        "docker start web": [(0, "web\n", "")],
        "docker container inspect": [
            (0, _line("web", health="starting"), ""), (0, _line("web", health="unhealthy"), ""),
            (0, _line("web", health="healthy"), ""),
        ],
    })
    result = await caps.DockerContainerExecutor(_ctx(exec_handle)).execute(_req("container.start", {"container": "web"}))
    assert result.success is True and result.output.endswith("Prüfung danach: Container läuft und ist gesund.")


@pytest.mark.asyncio
async def test_docker_not_answering_is_not_a_missing_container(monkeypatch):
    for module in _after_check_modules():
        monkeypatch.setattr(module, "CHECK_TIMEOUT_S", 0.05)
    exec_handle = _Exec({"docker start web": [(0, "web\n", "")],
                         "docker container inspect": [(1, "", "Cannot connect to the Docker daemon")]})
    result = await caps.DockerContainerExecutor(_ctx(exec_handle)).execute(_req("container.start", {"container": "web"}))
    assert result.success is False and "nicht prüfbar" in result.error
    exec_handle = _Exec({"docker start web": [(0, "web\n", "")],
                         "docker container inspect": [(1, "", "Error: No such container: web")]})
    result = await caps.DockerContainerExecutor(_ctx(exec_handle)).execute(_req("container.start", {"container": "web"}))
    assert result.success is False and result.error == "Prüfung danach: Container nicht mehr vorhanden."
