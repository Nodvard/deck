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
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity
from sqlalchemy import func, or_, select, update

from . import antivirus as av
from . import audit_job as aj
from . import detached as dt
from . import protection
from .hostscope import host_scope
from .ids import EXT_ID, LOGGER, SOC_PATH
from .models import AuditRecord, BaselineRecord, FindingRecord, ScanRecord
from .patching import load_baselines, save_baseline

if TYPE_CHECKING:
    from nodvard_sdk import Actor, ExtensionContext, Host

SCAN_TIMEOUTS = {"quick": 20 * 60, "deep": 4 * 60 * 60, "watch": 5 * 60, "custom": 60 * 60}
STATUS_TIMEOUT = 20
# Haertungs-Audit: laeuft entkoppelt auf dem Server (`audit_job.py`), das Dashboard fragt nur kurz nach. Obergrenze je Lauf
# auf dem Server (`timeout` dort). Ist ein Lauf danach noch nicht zu Ende (`timeout` fehlt oder half nicht), beendet ihn das
# Dashboard nach der Frist selbst (`audit_job.stop_command`).
AUDIT_LIMIT_S = aj.LIMIT_S
AUDIT_STOP_GRACE_S = 5 * 60
AUDIT_LAUNCH_TIMEOUT_S = 90  # Ordner anlegen, Lauf abkoppeln, bis 5 s auf die pid warten
AUDIT_POLL_INTERVAL_S = 30
AUDIT_POLL_TIMEOUT_S = 30
AUDIT_STOP_TIMEOUT_S = 60
AUDIT_UNKNOWN_GRACE_S = 60  # so lange darf ein gerade gestarteter Lauf auf dem Server noch "unbekannt" sein
AUDIT_REACH_GRACE_S = 3 * 60  # nach einem gescheiterten Start: so lange muss sich der Server wenigstens einmal melden
AUDIT_RESUME_DELAY_S = 5  # nach dem Start des Dashboards kurz warten, bis Netz und SSH da sind
# Stand eines laufenden (oder wartenden) Audits je Server in der Baselines-Tabelle (`<host_id>:audit_run`): ueberdauert einen
# Neustart des Dashboards. Ist der Lauf fertig, steht das Ergebnis in `AuditRecord` (gleiche ID) und die Zeile ist weg.
AUDIT_STATE_KIND = "audit_run"
_AUDIT_ID_RE = re.compile(r"^audit_[0-9a-f]{16}$")
QUARANTINE_TIMEOUT = 60
MAX_PARALLEL = 3
# So lange wartet ein Scan (nicht der Waechter) auf die Sperre des Servers (`av.guard_command`), wenn dort gerade ein anderer
# Lauf sie haelt: etwas laenger, als ein Waechter-Lauf hoechstens dauert.
SCAN_LOCK_WAIT_S = SCAN_TIMEOUTS["watch"] + 60
# Der Waechter prueft Dateien, die seit dem Beginn seines letzten Laufs geaendert wurden (plus 2 Minuten Puffer), mindestens
# das eingestellte Intervall plus 2 Minuten. Fiel ein Lauf aus (Server belegt, grosser Scan lief), wird das Fenster des naechsten
# entsprechend groesser, hoechstens so gross: Das deckt einen Tiefenscan (bis 4 Stunden) ab, ohne dass nach einer langen Pause
# (Waechter aus, Dashboard aus) ein Lauf alles der letzten Tage pruefen muss.
WATCH_MAX_WINDOW_MIN = 6 * 60
# Setzt der Waechter so lange aus, dass sein Fenster die Grenze erreicht, gehen Dateien ungeprueft verloren: Dann wird aus dem
# stillen Aussetzen eine sichtbare Fehlerzeile, hoechstens eine je `WATCH_MAX_WINDOW_MIN`. Haelt ein Lauf die Sperre dauerhaft
# (clamscan haengt an einem toten Netzlaufwerk und laesst sich nicht einmal mit KILL beenden, oder auf dem Server fehlt
# `timeout`), saehe man sonst nie, dass der Waechter nicht mehr laeuft. Die Zeile zaehlt beim Fenster nicht als Lauf
# (`_watch_minutes`): Wird die Sperre frei, prueft der naechste Lauf weiter so weit zurueck wie moeglich.
_WATCH_MAX_WINDOW_TEXT = av.duration_text(WATCH_MAX_WINDOW_MIN * 60)
WATCH_BLOCKED_MESSAGE = (
    f"Der Echtzeit-Wächter konnte seit etwa {_WATCH_MAX_WINDOW_TEXT} nicht prüfen: Auf dem Server lief die ganze Zeit ein "
    f"anderer Scan, oder einer hängt dort. Dateien, die vor mehr als {_WATCH_MAX_WINDOW_TEXT} geändert wurden, prüft der "
    "Wächter nicht mehr nach. Sieh auf dem Server nach, ob ein Scan hängt (zum Beispiel an einem Netzlaufwerk, das nicht mehr "
    "erreichbar ist), und starte danach einen Schnellscan."
)

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

_log = logging.getLogger(LOGGER)

# Scans und Update-Laeufe leben als Hintergrund-Task im Prozess -- nach einem Neustart
# des Dashboards kommt ein abgebrochener Lauf nie zurueck.
ABORTED_BY_RESTART = "Abgebrochen – Dashboard wurde neu gestartet"


def _unreliable_hint(parsed: av.ScanResult) -> str:
    """Hinweistext zu einem Lauf, der keine automatische Quarantaene bekommt: unleserliche Pfade, nicht gepruefte Dateien
    (Obergrenze des Waechters) oder beides."""
    if not parsed.skipped:
        return UNRELIABLE_HINT
    if not parsed.paths_unsure:
        return av.skipped_message(parsed.skipped, moved=True)
    return f"{av.skipped_message(parsed.skipped)} {UNRELIABLE_HINT}"


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


def audit_run_out(state: dict[str, Any]) -> dict[str, Any]:
    """Ein laufendes Audit fuer die Seite: seit wann angefordert (`requested_at`), seit wann es auf dem Server laeuft
    (`started_at`; None, solange es noch nicht gestartet ist), Ausloeser, und ob es auf einen freien Platz wartet
    (`waiting`, es laufen schon `MAX_PARALLEL` andere)."""
    return {
        "run_id": state.get("run_id"), "requested_at": state.get("requested_at"), "started_at": state.get("started_at"),
        "trigger": state.get("trigger"), "waiting": bool(state.get("waiting")),
    }


def _valid_audit_state(data: Any) -> bool:
    return (
        isinstance(data, dict) and isinstance(data.get("run_id"), str) and bool(_AUDIT_ID_RE.match(data["run_id"]))
        and isinstance(data.get("requested_at"), int | float)
        and (data.get("started_at") is None or isinstance(data.get("started_at"), int | float))
    )


class Defender:
    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        self._running: set[tuple[str, str]] = set()  # (host_id, kind) -- kein doppelter Lauf
        self._status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # Seit wann der Waechter je Server wegen der belegten Sperre aussetzt (nur im Speicher; fuer einen Server, auf dem
        # er noch nie eine Zeile hinterlassen hat, an der `_watch_minutes` das ablesen koennte).
        self._watch_busy_since: dict[str, datetime] = {}
        # Laufende (oder auf einen Platz wartende) Audits je Server: derselbe Stand wie in der Baselines-Tabelle
        # (`AUDIT_STATE_KIND`). Je Server hoechstens eines; ein zweiter Klick zeigt das laufende.
        self._audits: dict[str, dict[str, Any]] = {}
        # Hoechstens so viele Audits starten gleichzeitig (Server auf derselben Hardware, z. B. VMs eines Proxmox).
        self._audit_slots = asyncio.Semaphore(MAX_PARALLEL)
        self._audit_tasks: set[asyncio.Future[Any]] = set()
        # Beim Start aus der Datenbank geholte Audits, die `resume_audits` weiter abfragt.
        self._audits_to_resume: list[tuple[str, dict[str, Any]]] = []

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
                        _log.debug("shield_watch_skipped_big_scan_running host=%s", host.id)
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

    @staticmethod
    async def _drop_previous_capped_watch_row(session: Any, host_id: str, scan_id: str) -> None:
        """Haelt die Scan-Liste bei dauerhaft vielen neuen Dateien kurz: War auch der Waechter-Lauf davor (derselbe Server)
        nur wegen der Obergrenze unvollstaendig, ersetzt die Zeile dieses Laufs die alte. Sonst legte ein Server mit
        ununterbrochen vielen Aenderungen alle zehn Minuten eine sichtbare Fehlerzeile an und drueckte die echten Schnell-
        und Tiefenscans aus der Liste. Eine saubere Zeile dazwischen, ein Fund oder ein anderer Fehler unterbrechen die
        Folge: Dann bleiben beide Zeilen."""
        previous = (await session.execute(
            select(ScanRecord).where(ScanRecord.host_id == host_id, ScanRecord.kind == "watch", ScanRecord.id != scan_id)
            .order_by(ScanRecord.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        if previous is not None and previous.status == "error" and (previous.error or "").startswith(av.SKIPPED_PREFIX):
            await session.delete(previous)

    async def _watch_minutes(self, host_id: str, scan_id: str, interval_min: int) -> int:
        """Fenster des Waechters in Minuten (`find -mmin`): ab dem Beginn des letzten Waechter-Laufs auf diesem Server, der
        wirklich lief (ein uebersprungener hinterlaesst keine Zeile), plus 2 Minuten; mindestens Intervall plus 2 Minuten,
        hoechstens `WATCH_MAX_WINDOW_MIN`. Ein Lauf mit Fehler zaehlt als gelaufen: sonst wuerde nach einer Zeitueberschreitung
        das Fenster immer groesser, und jeder weitere Lauf braeuchte noch laenger. Nicht als Lauf zaehlen nur Zeilen, hinter
        denen kein Scan steht: ein Lauf, den ein Neustart des Dashboards abgebrochen hat (`ABORTED_BY_RESTART`, sein Ergebnis
        kam nie an, und ein Neustart wiederholt sich nicht von selbst), und die Meldung, dass der Waechter zu lange aussetzen
        musste (`WATCH_BLOCKED_MESSAGE`)."""
        base = interval_min + 2
        async with self._ctx.db.session() as session:
            last = (await session.execute(
                select(ScanRecord.started_at)
                .where(ScanRecord.host_id == host_id, ScanRecord.kind == "watch", ScanRecord.id != scan_id,
                       or_(ScanRecord.error.is_(None), ScanRecord.error.not_in((ABORTED_BY_RESTART, WATCH_BLOCKED_MESSAGE))))
                .order_by(ScanRecord.started_at.desc()).limit(1)
            )).scalar_one_or_none()
        if last is None:
            return base
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        since = int(max(0.0, (_now() - last).total_seconds()) // 60) + 2  # angebrochene Minuten deckt der Puffer ab
        return max(base, min(since, WATCH_MAX_WINDOW_MIN))

    async def _recently_blocked(self, host_id: str, scan_id: str) -> bool:
        """Steht fuer diesen Server schon eine Meldung `WATCH_BLOCKED_MESSAGE` aus den letzten `WATCH_MAX_WINDOW_MIN` Minuten?"""
        async with self._ctx.db.session() as session:
            row = (await session.execute(
                select(ScanRecord.id)
                .where(ScanRecord.host_id == host_id, ScanRecord.kind == "watch", ScanRecord.id != scan_id,
                       ScanRecord.error == WATCH_BLOCKED_MESSAGE,
                       ScanRecord.started_at >= _now() - timedelta(minutes=WATCH_MAX_WINDOW_MIN))
                .limit(1)
            )).scalar_one_or_none()
        return row is not None

    async def _run_scan(self, host: Host, scan_id: str, kind: str, paths: list[str]) -> None:
        settings = await self._settings()
        max_mb = int(settings.get("max_filesize_mb") or 50)
        limit = SCAN_TIMEOUTS.get(kind, 3600)
        # Zufallsmarke je Lauf: im Befehl und bei der Auswertung dieselbe, einem
        # Dateinamen auf dem Server nicht vorhersagbar.
        mark = av.new_rc_mark()
        minutes = 0
        if kind == "watch":
            minutes = await self._watch_minutes(host.id, scan_id, int(settings.get("watch_interval_min") or 10))
            # Einstellung "Echtzeit-Waechter mit clamdscan" (Standard aus): nur der
            # Waechter; Schnell- und Tiefenscan bleiben bei clamscan.
            command = av.build_watch_command(paths, minutes=minutes, max_filesize_mb=max_mb,
                                             use_clamd=bool(settings.get("watch_use_clamdscan", False)), mark=mark)
        else:
            command = av.build_scan_command(paths, max_filesize_mb=max_mb, exclude=settings.get("exclude_paths") or [],
                                            mark=mark)
        # Nie zwei Laeufe zugleich auf einem Server (auch nicht aus einem zweiten Dashboard oder einem Lauf, der einen
        # Neustart ueberdauert hat): der Waechter setzt aus, ein Scan wartet. Dazu ein Zeitlimit auf dem Server selbst.
        command = av.guard_command(command, mark=mark, limit_s=limit, wait_s=None if kind == "watch" else SCAN_LOCK_WAIT_S)
        command = av.as_root(command, required=False)
        try:
            result = await self._ctx.exec.run(host, command, timeout_s=limit)
            output = (result.stdout or "") + (result.stderr or "")
            # Die Wurzeln sind genau die Pfade, die der Befehl gescannt hat: Funde ausserhalb davon sind nie echt.
            parsed = av.parse_scan_output(output, mark, roots=paths, limit_s=limit)
            if result.exit_code == 127:
                parsed = av.ScanResult(status="error", error=av.NOT_INSTALLED_MESSAGE)
        except Exception as exc:  # noqa: BLE001 - jeder Fehler landet sichtbar am Scan
            output = ""
            parsed = av.ScanResult(status="error", error=_describe(exc, timeout_s=limit))

        if kind == "watch" and parsed.busy:
            busy_since = self._watch_busy_since.setdefault(host.id, _now())
            too_long = minutes >= WATCH_MAX_WINDOW_MIN or _now() - busy_since >= timedelta(minutes=WATCH_MAX_WINDOW_MIN)
            if not too_long or await self._recently_blocked(host.id, scan_id):
                # Auf dem Server lief gerade ein anderer Lauf: Der Waechter setzt diesmal aus, ohne Fehler und ohne Zeile in
                # der Liste. Das Fenster des naechsten Laufs beginnt dann beim letzten, der wirklich lief (`_watch_minutes`).
                async with self._ctx.db.session() as session:
                    scan = await session.get(ScanRecord, scan_id)
                    if scan is not None:
                        await session.delete(scan)
                _log.info("shield_watch_skipped_host_busy host=%s", host.id)
                await self._ctx.ws.broadcast("defender", {"scan": scan_id, "skipped": True})
                return
            # So lange belegt, dass Dateien ungeprueft verloren gehen: sichtbar als Fehler.
            _log.warning("shield_watch_blocked_host_busy host=%s", host.id)
            parsed, output = av.ScanResult(status="error", error=WATCH_BLOCKED_MESSAGE), ""
        elif kind == "watch":
            self._watch_busy_since.pop(host.id, None)

        finding_ids: list[str] = []
        known_findings: list[tuple[str, str]] = []  # von dir ignoriert oder wiederhergestellt: kein neuer Fund
        async with self._ctx.db.session() as session:
            scan = await session.get(ScanRecord, scan_id)
            if scan is None:
                return
            scan.status = parsed.status
            scan.files_scanned = parsed.files_scanned
            scan.infected = parsed.infected
            scan.error = parsed.error or ("; ".join(parsed.errors[:3]) if parsed.errors else None)
            hint = _unreliable_hint(parsed)
            if parsed.unreliable and parsed.status == "infected":
                scan.error = hint
            scan.output_tail = output[-4000:] if output else None
            scan.finished_at = _now()
            if kind == "watch" and parsed.status == "error" and parsed.skipped:
                await self._drop_previous_capped_watch_row(session, host.id, scan_id)
            for path, sig in parsed.findings:
                existing = (await session.execute(
                    select(FindingRecord).where(
                        FindingRecord.host_id == host.id, FindingRecord.path == path, FindingRecord.status == "detected"
                    )
                )).scalar_one_or_none()
                if existing is not None:
                    existing.scan_id = scan_id
                    if parsed.paths_unsure and not existing.note:
                        existing.note = UNRELIABLE_NOTE
                    finding_ids.append(existing.id)
                    continue
                # Vom Menschen entschieden: ignoriert ("Die Datei bleibt, wo sie ist") oder nach Freigabe
                # wiederhergestellt (Fehlalarm). Dieselbe Datei mit derselben Signatur ist dann kein neuer
                # Fund -- keine Quarantaene, kein Alarm; nur der juengste Scan wird daran vermerkt. Nicht bei
                # unsicheren Pfaden: ein vorgetaeuschter Pfad darf keinen echten Fund als bekannt tarnen.
                known = None if parsed.paths_unsure else (await session.execute(
                    select(FindingRecord).where(
                        FindingRecord.host_id == host.id, FindingRecord.path == path,
                        FindingRecord.signature == sig[:255], FindingRecord.status.in_(("ignored", "restored")),
                    ).order_by(FindingRecord.detected_at.desc())
                )).scalars().first()
                if known is not None:
                    known.scan_id = scan_id
                    known_findings.append((path, sig))
                    continue
                fid = _new_id("f")
                session.add(FindingRecord(
                    id=fid, scan_id=scan_id, host_id=host.id, host_name=host.display_name or host.name, path=path,
                    signature=sig[:255], status="detected", detected_at=_now(),
                    note=UNRELIABLE_NOTE if parsed.paths_unsure else None,
                ))
                finding_ids.append(fid)

        if parsed.status == "infected":
            fresh = [(p, s) for p, s in parsed.findings if (p, s) not in known_findings]
            await self._ctx.audit.log(
                action=f"{EXT_ID}.malware_found", outcome="success", target_type="host", target_id=host.id,
                reason=f"{parsed.infected} Fund(e) bei {KIND_LABEL.get(kind, kind)}",
                detail={"scan_id": scan_id, "findings": [{"path": p, "signature": s} for p, s in parsed.findings[:20]],
                        "unreliable": parsed.unreliable, "known": len(known_findings)},
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
                body = f"ClamAV meldet {parsed.infected} Fund(e). {hint}"
            else:
                names = ", ".join(f"{p} ({s})" for p, s in fresh[:3])
                body = (
                    f"{len(fresh)} Fund(e): {names}"
                    + (f"\n{quarantined} in Quarantäne verschoben." if quarantined else "\nNoch nicht in Quarantäne – bitte prüfen.")
                )
                if known_findings:
                    body += f"\n{len(known_findings)} bekannte(r) Fund(e) ausgelassen (von dir ignoriert oder wiederhergestellt)."
            if fresh or parsed.unreliable:  # nur bekannte Funde: kein Alarm, sie sind entschieden
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
            result = await self._ctx.exec.run(
                host, av.as_root(av.quarantine_command(finding.path, qname)), timeout_s=QUARANTINE_TIMEOUT,
            )
            ok = result.exit_code == 0
            message = (result.stdout or result.stderr or "").strip()
            if av.NO_ROOT in message:
                message = av.NO_ROOT_MESSAGE
        except Exception as exc:  # noqa: BLE001
            ok, message = False, _describe(exc, timeout_s=QUARANTINE_TIMEOUT)
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
            action=f"{EXT_ID}.quarantine", outcome="success" if ok else "failure", target_type="host", target_id=finding.host_id,
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
    # Lynis braucht auf manchen Servern weit ueber eine halbe Stunde. Der Lauf haengt deshalb nicht am SSH-Kanal, sondern
    # laeuft entkoppelt auf dem Server (`audit_job.py`); das Dashboard fragt alle `AUDIT_POLL_INTERVAL_S` Sekunden kurz
    # nach. Sein Stand steht sofort beim Anfordern im Speicher und in der Datenbank: Die Seite zeigt "laeuft seit ...",
    # auch nach dem Neuladen und fuer den Lauf nach Zeitplan, ein zweiter Klick startet keinen zweiten Lauf, und nach
    # einem Neustart des Dashboards nimmt `resume_audits` die Abfrage wieder auf.

    def audit_run(self, host_id: str) -> dict[str, Any] | None:
        """Das laufende (oder wartende) Audit eines Servers fuer die Seite, sonst None."""
        state = self._audits.get(host_id)
        return audit_run_out(state) if state is not None else None

    def _claim_audits(self, hosts: list[Host], trigger: str) -> tuple[list[tuple[Host, dict[str, Any]]], list[tuple[Host, dict[str, Any]]]]:
        """Belegt die Server sofort (ohne await dazwischen): Zwei gleichzeitige Anforderungen (zwei Klicks, Klick und
        Zeitplan) starten so nie zwei Laeufe. Rueckgabe: neu angeforderte und schon laufende, je mit ihrem Stand."""
        fresh: list[tuple[Host, dict[str, Any]]] = []
        already: list[tuple[Host, dict[str, Any]]] = []
        now = _now().timestamp()
        for host in hosts:
            current = self._audits.get(host.id)
            if current is not None:
                already.append((host, current))
                continue
            state: dict[str, Any] = {
                "run_id": _new_id("audit"), "host_name": host.display_name or host.name, "trigger": trigger,
                "requested_at": now, "started_at": None,
            }
            self._audits[host.id] = state
            fresh.append((host, state))
        return fresh, already

    def _release_audit(self, host_id: str, state: dict[str, Any]) -> None:
        if self._audits.get(host_id) is state:
            del self._audits[host_id]

    async def _begin_audits(self, hosts: list[Host], trigger: str) -> tuple[
        list[tuple[Host, dict[str, Any]]], list[tuple[Host, dict[str, Any]]], asyncio.Future[Any] | None,
    ]:
        fresh, already = self._claim_audits(hosts, trigger)
        try:
            for host, state in fresh:
                await save_baseline(self._ctx, host.id, AUDIT_STATE_KIND, dict(state))
        except BaseException:
            for host, state in fresh:
                self._release_audit(host.id, state)
                try:  # sonst startete der naechste Start des Dashboards ein Audit, das hier als gescheitert galt
                    await self._drop_audit_state(host.id, state["run_id"])
                except Exception:  # noqa: BLE001 - die Datenbank klemmt ohnehin
                    _log.exception("shield_audit_state_cleanup_failed host=%s", host.id)
            raise
        batch = None
        if fresh:
            batch = asyncio.ensure_future(self._audit_batch(fresh))
            self._audit_tasks.add(batch)  # Referenz halten, sonst kann der Task mitten im Lauf verschwinden
            batch.add_done_callback(self._audit_tasks.discard)
            await self._ctx.ws.broadcast("defender", {"audits_started": [h.id for h, _ in fresh]})
        return fresh, already, batch

    async def start_audits(self, hosts: list[Host], *, trigger: str = "manual") -> dict[str, Any]:
        """Fordert je Server ein Audit an und kehrt sofort zurueck. Laeuft auf einem Server schon eines, startet kein
        zweites: Es steht mit seinem Stand unter `running` (und in `audits` mit `already_running`)."""
        fresh, already, _batch = await self._begin_audits(hosts, trigger)
        audits = [
            {"host_id": h.id, "host_name": h.display_name or h.name, **audit_run_out(st), "already_running": False}
            for h, st in fresh
        ] + [
            {"host_id": h.id, "host_name": h.display_name or h.name, **audit_run_out(st), "already_running": True}
            for h, st in already
        ]
        return {"started": [h.id for h, _ in fresh], "running": [h.id for h, _ in already], "audits": audits}

    async def run_audits(self, hosts: list[Host], *, trigger: str = "schedule") -> list[tuple[Host, av.LynisResult]]:
        """Wie `start_audits`, wartet aber, bis die neu angeforderten Audits fertig sind (Zeitplan). Bricht jemand das
        Warten ab (Lauf des Zeitplans abgebrochen), laufen die Audits trotzdem zu Ende."""
        _fresh, _already, batch = await self._begin_audits(hosts, trigger)
        if batch is None:
            return []
        return await asyncio.shield(batch)

    def cancel_audit_tasks(self) -> None:
        """Beim Beenden der Erweiterung: nicht weiter nachfragen. Die Laeufe auf den Servern gehen weiter; ihr Stand bleibt
        in der Datenbank, und der naechste Start nimmt sie wieder auf (`load_running_audits`, `resume_audits`)."""
        for task in list(self._audit_tasks):
            task.cancel()

    async def load_running_audits(self) -> int:
        """Beim Start, vor der ersten Anfrage: Audits, die beim letzten Beenden noch liefen oder warteten, wieder als
        laufend eintragen (sonst startete ein Klick einen zweiten Lauf). Abgefragt werden sie von `resume_audits`."""
        states = await load_baselines(self._ctx, AUDIT_STATE_KIND)
        loaded = 0
        for host_id, data in states.items():
            if not _valid_audit_state(data):
                await self._drop_audit_state(host_id, None)
                continue
            if host_id in self._audits:
                continue
            self._audits[host_id] = data
            self._audits_to_resume.append((host_id, data))
            loaded += 1
        return loaded

    async def resume_audits(self) -> int:
        """Als Hintergrund-Aufgabe nach dem Start: die von `load_running_audits` geholten Audits weiter abfragen
        (ein wartendes startet jetzt) und abschliessen wie sonst, mit Meldung."""
        pending, self._audits_to_resume = self._audits_to_resume, []
        if not pending:
            return 0
        items: list[tuple[Host, dict[str, Any]]] = []
        try:
            await asyncio.sleep(AUDIT_RESUME_DELAY_S)
            for host_id, state in pending:
                if self._audits.get(host_id) is not state:
                    continue
                host = await self._ctx.hosts.get(host_id)
                if host is None:  # Server inzwischen geloescht: nichts mehr abzufragen
                    await self._drop_audit_state(host_id, state["run_id"])
                    self._release_audit(host_id, state)
                    continue
                items.append((host, state))
        except BaseException:
            # Nicht dauerhaft als "laeuft" stehen lassen; der Stand in der Datenbank bleibt fuer den naechsten Start.
            for host_id, state in pending:
                self._release_audit(host_id, state)
            raise
        if items:
            await self._audit_batch(items, resumed=True)
        return len(items)

    async def _drop_audit_state(self, host_id: str, run_id: str | None) -> None:
        async with self._ctx.db.session() as session:
            row = await session.get(BaselineRecord, f"{host_id}:{AUDIT_STATE_KIND}")
            if row is not None and (run_id is None or (row.data or {}).get("run_id") == run_id):
                await session.delete(row)

    async def _audit_batch(self, items: list[tuple[Host, dict[str, Any]]], *, resumed: bool = False) -> list[tuple[Host, av.LynisResult]]:
        results = await asyncio.gather(*(self._audit_one(h, st) for h, st in items), return_exceptions=True)
        pairs = [(h, r) for (h, _), r in zip(items, results, strict=True) if isinstance(r, av.LynisResult)]
        await self._ctx.ws.broadcast("defender", {"audits": len(pairs)})
        await self._notify_audits(pairs, resumed=resumed)
        return pairs

    async def _audit_one(self, host: Host, state: dict[str, Any]) -> av.LynisResult:
        run_id = state["run_id"]
        try:
            try:
                if state.get("started_at") is None:
                    if self._audit_slots.locked():
                        state["waiting"] = True  # nur fuer die Seite ("wartet seit ...")
                    async with self._audit_slots:
                        state.pop("waiting", None)
                        state["started_at"] = _now().timestamp()
                        await save_baseline(self._ctx, host.id, AUDIT_STATE_KIND, dict(state))
                        await self._ctx.ws.broadcast("defender", {"audit": run_id, "status": "running"})
                        result = await self._launch_audit(host, run_id, state["started_at"])
                else:
                    # Lief schon vor dem Neustart des Dashboards: nur weiter abfragen. Kennt der Server ihn nicht, wurde
                    # er nie gestartet.
                    result = await self._wait_audit(host, run_id, float(state["started_at"]), unknown_grace_s=0,
                                                    unknown_summary=ABORTED_BY_RESTART)
            except Exception as exc:  # noqa: BLE001 - jeder Fehler landet sichtbar am Audit
                _log.exception("shield_audit_failed host=%s run_id=%s", host.id, run_id)
                result = av.LynisResult(status="error", error=_describe(exc))
            try:
                await self._record_audit(host, state, result)
            except Exception:  # noqa: BLE001 - der Server darf nicht dauerhaft als "laeuft" stehen bleiben
                _log.exception("shield_audit_record_failed host=%s run_id=%s", host.id, run_id)
            return result
        finally:
            self._release_audit(host.id, state)

    async def _launch_audit(self, host: Host, run_id: str, started_ts: float) -> av.LynisResult:
        launch = av.as_root(dt.launch_command(run_id, aj.lynis_command(run_id, limit_s=AUDIT_LIMIT_S), kind=aj.AUDITS))
        unknown = aj.NOT_STARTED_MESSAGE
        reach_grace_s = None
        try:
            res = await self._ctx.exec.run(host, launch, timeout_s=AUDIT_LAUNCH_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            # Der Start kann trotzdem geklappt haben (Verbindung erst danach weg): nachsehen. Meldet sich der Server
            # aber gar nicht mehr, nicht drei Stunden lang "laeuft" zeigen.
            unknown = f"Das Audit konnte nicht gestartet werden: {_describe(exc)}"
            reach_grace_s = AUDIT_REACH_GRACE_S
        else:
            out = (res.stdout or "") + (res.stderr or "")
            if av.NO_ROOT in out:
                return av.LynisResult(status="error", error=av.NO_ROOT_MESSAGE)
            info = dt.parse_launch(out)
            if not info.started and info.method is None:  # schon der Ordner scheiterte: nichts gestartet
                lines = [ln for ln in out.strip().splitlines() if not ln.startswith("@@")]
                reason = lines[-1][:200] if lines else "keine Rückmeldung"
                return av.LynisResult(status="error", error=f"Das Audit konnte auf dem Server nicht gestartet werden: {reason}")
        return await self._wait_audit(host, run_id, started_ts, unknown_grace_s=AUDIT_UNKNOWN_GRACE_S,
                                      unknown_summary=unknown, reach_grace_s=reach_grace_s)

    async def _poll_audit(self, host: Host, run_id: str) -> dt.Poll | av.LynisResult | None:
        """Eine Abfrage: der Stand (`dt.Poll`), ein Fehler, der den Lauf beendet (keine root-Rechte), oder None, wenn
        keine Antwort kam."""
        try:
            res = await self._ctx.exec.run(host, av.as_root(aj.poll_command(run_id)), timeout_s=AUDIT_POLL_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - Verbindung weg: weiter nachfragen
            return None
        out = (res.stdout or "") + (res.stderr or "")
        if av.NO_ROOT in out:
            return av.LynisResult(status="error", error=av.NO_ROOT_MESSAGE)
        poll = dt.parse_poll(out)
        return None if poll.state == "noreply" else poll

    async def _wait_audit(self, host: Host, run_id: str, started_ts: float, *, unknown_grace_s: float,
                          unknown_summary: str, reach_grace_s: float | None = None) -> av.LynisResult:
        """Fragt nach, bis der Lauf fertig ist. Verbindungsfehler zwischendurch werden ausgesessen. Ist die Obergrenze
        samt Frist (gerechnet ab `started_ts`, also auch ueber einen Neustart des Dashboards hinweg) erreicht, wird der
        Lauf auf dem Server beendet (`_stop_audit`)."""
        deadline = started_ts + AUDIT_LIMIT_S + AUDIT_STOP_GRACE_S
        unknown_until = time.monotonic() + unknown_grace_s
        reach_until = None if reach_grace_s is None else time.monotonic() + reach_grace_s
        reached = False
        lost = 0
        while True:
            poll = await self._poll_audit(host, run_id)
            if isinstance(poll, av.LynisResult):
                return poll
            if poll is not None:
                reached = True
                if poll.state == "done":
                    return aj.parse_result(poll.output)
                lost = lost + 1 if poll.state == "lost" else 0
                if lost >= 2:  # zweimal hintereinander: kein Zufall beim Nachsehen
                    return av.LynisResult(status="error", error=aj.LOST_MESSAGE)
                if poll.state == "unknown" and time.monotonic() >= unknown_until:
                    return av.LynisResult(status="error", error=unknown_summary)
            if reach_until is not None and not reached and time.monotonic() >= reach_until:
                return av.LynisResult(status="error", error=unknown_summary)
            if _now().timestamp() >= deadline:
                return await self._stop_audit(host, run_id)
            await asyncio.sleep(AUDIT_POLL_INTERVAL_S)

    async def _stop_audit(self, host: Host, run_id: str) -> av.LynisResult:
        """Beendet einen Lauf ueber der Obergrenze auf dem Server (Lynis samt Unterprozessen, `audit_job.stop_command`)."""
        try:
            res = await self._ctx.exec.run(host, av.as_root(aj.stop_command(run_id)), timeout_s=AUDIT_STOP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            return av.LynisResult(status="error", error=aj.unreachable_at_limit_message(_describe(exc), AUDIT_LIMIT_S))
        out = (res.stdout or "") + (res.stderr or "")
        if av.NO_ROOT in out:
            return av.LynisResult(status="error", error=av.NO_ROOT_MESSAGE)
        words = out.split()
        if "@@stopped" in words:
            _log.warning("shield_audit_stopped_at_limit host=%s run_id=%s", host.id, run_id)
            return av.LynisResult(status="error", error=aj.too_long_message(AUDIT_LIMIT_S))
        if "@@stuck" in words:
            return av.LynisResult(status="error", error=aj.stuck_message(AUDIT_LIMIT_S))
        # Der Lauf war gerade eben zu Ende: sein Ergebnis gilt.
        poll = await self._poll_audit(host, run_id)
        if isinstance(poll, av.LynisResult):
            return poll
        if poll is not None and poll.state == "done":
            return aj.parse_result(poll.output)
        return av.LynisResult(status="error", error=aj.LOST_MESSAGE)

    async def _record_audit(self, host: Host, state: dict[str, Any], result: av.LynisResult) -> None:
        """Ergebnis speichern (ID = Lauf-ID, ein zweites Speichern desselben Laufs aendert nichts) und den Stand
        "laeuft" in derselben Transaktion entfernen."""
        run_id = state["run_id"]
        async with self._ctx.db.session() as session:
            if await session.get(AuditRecord, run_id) is None:
                session.add(AuditRecord(
                    id=run_id, host_id=host.id, host_name=host.display_name or host.name or state.get("host_name") or host.id,
                    status=result.status, hardening_index=result.hardening_index,
                    warnings=result.warnings[:100], suggestions=result.suggestions[:200],
                    error=result.error, created_at=_now(),
                ))
            row = await session.get(BaselineRecord, f"{host.id}:{AUDIT_STATE_KIND}")
            if row is not None and (row.data or {}).get("run_id") == run_id:
                await session.delete(row)
        await self._ctx.ws.broadcast("defender", {"audit": run_id, "status": result.status})

    async def _notify_audits(self, results: list[tuple[Host, av.LynisResult]], *, resumed: bool = False) -> None:
        warnings = sum(len(r.warnings) for _, r in results if r.status == "ok")
        failed = [h.display_name or h.name for h, r in results if r.status != "ok"]
        if results and (warnings or failed):
            lines = [
                f"- {h.display_name or h.name}: " + (f"Index {r.hardening_index}, {len(r.warnings)} Warnung(en)" if r.status == "ok" else f"Fehler ({r.error})")
                for h, r in results
            ]
            if resumed:
                lines.append("Das Dashboard wurde währenddessen neu gestartet.")
            await self._ctx.notify.send(Notification(
                title=f"Härtungs-Audit: {warnings} Warnung(en), {len(failed)} Fehler",
                body="\n".join(lines), severity=Severity.WARNING,
                payload={"path": f"{SOC_PATH}?tab=hardening", "tags": ["shield"], **host_scope(h.id for h, _ in results)},
            ))

    # --- Status / Uebersicht -------------------------------------------------------

    def forget_status(self, host_id: str) -> None:
        """Den gemerkten Stand eines Servers verwerfen (z. B. nach einer Installation): Die naechste Abfrage
        fragt den Server neu, statt bis zu 10 Minuten den alten Stand zu zeigen."""
        self._status_cache.pop(host_id, None)

    async def host_status(self, host: Host, *, refresh: bool = False) -> dict[str, Any]:
        cached = self._status_cache.get(host.id)
        if cached and not refresh and cached[0] > _now().timestamp() - 600:
            return cached[1]
        try:
            res = await self._ctx.exec.run(host, av.STATUS_COMMAND, timeout_s=STATUS_TIMEOUT)
            st = av.parse_status(res.stdout or "")
            data = {
                "reachable": True, "clamav_installed": st.clamav_installed, "clamav_version": st.clamav_version,
                "signature_version": st.signature_version, "signature_date": st.signature_date,
                "signature_ts": protection.parse_signature_date(st.signature_date),
                "freshclam_active": st.freshclam_active, "lynis_installed": st.lynis_installed,
                "lynis_version": st.lynis_version, "quarantine_files": st.quarantine_files, "os_id": st.os_id,
            }
        except Exception as exc:  # noqa: BLE001
            data = {"reachable": False, "error": _describe(exc, timeout_s=STATUS_TIMEOUT), "clamav_installed": False,
                    "lynis_installed": False}
        self._status_cache[host.id] = (_now().timestamp(), data)
        return data

    async def overview(self, *, refresh: bool = False) -> dict[str, Any]:
        hosts = await self.target_hosts()
        hosts_known = len(await self._ctx.hosts.list())
        statuses = await asyncio.gather(*(self.host_status(h, refresh=refresh) for h in hosts))
        async with self._ctx.db.session() as session:
            last_scans: dict[str, ScanRecord] = {}
            for row in (await session.execute(
                select(ScanRecord).where(ScanRecord.kind.in_(("quick", "deep", "custom"))).order_by(ScanRecord.started_at.desc()).limit(500)
            )).scalars():
                last_scans.setdefault(row.host_id, row)
            last_audits: dict[str, AuditRecord] = {}
            last_ok_audits: dict[str, AuditRecord] = {}
            for row in (await session.execute(select(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(500))).scalars():
                last_audits.setdefault(row.host_id, row)
                if row.status == "ok" and row.hardening_index is not None:
                    last_ok_audits.setdefault(row.host_id, row)
            counts = dict((await session.execute(
                select(FindingRecord.status, func.count()).group_by(FindingRecord.status)
            )).all())
            since = _now() - timedelta(days=30)
            recent = (await session.execute(
                select(func.count()).select_from(FindingRecord).where(FindingRecord.detected_at >= since)
            )).scalar_one()

        rows = []
        now = _now().timestamp()
        for host, st in zip(hosts, statuses, strict=True):
            scan = last_scans.get(host.id)
            audit = last_audits.get(host.id)
            ok_audit = last_ok_audits.get(host.id)
            rows.append({
                "host_id": host.id, "host_name": host.display_name or host.name, "host_status": host.status.value,
                **st,
                **protection.signature_info(st.get("signature_ts"), now),
                "last_scan": scan_out(scan) if scan else None,
                "last_audit": {"status": audit.status, "hardening_index": audit.hardening_index, "warnings": len(audit.warnings),
                               "created_at": _ts(audit.created_at), "error": audit.error} if audit else None,
                # Letztes erfolgreiches Audit: bleibt sichtbar, wenn das neueste gescheitert ist.
                "last_ok_audit": {"status": ok_audit.status, "hardening_index": ok_audit.hardening_index, "warnings": len(ok_audit.warnings),
                                  "created_at": _ts(ok_audit.created_at), "error": None} if ok_audit else None,
                "scanning": self._big_scan_running(host.id),
                "auditing": host.id in self._audits,
                # Laufendes (oder wartendes) Audit: seit wann, damit die Seite "laeuft seit ..." zeigen kann.
                "audit_run": self.audit_run(host.id),
            })

        open_threats = int(counts.get("detected", 0))
        protected = sum(1 for r in rows if r.get("clamav_installed"))
        indices = [r["last_ok_audit"]["hardening_index"] for r in rows if r.get("last_ok_audit")]
        settings = await self._settings()
        return {
            "hosts": rows,
            "summary": {
                # "hosts" sind nur die pruefbaren Server (Linux, verwaltet, mit SSH-Zugang, ggf. passende Markierung);
                # "hosts_known" alle eingerichteten -- die Seite unterscheidet "keiner angelegt" von "keiner pruefbar".
                "hosts": len(rows), "hosts_known": hosts_known, "protected": protected, "open_threats": open_threats,
                "quarantined": int(counts.get("quarantined", 0)),
                "neutralized_total": int(counts.get("quarantined", 0)) + int(counts.get("deleted", 0)),
                "findings_30d": int(recent),
                "avg_hardening": round(sum(indices) / len(indices)) if indices else None,
                "stale_signatures": sum(1 for r in rows if r.get("clamav_installed") and r.get("signature_stale") is True),
                "score": _score(rows, open_threats, indices),
            },
            "attention": protection.attention_items(rows),
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
        result = await self._ctx.notify.send(Notification(
            title=title, body=body, severity=sev,
            payload={"path": SOC_PATH, "tags": ["coffee"], "actions": [
                {"label": "Updates", "path": f"{SOC_PATH}?tab=updates"}, {"label": "Einbruchschutz", "path": f"{SOC_PATH}?tab=guard"},
            ]},
        ))
        return {"title": title, "body": body, "level": level, "push": push_state(result)}

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


def push_state(result: Any) -> str:
    """Was ist mit der Meldung auf dem Weg aufs Handy passiert? Ehrlich, nicht "gesendet":

    - `sent`: mindestens ein Kanal (z. B. ntfy) hat sie zugestellt
    - `suppressed`: ein Wartungsfenster hat den Push unterdrueckt (die Meldung steht trotzdem unter "Meldungen")
    - `not_delivered`: kein Kanal hat sie zugestellt -- keiner eingerichtet oder alle ausgefallen
    - `unknown`: aeltere Kerne liefern kein Ergebnis"""
    delivered = getattr(result, "delivered", None)
    if result is None or delivered is None:
        return "unknown"
    if getattr(result, "suppressed", False):
        return "suppressed"
    return "sent" if delivered else "not_delivered"


def build_briefing(
    overview: dict[str, Any], hosts_all: list[Any], updates: dict[str, Any] | None = None,
    guard: tuple[str | None, list[str]] | None = None,
) -> tuple[str, str, str]:
    """Titel, Text und Stufe des Lageberichts (wie im alten Skript: ein Blick
    aufs Handy genuegt, um zu wissen, ob etwas zu tun ist).

    Der Titel nennt keine Tageszeit: der Bericht kommt zum eingestellten Zeitpunkt, aber auch
    auf Knopfdruck um 21:45 Uhr -- "Guten Morgen" waere dann falsch."""
    sm = overview["summary"]
    down = [h.display_name or h.name for h in hosts_all if getattr(h.status, "value", h.status) == "down"]
    if not hosts_all:
        lines = ["Server: noch keiner eingerichtet – füge unter Einstellungen → Server & Zugänge den ersten hinzu."]
    else:
        lines = [f"Server: {len(hosts_all) - len(down)}/{len(hosts_all)} erreichbar" + (f" – aus/weg: {', '.join(down[:6])}" if down else "")]
    if sm["hosts"] == 0:
        # Ohne pruefbaren Server gibt es nichts zu bewerten -- kein "Schutzwert 0/100". Offene Funde aus frueheren
        # Scans bleiben aber offen (auch wenn der Server inzwischen fehlt): die gehoeren trotzdem in den Bericht.
        lines.append("Virenschutz: noch nichts zu prüfen – geprüft werden Linux-Server mit SSH-Zugang (und, falls eingestellt, der passenden Markierung).")
        if sm["open_threats"]:
            lines.append(f"Bedrohungen: {sm['open_threats']} offen, {sm['quarantined']} in Quarantäne, {sm['findings_30d']} Funde in 30 Tagen")
    else:
        lines.append(f"Virenschutz: Schutzwert {sm['score']}/100, {sm['protected']}/{sm['hosts']} Server mit ClamAV")
        lines.append(f"Bedrohungen: {sm['open_threats']} offen, {sm['quarantined']} in Quarantäne, {sm['findings_30d']} Funde in 30 Tagen")
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
        # Alte Signaturen und ausgeschaltetes Update sind fast immer dasselbe Problem: ein Eintrag je Server,
        # sonst verdraengen sie bei mehreren Servern die uebrigen Punkte (die Liste ist auf sechs begrenzt).
        sig_problem = protection.signature_problem(h)
        if not h.get("clamav_installed") and h.get("reachable") is not False:
            problems.append(f"{h['host_name']}: kein ClamAV")
        elif scan.get("status") == "error":
            problems.append(f"{h['host_name']}: letzter Scan fehlgeschlagen")
        elif h.get("freshclam_active") is False and not sig_problem:
            problems.append(f"{h['host_name']}: Signatur-Update aus")
        if sig_problem:
            problems.append(sig_problem + (", Signatur-Update aus" if h.get("freshclam_active") is False else ""))
    if problems:
        lines.append("Zu tun: " + "; ".join(problems[:6]))
    level = "critical" if sm["open_threats"] else ("warning" if (down or problems) else "info")
    if level == "critical":
        title = "Lagebericht – Bedrohung offen!"
    elif level == "warning":
        title = "Lagebericht – es gibt etwas zu tun"
    elif not hosts_all:
        title = "Lagebericht – noch kein Server eingerichtet"
    elif sm["hosts"] == 0:
        # Server sind da, aber keiner ist pruefbar (z. B. ohne SSH-Zugang): "alles im gruenen Bereich" waere gelogen.
        title = "Lagebericht – noch nichts geprüft"
    else:
        title = "Lagebericht – alles im grünen Bereich"
    return title, "\n".join(lines), level


def _score(rows: list[dict[str, Any]], open_threats: int, indices: list[int]) -> int:
    """Grobe Schutz-Note 0-100: Abdeckung (ClamAV installiert, aktuelle Scans),
    offene Funde, Haertungsindex, Abzug fuer veraltete Signaturen (Gewichtung in `protection.py`).
    Bewusst einfach und nachvollziehbar."""
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
    score -= protection.signature_penalty(rows)
    return max(0, min(100, round(score)))


def _describe(exc: BaseException, *, timeout_s: float | None = None) -> str:
    """Lesbarer Grund zu einer Ausnahme aus `ctx.exec.run` (und dem Weg dorthin).

    - Fehler der SSH-Verbindung (`core.ssh.SshError` und Unterklassen) tragen `readable = True`, ihr Text ist schon ein
      fertiger deutscher Satz ("Server antwortet nicht (Zeitüberschreitung bei ...)", "Anmeldung abgelehnt" ...). Sie kommen
      vor dem `TimeoutError`: `SshTimeout` ist auch einer, und sonst sah "Server nach 10 Sekunden nicht erreichbar" genauso
      aus wie "Lauf dauerte zu lange".
    - Ein reiner `TimeoutError` ist das Zeitlimit des Laufs (`timeout_s`, falls bekannt, steht als Dauer im Text).
    - Bricht die Verbindung mitten im Lauf ab, kommt der Fehler der SSH-Bibliothek ohne deutschen Text an (`ConnectionLost`
      u. Ae., die Erweiterung kennt die Bibliothek nicht): erkannt am Modul der Klasse, wie `ConnectionError`."""
    text = str(exc).strip()
    if getattr(exc, "readable", False) and text:
        return text
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        if timeout_s:
            return f"Zeitüberschreitung: Der Vorgang dauerte länger als {av.duration_text(timeout_s)} und wurde abgebrochen."
        return "Zeitüberschreitung – der Vorgang hat zu lange gebraucht."
    if isinstance(exc, ConnectionError) or any(cls.__module__.split(".")[0] == "asyncssh" for cls in type(exc).__mro__):
        return f"Die Verbindung zum Server ist abgebrochen ({text})." if text else "Die Verbindung zum Server ist abgebrochen."
    return text or type(exc).__name__
