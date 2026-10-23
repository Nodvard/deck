"""Virenschutz-Ablauf: Scans ausführen, Funde speichern und in Quarantäne verschieben, Härtungs-Audits, Schutzstatus."""

from __future__ import annotations

import asyncio
import contextlib
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
from . import realtime as rt
from .history import occurrences_of
from .hostscope import host_scope
from .ids import EXT_ID, LOGGER, SOC_PATH
from .lynis_text import lynis_help
from .models import (
    AuditRecord,
    BaselineRecord,
    FindingRecord,
    IncidentRecord,
    ScanRecord,
    SecurityEventRecord,
)
from .patching import (
    auto_update_hosts,
    last_run_status,
    load_baseline,
    load_baselines,
    no_auto_reboot,
    save_baseline,
    schedule_zone,
)
from .steps import minute_step

if TYPE_CHECKING:
    from nodvard_sdk import Actor, ExtensionContext, Host

SCAN_TIMEOUTS = {"quick": 20 * 60, "deep": 4 * 60 * 60, "watch": 5 * 60, "custom": 60 * 60}
STATUS_TIMEOUT = 20
# Härtungs-Audit (`audit_job.py`): Obergrenze je Lauf auf dem Server (`timeout` dort). Ist ein Lauf danach noch nicht zu
# Ende (`timeout` fehlt oder half nicht), beendet ihn das Dashboard nach der Frist selbst (`audit_job.stop_command`).
AUDIT_LIMIT_S = aj.LIMIT_S
AUDIT_STOP_GRACE_S = 5 * 60
AUDIT_LAUNCH_TIMEOUT_S = 90  # Ordner anlegen, Lauf abkoppeln, bis 5 s auf die pid warten
AUDIT_POLL_INTERVAL_S = 30
AUDIT_POLL_TIMEOUT_S = 30
AUDIT_STOP_TIMEOUT_S = 60
AUDIT_UNKNOWN_GRACE_S = 60  # so lange darf ein gerade gestarteter Lauf auf dem Server noch "unbekannt" sein
AUDIT_REACH_GRACE_S = 3 * 60  # nach einem gescheiterten Start: so lange muss sich der Server wenigstens einmal melden
AUDIT_RESUME_DELAY_S = 5  # nach dem Start des Dashboards kurz warten, bis Netz und SSH da sind
# Stand eines laufenden (oder wartenden) Audits je Server in der Baselines-Tabelle (`<host_id>:audit_run`): überdauert einen
# Neustart. Ist der Lauf fertig, steht das Ergebnis in `AuditRecord` (gleiche ID) und die Zeile ist weg.
AUDIT_STATE_KIND = "audit_run"
_AUDIT_ID_RE = re.compile(r"^audit_[0-9a-f]{16}$")
# Das Hilfsskript prüft die Datei vorher selbst; ohne clamd lädt clamscan dafür die ganze Datenbank (auf einem
# kleinen Rechner gut eine Minute).
QUARANTINE_TIMEOUT = 300
# Nach einer Zeitüberschreitung kann das Verschieben auf dem Server weiterlaufen (ClamAV prüft die Datei zweimal):
# so lange und so oft nachsehen, ob sie in der Quarantäne ankommt.
QUARANTINE_FOLLOW_S = 20 * 60
QUARANTINE_FOLLOW_INTERVAL_S = 30
QUARANTINE_STATE_TIMEOUT = 60
MAX_PARALLEL = 3
# So lange wartet ein Scan (nicht der Wächter) auf die Sperre des Servers (`av.guard_command`): etwas länger, als ein
# Wächter-Lauf höchstens dauert.
SCAN_LOCK_WAIT_S = SCAN_TIMEOUTS["watch"] + 60
# Der Wächter prüft Dateien, die seit dem Beginn seines letzten Laufs geändert wurden (plus 2 Minuten Puffer), mindestens
# das Intervall plus 2 Minuten. Fiel ein Lauf aus, wächst das Fenster mit, höchstens bis hierher: Das deckt einen Tiefenscan
# (bis 4 Stunden) ab, ohne dass nach einer langen Pause ein Lauf alles der letzten Tage prüfen muss.
WATCH_MAX_WINDOW_MIN = 6 * 60
# Setzt der Wächter so lange aus, dass sein Fenster die Grenze erreicht, gehen Dateien ungeprüft verloren: Dann gibt es
# eine sichtbare Fehlerzeile (höchstens eine je `WATCH_MAX_WINDOW_MIN`), sonst bliebe unbemerkt, dass der Wächter nicht
# mehr läuft (Sperre dauerhaft belegt: toter Netzlaufwerk-Scan, fehlendes `timeout`).
_WATCH_MAX_WINDOW_TEXT = av.duration_text(WATCH_MAX_WINDOW_MIN * 60)
WATCH_BLOCKED_MESSAGE = (
    f"Der Echtzeit-Wächter konnte seit etwa {_WATCH_MAX_WINDOW_TEXT} nicht prüfen: Auf dem Server lief die ganze Zeit ein "
    f"anderer Scan, oder einer hängt dort. Dateien, die vor mehr als {_WATCH_MAX_WINDOW_TEXT} geändert wurden, prüft der "
    "Wächter nicht mehr nach. Sieh auf dem Server nach, ob ein Scan hängt (zum Beispiel an einem Netzlaufwerk, das nicht mehr "
    "erreichbar ist), und starte danach einen Schnellscan."
)

# Ein Lauf, dessen Fund-Zeilen nicht verlässlich lesbar waren (`ScanResult.unreliable`): Hinweis am Scan, Notiz an jedem
# Fund und Text der Push-Meldung. Es wird nichts automatisch verschoben.
UNRELIABLE_HINT = (
    "Die Meldung von ClamAV ist nicht eindeutig lesbar (zum Beispiel ein Zeilenumbruch im Dateinamen). "
    "Es wurde nichts automatisch verschoben – bitte auf dem Server nachsehen."
)
UNRELIABLE_NOTE = (
    "Der Pfad ist nicht gesichert: Die Ausgabe von ClamAV war nicht eindeutig lesbar (zum Beispiel ein "
    "Zeilenumbruch im Dateinamen). Bitte vor dem Verschieben auf dem Server nachsehen."
)

REALTIME_LOG_BROKEN = "Das Protokoll des Echtzeit-Wächters kam nicht vollständig zurück."
REALTIME_UNCHECKED = (
    "clamd konnte den Fund aus dem Protokoll des Echtzeit-Wächters nicht noch einmal prüfen. Es wurde nichts "
    "automatisch verschoben – bitte auf dem Server nachsehen."
)
REALTIME_UNREADABLE = (
    "Im Protokoll des Echtzeit-Wächters stehen {count} Fund-Zeile(n), die Nodvard Deck nicht lesen kann. Bitte auf dem "
    "Server in /var/log/clamav/nodvard-onaccess.log nachsehen."
)
REALTIME_OUTSIDE = (
    "Der Echtzeit-Wächter meldet einen Fund außerhalb der eingestellten Ordner oder in einer Ausnahme. Es wurde nichts "
    "automatisch verschoben – bitte auf dem Server nachsehen."
)

QUARANTINE_NO_ANSWER = "Nach {duration} kam keine Rückmeldung vom Server."
QUARANTINE_FOLLOWING = (
    "Die Datei kann trotzdem noch in die Quarantäne kommen, das Verschieben läuft auf dem Server womöglich weiter. "
    "Deck sieht bis {until} Uhr nach und trägt das Ergebnis hier ein."
)
QUARANTINE_FOLLOWING_MARK = QUARANTINE_FOLLOWING.split("{until}")[1]
QUARANTINE_STILL_FOLLOWING = (
    "Deck sieht noch bis {until} Uhr nach, ob die Datei vom letzten Versuch in die Quarantäne kam – erst danach ist ein "
    "neuer Versuch möglich."
)
QUARANTINE_STILL_RUNNING = "Für diesen Fund läuft gerade schon ein Versuch – bitte warten, bis er fertig ist."
QUARANTINE_LATE_ABSENT = (
    "Quarantäne fehlgeschlagen: Der Server hat nicht rechtzeitig geantwortet, und die Datei ist auch danach nicht in "
    "der Quarantäne angekommen. Bitte erneut versuchen."
)
QUARANTINE_LATE_CHECKING = (
    f"Die Datei liegt noch im Prüfordner der Quarantäne ({av.QUARANTINE_DIR}/{av.CHECK_DIR_NAME}). Bitte auf dem Server "
    "nachsehen."
)
QUARANTINE_LATE_UNKNOWN = (
    f"Ob die Datei in die Quarantäne kam, ließ sich nicht feststellen. Bitte auf dem Server unter {av.QUARANTINE_DIR} "
    "und am ursprünglichen Ort nachsehen."
)

KIND_LABEL = {"quick": "Schnellscan", "deep": "Tiefenscan", "watch": "Echtzeit-Wächter", "custom": "Scan"}
# Die "großen" Scans: je Server läuft höchstens einer davon, und der Wächter wartet, bis er fertig ist
# (jeder clamscan lädt die ganze Datenbank, ~1 GB RAM).
BIG_SCAN_KINDS = ("quick", "deep", "custom")

_log = logging.getLogger(LOGGER)

# Scans und Update-Läufe leben als Hintergrund-Task im Prozess: Nach einem Neustart kommt ein abgebrochener Lauf nie zurück.
ABORTED_BY_RESTART = "Abgebrochen – Dashboard wurde neu gestartet"


# Grenzen wie in settings.schema.json. Ein älter gespeicherter Wert (0, -5, 5000) landete sonst im Befehl; über 1000 MB
# läge `--max-scansize` (das Vierfache) jenseits dessen, was ClamAV annimmt.
def max_filesize_mb(settings: dict[str, Any]) -> int:
    return max(1, min(1000, int(settings.get("max_filesize_mb") or 50)))


def watch_interval_min(settings: dict[str, Any]) -> int:
    return minute_step(int(settings.get("watch_interval_min") or 10), 2)


def _unreliable_hint(parsed: av.ScanResult) -> str:
    """Hinweistext zu einem Lauf ohne automatische Quarantäne: unleserliche Pfade, ungeprüfte Dateien oder beides."""
    if parsed.hint:
        return parsed.hint
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


def scan_outcome(statuses: dict[str, int] | None) -> str | None:
    """Was aus den Funden eines befallenen Scans geworden ist: "open", "quarantined", "accepted" (ignoriert oder
    wiederhergestellt, ein Mensch hat die Datei für harmlos erklärt) oder "cleaned" (alle gelöscht). Ohne gespeicherten
    Fund None: Dann bleibt es bei "Bedrohung", nie bei einer Entwarnung."""
    if not statuses:
        return None
    if statuses.get("detected"):
        return "open"
    if statuses.get("quarantined"):
        return "quarantined"
    if statuses.get("ignored") or statuses.get("restored"):
        return "accepted"
    return "cleaned"


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
        # Deutsche Texte zu den bekannten Lynis-Kennungen dieses Audits (lynis_text.py), nach Kennung.
        "help": lynis_help([*(r.warnings or []), *(r.suggestions or [])]),
    }


def audit_run_out(state: dict[str, Any]) -> dict[str, Any]:
    """Ein laufendes Audit für die Seite: `requested_at`, `started_at` (None bis zum Start), Auslöser und `waiting`
    (wartet auf einen freien Platz, es laufen schon `MAX_PARALLEL` andere)."""
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
        self._running: set[tuple[str, str]] = set()  # (host_id, kind), kein doppelter Lauf
        self._status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._readiness_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # Seit wann der Wächter je Server wegen der belegten Sperre aussetzt; im Speicher, da er keine Zeile hinterlässt.
        self._watch_busy_since: dict[str, datetime] = {}
        # Laufende (oder wartende) Audits je Server, derselbe Stand wie in der Baselines-Tabelle (`AUDIT_STATE_KIND`).
        self._audits: dict[str, dict[str, Any]] = {}
        # Höchstens so viele Audits starten gleichzeitig (Server auf derselben Hardware, z. B. VMs eines Proxmox).
        self._audit_slots = asyncio.Semaphore(MAX_PARALLEL)
        self._audit_tasks: set[asyncio.Future[Any]] = set()
        # Funde, bei denen nach einer Zeitüberschreitung noch nachgesehen wird, ob sie in die Quarantäne kamen.
        self._quarantine_follow: dict[str, tuple[asyncio.Future[Any], str]] = {}
        self._quarantining: set[str] = set()  # Funde, deren Verschieben gerade läuft: ein zweiter Klick wartet
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
        """Beim Start: Scans, die beim letzten Beenden noch liefen, als abgebrochen markieren (sonst "läuft" ewig)."""
        async with self._ctx.db.session() as session:
            result = await session.execute(
                update(ScanRecord).where(ScanRecord.status == "running")
                .values(status="error", error=ABORTED_BY_RESTART, finished_at=_now())
            )
            return result.rowcount or 0

    async def start_scans(self, hosts: list[Host], kind: str, *, paths: list[str] | None, trigger: str) -> list[str]:
        """Legt je Server einen Scan an und startet ihn im Hintergrund; gibt die IDs zurück."""
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
                # Nie zwei clamscan gleichzeitig auf einem Server: Läuft die gleiche Art oder ein großer Scan schon,
                # startet weder ein zweiter großer noch der Wächter. Ein laufender Wächter (kurz) hält einen großen
                # Scan nicht auf.
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
        """Die Wächter-Zeile ersetzt die des vorigen Laufs, wenn auch die nur wegen der Obergrenze unvollständig war,
        damit solche Zeilen die echten Scans nicht aus der Liste verdrängen; eine saubere Zeile, ein Fund oder ein
        anderer Fehler dazwischen unterbrechen die Folge."""
        previous = (await session.execute(
            select(ScanRecord).where(ScanRecord.host_id == host_id, ScanRecord.kind == "watch", ScanRecord.id != scan_id)
            .order_by(ScanRecord.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        if previous is not None and previous.status == "error" and (previous.error or "").startswith(av.SKIPPED_PREFIX):
            await session.delete(previous)

    async def _watch_minutes(self, host_id: str, scan_id: str, interval_min: int) -> int:
        """Fenster des Wächters in Minuten (`find -mmin`): ab Beginn des letzten wirklich gelaufenen Laufs plus 2, mindestens
        Intervall plus 2, höchstens `WATCH_MAX_WINDOW_MIN`. Ein Fehlerlauf zählt (sonst wüchse das Fenster endlos), ein von
        einem Neustart abgebrochener (`ABORTED_BY_RESTART`) und `WATCH_BLOCKED_MESSAGE` nicht."""
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
        """Gab es für diesen Server in den letzten `WATCH_MAX_WINDOW_MIN` Minuten schon `WATCH_BLOCKED_MESSAGE`?"""
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
        minutes = 0
        collected = await self._collect_realtime(host, settings) if kind == "watch" else None
        if collected is not None:
            parsed, output = collected
        else:
            parsed, output, minutes = await self._scan_on_host(host, scan_id, kind, paths, settings)
        await self._record_scan(host, scan_id, kind, parsed, output, minutes, settings)

    async def _collect_realtime(self, host: Host, settings: dict[str, Any]) -> tuple[av.ScanResult, str] | None:
        """Holt neue Funde aus dem Protokoll des Echtzeit-Wächters und prüft jeden noch einmal über clamd.

        `None`, wenn der Wächter dort nicht in Echtzeit läuft: Dann prüft dieser Lauf wie bisher im Minuten-Takt."""
        state = (await load_baseline(self._ctx, host.id, rt.STATE_KIND)) or {}
        if not state.get("enabled"):
            return None
        line = ((await load_baseline(self._ctx, host.id, rt.POSITION_KIND)) or {}).get("line")
        if isinstance(line, bool) or not isinstance(line, int) or not 0 <= line <= rt.MAX_LINE:
            line = 0
        command = av.via_helper(["realtime-log", str(line)], rt.log_command(line), version=av.HELPER_REALTIME_LOG_VERSION)
        try:
            res = await self._ctx.exec.run(host, command, timeout_s=STATUS_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - wie bei einem Scan: der Fehler steht sichtbar am Lauf
            return av.ScanResult(status="error", error=_describe(exc, timeout_s=STATUS_TIMEOUT)), ""
        stdout = res.stdout or ""
        if av.NO_ROOT in stdout:
            return av.ScanResult(status="error", error=av.no_root_message(stdout)), ""
        chunk = rt.parse_log(stdout)
        if chunk is None:
            return av.ScanResult(status="error", error=REALTIME_LOG_BROKEN), stdout[-4000:]
        # Der Rückfall steht in einer eigenen Zeile (`DOWN_KIND`), nicht im Schalter: Schaltet jemand während des Laufs aus,
        # schriebe der Lauf sonst „eingeschaltet“ zurück. `changed_at` bindet ihn an genau dieses Einschalten.
        down = (await load_baseline(self._ctx, host.id, rt.DOWN_KIND)) or {}
        if not chunk.active and not chunk.lines:
            # clamonacc läuft nicht (etwa nach einem Absturz), und das Protokoll ist abgeholt: Minuten-Takt. Das zeigt
            # `/realtime/readiness`, eingeschaltet bleibt der Wächter (läuft clamonacc wieder, gilt Echtzeit).
            _log.warning("shield_realtime_inactive host=%s", host.id)
            if not down.get("since") or down.get("changed_at") != state.get("changed_at"):
                marker = {"since": _now().isoformat(), "changed_at": state.get("changed_at")}
                await save_baseline(self._ctx, host.id, rt.DOWN_KIND, marker)
            return None
        if chunk.active and down:
            await save_baseline(self._ctx, host.id, rt.DOWN_KIND, {})
        candidates = rt.found_lines(chunk.lines)
        confirmed = await self._confirm_realtime(host, [path for path, _ in candidates]) if candidates else {}
        if confirmed is None:
            # Ohne Gegenprüfung bleibt jeder Fund aus dem Protokoll stehen, aber nur mit Notiz und ohne Quarantäne.
            findings, hint = candidates, REALTIME_UNCHECKED
        else:
            findings = [(path, confirmed[path]) for path, _ in candidates if path in confirmed]
            watch, exclude = settings.get("watch_paths") or av.WATCH_PATHS, settings.get("exclude_paths") or []
            hint = REALTIME_OUTSIDE if any(rt.outside_watch(path, watch, exclude) for path, _ in findings) else None
        await save_baseline(self._ctx, host.id, rt.POSITION_KIND, {"line": chunk.end})
        unsure = hint is not None
        unreadable = rt.unreadable_found(chunk.lines)
        parsed = av.ScanResult(
            status="infected" if findings else ("error" if unreadable else "clean"), findings=findings,
            infected=len(findings), unreliable=unsure, paths_unsure=unsure, hint=hint,
            error=REALTIME_UNREADABLE.format(count=unreadable) if unreadable else None,
        )
        checked = "nicht gegengeprüft" if confirmed is None else f"{len(findings)} bestätigt"
        summary = f"Protokoll des Echtzeit-Wächters, Zeilen {chunk.start + 1} bis {chunk.end}: "
        summary += f"{len(candidates)} Fund-Zeile(n), {checked}"
        return parsed, "\n".join([summary, *chunk.lines[-50:]])

    async def _confirm_realtime(self, host: Host, paths: list[str]) -> dict[str, str] | None:
        """Prüft die Pfade aus dem Protokoll noch einmal; nur was clamd jetzt wieder findet, gilt als Fund.

        Ein Dateiname mit Zeilenumbruch kann im Protokoll einen Fund für eine fremde, saubere Datei vortäuschen; die
        Gegenprüfung findet dort nichts. `None`, wenn clamd nicht prüfen konnte."""
        confirmed: dict[str, str] = {}
        for i in range(0, len(paths), rt.CONFIRM_BATCH):
            batch = paths[i:i + rt.CONFIRM_BATCH]
            command = av.via_helper(["realtime-confirm", *batch], rt.confirm_command(batch),
                                    version=av.HELPER_REALTIME_LOG_VERSION)
            try:
                res = await self._ctx.exec.run(host, command, timeout_s=SCAN_TIMEOUTS["watch"])
            except Exception:  # noqa: BLE001 - dann gilt der Fund als nicht gegengeprüft
                _log.warning("shield_realtime_confirm_failed host=%s", host.id, exc_info=True)
                return None
            found = rt.parse_confirm(res.stdout or "", batch)
            if found is None:
                return None
            confirmed.update(found)
        return confirmed

    async def _scan_on_host(
        self, host: Host, scan_id: str, kind: str, paths: list[str], settings: dict[str, Any],
    ) -> tuple[av.ScanResult, str, int]:
        """Der Scan oder Minuten-Lauf des Wächters auf dem Server; zurück: Ergebnis, Ausgabe, Fenster in Minuten."""
        max_mb = max_filesize_mb(settings)
        limit = SCAN_TIMEOUTS.get(kind, 3600)
        # Zufallsmarke je Lauf, im Befehl und bei der Auswertung dieselbe; ein Dateiname kann sie nicht erraten.
        mark = av.new_rc_mark()
        minutes = 0
        exclude = settings.get("exclude_paths") or []
        if kind == "watch":
            minutes = await self._watch_minutes(host.id, scan_id, watch_interval_min(settings))
            # Einstellung "Wächter über clamdscan" (Standard aus): nur der Wächter, die anderen Scans bleiben bei clamscan.
            use_clamd = bool(settings.get("watch_use_clamdscan", False))
            command = av.build_watch_command(paths, minutes=minutes, max_filesize_mb=max_mb, use_clamd=use_clamd,
                                             mark=mark, skip_folders=exclude)
            args = av.watch_helper_args(paths, minutes=minutes, max_filesize_mb=max_mb, use_clamd=use_clamd, mark=mark,
                                        skip_folders=exclude, limit_s=limit)
        else:
            command = av.build_scan_command(paths, max_filesize_mb=max_mb, exclude=exclude, mark=mark)
            args = av.scan_helper_args(paths, max_filesize_mb=max_mb, exclude=exclude, mark=mark, limit_s=limit,
                                       wait_s=SCAN_LOCK_WAIT_S)
        # Nie zwei Läufe zugleich auf einem Server (auch nicht aus einem zweiten Dashboard oder einem Lauf, der einen
        # Neustart überdauert hat): Der Wächter setzt aus, ein Scan wartet. Dazu ein Zeitlimit auf dem Server selbst.
        command = av.guard_command(command, mark=mark, limit_s=limit, wait_s=None if kind == "watch" else SCAN_LOCK_WAIT_S)
        # Das Hilfsskript bekommt dieselben Angaben und baut denselben Lauf (`scan`/`watch`), sonst der alte Weg.
        if args is None:
            command = av.as_root(command, required=False)
        else:
            command = av.via_helper(args, command, version=av.HELPER_SCAN_VERSION, required=False)
        try:
            result = await self._ctx.exec.run(host, command, timeout_s=limit)
            output = (result.stdout or "") + (result.stderr or "")
            # Die Wurzeln sind genau die Pfade, die der Befehl gescannt hat: Funde außerhalb davon sind nie echt.
            parsed = av.parse_scan_output(output, mark, roots=paths, limit_s=limit)
            if result.exit_code == 127:
                parsed = av.ScanResult(status="error", error=av.NOT_INSTALLED_MESSAGE)
        except Exception as exc:  # noqa: BLE001 - jeder Fehler landet sichtbar am Scan
            output = ""
            parsed = av.ScanResult(status="error", error=_describe(exc, timeout_s=limit))
        return parsed, output, minutes

    async def _record_scan(
        self, host: Host, scan_id: str, kind: str, parsed: av.ScanResult, output: str, minutes: int,
        settings: dict[str, Any],
    ) -> None:
        """Schreibt das Ergebnis an den Scan, legt Funde an, verschiebt sie (wenn erlaubt) und meldet sie."""
        if kind == "watch" and parsed.busy:
            busy_since = self._watch_busy_since.setdefault(host.id, _now())
            too_long = minutes >= WATCH_MAX_WINDOW_MIN or _now() - busy_since >= timedelta(minutes=WATCH_MAX_WINDOW_MIN)
            if not too_long or await self._recently_blocked(host.id, scan_id):
                # Auf dem Server lief gerade ein anderer Lauf: Der Wächter setzt diesmal aus, ohne Fehler und ohne Zeile in
                # der Liste. Das Fenster des nächsten Laufs beginnt dann beim letzten, der wirklich lief (`_watch_minutes`).
                async with self._ctx.db.session() as session:
                    scan = await session.get(ScanRecord, scan_id)
                    if scan is not None:
                        await session.delete(scan)
                _log.info("shield_watch_skipped_host_busy host=%s", host.id)
                await self._ctx.ws.broadcast("defender", {"scan": scan_id, "skipped": True})
                return
            # So lange belegt, dass Dateien ungeprüft verloren gehen: sichtbar als Fehler.
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
                    ).order_by(FindingRecord.detected_at.desc())
                )).scalars().first()
                if existing is not None:
                    existing.scan_id = scan_id
                    if parsed.paths_unsure and not existing.note:
                        existing.note = parsed.hint or UNRELIABLE_NOTE
                    finding_ids.append(existing.id)
                    continue
                # Vom Menschen entschieden (ignoriert oder nach Freigabe wiederhergestellt): Dieselbe Datei mit derselben
                # Signatur ist kein neuer Fund, ohne Quarantäne und Alarm; nur der jüngste Scan wird vermerkt. Nicht bei
                # unsicheren Pfaden: Ein vorgetäuschter Pfad darf keinen echten Fund als bekannt tarnen.
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
                    note=(parsed.hint or UNRELIABLE_NOTE) if parsed.paths_unsure else None,
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
            # Nie automatisch, wenn die Pfade nicht verlässlich sind: Ein Dateiname mit Zeilenumbruch kann einen Fund für
            # einen fremden Pfad vortäuschen (`/etc/passwd: ... FOUND`), und die Quarantäne läuft als root. Dann
            # entscheidet der Mensch (die Funde tragen eine Notiz).
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
                    # Absichtlich ohne `host_id`: Ein Fund ist nie Wartungslärm, ein Wartungsfenster (Tiefenscan
                    # sonntags 03:30) darf ihn nicht stummschalten (hostscope.py).
                    payload={"path": f"{SOC_PATH}?tab=quarantine", "tags": ["biohazard"],
                             "actions": [{"label": "Quarantäne öffnen", "path": f"{SOC_PATH}?tab=quarantine"}]},
                ))
        await self._ctx.ws.broadcast("defender", {"scan": scan_id, "status": parsed.status})

    # --- Quarantäne --------------------------------------------------------------

    async def _finding(self, finding_id: str) -> FindingRecord | None:
        async with self._ctx.db.session() as session:
            return await session.get(FindingRecord, finding_id)

    async def finding_record(self, finding_id: str) -> FindingRecord | None:
        """Frische Zeile (u. a. mit original_mode) für den Befehlsbau der Aktionen."""
        return await self._finding(finding_id)

    async def quarantine(self, finding_id: str, *, actor: Actor | None) -> tuple[bool, str]:
        # Bewusst ohne Freigabe: Ein Fund muss sofort unschädlich werden, nicht erst am nächsten Morgen. Das ist umkehrbar
        # (Wiederherstellen) und steht im Audit-Log; Löschen, Wiederherstellen und Installieren gehen dagegen durchs Gate.
        finding = await self._finding(finding_id)
        if finding is None:
            return False, "Unbekannter Fund."
        if finding.status != "detected":
            return False, "Dieser Fund ist nicht (mehr) offen."
        if finding_id in self._quarantine_follow:
            return False, QUARANTINE_STILL_FOLLOWING.format(until=self._quarantine_follow[finding_id][1])
        if finding_id in self._quarantining:
            return False, QUARANTINE_STILL_RUNNING
        # Ohne await zwischen Prüfung und Eintrag: Zwei gleichzeitige Klicks starten nie zwei Läufe. Frei wird der Fund erst,
        # wenn eine Nachschau eingetragen ist.
        self._quarantining.add(finding_id)
        try:
            return await self._move_to_quarantine(finding, actor)
        finally:
            self._quarantining.discard(finding_id)

    async def _move_to_quarantine(self, finding: FindingRecord, actor: Actor | None) -> tuple[bool, str]:
        finding_id = finding.id
        host = await self._ctx.hosts.get(finding.host_id)
        if host is None:
            return False, "Server nicht mehr bekannt."
        qname = av.quarantine_name(finding.id, finding.path)
        unanswered = False
        try:
            command = av.via_helper(
                av.quarantine_args(finding.path, qname), av.quarantine_command(finding.path, qname),
                version=av.HELPER_QUARANTINE_VERSION,
            )
            result = await self._ctx.exec.run(host, command, timeout_s=QUARANTINE_TIMEOUT)
            ok = result.exit_code == 0
            message = av.helper_text(result.stdout or result.stderr or "").strip()
            if av.NO_ROOT in message:
                message = av.no_root_message(result.stdout)
        except Exception as exc:  # noqa: BLE001
            ok, message = False, _describe(exc, timeout_s=QUARANTINE_TIMEOUT)
            # Nur wenn der Befehl losgeschickt war und die Antwort ausblieb, kann das Verschieben weiterlaufen; eine
            # abgelehnte Anmeldung, ein fremder Schlüssel oder ein nicht erreichbarer Server kommen nie bis dahin.
            unanswered = _lost_in_flight(exc)
            if unanswered and isinstance(exc, TimeoutError):
                message = QUARANTINE_NO_ANSWER.format(duration=av.duration_text(QUARANTINE_TIMEOUT))
        until = ""
        if unanswered:
            until = (_now() + timedelta(seconds=QUARANTINE_FOLLOW_S)).astimezone(await schedule_zone(self._ctx)).strftime("%H:%M")
            message = f"{message} {QUARANTINE_FOLLOWING.format(until=until)}"
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding_id)
            if row is not None:
                if ok:
                    row.status = "quarantined"
                    row.quarantine_path = f"{av.QUARANTINE_DIR}/{qname}"
                    row.original_mode = av.parse_mode(message)
                    row.note = None
                else:
                    row.note = message if unanswered else f"Quarantäne fehlgeschlagen: {message[-300:]}"
                row.status_changed_at = _now()
        await self._ctx.audit.log(
            action=f"{EXT_ID}.quarantine", outcome="success" if ok else "failure", target_type="host", target_id=finding.host_id,
            reason=finding.path, detail={"finding_id": finding_id, "signature": finding.signature, "automatic": actor is None},
            correlation_id=finding.scan_id, **({"actor": actor} if actor is not None else {}),
        )
        if unanswered:
            task = asyncio.ensure_future(self._follow_quarantine(host, finding, qname, actor))
            self._quarantine_follow[finding_id] = (task, until)
            task.add_done_callback(lambda _t: self._quarantine_follow.pop(finding_id, None))
        return ok, "In Quarantäne verschoben." if ok else message

    async def _follow_quarantine(self, host: Host, finding: FindingRecord, qname: str, actor: Actor | None) -> None:
        """Sieht nach einer Zeitüberschreitung nach, ob die Datei doch in der Quarantäne ankam, und trägt das am Fund ein."""
        command = av.via_helper(
            av.quarantine_state_args(qname), av.quarantine_state_command(qname), version=av.HELPER_QUARANTINE_STATE_VERSION,
        )
        deadline = time.monotonic() + QUARANTINE_FOLLOW_S
        state, output, written = None, "", False
        try:
            # „Nicht da“ zählt erst am Ende: Vorher kann ClamAV die Datei noch an ihrem Ort prüfen.
            while time.monotonic() < deadline:
                await asyncio.sleep(QUARANTINE_FOLLOW_INTERVAL_S)
                try:
                    res = await self._ctx.exec.run(host, command, timeout_s=QUARANTINE_STATE_TIMEOUT)
                except Exception:  # noqa: BLE001 - Server noch nicht wieder erreichbar: weiter nachsehen
                    _log.debug("shield_quarantine_follow_unreachable host=%s finding=%s", host.id, finding.id)
                    # Was eine frühere Abfrage sah, gilt ohne neue Antwort nicht mehr.
                    state, output = None, ""
                    continue
                output = res.stdout or ""
                if av.NO_ROOT in output:
                    # Ohne root antwortet jede weitere Abfrage genauso.
                    state = None
                    break
                state = av.parse_quarantine_state(output)
                if state == "quarantined":
                    break
            written = await self._settle_quarantine(finding, qname, state, output)
            if written and state == "quarantined":
                await self._ctx.audit.log(
                    action=f"{EXT_ID}.quarantine", outcome="success", target_type="host", target_id=finding.host_id,
                    reason=finding.path,
                    detail={"finding_id": finding.id, "signature": finding.signature, "automatic": actor is None, "late": True},
                    correlation_id=finding.scan_id, **({"actor": actor} if actor is not None else {}),
                )
            await self._ctx.ws.broadcast("defender", {"finding": finding.id})
        except BaseException:
            # Abgebrochen (Neustart, Erweiterung aus) oder gescheitert: das Versprechen am Fund nicht stehen lassen.
            if not written:
                _log.warning("shield_quarantine_follow_stopped host=%s finding=%s", host.id, finding.id, exc_info=True)
                with contextlib.suppress(Exception):
                    await asyncio.shield(self._note_unfinished(finding.id))
            raise

    async def _settle_quarantine(self, finding: FindingRecord, qname: str, state: str | None, output: str) -> bool:
        """Trägt das Ergebnis der Nachschau am Fund ein; `False`, wenn der Fund inzwischen anders erledigt ist."""
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding.id)
            # Wer den Fund inzwischen ignoriert hat, bekommt trotzdem die Wahrheit: Die Datei liegt in der Quarantäne.
            if row is None or row.status not in (("detected", "ignored") if state == "quarantined" else ("detected",)):
                return False
            if state == "quarantined":
                row.status = "quarantined"
                row.quarantine_path = f"{av.QUARANTINE_DIR}/{qname}"
                row.original_mode = av.parse_mode(output)
                row.note = None
            elif state == "absent":
                row.note = QUARANTINE_LATE_ABSENT
            elif state == "checking":
                row.note = QUARANTINE_LATE_CHECKING
            else:
                row.note = QUARANTINE_LATE_UNKNOWN
            row.status_changed_at = _now()
        return True

    async def _note_unfinished(self, finding_id: str) -> None:
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding_id)
            if row is not None and row.status == "detected":
                row.note = QUARANTINE_LATE_UNKNOWN
                row.status_changed_at = _now()

    async def drop_stale_follow_notes(self) -> int:
        """Beim Start: Eine Nachschau aus der Zeit vor dem Neustart läuft nicht mehr; ihr Versprechen am Fund wird ersetzt."""
        async with self._ctx.db.session() as session:
            rows = (await session.execute(
                select(FindingRecord).where(FindingRecord.status == "detected", FindingRecord.note.contains(QUARANTINE_FOLLOWING_MARK))
            )).scalars().all()
            for row in rows:
                row.note = QUARANTINE_LATE_UNKNOWN
                row.status_changed_at = _now()
            return len(rows)

    def cancel_quarantine_follow(self) -> None:
        """Beim Beenden der Erweiterung: nicht weiter nachsehen; der Fund bekommt den Hinweis, selbst nachzusehen."""
        for task, _until in list(self._quarantine_follow.values()):
            task.cancel()

    async def apply_action_result(self, finding_id: str, action: str, ok: bool, message: str) -> None:
        """Zieht den Fund nach Wiederherstellen oder Löschen (über das Gate) nach."""
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

    async def reopen(self, finding_id: str) -> tuple[bool, str]:
        """Nimmt ein Ignorieren oder Wiederherstellen zurück: Der Fund ist wieder offen und der nächste Scan meldet ihn."""
        async with self._ctx.db.session() as session:
            row = await session.get(FindingRecord, finding_id)
            if row is None or row.status not in ("ignored", "restored"):
                return False, "Dieser Fund ist weder ignoriert noch wiederhergestellt."
            # Je Datei höchstens ein offener Fund: Der Scan ordnet eine Meldung genau einem offenen Fund zu.
            other = (await session.execute(
                select(FindingRecord.id).where(
                    FindingRecord.host_id == row.host_id, FindingRecord.path == row.path, FindingRecord.id != row.id,
                    FindingRecord.status.in_(("detected", "quarantined")),
                ).limit(1)
            )).scalar_one_or_none()
            if other is not None:
                return False, ("Für diese Datei gibt es schon einen offenen Fund oder sie liegt in der Quarantäne. "
                               "Bitte den bei „Offen & in Quarantäne“ bearbeiten.")
            row.status = "detected"
            row.status_changed_at = _now()
            # Eine Nachschau, die nicht mehr läuft, verspricht am wieder offenen Fund nichts mehr.
            if row.note and QUARANTINE_FOLLOWING_MARK in row.note and finding_id not in self._quarantine_follow:
                row.note = QUARANTINE_LATE_UNKNOWN
            return True, ""

    # --- Audits ----------------------------------------------------------------

    # Lynis läuft entkoppelt auf dem Server (`audit_job.py`), weil es weit über eine halbe Stunde brauchen kann; das
    # Dashboard fragt alle `AUDIT_POLL_INTERVAL_S` Sekunden nach. Der Stand steht sofort in Speicher und Datenbank (kein
    # zweiter Lauf per Klick, `resume_audits` setzt nach einem Neustart fort).

    def audit_run(self, host_id: str) -> dict[str, Any] | None:
        """Das laufende (oder wartende) Audit eines Servers für die Seite, sonst None."""
        state = self._audits.get(host_id)
        return audit_run_out(state) if state is not None else None

    def _claim_audits(self, hosts: list[Host], trigger: str) -> tuple[list[tuple[Host, dict[str, Any]]], list[tuple[Host, dict[str, Any]]]]:
        """Belegt die Server sofort, ohne await dazwischen: So starten zwei gleichzeitige Anforderungen (zwei Klicks,
        Klick und Zeitplan) nie zwei Läufe. Rückgabe: neu angeforderte und schon laufende, je mit ihrem Stand."""
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
                try:  # sonst startete der nächste Start des Dashboards ein Audit, das hier als gescheitert galt
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
        """Fordert je Server ein Audit an und kehrt sofort zurück. Läuft schon eines, startet kein zweites: Es steht
        unter `running` (und in `audits` mit `already_running`)."""
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
        """Wie `start_audits`, wartet aber, bis die neu angeforderten Audits fertig sind (Zeitplan); sie laufen auch
        zu Ende, wenn das Warten abgebrochen wird."""
        _fresh, _already, batch = await self._begin_audits(hosts, trigger)
        if batch is None:
            return []
        return await asyncio.shield(batch)

    def cancel_audit_tasks(self) -> None:
        """Beim Beenden der Erweiterung: nicht weiter nachfragen. Die Läufe auf den Servern gehen weiter; ihr Stand bleibt
        in der Datenbank, und der nächste Start nimmt sie wieder auf (`load_running_audits`, `resume_audits`)."""
        for task in list(self._audit_tasks):
            task.cancel()

    async def load_running_audits(self) -> int:
        """Beim Start, vor der ersten Anfrage: noch laufende oder wartende Audits wieder eintragen, sonst startete ein
        Klick einen zweiten Lauf. Abgefragt werden sie von `resume_audits`."""
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
        """Hintergrund-Aufgabe nach dem Start: die von `load_running_audits` geholten Audits weiter abfragen (ein
        wartendes startet jetzt) und wie sonst abschließen, mit Meldung."""
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
                if host is None:  # Server inzwischen gelöscht: nichts mehr abzufragen
                    await self._drop_audit_state(host_id, state["run_id"])
                    self._release_audit(host_id, state)
                    continue
                items.append((host, state))
        except BaseException:
            # Nicht dauerhaft als "läuft" stehen lassen; der Stand in der Datenbank bleibt für den nächsten Start.
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
                        state["waiting"] = True  # nur für die Seite ("wartet seit ...")
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
            except Exception:  # noqa: BLE001 - der Server darf nicht dauerhaft als "läuft" stehen bleiben
                _log.exception("shield_audit_record_failed host=%s run_id=%s", host.id, run_id)
            return result
        finally:
            self._release_audit(host.id, state)

    async def _launch_audit(self, host: Host, run_id: str, started_ts: float) -> av.LynisResult:
        launch = aj.launch_as_root(run_id, limit_s=AUDIT_LIMIT_S)
        unknown = aj.NOT_STARTED_MESSAGE
        reach_grace_s = None
        try:
            res = await self._ctx.exec.run(host, launch, timeout_s=AUDIT_LAUNCH_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            # Der Start kann trotzdem geklappt haben (Verbindung erst danach weg): nachsehen. Meldet sich der Server
            # aber gar nicht mehr, nicht drei Stunden lang "läuft" zeigen.
            unknown = f"Das Audit konnte nicht gestartet werden: {_describe(exc)}"
            reach_grace_s = AUDIT_REACH_GRACE_S
        else:
            out = (res.stdout or "") + (res.stderr or "")
            if av.NO_ROOT in out:
                return av.LynisResult(status="error", error=av.no_root_message(out))
            info = dt.parse_launch(out)
            if not info.started and info.method is None:  # schon der Ordner scheiterte: nichts gestartet
                lines = [ln for ln in out.strip().splitlines() if not ln.startswith("@@")]
                reason = lines[-1][:200] if lines else "keine Rückmeldung"
                return av.LynisResult(status="error", error=f"Das Audit konnte auf dem Server nicht gestartet werden: {reason}")
        return await self._wait_audit(host, run_id, started_ts, unknown_grace_s=AUDIT_UNKNOWN_GRACE_S,
                                      unknown_summary=unknown, reach_grace_s=reach_grace_s)

    async def _poll_audit(self, host: Host, run_id: str) -> dt.Poll | av.LynisResult | None:
        """Eine Abfrage: der Stand (`dt.Poll`), ein den Lauf beendender Fehler (keine root-Rechte) oder None ohne Antwort."""
        try:
            res = await self._ctx.exec.run(host, aj.poll_as_root(run_id), timeout_s=AUDIT_POLL_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - Verbindung weg: weiter nachfragen
            return None
        out = (res.stdout or "") + (res.stderr or "")
        if av.NO_ROOT in out:
            return av.LynisResult(status="error", error=av.no_root_message(out))
        poll = dt.parse_poll(out)
        return None if poll.state == "noreply" else poll

    async def _wait_audit(self, host: Host, run_id: str, started_ts: float, *, unknown_grace_s: float,
                          unknown_summary: str, reach_grace_s: float | None = None) -> av.LynisResult:
        """Fragt nach, bis der Lauf fertig ist; Verbindungsfehler werden ausgesessen. Ist die Obergrenze samt Frist erreicht
        (ab `started_ts`, auch über einen Neustart hinweg), wird der Lauf auf dem Server beendet (`_stop_audit`)."""
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
        """Beendet einen Lauf über der Obergrenze auf dem Server (Lynis samt Unterprozessen, `audit_job.stop_command`)."""
        try:
            res = await self._ctx.exec.run(host, aj.stop_as_root(run_id), timeout_s=AUDIT_STOP_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            return av.LynisResult(status="error", error=aj.unreachable_at_limit_message(_describe(exc), AUDIT_LIMIT_S))
        out = (res.stdout or "") + (res.stderr or "")
        if av.NO_ROOT in out:
            return av.LynisResult(status="error", error=av.no_root_message(out))
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
        """Speichert das Ergebnis (ID = Lauf-ID, ein zweites Speichern ändert nichts) und entfernt den Stand "läuft"
        in derselben Transaktion."""
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

    # --- Status / Übersicht --------------------------------------------------------

    def forget_status(self, host_id: str) -> None:
        """Verwirft den gemerkten Stand eines Servers (etwa nach Installation), statt ihn bis zu 10 Minuten zu zeigen."""
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

    def forget_readiness(self, host_id: str) -> None:
        """Verwirft die gemerkte Eignung für Echtzeit, etwa nachdem der Wächter umgestellt wurde."""
        self._readiness_cache.pop(host_id, None)

    async def realtime_readiness(self, host: Host, *, refresh: bool = False) -> dict[str, Any]:
        """Ob der Viren-Wächter auf dem Server in Echtzeit laufen könnte; liest nur, zehn Minuten gemerkt."""
        cached = self._readiness_cache.get(host.id)
        if cached and not refresh and cached[0] > _now().timestamp() - 600:
            return cached[1]
        try:
            res = await self._ctx.exec.run(host, rt.READINESS_COMMAND, timeout_s=STATUS_TIMEOUT)
            data = {"reachable": True, **rt.readiness_dict(rt.parse_readiness(res.stdout or ""))}
        except Exception as exc:  # noqa: BLE001
            data = {"reachable": False, "possible": False, "error": _describe(exc, timeout_s=STATUS_TIMEOUT),
                    "reasons": ["Server nicht erreichbar."], "notes": []}
        self._readiness_cache[host.id] = (_now().timestamp(), data)
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
            # Für den Schutzwert zählt der letzte fertige Scan: Ein gerade laufender (oder abgebrochener) soll den Wert
            # nicht senken, solange der vorige noch frisch ist (eigene Abfrage, damit viele neue Läufe ihn nicht verdrängen).
            last_done_at: dict[str, datetime] = {}
            for host_id, started_at in (await session.execute(
                select(ScanRecord.host_id, func.max(ScanRecord.started_at)).where(
                    ScanRecord.kind.in_(("quick", "deep", "custom")), ScanRecord.status.in_(("clean", "infected")),
                ).group_by(ScanRecord.host_id)
            )).all():
                last_done_at[host_id] = started_at
            last_audits: dict[str, AuditRecord] = {}
            last_ok_audits: dict[str, AuditRecord] = {}
            for row in (await session.execute(select(AuditRecord).order_by(AuditRecord.created_at.desc()).limit(500))).scalars():
                last_audits.setdefault(row.host_id, row)
                if row.status == "ok" and row.hardening_index is not None:
                    last_ok_audits.setdefault(row.host_id, row)
            counts = dict((await session.execute(
                select(FindingRecord.status, func.count()).group_by(FindingRecord.status)
            )).all())
            # Je Server dieselbe Quelle wie die Gesamtzahl oben (die Funde und ihr Zustand, nicht nur das Ergebnis des
            # letzten Scans), sonst widersprächen sich Kopf und Zeile nach Quarantäne oder Löschen, und ein offener Fund
            # des Wächters fehlte in der Zeile.
            open_by_host = dict((await session.execute(
                select(FindingRecord.host_id, func.count()).where(FindingRecord.status == "detected").group_by(FindingRecord.host_id)
            )).all())
            # Was aus den Funden des letzten befallenen Scans wurde. Ein späterer Lauf (etwa der Wächter) hängt einen
            # Fund, den er wieder findet, an sich selbst (`scan_id`): Deshalb zählen die Funde aller Läufe des Servers
            # ab dem Start dieses Scans, nicht nur die mit seiner Kennung.
            infected_last = {s.host_id: s for s in last_scans.values() if s.status == "infected"}
            scan_findings: dict[str, dict[str, int]] = {}
            if infected_last:
                earliest = min(s.started_at for s in infected_last.values())
                for host_id, finding_status, started_at in (await session.execute(
                    select(FindingRecord.host_id, FindingRecord.status, ScanRecord.started_at)
                    .join(ScanRecord, ScanRecord.id == FindingRecord.scan_id)
                    .where(FindingRecord.host_id.in_(list(infected_last)), ScanRecord.started_at >= earliest)
                )).all():
                    if started_at >= infected_last[host_id].started_at:
                        counted = scan_findings.setdefault(host_id, {})
                        counted[finding_status] = counted.get(finding_status, 0) + 1
            since = _now() - timedelta(days=30)
            recent = (await session.execute(
                select(func.count()).select_from(FindingRecord).where(FindingRecord.detected_at >= since)
            )).scalar_one()
            host_ids = [h.id for h in hosts]
            critical_events = (await session.execute(
                select(func.count()).select_from(SecurityEventRecord).where(
                    SecurityEventRecord.acknowledged.is_(False), SecurityEventRecord.severity == "critical",
                    SecurityEventRecord.host_id.in_(host_ids),
                )
            )).scalar_one() if host_ids else 0

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
                "last_completed_scan_at": _ts(last_done_at.get(host.id)),
                "open_findings": int(open_by_host.get(host.id, 0)),
                "last_scan_outcome": scan_outcome(scan_findings.get(host.id)) if scan and scan.status == "infected" else None,
                "last_audit": {"status": audit.status, "hardening_index": audit.hardening_index, "warnings": len(audit.warnings),
                               "created_at": _ts(audit.created_at), "error": audit.error} if audit else None,
                # Letztes erfolgreiches Audit: bleibt sichtbar, wenn das neueste gescheitert ist.
                "last_ok_audit": {"status": ok_audit.status, "hardening_index": ok_audit.hardening_index, "warnings": len(ok_audit.warnings),
                                  "created_at": _ts(ok_audit.created_at), "error": None} if ok_audit else None,
                "scanning": self._big_scan_running(host.id),
                "auditing": host.id in self._audits,
                # Laufendes (oder wartendes) Audit: seit wann, damit die Seite "läuft seit ..." zeigen kann.
                "audit_run": self.audit_run(host.id),
            })

        open_threats = int(counts.get("detected", 0))
        protected = sum(1 for r in rows if r.get("clamav_installed"))
        indices = [r["last_ok_audit"]["hardening_index"] for r in rows if r.get("last_ok_audit")]
        settings = await self._settings()
        updates_state = await load_baselines(self._ctx, "updates")
        # Der Schutzwert zählt auch Updates, Einbruchschutz und SSH-Einstellungen (protection.posture_penalty).
        penalty, reasons = protection.posture_penalty(
            [r["host_id"] for r in rows], updates_state, await load_baselines(self._ctx, "guard"), int(critical_events),
        )
        held_reboots: set[str] = set()
        if settings.get("auto_updates_enabled", False) and settings.get("auto_reboot", False):
            for h in auto_update_hosts(hosts, settings):
                st = updates_state.get(h.id) or {}
                # Nur ein gescheiterter Lauf hält den Neustart auf; ein laufender ist noch kein Grund zur Sorge.
                if (st.get("reboot_required") and not st.get("reboot_pending_since") and not no_auto_reboot(h, settings)
                        and await last_run_status(self._ctx, h.id) == "error"):
                    held_reboots.add(h.id)
        reasons = protection.signature_reasons(rows) + _scan_reasons(rows) + reasons
        return {
            "hosts": rows,
            "summary": {
                # "hosts" sind nur die prüfbaren Server (Linux, verwaltet, mit SSH-Zugang, ggf. passende Markierung);
                # "hosts_known" alle eingerichteten: Die Seite unterscheidet "keiner angelegt" von "keiner prüfbar".
                "hosts": len(rows), "hosts_known": hosts_known, "protected": protected, "open_threats": open_threats,
                "quarantined": int(counts.get("quarantined", 0)),
                "neutralized_total": int(counts.get("quarantined", 0)) + int(counts.get("deleted", 0)),
                "findings_30d": int(recent),
                "avg_hardening": round(sum(indices) / len(indices)) if indices else None,
                "stale_signatures": sum(1 for r in rows if r.get("clamav_installed") and r.get("signature_stale") is True),
                "score": max(0, _score(rows, open_threats, indices) - penalty),
                # Was den Wert drückt, als kurze Sätze mit Punkten (offene Funde, Signaturen, Updates, Einbruchschutz, SSH).
                "score_reasons": ([f"{'1 offene Bedrohung' if open_threats == 1 else f'{open_threats} offene Bedrohungen'} (−{min(40, open_threats * 20)})"] if open_threats else [])
                + reasons,
            },
            "attention": protection.attention_items(rows, frozenset(held_reboots)),
            "config": {
                "auto_quarantine": settings.get("auto_quarantine", True),
                "realtime_enabled": settings.get("realtime_enabled", True),
                "watch_interval_min": watch_interval_min(settings),
                "quick_scan_cron": settings.get("quick_scan_cron") or DEFAULT_CRONS["quick"],
                "deep_scan_cron": settings.get("deep_scan_cron") or DEFAULT_CRONS["deep"],
                "audit_cron": settings.get("audit_cron") or DEFAULT_CRONS["audit"],
            },
        }

    async def send_briefing(self, updates: dict[str, Any] | None = None, guard: tuple[str | None, list[str]] | None = None) -> dict[str, Any]:
        overview = await self.overview(refresh=True)
        waiting = await self._waiting_approvals()
        crashes = await self._container_crashes()
        title, body, level = build_briefing(overview, await self._ctx.hosts.list(), updates, guard, waiting=waiting, crashes=crashes)
        sev = {"critical": Severity.CRITICAL, "warning": Severity.WARNING}.get(level, Severity.INFO)
        result = await self._ctx.notify.send(Notification(
            title=title, body=body, severity=sev,
            payload={"path": SOC_PATH, "tags": ["coffee"], "actions": briefing_buttons(waiting, crashes)},
        ))
        return {"title": title, "body": body, "level": level, "push": push_state(result)}

    async def _waiting_approvals(self) -> int | None:
        """Wie viele Aktionen (aller Module) auf eine Freigabe warten; `None`, wenn das nicht lesbar ist."""
        try:
            return int(await self._ctx.actions.waiting_count())
        except Exception:  # noqa: BLE001 - der Lagebericht kommt trotzdem, nur ohne diese Zeile
            _log.warning("shield_briefing_waiting_count_failed", exc_info=True)
            return None

    async def _container_crashes(self) -> list[dict[str, Any]]:
        """Abstürze der Container-Wache der letzten `BRIEFING_CRASH_WINDOW_H` Stunden je Container (Server + Name): Anzahl
        samt Wiederholungen und ob einer noch unbearbeitet ist (wie `IncidentHistory.merge_into_active`). Offene zuerst,
        dann die meisten; ohne lesbare Datenbank kommt der Bericht ohne diese Zeile."""
        since = _now() - timedelta(hours=BRIEFING_CRASH_WINDOW_H)
        try:
            async with self._ctx.db.session() as session:
                rows = (await session.execute(
                    select(IncidentRecord).where(IncidentRecord.is_crash.is_(True), IncidentRecord.created_at >= since)
                    .order_by(IncidentRecord.created_at.desc()).limit(500)
                )).scalars().all()
        except Exception:  # noqa: BLE001 - der Lagebericht kommt trotzdem, nur ohne diese Zeile
            _log.warning("shield_briefing_crashes_failed", exc_info=True)
            return []
        by_target: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            entry = by_target.setdefault((r.host_name, r.target), {"host": r.host_name, "target": r.target, "count": 0, "open": False})
            entry["count"] += occurrences_of(r.details if isinstance(r.details, dict) else None)
            unhandled = r.status in ("open", "proposed") or (r.status == "reviewed" and r.status_changed_at is None)
            entry["open"] = entry["open"] or unhandled
        return sorted(by_target.values(), key=lambda e: (not e["open"], -e["count"], e["host"], e["target"]))

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
    """Was mit der Meldung auf dem Weg aufs Handy passiert ist: sent, suppressed, skipped, not_delivered oder unknown."""
    delivered = getattr(result, "delivered", None)
    if result is None or delivered is None:
        return "unknown"  # ältere Kerne liefern kein Ergebnis
    if getattr(result, "suppressed", False):
        return "suppressed"  # Wartungsfenster; die Meldung steht trotzdem unter "Meldungen"
    if delivered:
        return "sent"
    # skipped: Mindest-Stufe des Kanals nicht erreicht. not_delivered: keiner eingerichtet oder einer ausgefallen,
    # und ein Ausfall geht vor, auch wenn ein anderer Kanal nur übersprungen hat.
    return "skipped" if getattr(result, "skipped", None) is True else "not_delivered"


BRIEFING_CRASH_WINDOW_H = 24
BRIEFING_MAX_BUTTONS = 3


def briefing_buttons(waiting: int | None, crashes: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Knöpfe der Meldung (höchstens `BRIEFING_MAX_BUTTONS`): erst, was wartet (Freigaben, Abstürze), dann feste Reiter."""
    urgent: list[dict[str, str]] = []
    if waiting:
        urgent.append({"label": "Freigaben", "path": "/actions"})
    if crashes:
        urgent.append({"label": "Container-Wache", "path": f"{SOC_PATH}?tab=containers"})
    fixed = [{"label": "Updates", "path": f"{SOC_PATH}?tab=updates"}, {"label": "Einbruchschutz", "path": f"{SOC_PATH}?tab=guard"}]
    return (urgent + fixed)[:BRIEFING_MAX_BUTTONS]


def build_briefing(
    overview: dict[str, Any], hosts_all: list[Any], updates: dict[str, Any] | None = None,
    guard: tuple[str | None, list[str]] | None = None, *, waiting: int | None = None,
    crashes: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """Titel, Text und Stufe des Lageberichts; ein Blick aufs Handy soll genügen.

    Der Titel nennt keine Tageszeit, denn der Bericht kommt auch auf Knopfdruck um 21:45 Uhr."""
    sm = overview["summary"]
    down = [h.display_name or h.name for h in hosts_all if getattr(h.status, "value", h.status) == "down"]
    if not hosts_all:
        lines = ["Server: noch keiner eingerichtet – füge unter Einstellungen → Server & Zugänge den ersten hinzu."]
    else:
        lines = [f"Server: {len(hosts_all) - len(down)}/{len(hosts_all)} erreichbar" + (f" – aus/weg: {', '.join(down[:6])}" if down else "")]
    if sm["hosts"] == 0:
        # Ohne prüfbaren Server gibt es nichts zu bewerten, kein "Schutzwert 0/100". Offene Funde aus älteren Scans
        # bleiben aber offen (auch wenn der Server fehlt) und gehören in den Bericht.
        lines.append("Virenschutz: noch nichts zu prüfen – geprüft werden Linux-Server mit SSH-Zugang (und, falls eingestellt, der passenden Markierung).")
        if sm["open_threats"]:
            lines.append(f"Bedrohungen: {sm['open_threats']} offen, {sm['quarantined']} in Quarantäne, {sm['findings_30d']} Funde in 30 Tagen")
    else:
        # Der Schutzwert zählt mehr als den Virenschutz (Updates, SSH, Ereignisse), deshalb nicht "Virenschutz: …".
        lines.append(f"Schutzwert {sm['score']}/100 · Virenschutz: {sm['protected']}/{sm['hosts']} Server mit ClamAV")
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
    # Vergessene Freigaben und Sorgenkinder der Container-Wache sollen auffallen.
    if waiting:
        lines.append(
            f"Freigaben: {waiting} Vorschlag wartet" if waiting == 1 else f"Freigaben: {waiting} Vorschläge warten"
        )
        lines[-1] += " auf deine Bestätigung (unter „Aktionen“)"
    if crashes:
        parts = [f"{c['target']} auf {c['host']} {c['count']}×" + (" (noch offen)" if c["open"] else "") for c in crashes[:4]]
        more = f" und {len(crashes) - 4} weitere" if len(crashes) > 4 else ""
        lines.append(f"Container-Abstürze ({BRIEFING_CRASH_WINDOW_H} h): " + ", ".join(parts) + more)
    attention = bool(waiting) or any(c["open"] for c in crashes or [])
    for h in overview["hosts"]:
        scan = h.get("last_scan") or {}
        # Alte Signaturen und ausgeschaltetes Update sind fast immer dasselbe Problem: ein Eintrag je Server, sonst
        # verdrängen sie bei mehreren Servern die übrigen Punkte (die Liste ist auf sechs begrenzt).
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
    level = "critical" if sm["open_threats"] else ("warning" if (down or problems or attention) else "info")
    if level == "critical":
        title = "Lagebericht – Bedrohung offen!"
    elif level == "warning":
        title = "Lagebericht – es gibt etwas zu tun"
    elif not hosts_all:
        title = "Lagebericht – noch kein Server eingerichtet"
    elif sm["hosts"] == 0:
        # Server sind da, aber keiner ist prüfbar (z. B. ohne SSH-Zugang): "alles im grünen Bereich" wäre gelogen.
        title = "Lagebericht – noch nichts geprüft"
    else:
        title = "Lagebericht – alles im grünen Bereich"
    return title, "\n".join(lines), level


def _completed_scan_at(row: dict[str, Any]) -> float | None:
    """Beginn des letzten fertigen Scans (sauber oder mit Fund); ein laufender zählt nicht, sonst fiele der Schutzwert
    währenddessen und spränge danach zurück."""
    if "last_completed_scan_at" in row:
        return row["last_completed_scan_at"]
    scan = row.get("last_scan") or {}
    return scan.get("started_at") if scan.get("status") in ("clean", "infected") else None


SCAN_FRESH_S = 8 * 86400
"""So lange zählt ein fertiger Scan als aktuell."""
SCAN_FRESH_POINTS = 25


def _scan_fresh(row: dict[str, Any], now: float) -> bool:
    return (_completed_scan_at(row) or 0) > now - SCAN_FRESH_S


def _scan_reasons(rows: list[dict[str, Any]]) -> list[str]:
    """Sätze für „Was den Schutzwert senkt“ zum Scannen: Server ohne fertigen Scan in 8 Tagen (mit Abzug) und Server mit
    gescheitertem letztem Scan (ohne Abzug, es zählt der vorige; ein dauerhaft scheiternder Scan soll aber auffallen)."""
    if not rows:
        return []
    now = _now().timestamp()
    out: list[str] = []
    stale = [r for r in rows if not _scan_fresh(r, now)]
    if stale:
        points = round(SCAN_FRESH_POINTS * len(stale) / len(rows))
        names = ", ".join(r["host_name"] for r in stale[:3]) + (f" und {len(stale) - 3} weitere" if len(stale) > 3 else "")
        out.append(f"Kein fertiger Scan in den letzten 8 Tagen: {names} (−{points})")
    failed = [r for r in rows if (r.get("last_scan") or {}).get("status") == "error"]
    for r in failed[:3]:
        out.append(f"Letzter Scan auf {r['host_name']} fehlgeschlagen")
    return out


def _score(rows: list[dict[str, Any]], open_threats: int, indices: list[int]) -> int:
    """Grobe Schutz-Note 0-100 aus Abdeckung, aktuellen Scans, offenen Funden, Härtungsindex und veralteten Signaturen."""
    if not rows:
        return 0
    now = _now().timestamp()
    covered = sum(1 for r in rows if r.get("clamav_installed")) / len(rows)
    fresh = sum(1 for r in rows if _scan_fresh(r, now)) / len(rows)
    hardening = (sum(indices) / len(indices) / 100) if indices else 0.5
    score = 40 * covered + SCAN_FRESH_POINTS * fresh + 25 * hardening + 10
    score -= min(40, open_threats * 20)
    score -= protection.signature_penalty(rows)
    return max(0, min(100, round(score)))


def _lost_in_flight(exc: BaseException) -> bool:
    """Riss die Verbindung ab oder blieb die Antwort aus, nachdem der Befehl losgeschickt war?"""
    if getattr(exc, "readable", False):
        return False  # Fehler beim Verbindungsaufbau aus dem Kern: Der Befehl ging nie hinaus.
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    return any(cls.__module__.split(".")[0] == "asyncssh" for cls in type(exc).__mro__)


def _describe(exc: BaseException, *, timeout_s: float | None = None) -> str:
    """Lesbarer Grund zu einer Ausnahme aus `ctx.exec.run` (und dem Weg dorthin); bei Zeitlimit mit Dauer `timeout_s`."""
    text = str(exc).strip()
    # SSH-Fehler (`core.ssh.SshError`) tragen `readable` und schon einen deutschen Satz. Sie gehen dem `TimeoutError`
    # vor: `SshTimeout` ist auch einer, und "Server nicht erreichbar" soll nicht wie "Lauf dauerte zu lange" aussehen.
    if getattr(exc, "readable", False) and text:
        return text
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        if timeout_s:
            return f"Zeitüberschreitung: Der Vorgang dauerte länger als {av.duration_text(timeout_s)} und wurde abgebrochen."
        return "Zeitüberschreitung – der Vorgang hat zu lange gebraucht."
    # Bricht die Verbindung mitten im Lauf ab, kommt der Fehler der SSH-Bibliothek ohne deutschen Text an (`ConnectionLost`
    # u. ä.); die Erweiterung kennt die Bibliothek nicht und erkennt ihn am Modul der Klasse.
    if isinstance(exc, ConnectionError) or any(cls.__module__.split(".")[0] == "asyncssh" for cls in type(exc).__mro__):
        return f"Die Verbindung zum Server ist abgebrochen ({text})." if text else "Die Verbindung zum Server ist abgebrochen."
    return text or type(exc).__name__
