"""Proaktiver Docker-Wächter: fragt `docker ps -a` je überwachtem Host ab und meldet Zustandswechsel und Absturzschleifen."""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .ids import LOGGER
from .incident_text import describe_restart_loop, describe_transition, strip_relative_time

logger = logging.getLogger(f"{LOGGER}.watcher")

_STARTUP_DELAY_S = 60.0
_TICK_S = 8.0
RECOVERY_QUIET_S = 300.0
"""So lange muss ein Container nach einer Störung (nicht laufend oder neu gestartet) ruhig laufen, bis er als erholt gilt
(`on_recovery`). Docker wartet zwischen zwei Neustarts einer Absturzschleife höchstens eine Minute."""


_STATE_KIND = "docker"


class StateStore(Protocol):
    async def load(self, host_id: str) -> dict[str, Any] | None: ...

    async def save(self, host_id: str, data: dict[str, Any]) -> None: ...


class BaselineStateStore:
    """Legt den Container-Stand je Host in `ext_nexus_soc_baselines` (Art "docker") ab, wie Updates und Einbruchschutz."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def load(self, host_id: str) -> dict[str, Any] | None:
        from .patching import load_baseline

        return await load_baseline(self._ctx, host_id, _STATE_KIND)

    async def save(self, host_id: str, data: dict[str, Any]) -> None:
        from .patching import save_baseline

        await save_baseline(self._ctx, host_id, _STATE_KIND, data)


def parse_restart_counts(output: str) -> dict[str, tuple[int, str]]:
    """`docker inspect --format '{{.Name}}|{{.RestartCount}}|{{.Id}}'` -> {Name: (Zähler, Id)}; unpassende Zeilen entfallen."""
    counts: dict[str, tuple[int, str]] = {}
    for line in output.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        name, count, cid = parts[0].strip().lstrip("/"), parts[1].strip(), parts[2].strip()
        if not name or not count.isdigit() or not cid:
            continue
        counts[name] = (int(count), cid)
    return counts


def inspect_exit_command(names: list[str]) -> str:
    """Ein nur lesender `docker inspect` für die Beendigungs-Fakten mehrerer Container."""
    return (
        "docker inspect --type container --format "
        "'{{.Name}}|{{.State.OOMKilled}}|{{.State.ExitCode}}|{{.State.Running}}|{{.State.FinishedAt}}"
        "|{{.RestartCount}}|{{.Config.Image}}|{{.State.Error}}' "
        + " ".join(shlex.quote(n) for n in names)
    )


def parse_exit_facts(output: str) -> dict[str, dict[str, Any]]:
    """Ausgabe von `inspect_exit_command` -> {Name: {oom_killed, exit_code, running, finished_at, error, restart_count, image}}."""
    facts: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        line = line.strip()
        extra: dict[str, Any] = {}
        # `State.Error` steht zuletzt, weil es selbst ein "|" enthalten darf: darum höchstens 7 Trennungen.
        parts = line.split("|", 7)
        if len(parts) == 8 and parts[5].strip().isdigit():
            name, oom, code, running, finished, restarts, image, error = (p.strip() for p in parts)
            extra = {"restart_count": int(restarts), "image": image}
        else:
            # Kurzform mit 6 Feldern (ohne Neustart-Zähler und Image): Dann fehlen `restart_count` und `image`.
            parts = line.split("|", 5)
            if len(parts) != 6:
                continue
            name, oom, code, running, finished, error = (p.strip() for p in parts)
        name = name.lstrip("/")
        if (
            not name
            or oom not in ("true", "false")
            or running not in ("true", "false")
            or not code.lstrip("-").isdigit()
        ):
            continue
        facts[name] = {
            "oom_killed": oom == "true",
            "exit_code": int(code),
            "running": running == "true",
            "finished_at": "" if finished.startswith("0001-") else finished,
            "error": error,
            **extra,
        }
    return facts


@dataclass
class ContainerTransition:
    host: Any  # nodvard_sdk.Host
    target: str
    message: str
    details: dict[str, Any]
    is_crash: bool


class DockerWatcher:
    def __init__(
        self,
        ctx: Any,
        *,
        on_transition: Callable[[ContainerTransition], Awaitable[None]],
        state_store: StateStore | None = None,
        on_recovery: Callable[[Any, str, float], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`on_recovery(host, container, seit)`: ein gestörter Container läuft seit `RECOVERY_QUIET_S` ohne neuen Start wieder.

        `seit` ist Unix-Zeit. Einmal je Erholung."""
        self._ctx = ctx
        self._on_transition = on_transition
        self._state_store = state_store
        self._on_recovery = on_recovery
        self._clock = clock
        self._prev_states: dict[str, str] = {}
        self._prev_restarts: dict[str, int] = {}
        self._prev_ids: dict[str, str] = {}
        # Seit wann ein zuletzt gestörter Container ruhig läuft (Uhr `clock`). Nur im Speicher: Nach einem Neustart des Dashboards
        # beginnt die Beobachtung neu, der Vorfall bleibt dann offen, bis man ihn selbst erledigt.
        self._calm_since: dict[str, float] = {}
        # Container, deren Erholung zuletzt nicht gespeichert werden konnte: Nur der erste Fehler kommt mit Traceback ins Protokoll.
        self._recovery_failed: set[str] = set()
        self._loaded_hosts: set[str] = set()
        self._saved: dict[str, dict[str, Any]] = {}

    async def _load_host_state(self, host: Any) -> None:
        """Holt den beim letzten Lauf gespeicherten Stand eines Hosts (einmal je Start)."""
        if host.id in self._loaded_hosts:
            return
        self._loaded_hosts.add(host.id)
        if self._state_store is None:
            return
        try:
            data = await self._state_store.load(host.id)
        except Exception:  # noqa: BLE001 - ohne gespeicherten Stand geht es mit einer Baseline los
            logger.exception("shield_watcher_state_load_failed")
            return
        try:
            loaded = self._parse_saved_state(host, data)
        except (AttributeError, TypeError, ValueError):
            loaded = None
        if loaded is None:
            # Kaputter gespeicherter Stand: verwerfen und mit einer neuen Baseline anfangen.
            logger.warning("shield_watcher_state_invalid host=%s", host.id)
            return
        # Ein Container, der während der Dashboard-Pause von "running" auf "nicht laufend" gewechselt ist, wird so gemeldet;
        # einer, der schon vorher gestoppt war, nicht.
        self._prev_states.update(loaded[0])
        self._prev_restarts.update(loaded[1])
        self._prev_ids.update(loaded[2])
        self._saved[host.id] = self._snapshot(host)

    @staticmethod
    def _parse_saved_state(host: Any, data: Any) -> tuple[dict[str, str], dict[str, int], dict[str, str]] | None:
        """Prüft den gespeicherten Stand streng; `None` bei jeder Unstimmigkeit, damit nie ein halb gelesener Stand gilt."""
        if data is None:
            return {}, {}, {}
        if not isinstance(data, dict):
            return None
        containers = data.get("containers") or {}
        if not isinstance(containers, dict):
            return None
        states: dict[str, str] = {}
        restarts: dict[str, int] = {}
        ids: dict[str, str] = {}
        for name, entry in containers.items():
            if not isinstance(name, str) or not isinstance(entry, dict) or not isinstance(entry.get("state"), str):
                return None
            key = f"{host.id}:{name}"
            states[key] = entry["state"]
            count, cid = entry.get("restarts"), entry.get("id")
            if count is None and cid is None:
                continue
            if not (isinstance(count, int) and not isinstance(count, bool) and isinstance(cid, str)):
                return None
            restarts[key] = count
            ids[key] = cid
        return states, restarts, ids

    def _snapshot(self, host: Any) -> dict[str, Any]:
        prefix = f"{host.id}:"
        containers: dict[str, Any] = {}
        for key, state in self._prev_states.items():
            if not key.startswith(prefix):
                continue
            entry: dict[str, Any] = {"state": state}
            if key in self._prev_restarts and key in self._prev_ids:
                entry["restarts"] = self._prev_restarts[key]
                entry["id"] = self._prev_ids[key]
            containers[key[len(prefix):]] = entry
        return {"containers": containers}

    async def _save_host_state(self, host: Any) -> None:
        if self._state_store is None:
            return
        snapshot = self._snapshot(host)
        if snapshot == self._saved.get(host.id):
            return
        try:
            await self._state_store.save(host.id, snapshot)
        except Exception:  # noqa: BLE001 - Speichern ist eine Absicherung, kein Pflichtschritt
            logger.exception("shield_watcher_state_save_failed")
            return
        self._saved[host.id] = snapshot

    async def _read_restart_counts(self, host: Any, names: list[str]) -> dict[str, tuple[int, str]]:
        """Ein einziger, nur lesender `docker inspect` für alle übergebenen Container."""
        if not names:
            return {}
        command = (
            "docker inspect --type container --format '{{.Name}}|{{.RestartCount}}|{{.Id}}' "
            + " ".join(shlex.quote(n) for n in names)
        )
        try:
            result = await self._ctx.exec.run(host, command, timeout_s=10)
        except Exception:  # noqa: BLE001 - Anreicherung: ohne Zähler bleibt es beim Zustandsvergleich
            return {}
        # Auch bei Exit-Code != 0 (z. B. ein Container war inzwischen weg) die lesbaren Zeilen nutzen.
        return parse_restart_counts(result.stdout)

    async def tick(self) -> None:
        settings = await self._ctx.settings.get()
        tag = settings.get("docker_host_tag") or "docker"
        suppressed = {h.lower() for h in (settings.get("suppressed_hosts") or [])}

        for host in await self._ctx.hosts.list(tag=tag):
            if host.name.lower() in suppressed:
                continue
            await self._load_host_state(host)
            try:
                result = await self._ctx.exec.run(
                    host, "docker ps -a --format '{{.Names}}|{{.State}}|{{.Status}}'", timeout_s=10
                )
            except Exception:  # noqa: BLE001 - ein nicht erreichbarer Host darf den Takt nicht abreißen
                continue
            if result.exit_code != 0:
                continue

            rows: list[tuple[str, str, str]] = []
            for line in result.stdout.splitlines():
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                name, state, status_str = parts[0].strip(), parts[1].strip().lower(), parts[2].strip()
                if name:
                    rows.append((name, state, status_str))
            restart_counts = await self._read_restart_counts(host, [n for n, st, _s in rows if st == "running"])

            for name, state, status_str in rows:
                key = f"{host.id}:{name}"
                prev_state = self._prev_states.get(key)
                self._prev_states[key] = state

                # Neustarts durch die Restart-Policy seit dem letzten Stand. Ein Container, der zwischen zwei Takten abstürzt und
                # gleich wieder startet, steht bei beiden Takten auf "running"; nur der Zähler verrät es. Ein neu angelegter
                # Container (andere Id) beginnt wieder bei einer Baseline, und ein manuelles `docker restart` erhöht den Zähler
                # nicht: Beides ist kein Vorfall.
                restart_delta = 0
                current = restart_counts.get(name)
                if current is not None:
                    count, container_id = current
                    previous_count = self._prev_restarts.get(key)
                    if (
                        prev_state == "running"
                        and previous_count is not None
                        and self._prev_ids.get(key) == container_id
                        and count > previous_count
                    ):
                        restart_delta = count - previous_count
                    self._prev_restarts[key] = count
                    self._prev_ids[key] = container_id

                await self._track_recovery(host, name, key, prev_state, state, restart_delta)
                if prev_state is None:
                    continue  # erste Beobachtung: nur Baseline, nie ein Vorfall (auch nicht bei Containern, die schon offline starten)
                if prev_state == state:
                    if restart_delta:
                        await self._on_transition(
                            ContainerTransition(
                                host=host,
                                target=name,
                                message=describe_restart_loop(restart_delta),
                                details={
                                    "status": strip_relative_time(status_str),
                                    "state": state,
                                    "is_crash": True,
                                    "restart_count": current[0] if current else None,
                                    "restarts_since_last_check": restart_delta,
                                },
                                is_crash=True,
                            )
                        )
                    continue

                if prev_state == "running" and state != "running":
                    # Nur exited/dead/restarting können ein Absturz sein; "paused" (und alles andere) nie. Ob ein Exit 137 ein
                    # kill oder ein OOM war, klären die Beendigungs-Fakten.
                    is_crash = state in ("dead", "restarting") or (
                        state == "exited"
                        and "exited (0)" not in status_str.lower()
                        and "exited (143)" not in status_str.lower()
                    )
                    event_kind = "crash" if is_crash else ("paused" if state == "paused" else "stopped")
                    await self._on_transition(
                        ContainerTransition(
                            host=host,
                            target=name,
                            message=describe_transition(kind=event_kind, state=state, status=status_str),
                            details={"status": strip_relative_time(status_str), "state": state, "is_crash": is_crash},
                            is_crash=is_crash,
                        )
                    )
                # Erholung (vorher nicht laufend, jetzt laufend) ist bewusst kein Vorfall; die Baseline ist oben schon aktualisiert.

            # Nur Container der aktuellen, erfolgreichen `docker ps -a`-Ausgabe behalten: Entfernte Container (z. B. Zufallsnamen)
            # wachsen so nicht unbegrenzt im Stand, und ein später gleichnamiger, nicht laufender Container gilt als neu
            # (Baseline), nicht als Absturz.
            prefix = f"{host.id}:"
            current = {f"{prefix}{name}" for name, _st, _s in rows}
            for table in (self._prev_states, self._prev_restarts, self._prev_ids, self._calm_since):
                for key in [k for k in table if k.startswith(prefix) and k not in current]:
                    del table[key]

            # Erst jetzt speichern: Die Vorfälle dieses Takts sind übergeben (und dort dauerhaft gemerkt). Stirbt der Prozess
            # dazwischen, wird ein Wechsel nach dem Start nochmal erkannt, statt still verloren zu gehen.
            await self._save_host_state(host)

    async def _track_recovery(self, host: Any, name: str, key: str, prev_state: str | None, state: str, restart_delta: int) -> None:
        """Erholung erkennen: Nach einer Störung muss der Container `RECOVERY_QUIET_S` ruhig laufen, erst dann `on_recovery` (einmal).

        Jede neue Störung beginnt die Wartezeit neu."""
        if self._on_recovery is None:
            return
        now = self._clock()
        if state != "running":
            self._calm_since.pop(key, None)
            return
        if (prev_state is not None and prev_state != "running") or restart_delta:
            self._calm_since[key] = now
            return
        started = self._calm_since.get(key)
        if started is None or now - started < RECOVERY_QUIET_S:
            return
        try:
            await self._on_recovery(host, name, time.time() - (now - started))
        except Exception as exc:  # noqa: BLE001 - die Erholung ist optional: der Wächter läuft weiter und versucht es im nächsten Takt
            if key in self._recovery_failed:
                logger.warning("shield_watcher_recovery_failed_again target=%s error=%s", name, exc)
            else:
                self._recovery_failed.add(key)
                logger.exception("shield_watcher_recovery_failed")
            return
        self._recovery_failed.discard(key)
        self._calm_since.pop(key, None)

    async def run_forever(self) -> None:
        await asyncio.sleep(_STARTUP_DELAY_S)
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - ein Takt-Fehler darf den Wächter nicht beenden
                logger.exception("shield_watcher_tick_failed")
            await asyncio.sleep(_TICK_S)
