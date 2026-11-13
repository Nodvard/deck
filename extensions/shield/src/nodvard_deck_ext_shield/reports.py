"""Wöchentliche Berichte aus den gespeicherten Shield-Ergebnissen."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from nodvard_sdk import Notification, Severity
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from .defender_api import _JobSpec, _register_job
from .history import _ts
from .hostscope import host_scope
from .ids import SOC_PATH
from .models import (
    AuditRecord,
    BaselineRecord,
    FindingRecord,
    ScanRecord,
    WeeklyReportRecord,
)
from .patching import schedule_zone

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

DEFAULT_CRON = "0 8 * * 1"
RETENTION = timedelta(weeks=12)
CERTIFICATES_NOTE = "Zertifikatsabläufe sind in Shield noch nicht gespeichert."


class ReportSummary(BaseModel):
    updates: int
    security_updates: int
    new_findings: int
    resolved_findings: int
    unknown_update_hosts: int
    unknown_security_update_hosts: int


class ReportUpdates(BaseModel):
    open: int
    security: int | None
    checked_at: float
    error: str | None


class ReportScan(BaseModel):
    status: str
    infected: int
    files_scanned: int | None
    started_at: float
    finished_at: float | None


class ReportFail2ban(BaseModel):
    status: str
    banned: int | None
    checked_at: float
    error: str | None


class ReportLynis(BaseModel):
    hardening_index: int
    created_at: float


class ReportFinding(BaseModel):
    id: str
    path: str
    signature: str
    status: str


class ReportFindings(BaseModel):
    new: list[ReportFinding]
    resolved: list[ReportFinding]


class ReportHost(BaseModel):
    host_id: str
    host_name: str
    updates: ReportUpdates | None
    scan: ReportScan | None
    fail2ban: ReportFail2ban | None
    lynis: ReportLynis | None
    findings: ReportFindings
    certificates: None


class WeeklyReportMeta(BaseModel):
    id: str
    created_at: float
    period_start: float
    period_end: float
    host_count: int
    summary: ReportSummary


class WeeklyReportOut(WeeklyReportMeta):
    hosts: list[ReportHost]
    certificates_note: str


class WeeklyReportList(BaseModel):
    items: list[WeeklyReportMeta]
    total: int


def _updates(row: BaselineRecord | None) -> dict | None:
    if row is None:
        return None
    data = row.data
    if not isinstance(data.get("count"), int) or (data.get("error") and not data.get("checked_at")):
        return None
    return {"open": data["count"], "security": data.get("security_count"),
            "checked_at": data.get("checked_at") or _ts(row.updated_at), "error": data.get("error")}


def _fail2ban(row: BaselineRecord | None) -> dict | None:
    if row is None:
        return None
    data = row.data
    known = data.get("fail2ban") in ("running", "stopped", "none") and not data.get("error")
    return {"status": data.get("fail2ban") or "unknown", "banned": data.get("banned_count") if known else None,
            "checked_at": data.get("checked_at") or _ts(row.updated_at), "error": data.get("error")}


def _finding(row: FindingRecord) -> dict:
    return {"id": row.id, "path": row.path, "signature": row.signature, "status": row.status}


class WeeklyReports:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._lock = asyncio.Lock()

    async def run(self) -> dict:
        if not (await self._ctx.settings.get()).get("weekly_report_enabled", True):
            return {"skipped": True}
        return await self.generate()

    async def generate(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        local = now.astimezone(await schedule_zone(self._ctx))
        ident = (local.date() - timedelta(days=local.weekday())).isoformat()
        async with self._lock:
            async with self._ctx.db.session() as db:
                await db.execute(delete(WeeklyReportRecord).where(WeeklyReportRecord.created_at <= now - RETENTION))
                existing = await db.get(WeeklyReportRecord, ident)
                previous = await db.scalar(select(WeeklyReportRecord).order_by(WeeklyReportRecord.period_end.desc()).limit(1))
                start = previous.period_end if previous is not None else now - timedelta(weeks=1)
                saved = existing.data if existing is not None else None
            if saved is None:
                saved = await self._snapshot(ident, start, now)
                async with self._ctx.db.session() as db:
                    db.add(WeeklyReportRecord(id=ident, created_at=now, period_start=start, period_end=now, data=saved))
                    await db.flush()
                    older = select(WeeklyReportRecord.id).order_by(WeeklyReportRecord.created_at.desc()).offset(12)
                    await db.execute(delete(WeeklyReportRecord).where(WeeklyReportRecord.id.in_(older)))
            await self._send(saved, now)
            return saved

    async def _snapshot(self, ident: str, start: datetime, now: datetime) -> dict:
        settings = await self._ctx.settings.get()
        hosts = await self._ctx.hosts.list(tag=(settings.get("av_host_tag") or "").strip() or None)
        hosts = sorted((h for h in hosts if h.os_family == "linux" and h.is_managed and h.has_credential),
                       key=lambda h: (h.display_name.casefold(), h.id))
        ids = [h.id for h in hosts]
        async with self._ctx.db.session() as db:
            baselines = {(r.host_id, r.kind): r for r in (await db.scalars(
                select(BaselineRecord).where(BaselineRecord.host_id.in_(ids), BaselineRecord.kind.in_(("updates", "guard")))
            )).all()}
            scans = (await db.scalars(select(ScanRecord).where(ScanRecord.host_id.in_(ids), ScanRecord.status != "running")
                                     .order_by(ScanRecord.started_at.desc()))).all()
            audits = (await db.scalars(select(AuditRecord).where(AuditRecord.host_id.in_(ids), AuditRecord.status == "ok",
                                                                AuditRecord.hardening_index.is_not(None))
                                      .order_by(AuditRecord.created_at.desc()))).all()
            findings = (await db.scalars(select(FindingRecord).where(FindingRecord.host_id.in_(ids))
                                        .order_by(FindingRecord.detected_at, FindingRecord.id))).all()
        rows = []
        for host in hosts:
            scan = next((r for r in scans if r.host_id == host.id), None)
            audit = next((r for r in audits if r.host_id == host.id), None)
            found = [r for r in findings if r.host_id == host.id]
            rows.append({
                "host_id": host.id, "host_name": host.display_name or host.name,
                "updates": _updates(baselines.get((host.id, "updates"))),
                "fail2ban": _fail2ban(baselines.get((host.id, "guard"))),
                "scan": {"status": scan.status, "infected": scan.infected, "files_scanned": scan.files_scanned,
                         "started_at": _ts(scan.started_at), "finished_at": _ts(scan.finished_at)} if scan else None,
                "lynis": {"hardening_index": audit.hardening_index, "created_at": _ts(audit.created_at)} if audit else None,
                "findings": {
                    "new": [_finding(r) for r in found if start <= r.detected_at < now],
                    "resolved": [_finding(r) for r in found if r.status in ("quarantined", "deleted", "ignored")
                                 and r.status_changed_at is not None and start <= r.status_changed_at < now],
                },
                "certificates": None,
            })
        summary = {
            "updates": sum(r["updates"]["open"] for r in rows if r["updates"]),
            "security_updates": sum(r["updates"]["security"] or 0 for r in rows if r["updates"]),
            "new_findings": sum(len(r["findings"]["new"]) for r in rows),
            "resolved_findings": sum(len(r["findings"]["resolved"]) for r in rows),
            "unknown_update_hosts": sum(r["updates"] is None for r in rows),
            "unknown_security_update_hosts": sum(r["updates"] is None or r["updates"]["security"] is None for r in rows),
        }
        return {"id": ident, "created_at": now.timestamp(), "period_start": start.timestamp(), "period_end": now.timestamp(),
                "host_count": len(rows), "summary": summary, "hosts": rows, "certificates_note": CERTIFICATES_NOTE}

    async def _send(self, report: dict, now: datetime) -> None:
        async with self._ctx.db.session() as db:
            record = await db.get(WeeklyReportRecord, report["id"])
            if record.notified_at is not None:
                return
        summary = report["summary"]
        body = (f"{report['host_count']} Server: {summary['updates']} offene Updates, davon {summary['security_updates']} "
                f"Sicherheitsupdates. {summary['new_findings']} neue und {summary['resolved_findings']} erledigte Funde.")
        if summary["unknown_update_hosts"]:
            body += f" Update-Stand bei {summary['unknown_update_hosts']} Servern unbekannt."
        if summary["unknown_security_update_hosts"]:
            body += f" Sicherheitsupdate-Stand bei {summary['unknown_security_update_hosts']} Servern unbekannt."
        await self._ctx.notify.send(Notification(
            title="Shield: Wochenbericht", body=body, severity=Severity.INFO,
            correlation_id=f"shield-weekly:{report['id']}",
            payload={"notification_type": "shield.weekly_report", **host_scope(h["host_id"] for h in report["hosts"]),
                     "path": f"{SOC_PATH}?tab=reports&report={report['id']}"},
        ), raise_on_failure=True)
        async with self._ctx.db.session() as db:
            await db.execute(update(WeeklyReportRecord).where(WeeklyReportRecord.id == report["id"]).values(notified_at=now))

    async def list(self, *, now: datetime | None = None) -> dict:
        async with self._ctx.db.session() as db:
            rows = (await db.scalars(select(WeeklyReportRecord)
                                    .where(WeeklyReportRecord.created_at > (now or datetime.now(UTC)) - RETENTION)
                                    .order_by(WeeklyReportRecord.created_at.desc()).limit(12))).all()
        return {"items": [{k: v for k, v in r.data.items() if k not in ("hosts", "certificates_note")} for r in rows],
                "total": len(rows)}

    async def get(self, report_id: str, *, now: datetime | None = None) -> dict | None:
        async with self._ctx.db.session() as db:
            row = await db.get(WeeklyReportRecord, report_id)
            return row.data if row and row.created_at > (now or datetime.now(UTC)) - RETENTION else None


def build_router(service: WeeklyReports) -> APIRouter:
    router = APIRouter()

    @router.get("/reports", response_model=WeeklyReportList)
    async def list_reports() -> dict:
        return await service.list()

    @router.get(
        "/reports/{report_id}", response_model=WeeklyReportOut,
        responses={404: {"description": "Wochenbericht nicht gefunden.", "content": {"application/json": {}}}},
    )
    async def get_report(report_id: str) -> dict:
        report = await service.get(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Wochenbericht nicht gefunden oder älter als zwölf Wochen.")
        return report

    return router


async def register_job(ctx: ExtensionContext, service: WeeklyReports) -> None:
    settings = await ctx.settings.get()
    await _register_job(ctx, _JobSpec("weekly-report", "Shield: Wochenbericht", settings.get("weekly_report_cron") or DEFAULT_CRON,
                                    settings.get("weekly_report_enabled", True), service.run, fallback=DEFAULT_CRON))
