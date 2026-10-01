"""Virenschutz-Ablauf: Scans ausfuehren, Funde speichern und in Quarantaene
verschieben, Haertungs-Audits, Schutzstatus je Server.

**Quarantaene ohne Freigabe-Schritt, bewusst:** ein Virenschutz muss einen Fund sofort
unschaedlich machen, nicht erst nach einer Freigabe am naechsten Morgen. Das
automatische Verschieben ist eine eigene Einstellung (`auto_quarantine`, an wie im
Original), ist umkehrbar (Wiederherstellen) und steht jedes Mal im Kern-Audit-Log.
Loeschen, Wiederherstellen und Installieren gehen dagegen durch das Aktions-Gate.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity
from sqlalchemy import func, select, update

from . import antivirus as av
from .hostscope import host_scope
from .models import AuditRecord, FindingRecord, ScanRecord

if TYPE_CHECKING:
    from nodvard_sdk import Actor, ExtensionContext, Host

SCAN_TIMEOUTS = {"quick": 20 * 60, "deep": 4 * 60 * 60, "watch": 5 * 60, "custom": 60 * 60}
AUDIT_TIMEOUT = 30 * 60
MAX_PARALLEL = 3

SOC_PATH = "/ext/nexus-soc/soc"

# Ein Lauf, dessen Fund-Zeilen nicht verlaesslich lesbar waren (`ScanResult.unreliable`): Hinweis am Scan,
# Notiz an jedem Fund und Text der Push-Meldung. Es wird nichts automatisch verschoben.
UNRELIABLE_HINT = (
    "Die Meldung von ClamAV ist nicht eindeutig lesbar (zum Beispiel ein Zeilenumbruch im Dateinamen). "
    "Es wurde nichts automatisch verschoben – bitte auf dem Server nachsehen."
)
UNRELIABLE_NOTE = (
    "Der Pfad ist nicht gesichert: Die Ausgabe von ClamAV war nicht eindeutig lesbar (zum Beispiel ein "
    "Zeilenumbruch im Dateinamen). Bitte vor dem Verschieben auf dem Server nachsehen."
)

KIND_LABEL = {"quick": "Schnellscan", "deep": "Tiefenscan", "watch": "Echtzeit-Wächter", "custom": "Scan"}
# Die "grossen" Scans: je Server laeuft hoechstens einer davon, und der Waechter wartet,
# bis er fertig ist -- jeder clamscan laedt die ganze Datenbank (~1 GB RAM).
BIG_SCAN_KINDS = ("quick", "deep", "custom")

_log = logging.getLogger("nodvard_deck.ext.nexus-soc")

# Scans und Update-Laeufe leben als Hintergrund-Task im Prozess -- nach einem Neustart
# des Dashboards kommt ein abgebrochener Lauf nie zurueck.
ABORTED_BY_RESTART = "Abgebrochen – Dashboard wurde neu gestartet"


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def scan_out(r: ScanRecord) -> dict[str, Any]:
    return {
        "id": r.id, "host_id": r.host_id, "host_name": r.host_name, "kind": r.kind, "kind_label": KIND_LABEL.get(r.kind, r.kind),
        "paths": r.paths, "trigger": r.trigger, "status": r.status, "files_scanned": r.files_scanned,
        "infected": r.infected, "error": r.error, "output_tail": r.output_tail,
        "started_at": _ts(r.started_at), "finished_at": _ts(r.finished_at),
    }


def finding_out(r: FindingRecord) -> dict[str, Any]:
    return {
        "id": r.id, "scan_id": r.scan_id, "host_id": r.host_id, "host_name": r.host_name, "path": r.path,
        "signature": r.signature, "status": r.status, "quarantine_path": r.quarantine_path, "note": r.note,
        "detected_at": _ts(r.detected_at), "status_changed_at": _ts(r.status_changed_at),
    }


def audit_out(r: AuditRecord) -> dict[str, Any]:
    return {
        "id": r.id, "host_id": r.host_id, "host_name": r.host_name, "status": r.status,
        "hardening_index": r.hardening_index, "warnings": r.warnings, "suggestions": r.suggestions,
        "error": r.error, "created_at": _ts(r.created_at),
    }


class Defender:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._running: set[tuple[str, str]] = set()  # (host_id, kind) -- kein doppelter Lauf
        self._status_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    # --- Ziele -----------------------------------------------------------------

    async def _settings(self) -> dict[str, Any]:
        return await self._ctx.settings.get()

    async def target_hosts(self) -> list[Host]:
        """Alle verwalteten Linux-Server mit Zugangsdaten; optional nur mit Markierung."""
        settings = await self._settings()
        tag = (settings.get("av_host_tag") or "").strip() or None
        hosts = await self._ctx.hosts.list(tag=tag)
        return [h for h in hosts if h.os_family == "linux" and h.is_managed and h.has_credential]

    # --- Scans -----------------------------------------------------------------

    def is_running(self, host_id: str, kind: str) -> bool:
        return (host_id, kind) in self._running

    def _big_scan_running(self, host_id: str) -> bool:
        return any(self.is_running(host_id, k) for k in BIG_SCAN_KINDS)

    async def abort_interrupted_scans(self) -> int:
        """Beim Start: Scans, die beim letzten Beenden noch liefen, als abgebrochen
        markieren -- sonst stuenden sie fuer immer auf "laeuft", und die Scan-Liste
        fragte endlos alle paar Sekunden nach."""
        async with self._ctx.db.session() as session:
            result = await session.execute(
                update(ScanRecord).where(ScanRecord.status == "running")
                .values(status="error", error=ABORTED_BY_RESTART, finished_at=_now())
            )
            return result.rowcount or 0

    async def start_scans(self, hosts: list[Host], kind: str, *, paths: list[str] | None, trigger: str) -> list[str]:
        """Legt je Server einen Scan an und startet ihn im Hintergrund; gibt die IDs zurueck."""
        settings = await self._settings()
        if paths is None:
            paths = {
                "quick": settings.get("quick_scan_paths") or av.QUICK_PATHS,
                "deep": settings.get("deep_scan_paths") or av.DEEP_PATHS,
                "watch": settings.get("watch_paths") or av.WATCH_PATHS,
            }.get(kind, av.QUICK_PATHS)
        ids: list[str] = []
        todo: list[tuple[Host, str]] = []
        async with self._ctx.db.session() as session:
            for host in hosts:
                # Nie zwei clamscan gleichzeitig auf einem Server: gleiche Art
                # laeuft schon, oder ein grosser Scan laeuft -- dann weder ein zweiter
                # grosser noch der Waechter. Ein laufender Waechter (kurz) haelt einen
                # grossen Scan nicht auf.
                if self.is_running(host.id, kind) or self._big_scan_running(host.id):
                    if kind == "watch" and not self.is_running(host.id, kind):
                        _log.debug("nexus_soc_watch_skipped_big_scan_running host=%s", host.id)
                    continue
                scan_id = _new_id("scan")
                session.add(ScanRecord(
                    id=scan_id, host_id=host.id, host_name=host.display_name or host.name, kind=kind,
                    paths=list(paths), trigger=trigger, status="running", infected=0, started_at=_now(),
                ))
                self._running.add((host.id, kind))
                ids.append(scan_id)
                todo.append((host, scan_id))
        if todo:
            asyncio.ensure_future(self._run_many(todo, kind, list(paths)))
        return ids

    async def _run_many(self, todo: list[tuple[Host, str]], kind: str, paths: list[str]) -> None:
        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def one(host: Host, scan_id: str) -> None:
            async with sem:
                try:
                    await self._run_scan(host, scan_id, kind, paths)
                finally:
                    self._running.discard((host.id, kind))

        await asyncio.gather(*(one(h, s) for h, s in todo), return_exceptions=True)
        await self._ctx.ws.broadcast("defender", {"scans": [s for _, s in todo]})

    async def _run_scan(self, host: Host, scan_id: str, kind: str, paths: list[str]) -> None:
        settings = await self._settings()
        max_mb = int(settings.get("max_filesize_mb") or 50)
        # Zufallsmarke je Lauf (Fix N1): im Befehl und bei der Auswertung dieselbe, einem
        # Dateinamen auf dem Server nicht vorhersagbar.
        mark = av.new_rc_mark()
        if kind == "watch":
            minutes = int(settings.get("watch_interval_min") or 10) + 2
            # Einstellung "Echtzeit-Waechter mit clamdscan" (Standard aus): nur der
            # Waechter; Schnell- und Tiefenscan bleiben bei clamscan.
            command = av.build_watch_command(paths, minutes=minutes, max_filesize_mb=max_mb,
                                             use_clamd=bool(settings.get("watch_use_clamdscan", False)), mark=mark)
        else:
            command = av.build_scan_command(paths, max_filesize_mb=max_mb, exclude=settings.get("exclude_paths") or [],
                                            mark=mark)
        command = av.as_root(command, required=False)
        try:
            result = await self._ctx.exec.run(host, command, timeout_s=SCAN_TIMEOUTS.get(kind, 3600))
            output = (result.stdout or "") + (result.stderr or "")
            parsed = av.parse_scan_output(output, mark)
            if result.exit_code == 127:
                parsed = av.ScanResult(status="error", error=av.NOT_INSTALLED_MESSAGE)
        except Exception as exc:  # noqa: BLE001 - jeder Fehler landet sichtbar am Scan
            output = ""
            parsed = av.ScanResult(status="error", error=_describe(exc))

        finding_ids: list[str] = []
        async with self._ctx.db.session() as session:
            scan = await session.get(ScanRecord, scan_id)
            if scan is None:
                return
            scan.status = parsed.status
            scan.files_scanned = parsed.files_scanned
            scan.infected = parsed.infected
            scan.error = parsed.error or ("; ".join(parsed.errors[:3]) if parsed.errors else None)
            if parsed.unreliable and parsed.status == "infected":
                scan.error = UNRELIABLE_HINT
            scan.output_tail = output[-4000:] if output else None
            scan.finished_at = _now()
            for path, sig in parsed.findings:
                existing = (await session.execute(
                    select(FindingRecord).where(
                        FindingRecord.host_id == host.id, FindingRecord.path == path, FindingRecord.status == "detected"
                    )
                )).scalar_one_or_none()
                if existing is not None:
                    existing.scan_id = scan_id
                    if parsed.unreliable and not existing.note:
                        existing.note = UNRELIABLE_NOTE
                    finding_ids.append(existing.id)
                    continue
                fid = _new_id("f")
                session.add(FindingRecord(
                    id=fid, scan_id=scan_id, host_id=host.id, host_name=host.display_name or host.name, path=path,
                    signature=sig[:255], status="detected", detected_at=_now(),
                    note=UNRELIABLE_NOTE if parsed.unreliable else None,
                ))
                finding_ids.append(fid)

        if parsed.status == "infected":
            await self._ctx.audit.log(
                action="nexus_soc.malware_found", outcome="success", target_type="host", target_id=host.id,
                reason=f"{parsed.infected} Fund(e) bei {KIND_LABEL.get(kind, kind)}",
                detail={"scan_id": scan_id, "findings": [{"path": p, "signature": s} for p, s in parsed.findings[:20]],
                        "unreliable": parsed.unreliable},
                correlation_id=scan_id,
            )
            quarantined = 0
            # Nie automatisch, wenn die Pfade nicht verlaesslich sind: Ein Dateiname mit Zeilenumbruch
            # kann einen Fund fuer einen fremden Pfad vortaeuschen (`/etc/passwd: ... FOUND`), und die
            # Quarantaene laeuft als root. Dann entscheidet der Mensch (die Funde tragen eine Notiz).
            if settings.get("auto_quarantine", True) and not parsed.unreliable:
                for fid in finding_ids:
                    ok, _msg = await self.quarantine(fid, actor=None)
                    quarantined += int(ok)
            if parsed.unreliable:
                body = f"ClamAV meldet {parsed.infected} Fund(e). {UNRELIABLE_HINT}"
            else:
                names = ", ".join(f"{p} ({s})" for p, s in parsed.findings[:3])
                body = (
                    f"{len(parsed.findings)} Fund(e): {names}"
                    + (f"\n{quarantined} in Quarantäne verschoben." if quarantined else "\nNoch nicht in Quarantäne – bitte prüfen.")
                )
            await self._ctx.notify.send(Notification(
                title=f"Schadsoftware auf {host.display_name or host.name} gefunden",
                body=body,
                severity=Severity.CRITICAL,
                # Absichtlich ohne `host_id`: ein Fund ist nie Wartungslärm, ein Wartungsfenster
                # (Tiefenscan sonntags 03:30!) darf ihn nicht stummschalten (hostscope.py).
                payload={"path": f"{SOC_PATH}?tab=quarantine", "tags": ["biohazard"],
                         "actions": [{"label": "Quarantäne öffnen", "path": f"{SOC_PATH}?tab=quarantine"}]},
            ))
        await self._ctx.ws.broadcast("defender", {"scan": scan_id, "status": parsed.status})

    # --- Quarantaene -------------------------------------------------------------

    async def _finding(self, finding_id: str) -> FindingRecord | None:
        async with self._ctx.db.session() as session:
            return await session.get(FindingRecord, finding_id)

    async def finding_record(self, finding_id: str) -> FindingRecord | None:
        """Frische Zeile (u. a. mit original_mode) -- fuer den Befehlsbau der Aktionen."""
        return await self._finding(finding_id)

    async def quarantine(self, finding_id: str, *, actor: Actor | None) -> tuple[bool, str]:
        finding = await self._finding(finding_id)
        if finding is None:
            return False, "Unbekannter Fund."
        if finding.status != "detected":
            return False, "Dieser Fund ist nicht (mehr) offen."
        host = await self._ctx.hosts.get(finding.host_id)
        if host is None:
            return False, "Server nicht mehr bekannt."
        qname = av.quarantine_name(finding.id, finding.path)
        try:
            result = await self._ctx.exec.run(host, av.as_root(av.quarantine_command(finding.path, qname)), timeout_s=60)
            ok = result.exit_code == 0
            message = (result.stdout or result.stderr or "").strip()
            if av.NO_ROOT in message:
                message = av.NO_ROOT_MESSAGE
        except Exception as exc:  # noqa: BLE001
            ok, message = False, _describe(exc)
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding_id)
            if row is not None:
                if ok:
                    row.status = "quarantined"
                    row.quarantine_path = f"{av.QUARANTINE_DIR}/{qname}"
                    row.original_mode = av.parse_mode(message)
                    row.note = None
                else:
                    row.note = f"Quarantäne fehlgeschlagen: {message[-300:]}"
                row.status_changed_at = _now()
        await self._ctx.audit.log(
            action="nexus_soc.quarantine", outcome="success" if ok else "failure", target_type="host", target_id=finding.host_id,
            reason=finding.path, detail={"finding_id": finding_id, "signature": finding.signature, "automatic": actor is None},
            correlation_id=finding.scan_id, **({"actor": actor} if actor is not None else {}),
        )
        return ok, "In Quarantäne verschoben." if ok else message

    async def apply_action_result(self, finding_id: str, action: str, ok: bool, message: str) -> None:
        """Nach Wiederherstellen/Loeschen (ueber das Gate) den Fund nachziehen."""
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding_id)
            if row is None:
                return
            if ok:
                row.status = "restored" if action == "restore" else "deleted"
                row.note = None
            else:
                row.note = f"{'Wiederherstellen' if action == 'restore' else 'Löschen'} fehlgeschlagen: {message[-300:]}"
            row.status_changed_at = _now()

    async def ignore(self, finding_id: str) -> bool:
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding_id)
            if row is None or row.status != "detected":
                return False
            row.status = "ignored"
            row.status_changed_at = _now()
            return True

    # --- Audits ----------------------------------------------------------------

    async def run_audits(self, hosts: list[Host]) -> None:
        sem = asyncio.Semaphore(MAX_PARALLEL)
        results: list[tuple[Host, av.LynisResult]] = []

        async def one(host: Host) -> None:
            async with sem:
                if self.is_running(host.id, "audit"):
                    return
                self._running.add((host.id, "audit"))
                try:
                    try:
                        res = await self._ctx.exec.run(host, av.as_root(av.LYNIS_COMMAND), timeout_s=AUDIT_TIMEOUT)
                        parsed = av.parse_lynis((res.stdout or "") + (res.stderr or ""))
                    except Exception as exc:  # noqa: BLE001
                        parsed = av.LynisResult(status="error", error=_describe(exc))
                    async with self._ctx.db.session() as session:
                        session.add(AuditRecord(
                            id=_new_id("audit"), host_id=host.id, host_name=host.display_name or host.name,
                            status=parsed.status, hardening_index=parsed.hardening_index,
                            warnings=parsed.warnings[:100], suggestions=parsed.suggestions[:200],
                            error=parsed.error, created_at=_now(),
                        ))
                    results.append((host, parsed))
                finally:
                    self._running.discard((host.id, "audit"))

        await asyncio.gather(*(one(h) for h in hosts), return_exceptions=True)
        await self._ctx.ws.broadcast("defender", {"audits": len(results)})
        warnings = sum(len(r.warnings) for _, r in results if r.status == "ok")
        failed = [h.display_name or h.name for h, r in results if r.status != "ok"]
        if results and (warnings or failed):
            lines = [
                f"- {h.display_name or h.name}: " + (f"Index {r.hardening_index}, {len(r.warnings)} Warnung(en)" if r.status == "ok" else f"Fehler ({r.error})")
                for h, r in results
            ]
            await self._ctx.notify.send(Notification(
                title=f"Härtungs-Audit: {warnings} Warnung(en), {len(failed)} Fehler",
                body="\n".join(lines), severity=Severity.WARNING,
                payload={"path": f"{SOC_PATH}?tab=hardening", "tags": ["shield"], **host_scope(h.id for h, _ in results)},
            ))

    # --- Status / Uebersicht -------------------------------------------------------

    async def host_status(self, host: Host, *, refresh: bool = False) -> dict[str, Any]:
        cached = self._status_cache.get(host.id)
        if cached and not refresh and cached[0] > _now().timestamp() - 600:
            return cached[1]
        try:
            res = await self._ctx.exec.run(host, av.STATUS_COMMAND, timeout_s=20)
            st = av.parse_status(res.stdout or "")
            data = {
                "reachable": True, "clamav_installed": st.clamav_installed, "clamav_version": st.clamav_version,
                "signature_version": st.signature_version, "signature_date": st.signature_date,
                "freshclam_active": st.freshclam_active, "lynis_installed": st.lynis_installed,
                "lynis_version": st.lynis_version, "quarantine_files": st.quarantine_files, "os_id": st.os_id,
            }
        except Exception as exc:  # noqa: BLE001
            data = {"reachable": False, "error": _describe(exc), "clamav_installed": False, "lynis_installed": False}
        self._status_cache[host.id] = (_now().timestamp(), data)
        return data

    async def overview(self, *, refresh: bool = False) -> dict[str, Any]:
        hosts = await self.target_hosts()
        statuses = await asyncio.gather(*(self.host_status(h, refresh=refresh) for h in hosts))
        async with self._ctx.db.session() as session:
            last_scans: dict[str, ScanRecord] = {}
            for row in (await session.execute(
                select(ScanRecord).where(ScanRecord.kind.in_(("quick", "deep", "custom"))).order_by(ScanRecord.started_at.desc()).limit(500)
            )).scalars():
                last_scans.setdefault(row.host_id, row)
            last_audits: dict[str, AuditRecord] = {}
            for row in (await session.execute(select(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(500))).scalars():
                last_audits.setdefault(row.host_id, row)
            counts = dict((await session.execute(
                select(FindingRecord.status, func.count()).group_by(FindingRecord.status)
            )).all())
            since = _now() - timedelta(days=30)
            recent = (await session.execute(
                select(func.count()).select_from(FindingRecord).where(FindingRecord.detected_at >= since)
            )).scalar_one()

        rows = []
        for host, st in zip(hosts, statuses, strict=True):
            scan = last_scans.get(host.id)
            audit = last_audits.get(host.id)
            rows.append({
                "host_id": host.id, "host_name": host.display_name or host.name, "host_status": host.status.value,
                **st,
                "last_scan": scan_out(scan) if scan else None,
                "last_audit": {"status": audit.status, "hardening_index": audit.hardening_index, "warnings": len(audit.warnings),
                               "created_at": _ts(audit.created_at), "error": audit.error} if audit else None,
                "scanning": self._big_scan_running(host.id),
                "auditing": self.is_running(host.id, "audit"),
            })

        open_threats = int(counts.get("detected", 0))
        protected = sum(1 for r in rows if r.get("clamav_installed"))
        indices = [r["last_audit"]["hardening_index"] for r in rows if r.get("last_audit") and r["last_audit"]["hardening_index"] is not None]
        settings = await self._settings()
        return {
            "hosts": rows,
            "summary": {
                "hosts": len(rows), "protected": protected, "open_threats": open_threats,
                "quarantined": int(counts.get("quarantined", 0)),
                "neutralized_total": int(counts.get("quarantined", 0)) + int(counts.get("deleted", 0)),
                "findings_30d": int(recent),
                "avg_hardening": round(sum(indices) / len(indices)) if indices else None,
                "score": _score(rows, open_threats, indices),
            },
            "config": {
                "auto_quarantine": settings.get("auto_quarantine", True),
                "realtime_enabled": settings.get("realtime_enabled", True),
                "watch_interval_min": settings.get("watch_interval_min") or 10,
                "quick_scan_cron": settings.get("quick_scan_cron") or DEFAULT_CRONS["quick"],
                "deep_scan_cron": settings.get("deep_scan_cron") or DEFAULT_CRONS["deep"],
                "audit_cron": settings.get("audit_cron") or DEFAULT_CRONS["audit"],
            },
        }

    async def send_briefing(self, updates: dict[str, Any] | None = None, guard: tuple[str | None, list[str]] | None = None) -> dict[str, Any]:
        overview = await self.overview(refresh=True)
        title, body, level = build_briefing(overview, await self._ctx.hosts.list(), updates, guard)
        sev = {"critical": Severity.CRITICAL, "warning": Severity.WARNING}.get(level, Severity.INFO)
        await self._ctx.notify.send(Notification(
            title=title, body=body, severity=sev,
            payload={"path": SOC_PATH, "tags": ["coffee"], "actions": [
                {"label": "Updates", "path": f"{SOC_PATH}?tab=updates"}, {"label": "Einbruchschutz", "path": f"{SOC_PATH}?tab=guard"},
            ]},
        ))
        return {"title": title, "body": body, "level": level}

    # --- Listen -----------------------------------------------------------------

    async def list_scans(self, *, host: str | None = None, limit: int = 100, include_watch: bool = False) -> list[dict[str, Any]]:
        stmt = select(ScanRecord).order_by(ScanRecord.started_at.desc()).limit(limit)
        if host:
            stmt = stmt.where(ScanRecord.host_id == host)
        if not include_watch:
            stmt = stmt.where((ScanRecord.kind != "watch") | (ScanRecord.status != "clean"))
        async with self._ctx.db.session() as session:
            return [scan_out(r) for r in (await session.execute(stmt)).scalars()]

    async def list_findings(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        stmt = select(FindingRecord).order_by(FindingRecord.detected_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(FindingRecord.status == status)
        async with self._ctx.db.session() as session:
            return [finding_out(r) for r in (await session.execute(stmt)).scalars()]

    async def latest_audits(self) -> list[dict[str, Any]]:
        async with self._ctx.db.session() as session:
            rows = (await session.execute(select(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(500))).scalars()
            latest: dict[str, AuditRecord] = {}
            for r in rows:
                latest.setdefault(r.host_id, r)
            return [audit_out(r) for r in latest.values()]

    async def finding(self, finding_id: str) -> dict[str, Any] | None:
        row = await self._finding(finding_id)
        return finding_out(row) if row else None


DEFAULT_CRONS = {"quick": "0 2 * * *", "deep": "30 3 * * 0", "audit": "0 1 * * *", "briefing": "0 7 * * *"}


def build_briefing(
    overview: dict[str, Any], hosts_all: list[Any], updates: dict[str, Any] | None = None,
    guard: tuple[str | None, list[str]] | None = None,
) -> tuple[str, str, str]:
    """Titel, Text und Stufe des Morgen-Briefings (wie im alten Skript: ein Blick
    aufs Handy genuegt, um zu wissen, ob etwas zu tun ist)."""
    sm = overview["summary"]
    down = [h.display_name or h.name for h in hosts_all if getattr(h.status, "value", h.status) == "down"]
    lines = [
        f"Server: {len(hosts_all) - len(down)}/{len(hosts_all)} erreichbar" + (f" – aus/weg: {', '.join(down[:6])}" if down else ""),
        f"Virenschutz: Schutzwert {sm['score']}/100, {sm['protected']}/{sm['hosts']} Server mit ClamAV",
        f"Bedrohungen: {sm['open_threats']} offen, {sm['quarantined']} in Quarantäne, {sm['findings_30d']} Funde in 30 Tagen",
    ]
    if sm.get("avg_hardening") is not None:
        lines.append(f"Härtung: Ø {sm['avg_hardening']}/100 (Lynis)")
    problems = []
    if updates and updates.get("summary", {}).get("checked"):
        us = updates["summary"]
        lines.append(
            f"Updates: {us['packages']} offen" + (f", davon {us['security']} Sicherheit" if us["security"] else "")
            + (f" · Neustart nötig: {us['reboot']} Server" if us["reboot"] else "")
        )
        for h in updates.get("hosts", []):
            st = h.get("status") or {}
            if st.get("security_count"):
                problems.append(f"{h['host_name']}: {st['security_count']} Sicherheitsupdate(s)")
    if guard and guard[0]:
        lines.append(guard[0])
        problems.extend(guard[1])
    for h in overview["hosts"]:
        scan = h.get("last_scan") or {}
        if not h.get("clamav_installed") and h.get("reachable") is not False:
            problems.append(f"{h['host_name']}: kein ClamAV")
        elif scan.get("status") == "error":
            problems.append(f"{h['host_name']}: letzter Scan fehlgeschlagen")
        elif h.get("freshclam_active") is False:
            problems.append(f"{h['host_name']}: Signatur-Update aus")
    if problems:
        lines.append("Zu tun: " + "; ".join(problems[:6]))
    level = "critical" if sm["open_threats"] else ("warning" if (down or problems) else "info")
    title = "Guten Morgen – alles im grünen Bereich" if level == "info" else (
        "Guten Morgen – Bedrohung offen!" if level == "critical" else "Guten Morgen – es gibt etwas zu tun")
    return title, "\n".join(lines), level


def _score(rows: list[dict[str, Any]], open_threats: int, indices: list[int]) -> int:
    """Grobe Schutz-Note 0-100: Abdeckung (ClamAV installiert, aktuelle Scans),
    offene Funde, Haertungsindex. Bewusst einfach und nachvollziehbar."""
    if not rows:
        return 0
    now = _now().timestamp()
    covered = sum(1 for r in rows if r.get("clamav_installed")) / len(rows)
    fresh = sum(
        1 for r in rows
        if r.get("last_scan") and r["last_scan"]["status"] in ("clean", "infected") and (r["last_scan"]["started_at"] or 0) > now - 8 * 86400
    ) / len(rows)
    hardening = (sum(indices) / len(indices) / 100) if indices else 0.5
    score = 40 * covered + 25 * fresh + 25 * hardening + 10
    score -= min(40, open_threats * 20)
    return max(0, min(100, round(score)))


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "Zeitüberschreitung – der Vorgang hat zu lange gebraucht."
    return text or type(exc).__name__
