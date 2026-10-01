from __future__ import annotations

import sys
from pathlib import Path

import pytest

NEXUS_SOC_SRC = Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"
if str(NEXUS_SOC_SRC) not in sys.path:
    sys.path.insert(0, str(NEXUS_SOC_SRC))

from nodvard_deck_ext_nexus_soc.watcher import ContainerTransition, DockerWatcher  # noqa: E402


class _FakeHost:
    def __init__(self, id: str, name: str) -> None:
        self.id = id
        self.name = name


class _FakeExecResult:
    def __init__(self, exit_code: int, stdout: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = ""


class _FakeSettingsHandle:
    def __init__(self, data: dict) -> None:
        self._data = data

    async def get(self) -> dict:
        return self._data


class _FakeHostsHandle:
    def __init__(self, hosts: list[_FakeHost]) -> None:
        self._hosts = hosts

    async def list(self, *, tag: str | None = None) -> list[_FakeHost]:
        return self._hosts


class _FakeExecHandle:
    def __init__(self, outputs: dict, inspect: dict | None = None) -> None:
        self._outputs = outputs
        self._inspect = inspect
        self.commands: list[str] = []

    async def run(self, host, command: str, *, timeout_s: int = 60):  # noqa: ANN001
        self.commands.append(command)
        if command.startswith("docker inspect"):
            if self._inspect is None:
                return _FakeExecResult(1, "")
            return self._inspect[host.id]
        return self._outputs[host.id]


class _FakeCtx:
    def __init__(self, *, settings: dict, hosts: list[_FakeHost], outputs: dict, inspect: dict | None = None) -> None:
        self.settings = _FakeSettingsHandle(settings)
        self.hosts = _FakeHostsHandle(hosts)
        self.exec = _FakeExecHandle(outputs, inspect)


def _ps_line(name: str, state: str, status: str) -> str:
    return f"{name}|{state}|{status}"


@pytest.mark.asyncio
async def test_first_observation_is_baseline_only_no_transition():
    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("nginx", "running", "Up 2 hours"))},
    )
    transitions: list[ContainerTransition] = []

    async def _capture(t: ContainerTransition) -> None:
        transitions.append(t)

    watcher = DockerWatcher(ctx, on_transition=_capture)
    await watcher.tick()
    assert transitions == []


@pytest.mark.asyncio
async def test_crash_transition_is_detected_on_second_tick():
    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("nginx", "running", "Up 2 hours"))},
    )
    transitions: list[ContainerTransition] = []

    async def _capture(t: ContainerTransition) -> None:
        transitions.append(t)

    watcher = DockerWatcher(ctx, on_transition=_capture)
    await watcher.tick()

    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps_line("nginx", "exited", "Exited (1) 2 seconds ago"))
    await watcher.tick()

    assert len(transitions) == 1
    assert transitions[0].target == "nginx"
    assert transitions[0].is_crash is True


@pytest.mark.asyncio
async def test_manual_stop_exit_0_is_not_a_crash():
    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("nginx", "running", "Up 2 hours"))},
    )
    transitions: list[ContainerTransition] = []

    async def _capture(t: ContainerTransition) -> None:
        transitions.append(t)

    watcher = DockerWatcher(ctx, on_transition=_capture)
    await watcher.tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps_line("nginx", "exited", "Exited (0) 1 second ago"))
    await watcher.tick()

    assert len(transitions) == 1
    assert transitions[0].is_crash is False


@pytest.mark.asyncio
async def test_recovery_does_not_fire_a_transition():
    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("nginx", "exited", "Exited (1)"))},
    )
    transitions: list[ContainerTransition] = []

    async def _capture(t: ContainerTransition) -> None:
        transitions.append(t)

    watcher = DockerWatcher(ctx, on_transition=_capture)
    await watcher.tick()  # baseline: exited
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps_line("nginx", "running", "Up 1 second"))
    await watcher.tick()  # recovery

    assert transitions == []


@pytest.mark.asyncio
async def test_suppressed_host_is_skipped_entirely():
    host = _FakeHost("h1", "pihole")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker", "suppressed_hosts": ["pihole"]},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("dns", "running", "Up"))},
    )
    calls = {"n": 0}

    async def _capture(t):  # noqa: ANN001
        calls["n"] += 1

    watcher = DockerWatcher(ctx, on_transition=_capture)
    await watcher.tick()
    assert calls["n"] == 0
    assert watcher._prev_states == {}  # nie einmal betrachtet


@pytest.mark.asyncio
async def test_non_zero_exit_code_from_docker_ps_is_skipped_not_raised():
    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(1, "Permission denied")},
    )

    async def _capture(t):  # noqa: ANN001
        raise AssertionError("darf hier nie aufgerufen werden")

    watcher = DockerWatcher(ctx, on_transition=_capture)
    await watcher.tick()  # darf nicht werfen


# ---------------------------------------------------------------------------
# Letzter bekannter Zustand ueberlebt einen Neustart (state_store)
# ---------------------------------------------------------------------------


class _MemoryStateStore:
    """Wie die dauerhafte Ablage, aber im Speicher -- derselbe Store in zwei Watcher-
    Instanzen steht fuer "Dashboard neu gestartet"."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.saves: list[str] = []
        self.fail_load = False

    async def load(self, host_id: str) -> dict | None:
        if self.fail_load:
            raise RuntimeError("Datenbank nicht erreichbar")
        return self.data.get(host_id)

    async def save(self, host_id: str, data: dict) -> None:
        self.saves.append(host_id)
        self.data[host_id] = data


def _setup(ps_out: str, *, inspect_out: str | None = None, store: _MemoryStateStore | None = None):
    host = _FakeHost("h1", "docker")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, ps_out)},
        inspect=None if inspect_out is None else {"h1": _FakeExecResult(0, inspect_out)},
    )
    seen: list[ContainerTransition] = []

    async def _capture(t: ContainerTransition) -> None:
        seen.append(t)

    return ctx, seen, _capture, store if store is not None else _MemoryStateStore()


def _ps(*lines: tuple[str, str, str]) -> str:
    return "\n".join(_ps_line(*line) for line in lines)


@pytest.mark.asyncio
async def test_crash_during_dashboard_downtime_is_reported_after_restart():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up 2 hours")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert seen == []

    # Dashboard ist aus; waehrenddessen stuerzt nginx ab. Neuer Watcher = leerer Speicher.
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "exited", "Exited (137) 3 minutes ago")))
    restarted = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await restarted.tick()

    assert [(t.target, t.is_crash) for t in seen] == [("nginx", True)]
    await restarted.tick()
    assert len(seen) == 1  # kein zweites Mal


@pytest.mark.asyncio
async def test_manual_stop_during_downtime_is_reported_as_manual_stop():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up 2 hours")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "exited", "Exited (0) 3 minutes ago")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert [t.is_crash for t in seen] == [False]


@pytest.mark.asyncio
async def test_containers_stopped_before_the_restart_are_ignored():
    ctx, seen, capture, store = _setup(_ps(("old-job", "exited", "Exited (1) 2 days ago"), ("nginx", "running", "Up")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert seen == []


@pytest.mark.asyncio
async def test_container_removed_on_purpose_during_downtime_is_no_incident():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up"), ("redis", "running", "Up")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("redis", "running", "Up")))  # nginx nicht mehr da
    restarted = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await restarted.tick()
    await restarted.tick()
    assert seen == []


@pytest.mark.asyncio
async def test_new_container_after_restart_is_only_baseline():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "running", "Up"), ("neu", "exited", "Exited (1)")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert seen == []


@pytest.mark.asyncio
async def test_state_is_written_only_when_it_changes_and_after_the_incident_was_handed_over():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up")))
    order: list[str] = []

    async def capture_ordered(t: ContainerTransition) -> None:
        order.append(f"incident(saves={len(store.saves)})")
        await capture(t)

    watcher = DockerWatcher(ctx, on_transition=capture_ordered, state_store=store)
    for _ in range(3):
        await watcher.tick()
    assert store.saves == ["h1"]  # nur der erste Stand, danach unveraendert

    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "exited", "Exited (1)")))
    await watcher.tick()
    # Der Vorfall ist uebergeben (und dort dauerhaft gemerkt), bevor der neue Stand gespeichert wird.
    assert order == ["incident(saves=1)"]
    assert store.saves == ["h1", "h1"]
    assert store.data["h1"]["containers"]["nginx"]["state"] == "exited"


@pytest.mark.asyncio
async def test_unreadable_state_store_does_not_break_the_watcher():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up")))
    store.fail_load = True
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()  # darf nicht werfen, Baseline wie ohne Ablage
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "exited", "Exited (1)")))
    await watcher.tick()
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# Absturzschleife zwischen zwei Takten: RestartCount (ein `docker inspect` je Takt)
# ---------------------------------------------------------------------------


def _inspect(*rows: tuple[str, int, str]) -> str:
    return "\n".join(f"/{name}|{count}|{cid}" for name, count, cid in rows)


def _inspect_commands(ctx: _FakeCtx) -> list[str]:
    return [c for c in ctx.exec.commands if c.startswith("docker inspect")]


@pytest.mark.asyncio
async def test_restart_count_increase_between_two_ticks_is_a_crash_incident():
    ctx, seen, capture, store = _setup(
        _ps(("nginx", "running", "Up 2 hours")), inspect_out=_inspect(("nginx", 0, "abc123"))
    )
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    assert seen == []

    # Zwischen den Takten abgestuerzt und von der Restart-Policy sofort neu gestartet.
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "running", "Up 3 seconds")))
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 2, "abc123")))
    await watcher.tick()

    assert len(seen) == 1
    assert seen[0].target == "nginx" and seen[0].is_crash is True
    assert seen[0].details["is_crash"] is True
    assert seen[0].details["restart_count"] == 2 and seen[0].details["restarts_since_last_check"] == 2
    assert "2" in seen[0].message

    await watcher.tick()  # Zaehler unveraendert -> kein weiterer Vorfall
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_unchanged_restart_count_is_no_incident():
    ctx, seen, capture, store = _setup(
        _ps(("nginx", "running", "Up 2 hours")), inspect_out=_inspect(("nginx", 3, "abc123"))
    )
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()  # erste Beobachtung: auch ein hoher Zaehler ist nur Baseline
    await watcher.tick()
    assert seen == []


@pytest.mark.asyncio
async def test_restart_count_increase_during_dashboard_downtime_is_reported_after_restart():
    ctx, seen, capture, store = _setup(
        _ps(("nginx", "running", "Up 2 hours")), inspect_out=_inspect(("nginx", 1, "abc123"))
    )
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert store.data["h1"]["containers"]["nginx"]["restarts"] == 1

    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 4, "abc123")))
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert [t.details["restarts_since_last_check"] for t in seen] == [3]


@pytest.mark.asyncio
async def test_recreated_container_with_a_lower_counter_or_new_id_is_no_incident():
    ctx, seen, capture, store = _setup(
        _ps(("nginx", "running", "Up")), inspect_out=_inspect(("nginx", 5, "old-id"))
    )
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 0, "new-id")))  # neu angelegt
    await watcher.tick()
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 1, "new-id")))  # 0 -> 1 im neuen Container
    await watcher.tick()
    assert [t.details["restarts_since_last_check"] for t in seen] == [1]

    # Neue Id mit hoeherem Zaehler als vorher: trotzdem nur Baseline (anderer Container).
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 9, "third-id")))
    await watcher.tick()
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_crash_seen_as_state_change_is_not_reported_twice_because_of_the_counter():
    ctx, seen, capture, store = _setup(
        _ps(("nginx", "running", "Up 2 hours")), inspect_out=_inspect(("nginx", 0, "abc123"))
    )
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "exited", "Exited (1) 1 second ago")))
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 3, "abc123")))
    await watcher.tick()
    assert len(seen) == 1 and "restart_count" not in seen[0].details

    # Die Restart-Policy startet ihn wieder (Zaehler steigt, Vorzustand war "exited"): Erholung, kein Vorfall.
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "running", "Up 1 second")))
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 4, "abc123")))
    await watcher.tick()
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_one_read_only_inspect_per_tick_for_running_containers_only_and_quoted():
    ctx, seen, capture, store = _setup(
        _ps(
            ("nginx", "running", "Up"),
            ("web;touch${IFS}x", "running", "Up"),
            ("old-job", "exited", "Exited (1)"),
            ("loop", "restarting", "Restarting (1)"),
        ),
        inspect_out="",
    )
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    commands = _inspect_commands(ctx)
    assert len(commands) == 1
    assert commands[0].startswith("docker inspect --type container --format '{{.Name}}|{{.RestartCount}}|{{.Id}}' ")
    assert "'web;touch${IFS}x'" in commands[0]
    assert "nginx" in commands[0] and "old-job" not in commands[0]
    assert not any(word in commands[0] for word in ("rm ", "restart ", "stop ", "kill "))

    await watcher.tick()
    assert len(_inspect_commands(ctx)) == 2  # genau einer je Takt


@pytest.mark.asyncio
async def test_no_inspect_call_when_no_container_is_running():
    ctx, seen, capture, store = _setup(_ps(("old-job", "exited", "Exited (1)")), inspect_out="")
    await DockerWatcher(ctx, on_transition=capture, state_store=store).tick()
    assert _inspect_commands(ctx) == []


@pytest.mark.asyncio
async def test_failing_inspect_is_ignored_and_keeps_the_last_counter():
    ctx, seen, capture, store = _setup(
        _ps(("nginx", "running", "Up")), inspect_out=_inspect(("nginx", 1, "abc123"))
    )
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    ctx.exec._inspect["h1"] = _FakeExecResult(1, "Error: permission denied")
    await watcher.tick()  # darf nicht werfen, kein Vorfall
    ctx.exec._inspect["h1"] = _FakeExecResult(0, _inspect(("nginx", 2, "abc123")))
    await watcher.tick()  # der Zaehler wird weiter gegen den letzten bekannten Stand verglichen
    assert [t.details["restarts_since_last_check"] for t in seen] == [1]


# ---------------------------------------------------------------------------
# Kaputter gespeicherter Stand und aufgeraeumte Namen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        {"containers": "kaputt"},
        {"containers": ["nginx"]},
        {"containers": {"nginx": "running"}},
        {"containers": {"nginx": {"state": ["running"]}}},
        {"containers": {"nginx": {"state": "running", "restarts": "x", "id": 5}}},
        "kein-dict",
    ],
)
async def test_broken_saved_state_is_discarded_and_the_watcher_starts_over(broken):
    ctx, seen, capture, store = _setup(_ps(("nginx", "exited", "Exited (1)")))
    store.data["h1"] = broken
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()  # darf nicht werfen; neue Baseline
    assert seen == []
    assert store.data["h1"]["containers"]["nginx"]["state"] == "exited"


@pytest.mark.asyncio
async def test_removed_containers_are_dropped_from_memory_and_saved_state():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up"), ("zufall_abc123", "running", "Up")))
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    assert set(store.data["h1"]["containers"]) == {"nginx", "zufall_abc123"}

    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "running", "Up")))
    await watcher.tick()
    assert set(store.data["h1"]["containers"]) == {"nginx"}
    assert "h1:zufall_abc123" not in watcher._prev_states

    # Taucht der Name spaeter nicht laufend wieder auf, ist das ein neuer Container (Baseline), kein Vorfall.
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", "running", "Up"), ("zufall_abc123", "exited", "Exited (1)")))
    await watcher.tick()
    assert seen == []


@pytest.mark.asyncio
async def test_failed_docker_ps_keeps_the_saved_state_untouched():
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up")))
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(1, "Cannot connect to the Docker daemon")
    await watcher.tick()
    assert set(store.data["h1"]["containers"]) == {"nginx"}
    assert store.saves == ["h1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, status, expected_crash",
    [
        ("paused", "Up 3 minutes (Paused)", False),  # pausiert ist nie ein Absturz
        ("exited", "Exited (1) 2 seconds ago", True),
        ("exited", "Exited (137) 2 seconds ago", True),  # Bewertung (kill vs. OOM) machen die Fakten
        ("exited", "Exited (0) 2 seconds ago", False),
        ("exited", "Exited (143) 2 seconds ago", False),
        ("dead", "Dead", True),
        ("restarting", "Restarting (1) 3 seconds ago", True),
        ("removing", "Removal In Progress", False),
    ],
)
async def test_only_exited_dead_and_restarting_count_as_a_crash(state, status, expected_crash):
    ctx, seen, capture, store = _setup(_ps(("nginx", "running", "Up 2 hours")))
    watcher = DockerWatcher(ctx, on_transition=capture, state_store=store)
    await watcher.tick()
    ctx.exec._outputs["h1"] = _FakeExecResult(0, _ps(("nginx", state, status)))
    await watcher.tick()
    assert [t.is_crash for t in seen] == [expected_crash]
    assert seen[0].details["is_crash"] is expected_crash
