"""Einbruchschutz und Datei-Waechter: regelmaessig je Server SSH-Anmeldungen,
Fail2ban, offene Ports und wichtige Dateien pruefen, mit dem bestaetigten Stand
vergleichen und Auffaelligkeiten als Sicherheitsereignis speichern und melden.

Beim ERSTEN Blick auf einen Server wird nur der Stand gemerkt (keine Flut an
"neuen" Ports/Dateien). Danach meldet jede Auffaelligkeit genau einmal; bestaetigt
der Nutzer ein Ereignis, wird der neue Stand als bekannt uebernommen.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity
from sqlalchemy import func, select

from . import intrusion as ix
from .antivirus import as_root
from .hostscope import host_scope
from .models import SecurityEventRecord
from .patching import load_baseline, load_baselines, save_baseline

if TYPE_CHECKING:
    from nodvard_sdk import Actor, ExtensionContext, Host

    from .defender import Defender

GUARD_TIMEOUT = 90
MAX_PARALLEL = 4
REALERT_S = 24 * 3600

EVENT_LABEL = {
    "bruteforce": "Angriff auf SSH",
    "new_login_ip": "Anmeldung von neuer Adresse",
    "new_port": "Neuer offener Port",
    "file_changed": "Datei geändert",
    "fail2ban_down": "Fail2ban läuft nicht",
}


def event_out(r: SecurityEventRecord) -> dict[str, Any]:
    from .defender import _ts

    return {
        "id": r.id, "host_id": r.host_id, "host_name": r.host_name, "kind": r.kind, "kind_label": EVENT_LABEL.get(r.kind, r.kind),
        "severity": r.severity, "title": r.title, "detail": r.detail, "acknowledged": r.acknowledged, "created_at": _ts(r.created_at),
    }


class Guard:
    def __init__(self, ctx: ExtensionContext, defender: Defender) -> None:
        self._ctx = ctx
        self._defender = defender
        self._running: set[str] = set()

    async def _settings(self) -> dict[str, Any]:
        return await self._ctx.settings.get()

    # --- Pruefen ---------------------------------------------------------------

    async def inspect(self, host: Host, *, notify: bool = True) -> dict[str, Any]:
        from .defender import _describe, _now

        if host.id in self._running:
            return {"skipped": True}
        self._running.add(host.id)
        try:
            settings = await self._settings()
            files = settings.get("guard_watch_files") or ix.DEFAULT_WATCH_FILES
            try:
                res = await self._ctx.exec.run(host, as_root(ix.guard_command(files), required=False), timeout_s=GUARD_TIMEOUT)
                snap = ix.parse_guard(res.stdout or "", settings.get("guard_dynamic_processes"))
            except Exception as exc:  # noqa: BLE001
                await save_baseline(self._ctx, host.id, "guard", {"error": _describe(exc), "checked_at": _now().timestamp()})
                return {"error": _describe(exc)}
            events = await self._compare(host, snap, settings)
            view = self._view(snap, await load_baseline(self._ctx, host.id, "ports") or {},
                              await load_baseline(self._ctx, host.id, "logins") or {}, await load_baseline(self._ctx, host.id, "files") or {})
            view["checked_at"] = _now().timestamp()
            await save_baseline(self._ctx, host.id, "guard", view)
            if events and notify:
                await self._notify(host, events)
            return {"events": len(events)}
        finally:
            self._running.discard(host.id)

    async def inspect_all(self, hosts: list[Host] | None = None) -> dict[str, Any]:
        hosts = hosts if hosts is not None else await self._defender.target_hosts()
        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def one(h: Host) -> dict[str, Any]:
            async with sem:
                return await self.inspect(h)

        results = await asyncio.gather(*(one(h) for h in hosts), return_exceptions=True)
        await self._ctx.ws.broadcast("defender", {"guard": len(hosts)})
        return {"hosts": len(hosts), "events": sum(r.get("events", 0) for r in results if isinstance(r, dict))}

    def start(self, hosts: list[Host]) -> int:
        todo = [h for h in hosts if h.id not in self._running]
        if todo:
            asyncio.ensure_future(self.inspect_all(todo))
        return len(todo)

    def is_running(self, host_id: str) -> bool:
        return host_id in self._running

    async def _compare(self, host: Host, snap: ix.GuardSnapshot, settings: dict[str, Any]) -> list[dict[str, Any]]:
        from .defender import _now

        now = _now().timestamp()
        events: list[dict[str, Any]] = []

        # Offene Ports
        ports = await load_baseline(self._ctx, host.id, "ports")
        current_keys = {p.key for p in snap.ports}
        if ports is None:
            ports = {"known": sorted(current_keys), "alerted": []}
        else:
            known, alerted = set(ports.get("known", [])), set(ports.get("alerted", []))
            fresh = [p for p in snap.ports if not ix.port_is_known(p, known, dynamic_min=snap.dynamic_min) and p.key not in alerted]
            if fresh:
                events.append({
                    "kind": "new_port", "severity": "warning" if any(p.public for p in fresh) else "info",
                    "title": "Neuer offener Port: " + ", ".join(p.label for p in fresh[:5]),
                    "detail": {"ports": [{"key": p.key, "port": p.port, "proto": p.proto, "address": p.address, "process": p.process,
                                          "public": p.public, "dynamic": p.dynamic, "count": p.count} for p in fresh]},
                })
                ports["alerted"] = sorted(alerted | {p.key for p in fresh})
        await save_baseline(self._ctx, host.id, "ports", ports)

        # Erfolgreiche Anmeldungen von neuen Adressen
        logins = await load_baseline(self._ctx, host.id, "logins")
        seen_now = {lg.ip for lg in snap.logins}
        if logins is None:
            logins = {"known": sorted(seen_now)}
        else:
            known_ips = set(logins.get("known", []))
            new = [lg for lg in snap.logins if lg.ip not in known_ips]
            if new and settings.get("guard_notify_new_login", True):
                first: dict[str, ix.Login] = {}
                for lg in new:
                    first.setdefault(lg.ip, lg)
                events.append({
                    "kind": "new_login_ip", "severity": "warning",
                    "title": "Anmeldung von neuer Adresse: " + ", ".join(f"{lg.user}@{lg.ip}" for lg in list(first.values())[:4]),
                    "detail": {"logins": [{"user": lg.user, "ip": lg.ip, "method": lg.method, "ts": lg.ts} for lg in first.values()]},
                })
            logins["known"] = sorted(known_ips | seen_now)[-500:]
        await save_baseline(self._ctx, host.id, "logins", logins)

        # Brute-Force: viele Fehlversuche von einer Adresse, die (noch) nicht gesperrt ist.
        # Kritisch nur, wenn Fail2ban sicher fehlt oder steht -- ohne root ist sein
        # Zustand nur unbekannt ("noaccess").
        threshold = int(settings.get("bruteforce_threshold") or 20)
        state = await load_baseline(self._ctx, host.id, "alerts") or {}
        bf_alerted: dict[str, float] = {ip: ts for ip, ts in (state.get("bruteforce") or {}).items() if ts > now - REALERT_S}
        banned = snap.banned
        attackers = sorted((a for a in snap.failures.values() if a.count >= threshold and a.ip not in banned and a.ip not in bf_alerted),
                           key=lambda a: -a.count)
        if attackers:
            events.append({
                "kind": "bruteforce", "severity": "critical" if snap.fail2ban in ("stopped", "none") else "warning",
                "title": f"{sum(a.count for a in attackers)} fehlgeschlagene SSH-Anmeldungen von {len(attackers)} Adresse(n)",
                "detail": {"attackers": [{"ip": a.ip, "count": a.count, "users": sorted(a.users)[:10]} for a in attackers[:20]],
                           "fail2ban": snap.fail2ban},
            })
            for a in attackers:
                bf_alerted[a.ip] = now
        # Fail2ban installiert, aber gestoppt -> einmal am Tag erinnern
        if snap.fail2ban == "stopped" and (state.get("f2b_down") or 0) < now - REALERT_S:
            events.append({"kind": "fail2ban_down", "severity": "warning", "title": "Fail2ban ist installiert, läuft aber nicht", "detail": {}})
            state["f2b_down"] = now
        state["bruteforce"] = bf_alerted
        await save_baseline(self._ctx, host.id, "alerts", state)

        # Datei-Waechter
        files = await load_baseline(self._ctx, host.id, "files")
        if settings.get("file_watch_enabled", True) and snap.files:
            if files is None:
                files = {"accepted": {p: ix.file_entry_dict(e) for p, e in snap.files.items()}, "alerted": {}}
            else:
                accepted: dict[str, Any] = files.get("accepted", {})
                alerted: dict[str, str] = files.get("alerted", {})
                changes = ix.diff_files(accepted, snap.files)
                report = []
                for ch in changes:
                    entry = snap.files.get(ch.path)
                    marker = entry.sha256 + entry.mode + entry.owner if entry else "removed"
                    if ch.auto_accept and entry is not None:
                        accepted[ch.path] = ix.file_entry_dict(entry)
                        alerted.pop(ch.path, None)
                        continue
                    if alerted.get(ch.path) == marker:
                        continue
                    alerted[ch.path] = marker
                    report.append(ch)
                if report:
                    worst = "critical" if any(c.severity == "critical" for c in report) else ("warning" if any(c.severity == "warning" for c in report) else "info")
                    events.append({
                        "kind": "file_changed", "severity": worst,
                        "title": ("Wichtige Datei geändert: " if len(report) == 1 else f"{len(report)} wichtige Dateien geändert: ")
                        + ", ".join(c.path for c in report[:3]),
                        "detail": {"changes": [{"path": c.path, "change": c.change, "severity": c.severity, "note": c.note} for c in report]},
                    })
                files = {"accepted": accepted, "alerted": alerted}
            await save_baseline(self._ctx, host.id, "files", files)

        if events:
            async with self._ctx.db.session() as session:
                from .defender import _new_id

                for ev in events:
                    ev["id"] = _new_id("ev")
                    session.add(SecurityEventRecord(
                        id=ev["id"], host_id=host.id, host_name=host.display_name or host.name, kind=ev["kind"],
                        severity=ev["severity"], title=ev["title"][:255], detail=ev["detail"], acknowledged=False, created_at=_now(),
                    ))
            for ev in events:
                await self._ctx.audit.log(
                    action=f"nexus_soc.guard_{ev['kind']}", outcome="success", target_type="host", target_id=host.id,
                    reason=ev["title"][:500], detail=ev["detail"], correlation_id=ev["id"],
                )
        return events

    def _view(self, snap: ix.GuardSnapshot, ports: dict[str, Any], logins: dict[str, Any], files: dict[str, Any]) -> dict[str, Any]:
        """Was die Oberflaeche zeigt -- ohne neue SSH-Verbindung."""
        known_ports = set(ports.get("known", []))
        attackers = sorted(snap.failures.values(), key=lambda a: -a.count)
        banned = snap.banned
        accepted = files.get("accepted", {})
        pending = [p for p in files.get("alerted", {}) if p in accepted or p in snap.files]
        return {
            "is_root": snap.is_root,
            "ssh_log_found": snap.ssh_log_found,
            "failed_24h": snap.failed_total,
            "attackers": [{"ip": a.ip, "count": a.count, "users": sorted(a.users)[:8], "last_ts": a.last_ts, "banned": a.ip in banned}
                          for a in attackers[:15]],
            "attacker_count": len(attackers),
            "logins": [{"user": lg.user, "ip": lg.ip, "method": lg.method, "ts": lg.ts} for lg in snap.logins[-8:][::-1]],
            "fail2ban": snap.fail2ban,
            "jails": {name: {k: v for k, v in j.items()} for name, j in snap.jails.items()},
            "banned_count": len(banned),
            "ports": [{"key": p.key, "proto": p.proto, "address": p.address, "port": p.port, "process": p.process,
                       "public": p.public, "dynamic": p.dynamic, "count": p.count,
                       "new": not ix.port_is_known(p, known_ports, dynamic_min=snap.dynamic_min)} for p in snap.ports],
            "ssh_port": snap.sshd.get("port"),
            # None = nicht lesbar (ohne root liefert `sshd -T` nichts) -- nicht "keine Befunde".
            "ssh_findings": [{"key": f.key, "value": f.value, "severity": f.severity, "text": f.text, "advice": f.advice}
                             for f in snap.ssh_findings] if snap.sshd else None,
            "files_watched": len(snap.files),
            "files_pending": sorted(set(pending)),
        }

    async def _notify(self, host: Host, events: list[dict[str, Any]]) -> None:
        worst = "critical" if any(e["severity"] == "critical" for e in events) else ("warning" if any(e["severity"] == "warning" for e in events) else "info")
        if worst == "info":
            return
        name = host.display_name or host.name
        title = events[0]["title"] if len(events) == 1 else f"{len(events)} Sicherheitsereignisse auf {name}"
        body = "\n".join(f"- {EVENT_LABEL.get(e['kind'], e['kind'])}: {e['title']}" for e in events)
        payload: dict[str, Any] = {"path": f"/ext/nexus-soc/soc?tab=guard&host={host.id}"}
        if worst != "critical":
            # Wartungsfenster stellen nur Warnungen stumm (z. B. neue Ports nach einem
            # Dienst-Neustart). Kritisches (Einbruch, Rootkit-Verdacht) bleibt immer hörbar.
            payload.update(host_scope([host.id]))
        await self._ctx.notify.send(Notification(
            title=f"{name}: {title}" if len(events) == 1 else title, body=body,
            severity=Severity.CRITICAL if worst == "critical" else Severity.WARNING,
            payload=payload,
        ))

    # --- Uebersicht & Bestaetigen ----------------------------------------------------

    async def overview(self) -> dict[str, Any]:
        hosts = await self._defender.target_hosts()
        views = await load_baselines(self._ctx, "guard")
        async with self._ctx.db.session() as session:
            open_by_host = dict((await session.execute(
                select(SecurityEventRecord.host_id, func.count()).where(SecurityEventRecord.acknowledged.is_(False))
                .group_by(SecurityEventRecord.host_id)
            )).all())
        rows = []
        for h in hosts:
            rows.append({
                "host_id": h.id, "host_name": h.display_name or h.name, "host_status": h.status.value,
                "view": views.get(h.id), "checking": h.id in self._running, "open_events": int(open_by_host.get(h.id, 0)),
            })
        checked = [r["view"] for r in rows if r["view"] and not r["view"].get("error")]
        settings = await self._settings()
        return {
            "hosts": rows,
            "summary": {
                "failed_24h": sum(v.get("failed_24h", 0) for v in checked),
                "attackers": sum(v.get("attacker_count", 0) for v in checked),
                "banned": sum(v.get("banned_count", 0) for v in checked),
                "open_events": sum(r["open_events"] for r in rows),
                "fail2ban_running": sum(1 for v in checked if v.get("fail2ban") == "running"),
                "fail2ban_unreadable": sum(1 for v in checked if v.get("fail2ban") == "noaccess"),
                "hosts": len(rows),
                "new_ports": sum(1 for v in checked for p in v.get("ports", []) if p.get("new")),
                "files_pending": sum(len(v.get("files_pending", [])) for v in checked),
            },
            "config": {
                "enabled": settings.get("guard_enabled", True),
                "interval_min": int(settings.get("guard_interval_min") or 15),
                "threshold": int(settings.get("bruteforce_threshold") or 20),
                "file_watch": settings.get("file_watch_enabled", True),
            },
        }

    async def list_events(self, *, include_acknowledged: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        stmt = select(SecurityEventRecord).order_by(SecurityEventRecord.created_at.desc()).limit(limit)
        if not include_acknowledged:
            stmt = stmt.where(SecurityEventRecord.acknowledged.is_(False))
        async with self._ctx.db.session() as session:
            return [event_out(r) for r in (await session.execute(stmt)).scalars()]

    async def acknowledge(self, event_id: str, actor: Actor | None = None) -> bool:
        """Ereignis erledigen und -- je nach Art -- den neuen Stand als bekannt uebernehmen."""
        async with self._ctx.db.session() as session:
            row = await session.get(SecurityEventRecord, event_id)
            if row is None or row.acknowledged:
                return False
            row.acknowledged = True
            host_id, kind, detail = row.host_id, row.kind, dict(row.detail or {})
        if kind == "new_port":
            ports = await load_baseline(self._ctx, host_id, "ports") or {"known": [], "alerted": []}
            keys = {p["key"] for p in detail.get("ports", [])}
            ports["known"] = sorted(set(ports.get("known", [])) | keys)
            ports["alerted"] = sorted(set(ports.get("alerted", [])) - keys)
            await save_baseline(self._ctx, host_id, "ports", ports)
        elif kind == "file_changed":
            files = await load_baseline(self._ctx, host_id, "files")
            view = await load_baseline(self._ctx, host_id, "guard") or {}
            if files is not None:
                paths = {c["path"] for c in detail.get("changes", [])}
                current = await self._current_files(host_id)
                for p in paths:
                    if p in current:
                        files["accepted"][p] = current[p]
                    else:
                        files["accepted"].pop(p, None)
                    files["alerted"].pop(p, None)
                await save_baseline(self._ctx, host_id, "files", files)
                view["files_pending"] = [p for p in view.get("files_pending", []) if p not in paths]
                await save_baseline(self._ctx, host_id, "guard", view)
        await self._ctx.audit.log(
            action="nexus_soc.guard_acknowledged", outcome="success", target_type="host", target_id=host_id,
            reason=f"{EVENT_LABEL.get(kind, kind)} bestätigt", correlation_id=event_id, **({"actor": actor} if actor is not None else {}),
        )
        return True

    async def acknowledge_all(self, actor: Actor | None = None) -> int:
        ids = [e["id"] for e in await self.list_events(limit=1000)]
        done = 0
        for event_id in ids:
            done += int(await self.acknowledge(event_id, actor))
        return done

    async def _current_files(self, host_id: str) -> dict[str, Any]:
        """Aktuellen Datei-Stand frisch holen (fuer das Uebernehmen beim Bestaetigen)."""
        host = await self._ctx.hosts.get(host_id)
        if host is None:
            return {}
        settings = await self._settings()
        try:
            res = await self._ctx.exec.run(host, as_root(ix.guard_command(settings.get("guard_watch_files") or None), required=False),
                                           timeout_s=GUARD_TIMEOUT)
        except Exception:  # noqa: BLE001
            return {}
        return {p: ix.file_entry_dict(e) for p, e in ix.parse_guard(res.stdout or "").files.items()}

    async def summary_line(self) -> tuple[str | None, list[str]]:
        """Zeile und Aufgaben fuer das Morgen-Briefing."""
        o = await self.overview()
        sm = o["summary"]
        checked = [r for r in o["hosts"] if r["view"] and not r["view"].get("error")]
        if not checked:
            return None, []
        line = (f"Einbruchschutz: {sm['failed_24h']} fehlgeschlagene SSH-Anmeldungen (24 h) von {sm['attackers']} Adresse(n), "
                f"{sm['banned']} gesperrt, {sm['open_events']} offene Ereignis(se)")
        todo = []
        if sm["files_pending"]:
            todo.append(f"{sm['files_pending']} geänderte Systemdatei(en) prüfen")
        if sm["new_ports"]:
            todo.append(f"{sm['new_ports']} neue offene Port(s) prüfen")
        return line, todo
