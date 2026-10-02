"""Der proaktive Docker-Waechter -- ersetzt `docker_proactive_watcher_loop()` aus
dem Vorgaengersystem: 60s Startverzoegerung, danach ein 8-Sekunden-Takt
(derselbe Wert wie im Original), der
`docker ps -a` auf jedem ueberwachten Host abfragt und Zustandswechsel gegen den
zuletzt gesehenen Zustand diffed.

**Behobener Fehler:** das Vorgaengersystem iterierte eine HARTKODIERTE Hostliste,
obwohl es eine konfigurierbare Liste gab. Hier: die
Hosts kommen aus `ctx.hosts.list(tag=docker_host_tag)` (Einstellung, Default
"docker") -- ein Host wird ueberwacht, weil er getaggt ist, nicht weil sein Name im
Code steht.

**Bewusst NICHT portiert:** das Vorgaengersystem behandelte die ERSTE Beobachtung eines Containers
als moeglichen Vorfall, wenn er in `CRITICAL_CONTAINERS` stand (einer von Hand
gepflegten Liste). `CRITICAL_CONTAINERS`/`WEB_URLS` gehoeren nicht zu nexus-soc,
sondern zur service-matrix-Extension ("entdeckt, nicht handgepflegt"). Diese Version
behandelt die erste Beobachtung eines Containers deshalb NIE als Vorfall (nur
Zustands-Baseline), unabhaengig vom Namen --
etwas strenger als das Vorgaengersystem fuer bereits-offline gestartete Container, aber ohne die
inzwischen unerwuenschte Handliste.

**Zustand ueberlebt einen Neustart:** der zuletzt gesehene Zustand je Container wird
pro Host in `ext_nexus_soc_baselines` (Art "docker") gespeichert und beim ersten Takt
eines Hosts geladen. Ein Container, der waehrend der Dashboard-Pause von "running" auf
"nicht laufend" gewechselt ist, wird dadurch gemeldet; einer, der schon vorher gestoppt
war, nicht. Verschwindet ein Container ganz (absichtlich entfernt), ist das kein Vorfall; sein Stand
wird dann gleich verworfen (nur Namen der aktuellen `docker ps -a`-Ausgabe bleiben), ein
spaeter gleichnamiger Container faengt mit einer neuen Baseline an. Ein kaputter
gespeicherter Stand wird verworfen. Der Stand wird
erst NACH der Uebergabe der Vorfaelle des Taktes gespeichert (die Vorfaelle sind dort
selbst dauerhaft) und nur, wenn er sich geaendert hat.

**Absturzschleifen zwischen zwei Takten:** ein Container, der zwischen zwei 8-s-Takten
abstuerzt und von der Restart-Policy gleich wieder gestartet wird, steht bei beiden
Takten auf "running". Darum fragt jeder Takt mit EINEM zusaetzlichen, nur lesenden
`docker inspect` (alle laufenden Container auf einmal) den `RestartCount` ab; steigt er
gegenueber dem letzten Stand (auch ueber einen Neustart hinweg), ist das ein Absturz-
Vorfall. Ein neu angelegter Container (andere Id) faengt wieder bei einer Baseline an, und
ein manuelles `docker restart` erhoeht den Zaehler nicht -- beides ist kein Vorfall.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .incident_text import describe_restart_loop, describe_transition, strip_relative_time

logger = logging.getLogger("nodvard_deck.ext.nexus-soc.watcher")

_STARTUP_DELAY_S = 60.0
_TICK_S = 8.0


_STATE_KIND = "docker"


class StateStore(Protocol):
    async def load(self, host_id: str) -> dict[str, Any] | None: ...

    async def save(self, host_id: str, data: dict[str, Any]) -> None: ...


class BaselineStateStore:
    """Legt den Container-Stand je Host in `ext_nexus_soc_baselines` (Art "docker") ab --
    dieselbe Ablage, die Updates/Einbruchschutz fuer ihren letzten Stand nutzen."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def load(self, host_id: str) -> dict[str, Any] | None:
        from .patching import load_baseline

        return await load_baseline(self._ctx, host_id, _STATE_KIND)

    async def save(self, host_id: str, data: dict[str, Any]) -> None:
        from .patching import save_baseline

        await save_baseline(self._ctx, host_id, _STATE_KIND, data)


def parse_restart_counts(output: str) -> dict[str, tuple[int, str]]:
    """`docker inspect --format '{{.Name}}|{{.RestartCount}}|{{.Id}}'` -> {Name: (Zaehler, Id)}.
    Zeilen, die nicht passen (Fehlermeldungen, leere Zeilen), werden uebersprungen."""
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
    """Ein nur lesender `docker inspect` fuer die Beendigungs-Fakten mehrerer Container."""
    return (
        "docker inspect --type container --format "
        "'{{.Name}}|{{.State.OOMKilled}}|{{.State.ExitCode}}|{{.State.Running}}|{{.State.FinishedAt}}"
        "|{{.RestartCount}}|{{.Config.Image}}|{{.State.Error}}' "
        + " ".join(shlex.quote(n) for n in names)
    )


def parse_exit_facts(output: str) -> dict[str, dict[str, Any]]:
    """Ausgabe von `inspect_exit_command` -> {Name: {oom_killed, exit_code, running, finished_at, error,
    restart_count, image}}. `State.Error` steht zuletzt (darf selbst ein "|" enthalten); unlesbare
    Zeilen entfallen. Aeltere Ausgaben ohne Neustart-Zaehler und Image (6 Felder) werden weiter
    gelesen, dann fehlen `restart_count` und `image`."""
    facts: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        line = line.strip()
        extra: dict[str, Any] = {}
        parts = line.split("|", 7)
        if len(parts) == 8 and parts[5].strip().isdigit():
            name, oom, code, running, finished, restarts, image, error = (p.strip() for p in parts)
            extra = {"restart_count": int(restarts), "image": image}
        else:
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
    ) -> None:
        self._ctx = ctx
        self._on_transition = on_transition
        self._state_store = state_store
        self._prev_states: dict[str, str] = {}
        self._prev_restarts: dict[str, int] = {}
        self._prev_ids: dict[str, str] = {}
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
        except Exception:  # noqa: BLE001 - ohne gespeicherten Stand geht es wie bisher mit einer Baseline los
            logger.exception("nexus_soc_watcher_state_load_failed")
            return
        try:
            loaded = self._parse_saved_state(host, data)
        except (AttributeError, TypeError, ValueError):
            loaded = None
        if loaded is None:
            # Kaputter gespeicherter Stand: verwerfen und mit einer neuen Baseline anfangen.
            logger.warning("nexus_soc_watcher_state_invalid host=%s", host.id)
            return
        self._prev_states.update(loaded[0])
        self._prev_restarts.update(loaded[1])
        self._prev_ids.update(loaded[2])
        self._saved[host.id] = self._snapshot(host)

    @staticmethod
    def _parse_saved_state(host: Any, data: Any) -> tuple[dict[str, str], dict[str, int], dict[str, str]] | None:
        """Prueft den gespeicherten Stand streng; `None` bei jeder Unstimmigkeit, damit nie
        ein halb gelesener Stand uebernommen wird."""
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
            logger.exception("nexus_soc_watcher_state_save_failed")
            return
        self._saved[host.id] = snapshot

    async def _read_restart_counts(self, host: Any, names: list[str]) -> dict[str, tuple[int, str]]:
        """Ein einziger, nur lesender `docker inspect` fuer alle uebergebenen Container."""
        if not names:
            return {}
        command = (
            "docker inspect --type container --format '{{.Name}}|{{.RestartCount}}|{{.Id}}' "
            + " ".join(shlex.quote(n) for n in names)
        )
        try:
            result = await self._ctx.exec.run(host, command, timeout_s=10)
        except Exception:  # noqa: BLE001 - Anreicherung: ohne Zaehler bleibt es beim Zustandsvergleich
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
            except Exception:  # noqa: BLE001 - ein nicht erreichbarer Host darf den Takt nicht abreissen
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

                # Neustarts durch die Restart-Policy seit dem letzten Stand (nur Container, die
                # vorher UND jetzt laufen -- alles andere sieht der Zustandsvergleich unten).
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

                if prev_state is None:
                    continue  # erste Beobachtung: nur Baseline, siehe Modul-Docstring
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
                    # Nur exited/dead/restarting koennen ein Absturz sein; "paused" (und alles andere)
                    # nie. Ob ein Exit 137 ein kill oder ein OOM war, klaeren die Beendigungs-Fakten.
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
                # Erholung (prev_state != "running" and state == "running") ist bewusst
                # kein Vorfall -- nur die Baseline oben wird aktualisiert.

            # Nur Container der aktuellen, erfolgreichen `docker ps -a`-Ausgabe behalten: entfernte
            # Container (z. B. Zufallsnamen) wachsen nicht unbegrenzt im Stand, und ein spaeter
            # gleichnamiger, nicht laufender Container gilt als neu (Baseline), nicht als Absturz.
            prefix = f"{host.id}:"
            current = {f"{prefix}{name}" for name, _st, _s in rows}
            for table in (self._prev_states, self._prev_restarts, self._prev_ids):
                for key in [k for k in table if k.startswith(prefix) and k not in current]:
                    del table[key]

            # Erst jetzt speichern: die Vorfaelle dieses Taktes sind uebergeben (und dort
            # dauerhaft gemerkt). Stirbt der Prozess dazwischen, wird ein Wechsel nach dem
            # Start nochmal erkannt, statt still verloren zu gehen.
            await self._save_host_state(host)

    async def run_forever(self) -> None:
        await asyncio.sleep(_STARTUP_DELAY_S)
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - ein Takt-Fehler darf den Waechter nicht beenden
                logger.exception("nexus_soc_watcher_tick_failed")
            await asyncio.sleep(_TICK_S)
