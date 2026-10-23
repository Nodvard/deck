"""Volume-Sicherungen eines Stacks zurückspielen oder löschen. Zurückgespielt wird erst nach einer Prüfung des Archivs und in
einen Hilfsordner im Volume, getauscht erst danach: Ein kaputtes Archiv lässt die Daten so unangetastet."""

from __future__ import annotations

import asyncio
import stat as statmod
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import Actor, Risk
from pydantic import BaseModel, ConfigDict, Field

from .applier import RISK_ORDER
from .commands import VOLUME_NAME_RE
from .editor import StackEditor
from .image_apply import PROJECT_RE, _valid_path
from .resources import DockerResources, ResourceProblem
from .stack_actions import busy_text, failure_line
from .stack_backup import (
    _STAMP_RE,
    BACKUP_IMAGE,
    BACKUP_ROOT,
    CONTENTS_FILE,
    MAX_CONTENTS_BYTES,
    PART_SUFFIX,
    STOP_TIMEOUT_S,
    _gb,
    backup_folder,
    cmd_start_services,
    cmd_stop_services,
    parse_contents,
)
from .stack_update import Running, StackUpdate
from .stacks import Stack, StackProblem, no_such_file

RESTORE_ACTION = "docker.stack_volume_restore"
DROP_ACTION = "docker.stack_backup_delete"
STAGING = ".nodvard-zurueck"
ASIDE = ".nodvard-vorher"
HELPER_PREFIX = "nodvard-zurueck-"
# Exit-Codes der Skripte im Hilfscontainer; bei BROKEN, LEFTOVER und PREPARE ist an den Daten nichts geändert.
BROKEN_EXIT = 3
SWAP_EXIT = 4
LEFTOVER_EXIT = 5
CLEANUP_EXIT = 6
PREPARE_EXIT = 7
RESTORE_UNTIL_S = 2700
"""Spätestens so lange nach dem Start endet das Entpacken; danach bleibt bis zur Grenze der Freigabe Zeit für den Rest."""
CHECK_TIMEOUT_S = 900
SPACE_MARGIN = 64 * 1024 * 1024
MAX_BACKUPS = 50
MAX_VOLUMES = 40
# Vor dem Stoppen: Archiv ganz lesen (als tar und wie groß entpackt), freier Platz im Volume, keine Reste eines früheren Versuchs.
_CHECK_SCRIPT = (
    f'[ ! -e /data/{ASIDE} ] || exit {LEFTOVER_EXIT}; tar tzf "/backup/$1" >/dev/null || exit {BROKEN_EXIT}; '
    f'set -o pipefail; n=$(gzip -dc "/backup/$1" | wc -c) || exit {BROKEN_EXIT}; '
    'set -- $(df -Pk /data | tail -n 1); echo "$n $4"'
)
_RESTORE_SCRIPT = (
    f't=/data/{STAGING}; o=/data/{ASIDE}; [ ! -e "$o" ] || exit {LEFTOVER_EXIT}; '
    # Ohne Eintrag `./` im Archiv behält das Volume Besitzer und Rechte von vorher.
    f'rm -rf "$t" && mkdir "$t" && chown "$(stat -c %u:%g /data)" "$t" && chmod "$(stat -c %a /data)" "$t" || {{ rm -rf "$t"; exit {PREPARE_EXIT}; }}; '
    f'if ! tar xzf "/backup/$1" -C "$t"; then rm -rf "$t"; exit {BROKEN_EXIT}; fi; '
    f'rm -rf "$t/{STAGING}" "$t/{ASIDE}"; mkdir "$o" || {{ rm -rf "$t"; exit {PREPARE_EXIT}; }}; '
    f'find /data -mindepth 1 -maxdepth 1 ! -name {STAGING} ! -name {ASIDE} -exec mv -t "$o" {{}} + || exit {SWAP_EXIT}; '
    f'find "$t" -mindepth 1 -maxdepth 1 -exec mv -t /data {{}} + || exit {SWAP_EXIT}; '
    f'chown "$(stat -c %u:%g "$t")" /data && chmod "$(stat -c %a "$t")" /data && rmdir "$t" || exit {SWAP_EXIT}; '
    f'rm -rf "$o" || exit {CLEANUP_EXIT}'
)
"""Entpackt neben die alten Daten, legt die alten beiseite und tauscht erst dann; die alten Daten löscht es zuletzt."""
_ARCHIVE_SUFFIX = ".tar.gz"
_HARDENING = ["--network", "none", "--security-opt", "no-new-privileges", "--pids-limit", "64"]


def helper_name(volume: str) -> str:
    """Fester Name je Volume: Läuft schon einer, scheitert ein zweiter, und nach einem Abbruch lässt er sich finden."""
    if not VOLUME_NAME_RE.match(volume):
        raise ValueError(f"Ungültiges Volume: {volume!r}")
    return f"{HELPER_PREFIX}{volume}"


def _mounts(volume: str, folder: str, *, read_only: bool) -> list[str]:
    if not VOLUME_NAME_RE.match(volume) or not _valid_path(folder) or ":" in folder:
        raise ValueError(f"Ungültiges Zurückspielen: {volume!r} <- {folder!r}")
    return ["-v", f"{volume}:/data{':ro' if read_only else ''}", "-v", f"{folder}:/backup:ro"]


def cmd_check(volume: str, folder: str) -> list[str]:
    """Liest das Archiv ganz und gibt „<Bytes entpackt> <frei in KB>“ aus; das Volume ist dabei nur lesend eingebunden."""
    return ["docker", "run", "--rm", *_HARDENING, *_mounts(volume, folder, read_only=True), BACKUP_IMAGE,
            "sh", "-c", _CHECK_SCRIPT, "sh", f"{volume}{_ARCHIVE_SUFFIX}"]


def cmd_restore(volume: str, folder: str) -> list[str]:
    """Spielt `<folder>/<volume>.tar.gz` in das Volume zurück; der Ordner mit den Archiven ist nur lesend eingebunden."""
    return ["docker", "run", "--rm", "--name", helper_name(volume), *_HARDENING, *_mounts(volume, folder, read_only=False),
            BACKUP_IMAGE, "sh", "-c", _RESTORE_SCRIPT, "sh", f"{volume}{_ARCHIVE_SUFFIX}"]


def cmd_helper_state(volume: str) -> list[str]:
    return ["docker", "container", "inspect", "--format", "{{.State.Running}}", helper_name(volume)]


def stamp_text(stamp: str) -> str:
    """`20261016-041500` (UTC) als ISO-Zeit."""
    return datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=UTC).isoformat(timespec="seconds")


def _archive_volume(name: str, suffix: str = _ARCHIVE_SUFFIX) -> str | None:
    volume = name[: -len(suffix)] if name.endswith(suffix) else ""
    return volume if VOLUME_NAME_RE.match(volume) else None


def restore_specs() -> list[ActionSpec]:
    common: dict[str, Any] = {"permissions": ["hosts.execute"], "host_bound": False}
    return [
        ActionSpec(
            action_type=RESTORE_ACTION, label="Volume-Sicherung zurückspielen", icon="history", default_risk=Risk.HIGH,
            description="Ersetzt die Daten in Volumes eines Stacks durch den Stand einer Sicherung; die Dienste stoppen dafür kurz.",
            confirm_text="Was seit der Sicherung in diesen Volumes dazukam oder sich änderte, ist danach weg. Fortfahren?", **common,
        ),
        ActionSpec(
            action_type=DROP_ACTION, label="Volume-Sicherung löschen", icon="trash-2", default_risk=Risk.MEDIUM,
            description="Löscht die Archive einer Volume-Sicherung im Heimatordner des Zugangs.",
            confirm_text="Diese Sicherung lässt sich danach nicht mehr zurückspielen. Fortfahren?", **common,
        ),
    ]


class StackRestore:
    """Welche Volume-Sicherungen es zu einem Stack gibt und welche Volumes davon er heute nutzt."""

    def __init__(self, ctx: Any, update: StackUpdate, resources: DockerResources) -> None:
        self._ctx = ctx
        self.update = update
        self.resources = resources

    async def backups(self, host: Any, stack: Stack) -> dict[str, Any]:
        # Während ein Update packt, ist ein halbes Archiv noch im Werden und nicht abgebrochen.
        busy = self.update.actions.locks.holder(host.id, stack.project)
        out: dict[str, Any] = {"backups": [], "folder": None, "error": None, "note": None, "busy": busy}
        found = await self.update.volumes.home(host)
        if found is None:
            out["error"] = "Der Heimatordner des Zugangs von Nodvard Deck ließ sich nicht lesen."
            return out
        home, _ = found
        base = f"{home}/{BACKUP_ROOT}/{stack.project}"
        out["folder"] = f"{base}/"
        try:
            running = await self.update.running(host, stack)
        except (OSError, StackProblem) as exc:
            out["error"] = f"Die Container des Stacks ließen sich nicht lesen: {str(exc) or type(exc).__name__}"
            return out
        users: dict[str, list[str]] = {}
        idle: dict[str, list[str]] = {}
        for service, entry in running.items():
            for volume in entry.volumes:
                users.setdefault(volume, []).append(service)
                if not entry.running:
                    idle.setdefault(volume, []).append(service)
        if not running:
            out["note"] = "Der Stack hat gerade keine Container, deshalb lässt sich kein Volume zurückspielen. Starte ihn zuerst im Reiter Stacks."
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                if not await _is_dir(sftp, f"{home}/{BACKUP_ROOT}") or not await _is_dir(sftp, base):
                    return out
                stamps = sorted((n for n in await sftp.listdir(base) if _STAMP_RE.match(str(n))), reverse=True)[:MAX_BACKUPS]
                for stamp in stamps:
                    folder = backup_folder(home, stack.project, str(stamp))
                    if not await _is_dir(sftp, folder):
                        continue
                    volumes, unfinished, planned, unclear = [], [], None, None
                    for name in sorted(str(n) for n in await sftp.listdir(folder)):
                        if name == CONTENTS_FILE:
                            planned, unclear = await _planned(sftp, f"{folder}/{name}")
                            continue
                        volume = _archive_volume(name)
                        if volume is None and (part := _archive_volume(name, PART_SUFFIX)) is not None:
                            unfinished.append(part)
                        attrs = await sftp.lstat(f"{folder}/{name}") if volume else None
                        if volume and attrs is not None and statmod.S_ISREG(attrs.permissions or 0):
                            volumes.append({"name": volume, "size_bytes": attrs.size, "services": sorted(users.get(volume, [])),
                                            "stopped_services": sorted(idle.get(volume, [])), "restorable": volume in users})
                    # Ohne Inhaltsliste (ältere Sicherungen) lässt sich nicht sagen, ob etwas fehlt.
                    missing = sorted(set(planned or ()) - {v["name"] for v in volumes})
                    out["backups"].append({"stamp": str(stamp), "at": stamp_text(str(stamp)), "folder": folder, "volumes": volumes,
                                           "unfinished": unfinished, "missing": missing, "contents_error": unclear})
        except Exception as exc:  # noqa: BLE001 - asyncssh meldet mit eigenen Klassen; als Grund zeigen
            out["error"] = f"Die Sicherungen ließen sich nicht lesen: {str(exc) or type(exc).__name__}"
        return out

    async def state(self, host: Any, stack: Stack) -> tuple[dict[str, Running] | None, str | None]:
        """(Dienste des Stacks mit ihren Containern, warum sie sich nicht lesen ließen)."""
        try:
            return await self.update.running(host, stack), None
        except (OSError, StackProblem) as exc:
            return None, f"Die Container ließen sich nicht lesen: {(str(exc) or type(exc).__name__).rstrip('.')}."

    async def check_after(self, host: Any, running: dict[str, Running], services: list[str]) -> tuple[Any, str | None]:
        """(Urteil der Prüfung nach dem Start, warum es keins gibt) für die Container dieser Dienste."""
        from . import after_check as ac  # spät: after_check braucht capabilities

        refs = [name for s in services if s in running for name in running[s].containers or (running[s].container,)]
        if not refs:
            return None, "Docker zeigt zu den Diensten keinen Container."
        verdict, _ = await ac.check_running(self._ctx, host, refs)
        return verdict, None

    async def entry(self, host: Any, stack: Stack, stamp: str) -> tuple[dict[str, Any] | None, str | None]:
        """Die Sicherung mit diesem Zeitstempel oder warum es sie so nicht gibt."""
        listing = await self.backups(host, stack)
        if listing["error"]:
            return None, listing["error"]
        found = next((b for b in listing["backups"] if b["stamp"] == stamp), None)
        return (found, None) if found else (None, "Diese Sicherung gibt es nicht mehr.")


async def _planned(sftp: Any, path: str) -> tuple[list[str] | None, str | None]:
    """(Volumes aus der Inhaltsliste, Satz, falls sie sich nicht lesen ließ); eine kaputte Liste gilt nie als vollständig."""
    try:
        attrs = await sftp.lstat(path)
        if not statmod.S_ISREG(attrs.permissions or 0):
            raise ValueError("keine normale Datei")
        async with sftp.open(path, "rb") as handle:
            planned = parse_contents(bytes(await handle.read(MAX_CONTENTS_BYTES + 1)))
    except Exception as exc:  # noqa: BLE001 - als Grund zeigen; die übrigen Sicherungen bleiben lesbar
        what = f"ließ sich nicht lesen ({str(exc) or type(exc).__name__})"
    else:
        if planned is not None:
            return planned, None
        what = "ist beschädigt"
    return None, f"Die Inhaltsliste dieser Sicherung {what}; ob die Sicherung vollständig ist, lässt sich nicht sagen."


async def _is_dir(sftp: Any, path: str) -> bool:
    """Nur „gibt es nicht“ heißt hier nein; jeder andere Fehler geht weiter, damit die Liste nicht falsch beruhigt."""
    try:
        attrs = await sftp.lstat(path)
    except Exception as exc:
        if no_such_file(exc):
            return False
        raise
    return attrs.permissions is not None and statmod.S_ISDIR(attrs.permissions)


class DockerStackRestoreExecutor:
    """Spielt eine Volume-Sicherung zurück oder löscht sie, nur für übernommene Stacks und nur nach der Freigabe."""

    action_types = frozenset({RESTORE_ACTION, DROP_ACTION})

    def __init__(self, ctx: Any, restore: StackRestore) -> None:
        self._ctx = ctx
        self._restore = restore

    async def execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload or {}
        if getattr(req.proposed_by.type, "value", req.proposed_by.type) != "user":
            return ActionResult(success=False, error="Eine Volume-Sicherung spielt nur ein Mensch über die Docker-Seite zurück oder löscht sie.")
        stamp = str(payload.get("stamp") or "")
        if not _STAMP_RE.match(stamp):
            return ActionResult(success=False, error=f"Ungültige Sicherung: {stamp!r}")
        target = await self._restore.update.actions.target(req.host_ref, str(payload.get("project") or ""))
        if isinstance(target, str):
            return ActionResult(success=False, error=target)
        host, stack = target
        what = "Zurückspielen" if req.action_type == RESTORE_ACTION else "Sicherung löschen"
        with self._restore.update.actions.locks.hold(host.id, stack.project, what) as busy:
            if busy is not None:
                return ActionResult(success=False, error=busy_text(busy))
            if req.action_type == DROP_ACTION:
                return await self._drop(host, stack, stamp)
            volumes = payload.get("volumes")
            if not isinstance(volumes, list) or not volumes or len(volumes) > MAX_VOLUMES or not all(
                isinstance(v, str) and VOLUME_NAME_RE.match(v) for v in volumes
            ):
                return ActionResult(success=False, error="Ungültige Auswahl der Volumes.")
            if RISK_ORDER.index(req.risk) < RISK_ORDER.index(Risk.HIGH):
                return ActionResult(success=False, error="Das Zurückspielen braucht eine Freigabe mit hohem Risiko.")
            return await self._run(host, stack, stamp, sorted(set(volumes)))

    async def _call(self, host: Any, argv: list[str], timeout_s: int) -> tuple[Any | None, str | None]:
        """(Ergebnis, Grund ohne Antwort vom Server)."""
        try:
            return await self._ctx.exec.run_argv(host, argv, timeout_s=timeout_s), None
        except TimeoutError:
            return None, f"nach {_duration(timeout_s)} ohne Ende abgebrochen"
        except Exception as exc:  # noqa: BLE001 - auch asyncssh-Fehler (keine OSError) zählen als „keine Antwort“
            return None, str(exc) or type(exc).__name__

    async def _exec(self, host: Any, argv: list[str], timeout_s: int, what: str) -> tuple[int | None, str | None]:
        """(Exit-Code, Grund für Menschen); ohne Antwort vom Server ist der Code `None`."""
        result, lost = await self._call(host, argv, timeout_s)
        if result is None:
            return None, f"{what}: {lost}"
        if result.exit_code != 0:
            return result.exit_code, f"{what}: {failure_line(result.stderr or result.stdout)}"
        return 0, None

    async def _start_again(self, host: Any, stack: Stack, services: list[str]) -> str | None:
        return (await self._exec(host, cmd_start_services(stack, services), STOP_TIMEOUT_S, "Wieder starten gescheitert"))[1]

    async def _helper_state(self, host: Any, volume: str) -> str | None:
        """Der Hilfscontainer dieses Volumes: `running`, `stopped` oder `absent`; `None`, wenn sich das nicht feststellen ließ."""
        result, _ = await self._call(host, cmd_helper_state(volume), 60)
        if result is None:
            return None
        if result.exit_code != 0:
            return "absent" if "no such" in (result.stderr or "").lower() else None
        return {"true": "running", "false": "stopped"}.get(result.stdout.strip())

    async def _helper_running(self, host: Any, volume: str) -> bool | None:
        """Läuft der Hilfscontainer dieses Volumes noch? `None`, wenn sich das nicht feststellen ließ."""
        state = await self._helper_state(host, volume)
        return None if state is None else state == "running"

    async def _check(self, host: Any, folder: str, volume: str, started: float) -> str | None:
        """Vor dem Stoppen: Läuft schon ein Zurückspielen, ist das Archiv lesbar, reicht der Platz? Der Grund dagegen."""
        state, name = await self._helper_state(host, volume), helper_name(volume)
        if state == "running":
            return f"Für {volume} läuft auf dem Server noch ein Zurückspielen (Container {name})."
        if state == "stopped":
            # Mit diesem Namen schlüge `docker run` erst nach dem Stoppen der Dienste fehl.
            return (f"Auf dem Server steht noch der Hilfscontainer {name} von einem früheren Zurückspielen. Sieh im Volume {volume} nach, "
                    f"ob dort {ASIDE} liegt, entferne den Container mit „docker rm {name}“ und versuch es dann noch einmal.")
        if state is None:
            return f"Ob für {volume} noch ein Zurückspielen läuft, ließ sich nicht feststellen."
        left = min(CHECK_TIMEOUT_S, int(started + RESTORE_UNTIL_S - time.monotonic()))
        if left <= 0:
            return "Für das Prüfen der Archive reichte die Zeit nicht."
        result, lost = await self._call(host, cmd_check(volume, folder), left)
        if result is None:
            return f"Das Archiv von {volume} ließ sich nicht prüfen: {lost}."
        if result.exit_code == BROKEN_EXIT:
            return f"Das Archiv von {volume} ist beschädigt oder unvollständig ({failure_line(result.stderr or result.stdout)})."
        if result.exit_code == LEFTOVER_EXIT:
            return (f"Im Volume {volume} liegt noch {ASIDE} von einem früheren Zurückspielen, darin können die alten Daten stehen. "
                    "Sieh dir den Ordner an und lösch ihn, bevor du noch einmal zurückspielst.")
        try:
            size, free_kb = (int(v) for v in result.stdout.split())
        except ValueError:
            reason = failure_line(result.stderr or result.stdout) or f"Exit-Code {result.exit_code}"
            return f"Das Archiv von {volume} ließ sich nicht prüfen ({reason})."
        need = size + max(SPACE_MARGIN, size // 20)
        if need > free_kb * 1024:
            return (f"Für {volume} ist zu wenig Platz: Deck entpackt die Sicherung erst neben die alten Daten und braucht dafür bis zu "
                    f"{_gb(need)}, frei sind {_gb(free_kb * 1024)}.")
        return None

    async def _run(self, host: Any, stack: Stack, stamp: str, volumes: list[str]) -> ActionResult:
        started = time.monotonic()
        entry, problem = await self._restore.entry(host, stack, stamp)
        if entry is None:
            return _fail(f"{problem} Zurückgespielt wurde nichts.", started)
        known = {v["name"]: v for v in entry["volumes"]}
        missing = [v for v in volumes if v not in known]
        if missing:
            return _fail(f"In dieser Sicherung fehlt {', '.join(missing)}. Zurückgespielt wurde nichts.", started)
        foreign = [v for v in volumes if not known[v]["restorable"]]
        if foreign:
            return _fail(f"{', '.join(foreign)} nutzt der Stack heute nicht mehr; Deck spielt nur Volumes seiner Dienste zurück. "
                         "Zurückgespielt wurde nichts.", started)
        others = await self._other_users(host, stack, volumes)
        if isinstance(others, str):
            return _fail(f"{others} Zurückgespielt wurde nichts.", started)
        if others:
            return _fail(f"{'; '.join(others)}. Halte diese Container an oder nimm sie aus dem Volume, dann versuch es noch einmal. "
                         "Zurückgespielt wurde nichts.", started)
        for volume in volumes:
            if (refused := await self._check(host, entry["folder"], volume, started)) is not None:
                return _fail(f"{refused} Zurückgespielt und gestoppt wurde nichts.", started)
        # Erst nach der Prüfung der Archive gelesen, die lange dauern kann: Was jetzt läuft, startet danach wieder.
        before, unreadable = await self._restore.state(host, stack)
        if before is None:
            return _fail(f"{unreadable} Zurückgespielt und gestoppt wurde nichts.", started)
        users = sorted(s for s, e in before.items() if set(e.volumes) & set(volumes))
        services = [s for s in users if before[s].running]
        idle = [s for s in users if not before[s].running]
        up = {name for e in before.values() for name in e.up}
        try:
            if services:
                # Nur was läuft: Einen gestoppten Container ohne Dienst in der Compose-Datei lehnt `compose stop` ab.
                _, stopped = await self._exec(host, cmd_stop_services(stack, services), STOP_TIMEOUT_S, "Stoppen vor dem Zurückspielen gescheitert")
                if stopped is not None and "no such service" in stopped.lower():
                    stopped += (" Ein laufender Container gehört zu einem Dienst, der nicht mehr in der Compose-Datei steht; halte ihn im "
                                "Reiter Container an oder entferne ihn, dann versuch es noch einmal.")
                ran = "die Dienste laufen weiter wie bisher."
                if stopped is None and (stopped := await self._still_running(host, stack, volumes)) is not None:
                    ran = "die Dienste sind wieder gestartet."
                if stopped is not None:
                    not_started = await self._start_again(host, stack, services)
                    after = ran if not_started is None else (
                        f"die Dienste ließen sich aber nicht wieder starten ({not_started}). Starte den Stack im Reiter Stacks.")
                    _, _, others = await self._look(host, stack, idle, up)
                    return _fail(f"{stopped} Zurückgespielt wurde nichts; {after}{others}", started)
            done, problem, keep_stopped, notes = await self._unpack(host, entry["folder"], volumes, started)
        except BaseException:
            # Abbruch (Zeitgrenze der Freigabe, Neustart von Deck): wieder starten, außer ein Hilfscontainer läuft noch.
            await asyncio.shield(self._after_abort(host, stack, services, volumes))
            raise
        finished = f" Schon zurückgespielt: {', '.join(done)}." if done and problem else ""
        hints = "".join(f" {note}" for note in notes)
        if keep_stopped:
            held = (f" Die Dienste bleiben gestoppt ({', '.join(services)}); starte den Stack im Reiter Stacks, wenn die Daten stimmen."
                    if services else "")
            wait = " Starte den Stack im Reiter Stacks erst, wenn das Zurückspielen fertig ist und die Daten stimmen." if idle and not services else ""
            return _fail(f"{problem}{finished}{hints}{held}{_kept(idle)}{wait}", started)
        if services:
            not_started = await self._start_again(host, stack, services)
            now, unreadable, others = await self._look(host, stack, idle, up)
            woke = None
        else:
            not_started, now, unreadable, others = None, None, None, _kept(idle)
            # Gestartet hat Deck nichts; läuft trotzdem ein Nutzer, kann er die Daten schon während des Tauschs benutzt haben.
            if woke := await self._still_running(host, stack, volumes, after_swap=True):
                others = f" {woke}"
        if problem is not None:
            return _fail(f"{problem}{finished}{hints}{_restarted(services, not_started)}{others}", started)
        ok, after = await self._judge(host, services, not_started, now, unreadable)
        ok = ok and not woke
        text = f"Stack „{stack.project}“: {', '.join(done)} auf den Stand vom {_when(entry['at'])} zurückgespielt.{after}{others}{hints}"
        duration_ms = int((time.monotonic() - started) * 1000)
        if ok:
            return ActionResult(success=True, exit_code=0, duration_ms=duration_ms, output=text, detail={"restored": done})
        return ActionResult(success=False, duration_ms=duration_ms, output=text, error=text, detail={"restored": done})

    async def _look(self, host: Any, stack: Stack, idle: list[str], up: set[str]) -> tuple[dict[str, Running] | None, str | None, str]:
        """Nach dem Wiederstarten: (Stand, warum er fehlt, Sätze zu dem, was vorher stand); `compose start` startet gestoppte
        Abhängigkeiten und alle Container eines Dienstes mit."""
        now, unreadable = await self._restore.state(host, stack)
        if now is None:
            return None, unreadable, (f" {', '.join(idle)} {'war' if len(idle) == 1 else 'waren'} vorher gestoppt." if idle else "")
        # Ein Einmal-Dienst (etwa eine Migration) kann mitgelaufen und schon wieder beendet sein; „bleibt gestoppt“ wäre geraten.
        text = _not_started_by_deck([s for s in idle if not (s in now and now[s].running)])
        if extra := sorted({name for e in now.values() for name in e.up} - up):
            text += (f" Vorher gestoppt, jetzt aber gestartet: {', '.join(extra)}. Compose startet Abhängigkeiten und alle "
                     "Container eines Dienstes mit; soll das nicht laufen, stoppe es im Reiter Container.")
        return now, None, text

    async def _still_running(self, host: Any, stack: Stack, volumes: list[str], *, after_swap: bool = False) -> str | None:
        """Läuft ein Container des Stacks mit einem der Volumes? Der Satz dazu, sonst `None`."""
        now, unreadable = await self._restore.state(host, stack)
        if now is None:
            return None if after_swap else f"Ob nach dem Stoppen noch etwas läuft, ließ sich nicht prüfen. {unreadable}"
        busy = sorted(name for e in now.values() if set(e.volumes) & set(volumes) for name in e.up)
        if not busy:
            return None
        if after_swap:
            return (f"Inzwischen läuft {', '.join(busy)}, obwohl Deck nichts gestartet hat; die Daten können schon während des Tauschs "
                    "benutzt worden sein. Sieh nach, ob sie stimmen.")
        return f"Nach dem Stoppen laufen noch Container mit diesen Volumes: {', '.join(busy)}. Halte sie im Reiter Container an, dann versuch es noch einmal."

    async def _judge(
        self, host: Any, services: list[str], not_started: str | None, now: dict[str, Running] | None, unreadable: str | None,
    ) -> tuple[bool, str]:
        """Nach dem Wiederstarten: (sauber, Sätze zu den Diensten); ohne Urteil der Prüfung ist es nicht sauber."""
        if not services or not_started is not None:
            return not_started is None, _restarted(services, not_started)
        verdict, why = (None, unreadable) if now is None else await self._restore.check_after(host, now, services)
        if verdict is None:
            return False, f"{_restarted(services, None)} Prüfung danach: nicht möglich. {why} Sieh im Reiter Stacks nach, ob sie laufen."
        if not verdict.ok:
            return False, f"{_restarted(services, None)} Prüfung danach: {verdict.text}"
        return True, f" Die Dienste laufen wieder. Prüfung danach: {verdict.text}"

    async def _after_abort(self, host: Any, stack: Stack, services: list[str], volumes: list[str]) -> None:
        if services and all([await self._helper_running(host, volume) is False for volume in volumes]):
            await self._start_again(host, stack, services)

    async def _unpack(self, host: Any, folder: str, volumes: list[str], started: float) -> tuple[list[str], str | None, bool, list[str]]:
        """(zurückgespielt, Grund für den Abbruch, Dienste lieber gestoppt lassen, Hinweise)."""
        done: list[str] = []
        notes: list[str] = []
        for volume in volumes:
            left = int(started + RESTORE_UNTIL_S - time.monotonic())
            if left <= 0:
                rest = volumes[volumes.index(volume):]
                return done, f"Für das Zurückspielen reichte die Zeit nicht; an {', '.join(rest)} hat sich nichts geändert.", False, notes
            result, lost = await self._call(host, cmd_restore(volume, folder), left)
            code = None if result is None else result.exit_code
            reason = lost if result is None else failure_line(result.stderr or result.stdout) or f"Exit-Code {code}"
            if code in (0, CLEANUP_EXIT):
                done.append(volume)
                if code == CLEANUP_EXIT:
                    notes.append(f"Die alten Daten von {volume} liegen noch im Volume unter {ASIDE} ({reason}); lösch den Ordner, sonst "
                                 "blockiert er das nächste Zurückspielen.")
            elif code == BROKEN_EXIT:
                why = "Im Volume war zu wenig Platz" if "no space left" in reason.lower() else "Das Archiv ist beschädigt oder unvollständig"
                return done, f"Zurückspielen von {volume} gescheitert: {why} ({reason}). An {volume} hat sich nichts geändert.", False, notes
            elif code in (LEFTOVER_EXIT, PREPARE_EXIT) or (code in (125, 126, 127) and "already in use" not in reason):
                return done, f"Zurückspielen von {volume} gescheitert ({reason}). An {volume} hat sich nichts geändert.", False, notes
            elif code == SWAP_EXIT:
                return done, (f"Beim Tausch der Daten von {volume} ging etwas schief ({reason}). Die bisherigen Daten liegen ganz oder zum Teil "
                              f"im Volume unter {ASIDE}, die aus der Sicherung unter {STAGING} oder schon an ihrem Platz. Sieh im Volume "
                              "nach, bevor die Dienste wieder laufen."), True, notes
            else:
                state = await self._helper_running(host, volume)
                now = (f"Das Zurückspielen läuft auf dem Server noch (Container {helper_name(volume)}) und tauscht am Ende die Daten aus."
                       if state else "Ob es fertig wurde, ließ sich nicht feststellen.")
                return done, (f"Zurückspielen von {volume}: keine klare Antwort ({reason}). {now} Bei einem Fehler liegen die Daten im "
                              f"Volume unter {STAGING} bzw. {ASIDE}."), True, notes
        return done, None, False, notes

    async def _other_users(self, host: Any, stack: Stack, volumes: list[str]) -> list[str] | str:
        """Container außerhalb des Stacks, die eines der Volumes nutzen; als Satz je Volume, oder warum sich das nicht lesen ließ."""
        resources = self._restore.resources
        resources.forget(host.id)
        try:
            snapshot = await resources.snapshot(host)
        except ResourceProblem as exc:
            return f"Wer die Volumes sonst noch nutzt, ließ sich nicht lesen: {exc}"
        try:
            running = await self._restore.update.running(host, stack)
        except (OSError, StackProblem) as exc:
            return f"Die Container des Stacks ließen sich nicht lesen: {str(exc) or type(exc).__name__}"
        own = {name for entry in running.values() for name in (*entry.containers, entry.container)}
        out = []
        for row in snapshot.volumes:
            if row.get("name") in volumes:
                foreign = sorted(str(c) for c in row.get("used_by") or [] if str(c) not in own)
                if foreign:
                    out.append(f"{row['name']} nutzt auch {', '.join(foreign)}")
        return out

    async def _drop(self, host: Any, stack: Stack, stamp: str) -> ActionResult:
        found = await self._restore.update.volumes.home(host)
        if found is None:
            return ActionResult(success=False, error="Der Heimatordner des Zugangs von Nodvard Deck ließ sich nicht lesen.")
        folder = backup_folder(found[0], stack.project, stamp)
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                if not await _is_dir(sftp, folder):
                    return ActionResult(success=False, error="Diese Sicherung gibt es nicht mehr.")
                names = [str(n) for n in await sftp.listdir(folder) if str(n) not in (".", "..")]
                # Ein halbes Archiv eines abgebrochenen Packens und die Inhaltsliste gehören zur Sicherung und gehen mit.
                odd = [n for n in names if n != CONTENTS_FILE and _archive_volume(n) is None and _archive_volume(n, PART_SUFFIX) is None]
                if odd:
                    return ActionResult(success=False, error=f"Im Ordner {folder} liegt noch anderes ({', '.join(sorted(odd)[:5])}); Deck "
                                        "löscht nur seine eigenen Archive. Gelöscht wurde nichts.")
                for name in names:
                    attrs = await sftp.lstat(f"{folder}/{name}")
                    if not statmod.S_ISREG(attrs.permissions or 0):
                        return ActionResult(success=False, error=f"{name} ist keine normale Datei. Gelöscht wurde nichts.")
                # Die Inhaltsliste zuletzt: Bleibt nach einem Abbruch ein Rest, zeigt die Liste ihn als unvollständig.
                for name in sorted(names, key=lambda n: n == CONTENTS_FILE):
                    await sftp.remove(f"{folder}/{name}")
                await sftp.rmdir(folder)
        except Exception as exc:  # noqa: BLE001 - als Grund melden
            return ActionResult(success=False, error=f"Die Sicherung ließ sich nicht ganz löschen: {str(exc) or type(exc).__name__}")
        return ActionResult(success=True, exit_code=0, output=f"Sicherung vom {_when(stamp_text(stamp))} gelöscht.")

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        payload = req.payload or {}
        if req.action_type == DROP_ACTION:
            return DryRunReport(would_change=True, summary=f"Würde die Sicherung {payload.get('stamp', '?')} von „{payload.get('project', '?')}“ löschen.")
        volumes = ", ".join(str(v) for v in payload.get("volumes") or []) or "?"
        return DryRunReport(would_change=True, summary=f"Würde {volumes} im Stack „{payload.get('project', '?')}“ zurückspielen.")


def _kept(idle: list[str]) -> str:
    if not idle:
        return ""
    return f" {', '.join(idle)} {'war' if len(idle) == 1 else 'waren'} gestoppt und {'bleibt' if len(idle) == 1 else 'bleiben'} es."


def _not_started_by_deck(idle: list[str]) -> str:
    if not idle:
        return ""
    one = len(idle) == 1
    return (f" {', '.join(idle)} {'war' if one else 'waren'} gestoppt. Deck hat {'ihn' if one else 'sie'} nicht gestartet; Compose kann "
            f"{'ihn' if one else 'sie'} aber mitstarten, wenn ein laufender Dienst {'ihn' if one else 'sie'} braucht, etwa als Einmal-Dienst "
            "für Migrationen.")


def _restarted(services: list[str], not_started: str | None) -> str:
    if not services:
        return ""
    if not_started is None:
        return " Die Dienste sind wieder gestartet."
    return f" Die Dienste ließen sich danach nicht wieder starten ({not_started}). Starte den Stack im Reiter Stacks."


def _duration(seconds: int) -> str:
    return f"{seconds // 60} Minuten" if seconds >= 120 else f"{seconds} Sekunden"


def _when(iso: str) -> str:
    moment = datetime.fromisoformat(iso).astimezone()
    return moment.strftime("%d.%m.%Y, %H:%M").lstrip("0")


def _fail(text: str, started: float) -> ActionResult:
    return ActionResult(success=False, error=text, duration_ms=int((time.monotonic() - started) * 1000))


class _RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volumes: list[str] = Field(min_length=1, max_length=MAX_VOLUMES)


def restore_routes(restore: StackRestore, editor: StackEditor, docker_host: Any, current_actor: Any) -> APIRouter:
    router = APIRouter()

    async def _target(host_id: str, project: str) -> tuple[Any, Stack]:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")
        host = await docker_host(host_id)
        target = await restore.update.actions.target(host.id, project)
        if isinstance(target, str):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=target)
        return target

    @router.get("/hosts/{host_id}/stacks/{project}/backups")
    async def list_backups(host_id: str, project: str) -> dict:
        """Die Volume-Sicherungen des Stacks, neueste zuerst, je Volume mit Größe und den Diensten, die es heute nutzen."""
        return await restore.backups(*await _target(host_id, project))

    async def _propose(host_id: str, project: str, stamp: str, actor: Actor, action_type: str, volumes: list[str]) -> dict:
        if not _STAMP_RE.match(stamp):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültige Sicherung.")
        host, stack = await _target(host_id, project)
        entry, problem = await restore.entry(host, stack, stamp)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=problem)
        known = {v["name"]: v for v in entry["volumes"]}
        if action_type == RESTORE_ACTION:
            unknown = [v for v in volumes if v not in known or not known[v]["restorable"]]
            if unknown:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Diese Volumes lassen sich nicht zurückspielen: {', '.join(unknown)}")
            reason = f"Volumes von „{stack.project}“ auf den Stand vom {_when(entry['at'])} zurückspielen: {', '.join(volumes)}."
            risk = Risk.HIGH
        else:
            reason = f"Volume-Sicherung vom {_when(entry['at'])} von „{stack.project}“ löschen ({', '.join(sorted(known)) or 'leer'})."
            risk = Risk.MEDIUM
        decision = await editor.propose(ActionRequest(
            action_type=action_type, host_ref=host.id, risk=risk, proposed_by=actor, reason=reason,
            payload={"project": stack.project, "stamp": stamp, **({"volumes": volumes} if action_type == RESTORE_ACTION else {})},
        ))
        return {"action_id": decision.action_id, "status": decision.status.value, "risk": risk.value, "detail": decision.detail}

    @router.post("/hosts/{host_id}/stacks/{project}/backups/{stamp}/restore", status_code=status.HTTP_202_ACCEPTED)
    async def propose_restore(host_id: str, project: str, stamp: str, body: _RestoreIn, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Zurückspielen vor (Gate-Aktion `docker.stack_volume_restore`, hohes Risiko)."""
        volumes = sorted(set(body.volumes))
        if not all(VOLUME_NAME_RE.match(v) for v in volumes):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Name eines Volumes.")
        return await _propose(host_id, project, stamp, actor, RESTORE_ACTION, volumes)

    @router.post("/hosts/{host_id}/stacks/{project}/backups/{stamp}/delete", status_code=status.HTTP_202_ACCEPTED)
    async def propose_drop(host_id: str, project: str, stamp: str, actor: Actor = Depends(current_actor)) -> dict:
        """Schlägt das Löschen der Sicherung vor (Gate-Aktion `docker.stack_backup_delete`)."""
        return await _propose(host_id, project, stamp, actor, DROP_ACTION, [])

    return router
