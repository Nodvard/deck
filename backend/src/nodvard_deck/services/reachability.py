"""Regelmaessige Erreichbarkeitspruefung -- Kern-Job `host-reachability`
(docs/04-API.md §5 `hosts.reachability.*`).

**Wen der Job prueft:** jeden aktiven Server, dessen Zustand *kein Modul selbst pflegt*. Das
Merkmal dafuer steht am Host: `provider_ext_id`. Ein Modul, das Server einliest
(`ctx.hosts.upsert_discovered`), traegt sich dort ein und fuehrt den Zustand dann selbst nach
(`HostProvider.host_status`). Von Hand angelegte Server haben es nicht, der Kern ist fuer sie
zustaendig. So kennt der Kern keine Modul-ID. Bewusst auch dann *nicht* geprueft, wenn das
einlesende Modul gerade aus ist: Die Adresse eingelesener Gaeste ist oft nur ein Platzhalter (die
Adresse des Hypervisors), eine TCP-Antwort dort sagt nichts ueber den Gast. Uebersprungen werden
ausserdem Beispiel-Server (`core.demo_seed`), Server im Zustand "Wartung", ohne Adresse und
Server, deren Standard-Zugang kein SSH ist (dann ist der Port nicht der SSH-Port).

**Wie geprueft wird:** nur eine TCP-Verbindung zum SSH-Port (Port des Standard-Zugangs, ohne
Zugang 22), 3 Sekunden Zeitgrenze. Kein Login, kein Schluessel, kein SSH-Pool -- das geht auch
fuer Server ohne Zugang und belastet die Server kaum. Kein ICMP: Ping braucht im Container Root
oder Capabilities. "Erreichbar" heisst also: der SSH-Port nimmt Verbindungen an. Hoechstens
`MAX_PARALLEL` Pruefungen gleichzeitig; ein Durchlauf wartet nie laenger als das Intervall, und
laeuft noch einer, wird der naechste uebersprungen (Lock).

**Entprellung:** "nicht erreichbar" gilt erst nach `FAILS_BEFORE_DOWN` Fehlschlaegen hintereinander,
"erreichbar" sofort. Die Zaehler liegen im Prozessspeicher (ein Neustart beginnt bei null, das
kostet hoechstens eine Pruefrunde).

**Meldungen** (`services.notifications.deliver`, Kanaele wie ntfy, `payload.path` zur Server-Seite):
nur bei Wechseln, nie fuer denselben Zustand erneut.

* up -> down: "Server X ist nicht erreichbar" (kritisch). Ein Server, der noch nie "up" war
  (`last_seen_at` leer, z. B. ein Tippfehler in der Adresse), bekommt den Zustand, aber keine
  Meldung.
* down -> up: "Server X ist wieder erreichbar" (Info) -- nur, wenn der Ausfall gemeldet wurde.
* Wartungsfenster (`core.maintenance`): der Zustand wird trotzdem nachgefuehrt. Die Meldung
  traegt `payload.host_id`; `deliver()` stellt sie im Fenster stumm (nur Verlauf). Dauert der
  Ausfall nach dem Fenster an, kommt die Ausfallmeldung einmal hoerbar nach.

Was gemeldet wurde, steht in der Einstellung `hosts.reachability.state` (Host-ID -> stumm ja/nein),
damit ein Neustart (Deploy) keine Meldungsflut und keine verlorene Entwarnung verursacht.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from nodvard_sdk.types import Event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import demo_seed
from ..db import utcnow
from ..db.session import session_scope
from ..models import Host
from . import jobs as jobs_service
from . import notifications as notifications_service
from . import settings as settings_service

logger = logging.getLogger("nodvard_deck.reachability")

JOB_KEY = "host-reachability"
JOB_NAME = "Erreichbarkeit der Server"

KEY_ENABLED = "hosts.reachability.enabled"
KEY_INTERVAL = "hosts.reachability.interval_minutes"
KEY_STATE = "hosts.reachability.state"
"""Interner Merker (Host-ID -> {"muted": bool}); nicht ueber die Einstellungs-API sichtbar."""

DEFAULT_ENABLED = True
DEFAULT_INTERVAL_MINUTES = 2
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 60

CONNECT_TIMEOUT_S = 3.0
MAX_PARALLEL = 10
FAILS_BEFORE_DOWN = 2
KEEP_RUNS = timedelta(hours=24)
"""So lange bleiben die Laeufe dieses Jobs im Protokoll (er laeuft bis zu 1440 mal am Tag)."""

DEFAULT_SSH_PORT = 22
EVENT_NAME = "host.status_changed"

DOWN_TITLE = "Server {name} ist nicht erreichbar"
DOWN_BODY = "{address}:{port} antwortet nicht (zwei Prüfungen hintereinander)."
DOWN_AFTER_WINDOW = " (seit dem Wartungsfenster)"
DOWN_AFTER_WINDOW_BODY = (
    "{address}:{port} antwortet nicht. Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) "
    "und hält noch an."
)
UP_TITLE = "Server {name} ist wieder erreichbar"
UP_BODY = "{address}:{port} antwortet wieder."

_run_lock = asyncio.Lock()
_streaks: dict[str, tuple[tuple[str, int], int]] = {}
"""Host-ID -> (Ziel, Fehlschlaege in Folge). Ein neues Ziel (Adresse/Port geaendert) beginnt neu."""


def reset_state() -> None:
    """Nur fuer Tests: Zaehler und Lock verwerfen (jeder Test hat seinen eigenen Event-Loop)."""
    global _run_lock
    _streaks.clear()
    _run_lock = asyncio.Lock()


@dataclass(frozen=True)
class Target:
    host_id: str
    name: str
    address: str
    port: int

    @property
    def key(self) -> tuple[str, int]:
        return (self.address, self.port)


@dataclass(frozen=True)
class Config:
    enabled: bool
    interval_minutes: int


def cron_for(interval_minutes: int) -> str:
    """Zeitplan fuer das Intervall: alle N Minuten ab voller Stunde (60 = stuendlich)."""
    if interval_minutes >= 60:
        return "0 * * * *"
    return f"*/{interval_minutes} * * * *"


def _valid_interval(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and MIN_INTERVAL_MINUTES <= value <= MAX_INTERVAL_MINUTES
    )


async def load_config(session: AsyncSession) -> Config:
    """Schalter und Intervall; ein unbrauchbarer gespeicherter Wert zaehlt als nicht gesetzt."""
    enabled = await settings_service.get_global(session, KEY_ENABLED)
    minutes = await settings_service.get_global(session, KEY_INTERVAL)
    return Config(
        enabled=enabled if isinstance(enabled, bool) else DEFAULT_ENABLED,
        interval_minutes=minutes if _valid_interval(minutes) else DEFAULT_INTERVAL_MINUTES,
    )


# ---------------------------------------------------------------------------
# Pruefung
# ---------------------------------------------------------------------------


async def probe_tcp(address: str, port: int, timeout_s: float = CONNECT_TIMEOUT_S) -> bool:
    """Nimmt `address:port` innerhalb von `timeout_s` eine TCP-Verbindung an? Es wird nichts
    gesendet; die Verbindung wird sofort wieder geschlossen."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout=timeout_s)
    except (OSError, TimeoutError, UnicodeError, ValueError):
        # OSError: abgelehnt, kein Weg, Name unbekannt; UnicodeError/ValueError: unbrauchbarer Name.
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
    return True


async def select_targets(session: AsyncSession) -> list[Target]:
    """Die Server, die dieser Job prueft (siehe Modul-Docstring)."""
    hosts = (
        await session.execute(
            select(Host).where(Host.enabled.is_(True), Host.provider_ext_id.is_(None)).order_by(Host.name)
        )
    ).scalars().unique().all()
    demo_ids = await demo_seed.still_demo_host_ids(session)
    targets: list[Target] = []
    for host in hosts:
        address = (host.address or "").strip()
        if not address or host.status == "maintenance" or host.id in demo_ids:
            continue
        credential = next((c for c in host.credentials if c.is_default), None)
        if credential is not None and not credential.kind.startswith("ssh"):
            continue  # z. B. ein API-Zugang: der Port dort ist kein SSH-Port
        port = credential.port if credential is not None else DEFAULT_SSH_PORT
        if not 1 <= port <= 65535:
            continue
        targets.append(Target(host_id=host.id, name=host.display_name or host.name, address=address, port=port))
    return targets


async def _probe_all(targets: list[Target], budget_s: float) -> dict[str, bool]:
    """Pruft alle Ziele, hoechstens `MAX_PARALLEL` gleichzeitig. Was nach `budget_s` noch laeuft,
    wird abgebrochen und zaehlt weder als Erfolg noch als Fehlschlag (kein Ergebnis)."""
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def _one(target: Target) -> bool:
        async with semaphore:
            return await probe_tcp(target.address, target.port)

    tasks = {asyncio.ensure_future(_one(t)): t for t in targets}
    if not tasks:
        return {}
    done, pending = await asyncio.wait(tasks, timeout=budget_s)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    results: dict[str, bool] = {}
    for task in done:
        if task.cancelled() or task.exception() is not None:
            continue
        results[tasks[task].host_id] = task.result()
    return results


# ---------------------------------------------------------------------------
# Auswerten und Melden
# ---------------------------------------------------------------------------


@dataclass
class _Message:
    kind: str  # "down" | "up" | "late_down"
    target: Target


def _payload(host_id: str, *, down: bool) -> dict[str, Any]:
    return {
        "host_id": host_id, "path": f"/hosts/{host_id}",
        "tags": ["rotating_light"] if down else ["white_check_mark"],
    }


async def _apply(
    targets: list[Target], results: dict[str, bool]
) -> tuple[list[_Message], list[tuple[str, str, str]], dict[str, int]]:
    """Fuehrt Zustand, `last_seen_at` und den Merker nach. Liefert die zu sendenden Meldungen, die
    Zustandswechsel (Host-ID, alt, neu) und eine Zaehlung."""
    messages: list[_Message] = []
    changes: list[tuple[str, str, str]] = []
    counts = {"up": 0, "down": 0, "unknown": 0}
    now = utcnow()
    async with session_scope() as session:
        state: dict[str, Any] = dict(await settings_service.get_global(session, KEY_STATE, {}) or {})
        state_before = {k: dict(v) if isinstance(v, dict) else v for k, v in state.items()}
        wanted = [t for t in targets if t.host_id in results]
        rows = (
            await session.execute(select(Host).where(Host.id.in_([t.host_id for t in wanted])))
        ).scalars().unique().all() if wanted else []
        hosts = {h.id: h for h in rows}

        for target in wanted:
            host = hosts.get(target.host_id)
            if host is None or host.provider_ext_id is not None or host.status == "maintenance":
                continue  # inzwischen geloescht, von einem Modul uebernommen oder in Wartung
            if (host.address or "").strip() != target.address:
                continue  # waehrend der Pruefung geaendert: das Ergebnis gilt dem alten Ziel
            previous = host.status
            reached = results[target.host_id]
            payload = _payload(host.id, down=not reached)

            if reached:
                _streaks.pop(host.id, None)
                host.last_seen_at = now
                if previous != "up":
                    host.status = "up"
                    changes.append((host.id, previous, "up"))
                if host.id in state:
                    state.pop(host.id, None)
                    messages.append(_Message("up", target))
                counts["up"] += 1
                continue

            known_target, streak = _streaks.get(host.id, (target.key, 0))
            streak = streak + 1 if known_target == target.key else 1
            _streaks[host.id] = (target.key, streak)
            if streak < FAILS_BEFORE_DOWN and previous != "down":
                counts["up" if previous == "up" else "unknown"] += 1
                continue  # noch kein gesicherter Ausfall: Zustand bleibt, wie er ist

            counts["down"] += 1
            if previous != "down":
                host.status = "down"
                changes.append((host.id, previous, "down"))
                if previous == "up" or host.last_seen_at is not None:
                    muted = await notifications_service.would_suppress(session, payload)
                    state[host.id] = {"muted": muted}
                    messages.append(_Message("down", target))
            elif isinstance(state.get(host.id), dict) and state[host.id].get("muted"):
                # Im Wartungsfenster still gemeldet: ist es vorbei und der Server noch weg, einmal hoerbar nachholen.
                if not await notifications_service.would_suppress(session, payload):
                    state[host.id] = {"muted": False}
                    messages.append(_Message("late_down", target))

        # Geloeschte Server aus dem Merker raeumen.
        if state:
            existing = set((await session.execute(select(Host.id).where(Host.id.in_(list(state))))).scalars())
            for host_id in [h for h in state if h not in existing]:
                state.pop(host_id)
        if state != state_before:
            await settings_service.set_global(session, KEY_STATE, state)
    return messages, changes, counts


async def _send(message: _Message) -> None:
    target = message.target
    fields = {"name": target.name, "address": target.address, "port": target.port}
    if message.kind == "up":
        title, body, severity = UP_TITLE.format(**fields), UP_BODY.format(**fields), "info"
    elif message.kind == "late_down":
        title = DOWN_TITLE.format(**fields) + DOWN_AFTER_WINDOW
        body, severity = DOWN_AFTER_WINDOW_BODY.format(**fields), "critical"
    else:
        title, body, severity = DOWN_TITLE.format(**fields), DOWN_BODY.format(**fields), "critical"
    try:
        async with session_scope() as session:
            await notifications_service.deliver(
                session, title=title, body=body, severity=severity,
                correlation_id=f"host-reachability:{target.host_id}",
                payload=_payload(target.host_id, down=message.kind != "up"),
            )
    except Exception:  # noqa: BLE001 - eine Meldung darf den Durchlauf nie abbrechen
        logger.exception("reachability_notify_failed host=%s", target.host_id)


async def _publish_changes(changes: list[tuple[str, str, str]]) -> None:
    from ..core.events import get_event_bus

    bus = get_event_bus()
    for host_id, previous, current in changes:
        try:
            await bus.publish(Event(name=EVENT_NAME, payload={"host_id": host_id, "status": current, "previous": previous}))
        except Exception:  # noqa: BLE001 - die Oberflaeche holt den Stand sonst beim naechsten Abruf
            logger.exception("reachability_event_failed host=%s", host_id)


async def run_once() -> dict[str, Any]:
    """Ein Durchlauf. Laeuft noch einer, wird dieser uebersprungen."""
    if _run_lock.locked():
        logger.info("reachability_skipped reason=previous_run_still_running")
        return {"skipped": True}
    async with _run_lock:
        async with session_scope() as session:
            config = await load_config(session)
            targets = await select_targets(session)
        budget_s = max(10.0, config.interval_minutes * 60 - 5.0)
        results = await _probe_all(targets, budget_s)
        # Zaehler der Server aufraeumen, die es nicht mehr zu pruefen gibt.
        for host_id in [h for h in _streaks if h not in {t.host_id for t in targets}]:
            _streaks.pop(host_id)
        messages, changes, counts = await _apply(targets, results)
        for message in messages:
            await _send(message)
        await _publish_changes(changes)
        return {"checked": len(results), "of": len(targets), "changed": len(changes), **counts}


# ---------------------------------------------------------------------------
# Kern-Job
# ---------------------------------------------------------------------------


async def _prune_own_runs() -> None:
    from ..core.scheduler import get_scheduler_service

    runs_dir = get_scheduler_service().runs_dir
    async with session_scope() as session:
        job = await jobs_service.get_job_by_key(session, ext_id=None, ext_job_key=JOB_KEY)
        if job is None:
            return
        refs = await jobs_service.prune_runs(session, job_id=job.id, older_than=utcnow() - KEEP_RUNS)

    def _unlink() -> None:
        for ref in refs:
            path = Path(ref)
            if path.parent == runs_dir:
                with contextlib.suppress(OSError):
                    path.unlink()

    if refs:
        await asyncio.to_thread(_unlink)


async def _job_handler(**_: Any) -> dict[str, Any]:
    result = await run_once()
    with contextlib.suppress(Exception):
        await _prune_own_runs()
    return result


async def sync_job() -> None:
    """Legt den Kern-Job an bzw. passt Zeitplan und Schalter an und plant ihn neu. Aufgerufen
    beim Start (`register_core_jobs`) und nach jeder Aenderung der Einstellungen."""
    from ..core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
    from ..ext.runtime import get_extension_runtime

    async with session_scope() as session:
        config = await load_config(session)
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_KEY, name=JOB_NAME, kind="core",
            schedule=cron_for(config.interval_minutes), params={}, enabled=config.enabled,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_KEY, _job_handler)
    await get_scheduler_service().schedule(job, _job_handler)
