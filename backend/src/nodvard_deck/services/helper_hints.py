"""Hinweis in der Übersicht, wenn auf einem Server die Hilfsskripte der Module fehlen oder veraltet sind.

Nach einem Update bringt Nodvard Deck neue Hilfsskripte mit, auf den Server kommen sie aber erst mit dem
Einrichtungsbefehl. Bis dahin laufen Shield und Server-Zustand dort über die volle sudo-Regel, ohne sie (nur die Regeln
der Skripte) gar nicht mit root; Docker nutzt nur sein Skript. Das soll man sehen, ohne „Verbindung prüfen“ zu drücken.
Gemeldet wird nur, wenn sudo dort Skripte erlaubt (volle Regel `NOPASSWD: ALL` oder die Regel eines Skripts) und
mindestens ein Skript nicht Byte für Byte das mitgelieferte ist; nie für root (braucht kein Skript) und nie für Windows.

Ebenso gemeldet wird eine volle Regel, die nicht nötig ist: Jedes Modul, das dort root braucht, hat ein brauchbares
Skript mit eigener Regel (außer sie ist beim Server gewollt, `hosts.full_sudo_wanted`).

Der Stand je Server liegt in einem internen Merker (`KEY_STATE`). Ihn schreiben „Verbindung prüfen“ und der Kern-Job
`helper-hints` (alle 6 Stunden ein lesender Befehl je Server, derselbe wie bei der Prüfung). Die Übersicht liest nur
den Merker, also keine SSH-Abfrage beim Seitenaufruf. Wegklicken gilt je Server für genau die Skripte und Versionen, die
gerade fehlen: Bringt ein Update neuere mit, erscheint der Hinweis wieder."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core import ssh
from ..db import utcnow
from ..db.session import session_scope
from ..ext.root_helpers import RootHelperFile
from ..models import Host
from . import hosts as hosts_service
from . import settings as settings_service

logger = logging.getLogger("nodvard_deck.helper_hints")

JOB_KEY = "helper-hints"
JOB_NAME = "Hilfsskripte auf den Servern prüfen"
SCHEDULE = "41 */6 * * *"

KEY_STATE = "hosts.helper_hints.state"
"""Interner Merker (Host-ID -> {"stale": {ext_id: Version}, "removable": bool, "checked_at": ISO, "dismissed":
{ext_id: Version}, "dismissed_removable": bool}); nicht über die Einstellungs-API sichtbar."""

COMMAND_TIMEOUT_S = 20.0

_RUNAS_RE = re.compile(r"\(([^()]*)\)")
_TAG_RE = re.compile(r"([A-Z_]+):")


def _root_commands(line: str) -> list[str] | None:
    """Die Befehlsliste einer Zeile von `sudo -n -l`, wenn sie für root gilt und kein Passwort verlangt (auch mit
    weiteren Angaben wie `NOPASSWD: SETENV:`), sonst `None`."""
    text = line.strip()
    runas = _RUNAS_RE.match(text)
    if runas is None or runas.group(1).split(":")[0].strip() not in ("root", "ALL"):
        return None
    rest = text[runas.end():].lstrip()
    tags: list[str] = []
    while (tag := _TAG_RE.match(rest)) is not None:
        tags.append(tag.group(1))
        rest = rest[tag.end():].lstrip()
    return [command.strip() for command in rest.split(",")] if "NOPASSWD" in tags else None


def _is_full_rule(line: str) -> bool:
    """`(root) NOPASSWD: ALL`, `(ALL : ALL) NOPASSWD: ALL`, auch mit weiteren Angaben. Nur mit genau `ALL`."""
    return _root_commands(line) == ["ALL"]


def allows_helper(sudo_lines: Sequence[str], path: str) -> bool:
    """Ob eine Regel ohne Passwort genau dieses Skript für root erlaubt (`path` als eigener Eintrag der Liste, nicht als
    Argument eines anderen Befehls)."""
    return any(path in (_root_commands(line) or []) for line in sudo_lines)


def has_full_rule(sudo_lines: Sequence[str]) -> bool:
    """Ob `sudo -n -l` die volle Regel ohne Passwort zeigt. Dieselbe Prüfung nutzen „Verbindung prüfen“ und der Job,
    damit der Hinweis nicht zwischen beiden hin und her springt."""
    return any(_is_full_rule(line) for line in sudo_lines)


def sudo_runs_helpers(sudo_lines: Sequence[str], seen: Mapping[str, Any]) -> bool:
    """Ob sudo auf dem Server Hilfsskripte ausführen lässt: mit der vollen Regel oder der eigenen Regel eines Skripts
    (`seen` aus `host_check.parse_helper_check`). Nur dann ist ein fehlendes oder veraltetes Skript einen Hinweis wert."""
    return has_full_rule(sudo_lines) or any(item.tight_rule for item in seen.values())


def judge(
    helpers: Sequence[RootHelperFile], sudo_lines: Sequence[str], seen: Mapping[str, Any]
) -> tuple[dict[str, int], bool]:
    """Was der Hinweis meldet: die Skripte, die nicht das mitgelieferte sind, und ob dort die volle Regel fehlt (dann
    haben diese Module gar kein root). Gemeinsam für „Verbindung prüfen“ und den Job."""
    if not sudo_runs_helpers(sudo_lines, seen):
        return {}, False
    return stale_helpers(helpers, seen), not has_full_rule(sudo_lines)


def full_rule_removable(helpers: Sequence[RootHelperFile], sudo_lines: Sequence[str], seen: Mapping[str, Any]) -> bool:
    """Ob die volle Regel da, aber unnötig ist: Jedes Skript ist brauchbar und hat seine eigene Regel. Ob jedes Modul
    mit root-Bedarf ein Skript hat, prüft der Aufrufer."""
    return bool(helpers) and has_full_rule(sudo_lines) and all(
        (item := seen.get(helper.install_path)) is not None and item.tight_rule and usable(helper, item)
        for helper in helpers
    )


def stale_helpers(helpers: Sequence[RootHelperFile], seen: Mapping[str, Any]) -> dict[str, int]:
    """Die Skripte, die nicht das mitgelieferte sind (fehlen, älter, verändert, unlesbar), als {ext_id: Version des
    mitgelieferten}. `seen` kommt aus `host_check.parse_helper_check` (je Ort auf dem Server)."""
    stale: dict[str, int] = {}
    for helper in helpers:
        item = seen.get(helper.install_path)
        if item is None or not item.present or item.sha256 != helper.sha256:
            stale[helper.ext_id] = helper.version
    return stale


def usable(helper: RootHelperFile, item: Any) -> bool:
    """Ob das Modul über dieses Skript root bekommt, sofern sudo es erlaubt: Nur root kann es ändern, und es ist das
    mitgelieferte oder, bei `calls_any_version`, eine lesbar ältere Fassung. „Verbindung prüfen“ und der Job urteilen
    beide hiermit."""
    if item is None or not item.present or not item.owner_ok:
        return False
    if item.sha256 == helper.sha256:
        return True
    return helper.calls_any_version and item.version is not None and item.version < helper.version


def still_used(
    helpers: Sequence[RootHelperFile], sudo_lines: Sequence[str], seen: Mapping[str, Any], stale: Mapping[str, int]
) -> list[str]:
    """Die Module aus `stale`, die ihr älteres Skript trotzdem aufrufen (`usable`) und die sudo dort lässt: Sie haben
    dort weiter root, nur mit der alten Fassung."""
    full = has_full_rule(sudo_lines)
    return [
        helper.ext_id for helper in helpers
        if helper.ext_id in stale
        and usable(helper, item := seen.get(helper.install_path)) and (full or item.tight_rule)
    ]


def _entry(state: Mapping[str, Any], host_id: str) -> dict[str, Any] | None:
    entry = state.get(host_id)
    return dict(entry) if isinstance(entry, dict) else None


async def record(
    session: AsyncSession,
    host_id: str,
    stale: Mapping[str, int] | None,
    *,
    without_root: bool = False,
    still_used: Sequence[str] = (),
    removable: bool = False,
) -> None:
    """Merkt das Ergebnis einer Prüfung. Ohne veraltete Skripte und ohne unnötige volle Regel ist nichts zu melden
    (Merker weg, auch das Wegklicken)."""
    state: dict[str, Any] = dict(await settings_service.get_global(session, KEY_STATE, {}) or {})
    before = _entry(state, host_id)
    if not stale and not removable:
        if before is None:
            return
        state.pop(host_id, None)
    else:
        entry = before or {}
        entry["stale"] = dict(stale or {})
        entry["without_root"] = without_root
        entry["still_used"] = sorted(still_used)
        entry["removable"] = removable
        if not removable:
            entry.pop("dismissed_removable", None)
        entry["checked_at"] = utcnow().isoformat()
        state[host_id] = entry
    await settings_service.set_global(session, KEY_STATE, state)


async def dismiss(session: AsyncSession, host_id: str) -> bool:
    """Blendet den Hinweis für diesen Server aus, bis sich die fehlenden Skripte ändern oder die volle Regel einmal weg
    war. False: es gab keinen."""
    state: dict[str, Any] = dict(await settings_service.get_global(session, KEY_STATE, {}) or {})
    entry = _entry(state, host_id)
    if entry is None or not (entry.get("stale") or entry.get("removable")):
        return False
    if entry.get("stale"):
        entry["dismissed"] = dict(entry["stale"])
    if entry.get("removable"):
        entry["dismissed_removable"] = True
    state[host_id] = entry
    await settings_service.set_global(session, KEY_STATE, state)
    return True


@dataclass(frozen=True)
class Hint:
    host_id: str
    host_name: str
    modules: list[str]
    """Anzeigenamen der Module, deren Skript fehlt oder veraltet ist."""
    checked_at: datetime
    without_root: bool = False
    """Ohne volle sudo-Regel: Diese Module haben dort gerade gar kein root."""
    still_used: list[str] = field(default_factory=list)
    """Anzeigenamen der Module aus `modules`, die ihr veraltetes Skript weiter aufrufen und dort root haben."""
    no_fallback: list[str] = field(default_factory=list)
    """Anzeigenamen der Module aus `modules`, die ohne ihr Skript auch mit der vollen Regel kein root haben."""
    full_rule_removable: bool = False
    """Die volle sudo-Regel ist dort nicht nötig, die Skripte reichen (`modules` kann dann leer sein)."""


async def current_hints(
    session: AsyncSession, helpers_for: Callable[[Host], Sequence[RootHelperFile]],
    helpers_suffice: Callable[[Host], bool] = lambda _host: True,
) -> list[Hint]:
    """Die offenen Hinweise, nur aus dem Merker. Ein Modul, das inzwischen aus ist, zählt nicht mehr mit; die unnötige
    volle Regel nur, solange jedes Modul mit root-Bedarf ein Skript hat (`helpers_suffice`) und sie nicht gewollt
    ist."""
    state: dict[str, Any] = dict(await settings_service.get_global(session, KEY_STATE, {}) or {})
    if not state:
        return []
    hosts = (
        await session.execute(select(Host).where(Host.id.in_(list(state))).options(selectinload(Host.tags)))
    ).scalars().all()
    hints: list[Hint] = []
    for host in sorted(hosts, key=lambda h: (h.display_name or h.name).lower()):
        entry = _entry(state, host.id) or {}
        stale = entry.get("stale") or {}
        if not host.enabled or host.os_family != "linux" or not isinstance(stale, dict):
            continue
        shown = bool(stale) and entry.get("dismissed") != stale
        helpers = [h for h in helpers_for(host) if h.ext_id in stale] if shown else []
        removable = (
            entry.get("removable") is True and not entry.get("dismissed_removable")
            and not hosts_service.full_sudo_wanted(host) and helpers_suffice(host)
        )
        if not helpers and not removable:
            continue
        used = entry.get("still_used") if isinstance(entry.get("still_used"), list) else []
        try:
            checked_at = datetime.fromisoformat(str(entry.get("checked_at")))
        except ValueError:
            checked_at = utcnow()
        hints.append(Hint(
            host_id=host.id, host_name=host.display_name or host.name, modules=[h.ext_name for h in helpers],
            checked_at=checked_at, without_root=entry.get("without_root") is True,
            still_used=[h.ext_name for h in helpers if h.ext_id in used],
            no_fallback=[h.ext_name for h in helpers if not h.sudo_fallback],
            full_rule_removable=removable,
        ))
    return hints


async def _check_host(
    host_id: str, helpers_for: Callable[[Host], Sequence[RootHelperFile]], helpers_suffice: Callable[[Host], bool],
) -> str:
    """Prüft einen Server (lesend, ein Befehl über die Verbindung aus dem Pool) und merkt das Ergebnis."""
    from ..config import get_settings
    from .host_check import helper_check_command, parse_helper_check, split_sudo_lines

    settings = get_settings()
    async with session_scope() as session:
        host = (
            await session.execute(select(Host).where(Host.id == host_id).options(selectinload(Host.tags)))
        ).scalars().first()
        if host is None:
            return "gone"
        credential = await hosts_service.default_credential(session, host.id)
        helpers = list(helpers_for(host))
        if (
            not host.enabled or host.os_family != "linux" or credential is None
            or not credential.kind.startswith("ssh") or credential.username == "root" or not helpers
        ):
            await record(session, host.id, None)
            return "skipped"
        target = await hosts_service.resolve_connection_target(session, settings, host, credential)
        conn = await ssh.get_ssh_pool().get(
            session, target, credential_id=credential.id, connect_timeout_s=settings.ssh_connect_timeout_s
        )
        _code, out, _err = await ssh.run(conn, helper_check_command(helpers), timeout_s=COMMAND_TIMEOUT_S)
        sudo_lines, seen = split_sudo_lines(out), parse_helper_check(out, helpers)
        stale, without_root = judge(helpers, sudo_lines, seen)
        removable = helpers_suffice(host) and full_rule_removable(helpers, sudo_lines, seen)
        await record(
            session, host.id, stale, without_root=without_root, still_used=still_used(helpers, sudo_lines, seen, stale),
            removable=removable,
        )
        return "hint" if stale or removable else "ok"


async def run_once(
    helpers_for: Callable[[Host], Sequence[RootHelperFile]] | None = None,
    helpers_suffice: Callable[[Host], bool] | None = None,
) -> dict[str, int]:
    """Der Kern-Job: alle Linux-Server nacheinander. Ein nicht erreichbarer Server behält seinen letzten Stand."""
    if helpers_for is None:
        # Erst hier: Der Dienst soll beim Import nicht von der API-Schicht abhängen.
        from ..api.v1.host_access import root_helpers_for as helpers_for
    if helpers_suffice is None:
        from ..api.v1.host_access import root_needs_helpers_only as helpers_suffice
    async with session_scope() as session:
        ids = list((await session.execute(
            select(Host.id).where(Host.enabled.is_(True), Host.os_family == "linux").order_by(Host.name)
        )).scalars().all())
        # Gelöschte, abgeschaltete und Nicht-Linux-Server fallen aus dem Merker (angezeigt würden sie ohnehin nicht).
        state: dict[str, Any] = dict(await settings_service.get_global(session, KEY_STATE, {}) or {})
        kept = {host_id: entry for host_id, entry in state.items() if host_id in ids}
        if kept != state:
            await settings_service.set_global(session, KEY_STATE, kept)
    counts = {"checked": 0, "hint": 0, "ok": 0, "skipped": 0, "failed": 0}
    for host_id in ids:
        try:
            outcome = await _check_host(host_id, helpers_for, helpers_suffice)
        except Exception as exc:  # noqa: BLE001 - ein Server hält die anderen nicht auf, der Stand bleibt
            counts["failed"] += 1
            logger.info("helper_hints_check_failed host_id=%s error=%s", host_id, type(exc).__name__)
            continue
        counts["checked"] += 1
        if outcome in counts:
            counts[outcome] += 1
    return counts


async def _job_handler(**_: Any) -> dict[str, int]:
    return await run_once()


async def sync_job() -> None:
    """Legt den Job an oder aktualisiert ihn und plant ihn ein."""
    from ..core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
    from ..ext.runtime import get_extension_runtime
    from . import jobs as jobs_service

    async with session_scope() as session:
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_KEY, name=JOB_NAME,
            kind="core", schedule=SCHEDULE, params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_KEY, _job_handler)
    await get_scheduler_service().schedule(job, _job_handler)


__all__ = [
    "JOB_KEY",
    "KEY_STATE",
    "SCHEDULE",
    "Hint",
    "current_hints",
    "dismiss",
    "full_rule_removable",
    "has_full_rule",
    "record",
    "run_once",
    "stale_helpers",
    "sync_job",
]
