"""Prüft für „Stacks aus Git“ nach Zeitplan, auf welchen Commit Branch oder Tag gerade zeigen, und meldet einen neuen
Stand einmal je Commit. Geholt und eingespielt wird dabei nichts, das bleibt beim Klick mit Freigabe."""

from __future__ import annotations

from http import HTTPStatus
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import Notification, Severity

from .git_fetch import GitProblem, _shown
from .git_source import NO_SOURCE, GitStacks, Source
from .image_apply import PROJECT_RE
from .image_updates import _NEVER, _write_atomic

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

log = logging.getLogger("nodvard_deck.ext.docker")

STATE_FILE = "git_watch.json"
JOB_ID = "git-watch"
DEFAULT_CRON = "47 * * * *"
MAX_LINES = 20
FAILURES_BEFORE_WARNING = 3
"""So viele Läufe nach Zeitplan hintereinander darf die Prüfung eines Stacks scheitern, bevor eine Warnung kommt."""
SAME_FOLDER_HINT = "Ob sich dadurch der Ordner eines Stacks ändert, zeigt „Holen und ansehen“ im Reiter Stacks unter „Aus Git“."


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class GitWatch:
    """Stand je Server und Stack: zuletzt gesehener Commit, Zeitpunkt, Grund einer gescheiterten Prüfung und welcher
    Commit schon gemeldet ist; gehört zur eingetragenen Quelle und gilt nach einem Wechsel von Adresse oder Branch nicht mehr."""

    def __init__(self, ctx: Any, git: GitStacks, applied: Callable[[str, str], str | None], data_dir: Path) -> None:
        self._ctx = ctx
        self._git = git
        self._applied = applied
        self._path = data_dir / STATE_FILE
        self._lock = asyncio.Lock()
        self._run = asyncio.Lock()

    def _read(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8")).get("stacks")
        except (OSError, ValueError, AttributeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(h): {str(p): e for p, e in projects.items() if isinstance(e, dict)}
                for h, projects in raw.items() if isinstance(projects, dict)}

    def _write(self, data: dict[str, dict[str, dict[str, Any]]]) -> bool:
        try:
            _write_atomic(self._path, json.dumps({"stacks": {h: p for h, p in data.items() if p}}, indent=1, sort_keys=True))
        except OSError as exc:
            log.warning("docker_git_watch_not_saved path=%s error=%s", self._path, exc)
            return False
        return True

    def adopted(self, host_id: str, project: str) -> bool:
        return self._git.is_adopted(host_id, project)

    @staticmethod
    def _belongs(entry: dict[str, Any], source: Source) -> bool:
        return entry.get("url") == source.url and entry.get("ref") == source.ref

    def _shown(self, host_id: str, project: str, entry: dict[str, Any]) -> dict[str, Any]:
        remote, applied = entry.get("remote"), self._applied(host_id, project)
        return {
            "remote": remote,
            "short": remote[:7] if isinstance(remote, str) else None,
            "ref_kind": entry.get("ref_kind"),
            "checked_at": entry.get("checked_at"),
            "problem": entry.get("problem"),
            "applied": applied,
            "new": isinstance(remote, str) and applied is not None and remote != applied,
        }

    def state(self, host_id: str, project: str) -> dict[str, Any] | None:
        """Was die Oberfläche zeigt; `None`, solange für die eingetragene Quelle noch nicht geprüft wurde."""
        source = self._git.sources.get(host_id, project)
        entry = self._read().get(host_id, {}).get(project)
        if source is None or entry is None or not self._belongs(entry, source):
            return None
        return self._shown(host_id, project, entry)

    async def _look(self, host_id: str, project: str, source: Source, entry: dict[str, Any], *,
                    scheduled: bool = False) -> dict[str, Any]:
        """Ein neuer Eintrag nach einer Abfrage; bei einem Problem bleibt der zuletzt gesehene Commit stehen."""
        kept = entry if self._belongs(entry, source) else {}
        fresh: dict[str, Any] = {"url": source.url, "ref": source.ref, "checked_at": _now(),
                                 "remote": kept.get("remote"), "ref_kind": kept.get("ref_kind")}
        try:
            known = tuple(c for c in (kept.get("remote"), self._applied(host_id, project)) if isinstance(c, str))
            fresh["remote"], fresh["ref_kind"] = await self._git.head(host_id, project, source, known, scheduled=scheduled)
            fresh["problem"] = None
        except GitProblem as exc:
            fresh["problem"] = str(exc)
        except Exception as exc:  # eine Quelle darf die Prüfung der anderen nicht abbrechen
            log.exception("docker_git_watch_failed host=%s project=%s", host_id, project)
            fresh["problem"] = f"Die Prüfung ist gescheitert ({type(exc).__name__})."
        return fresh

    @staticmethod
    def _carry(fresh: dict[str, Any], before: dict[str, Any]) -> None:
        """Was nur der Lauf nach Zeitplan pflegt, bleibt bei einer Prüfung auf Knopfdruck stehen."""
        for key in ("notified", "failures", "warned"):
            fresh[key] = before.get(key)

    async def check(self, host_id: str, project: str) -> dict[str, Any] | None:
        """Jetzt für einen Stack prüfen, ohne Meldung; danach wie `state`. Die Abfrage läuft außerhalb der Sperre."""
        source = self._git.sources.get(host_id, project)
        if source is None:
            return None
        fresh = await self._look(host_id, project, source, self._read().get(host_id, {}).get(project, {}))
        async with self._lock:
            data = self._read()
            before = data.get(host_id, {}).get(project, {})
            self._carry(fresh, before if self._belongs(before, source) else {})
            if self._git.sources.get(host_id, project) == source:
                data.setdefault(host_id, {})[project] = fresh
                self._write(data)
        return self._shown(host_id, project, fresh)

    async def forget(self, host_id: str, project: str) -> None:
        async with self._lock:
            data = self._read()
            if data.get(host_id, {}).pop(project, None) is not None:
                self._write(data)

    async def check_all(self) -> dict[str, Any]:
        """Alle übernommenen Stacks mit Quelle, einer nach dem anderen; gemeldet wird erst, wenn der neue Stand gespeichert ist."""
        async with self._run:
            snapshot = self._read()
            looked: list[tuple[str, str, Source, dict[str, Any]]] = []
            for host_id, project, source in self._git.sources.all():
                if self._git.is_adopted(host_id, project):
                    looked.append((host_id, project, source, await self._look(host_id, project, source,
                                                                               snapshot.get(host_id, {}).get(project, {}),
                                                                               scheduled=True)))
            news: list[tuple[str, str, str]] = []
            failing: list[tuple[str, str, str]] = []
            problems = 0
            async with self._lock:
                current = self._read()
                data: dict[str, dict[str, dict[str, Any]]] = {}
                for host_id, project, source, entry in looked:
                    if self._git.sources.get(host_id, project) != source:
                        continue
                    before = current.get(host_id, {}).get(project, {})
                    if self._belongs(before, source) and before != snapshot.get(host_id, {}).get(project, {}):
                        entry = dict(before)
                    self._carry(entry, before if self._belongs(before, source) else {})
                    if entry["problem"] is None:
                        entry["failures"], entry["warned"] = 0, False
                        applied = self._applied(host_id, project)
                        if applied is not None and entry["remote"] != applied and entry["remote"] != entry["notified"]:
                            news.append((host_id, project, entry["remote"]))
                    else:
                        problems += 1
                        entry["failures"] = int(entry.get("failures") or 0) + 1
                        if entry["failures"] >= FAILURES_BEFORE_WARNING and not entry.get("warned"):
                            failing.append((host_id, project, entry["problem"]))
                    data.setdefault(host_id, {})[project] = entry
                saved = self._write(data)
        if saved:
            if news and await self._notify(news):
                await self._mark("notified", news)
            if failing and await self._warn(failing):
                await self._mark("warned", failing)
        return {"stacks": sum(len(p) for p in data.values()), "new": len(news) if saved else 0, "problems": problems, "saved": saved}

    async def _mark(self, key: str, items: list[tuple[str, str, str]]) -> None:
        """Erst nach dem Versand festhalten, damit eine Meldung, die nicht rausging, beim nächsten Lauf wiederkommt."""
        async with self._lock:
            data = self._read()
            for host_id, project, value in items:
                entry = data.get(host_id, {}).get(project)
                if entry is None:
                    continue
                if key == "notified" and entry.get("remote") == value:
                    entry["notified"] = value
                elif key == "warned" and entry.get("problem") is not None:
                    entry["warned"] = True
            self._write(data)

    async def _names(self, host_ids: set[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for host_id in host_ids:
            try:
                host = await self._ctx.hosts.get(host_id)
            except Exception:  # ohne Namen steht die Kennung da
                host = None
            names[host_id] = _shown(str(getattr(host, "display_name", None) or getattr(host, "name", None) or host_id), 80)
        return names

    @staticmethod
    def _lines(items: list[tuple[str, str, str]], names: dict[str, str], shown: Callable[[str], str]) -> list[str]:
        lines = [f"- {names[h]}: {p} ({shown(x)})" for h, p, x in items[:MAX_LINES]]
        if len(items) > MAX_LINES:
            lines.append(f"- und {len(items) - MAX_LINES} weitere")
        return lines

    @staticmethod
    def _link(items: list[tuple[str, str, str]]) -> str:
        hosts = {h for h, _, _ in items}
        return "/ext/docker/matrix?tab=stacks" + (f"&host={next(iter(hosts))}" if len(hosts) == 1 else "")

    async def _notify(self, news: list[tuple[str, str, str]]) -> bool:
        names = await self._names({h for h, _, _ in news})
        try:
            await self._ctx.notify.send(Notification(
                title=f"Stacks aus Git: neuer Stand für {len(news)} {'Stack' if len(news) == 1 else 'Stacks'}",
                body="\n".join(self._lines(news, names, lambda c: c[:7]))
                + f"\nEs wurde nichts geändert. {SAME_FOLDER_HINT} Eingespielt wird wie immer erst nach Freigabe.",
                severity=Severity.INFO,
                payload={"notification_type": "docker.git_available", "path": self._link(news), "tags": ["package"]},
            ))
        except Exception:  # die Meldung ist ein Zusatz, der Stand steht schon am Stack
            log.exception("docker_git_watch_notify_failed")
            return False
        return True

    async def _warn(self, failing: list[tuple[str, str, str]]) -> bool:
        names = await self._names({h for h, _, _ in failing})
        try:
            await self._ctx.notify.send(Notification(
                title=f"Stacks aus Git: Prüfung scheitert bei {len(failing)} {'Stack' if len(failing) == 1 else 'Stacks'}",
                body="\n".join(self._lines(failing, names, lambda reason: _shown(reason, 200)))
                + f"\nDie Prüfung ist {FAILURES_BEFORE_WARNING}-mal hintereinander gescheitert. Ohne erfolgreiche Prüfung meldet Deck "
                "keinen neuen Stand.",
                severity=Severity.WARNING,
                payload={"notification_type": "docker.git_failed", "path": self._link(failing), "tags": ["warning"]},
            ))
        except Exception:  # die Warnung ist ein Zusatz, der Grund steht schon am Stack
            log.exception("docker_git_watch_warn_failed")
            return False
        return True


class GitWatchJobSpec:
    """Lauf nach Zeitplan (Einstellung `git_watch_enabled`, Standard: aus)."""

    id = JOB_ID
    name = "Stacks aus Git prüfen"

    def __init__(self, watch: GitWatch, *, enabled: bool, schedule: str) -> None:
        self._watch = watch
        self.enabled = enabled
        self.schedule = schedule if enabled else _NEVER
        self.params: dict[str, Any] = {}

    async def handler(self, **_: Any) -> dict[str, Any]:
        return await self._watch.check_all()


async def register_watch_job(ctx: ExtensionContext, watch: GitWatch, settings: dict[str, Any]) -> None:
    """Bei jedem Start und nach jeder Einstellungsänderung (`register_job` ist ein Upsert)."""
    enabled = bool(settings.get("git_watch_enabled", False))
    schedule = (settings.get("git_watch_cron") or "").strip() or DEFAULT_CRON
    try:
        await ctx.scheduler.register_job(GitWatchJobSpec(watch, enabled=enabled, schedule=schedule))
    except Exception:
        log.exception("docker_git_watch_register_failed schedule=%r", schedule)
        await ctx.scheduler.register_job(GitWatchJobSpec(watch, enabled=enabled, schedule=DEFAULT_CRON))


def watch_routes(watch: GitWatch, docker_host: Callable[[str], Awaitable[Any]]) -> APIRouter:
    router = APIRouter()

    def _project(project: str) -> None:
        if not PROJECT_RE.match(project):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültiger Stack-Name.")

    @router.get(
        "/hosts/{host_id}/stacks/{project}/git/watch",
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400,)},
    )
    async def get_watch(host_id: str, project: str) -> dict:
        """Zuletzt gesehener Stand im Repository, nur aus dem Speicher von Deck."""
        _project(project)
        host = await docker_host(host_id)
        return {"watch": watch.state(host.id, project)}

    @router.post(
        "/hosts/{host_id}/stacks/{project}/git/check",
        responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}}
                   for code in (400, 404, 409,)},
    )
    async def check_now(host_id: str, project: str) -> dict:
        """Fragt jetzt nur den neuesten Commit ab; lädt keine Dateien und ändert nichts am Server."""
        _project(project)
        host = await docker_host(host_id)
        if not watch.adopted(host.id, project):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Der Stack ist nicht übernommen.")
        found = await watch.check(host.id, project)
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_SOURCE)
        return {"watch": found}

    return router
