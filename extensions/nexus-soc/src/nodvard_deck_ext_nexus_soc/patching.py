"""Update-Zentrale: Update-Stand je Server pruefen und speichern, Updates einspielen
(manuell ueber das Gate oder automatisch nach Zeitplan) und darueber berichten.

Automatisches Einspielen laeuft -- wie die automatische Quarantaene -- ohne
Freigabe-Schritt, aber nur wenn es in den Einstellungen eingeschaltet ist (Standard:
aus), optional nur auf markierten Servern, und jeder Lauf steht im Kern-Audit-Log
und in der Lauf-Liste.

Einspielen (nicht Neustart) laeuft auf dem Server entkoppelt vom SSH-Kanal
(`detached.py`): als systemd-Unit `lattice-upgrade-<lauf>` bzw. per
`setsid`, Protokoll und Rueckgabecode unter `/var/lib/nexus-updates/`. Das Dashboard
fragt nur kurz nach. Reisst die Verbindung ab oder startet das Dashboard neu (auf dem
Raspberry Pi beendet ein Docker-Update den Nodvard-Deck-Container), laeuft apt/dpkg zu Ende;
nach dem Neustart nimmt `resume_interrupted` die Abfrage wieder auf.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from nodvard_sdk import Notification, Severity
from sqlalchemy import select, update

from . import detached as dt
from . import updates as up
from .antivirus import NO_ROOT, NO_ROOT_MESSAGE, as_root
from .hostscope import host_scope
from .models import BaselineRecord, UpdateRunRecord

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext, Host

    from .defender import Defender

SOC_PATH = "/ext/nexus-soc/soc"

CHECK_TIMEOUT = 4 * 60
# So lange wartet das Dashboard hoechstens auf einen entkoppelten Lauf. Der
# Lauf selbst wird nie beendet -- er kann auf dem Server weiterlaufen.
UPGRADE_TIMEOUT = 45 * 60
LAUNCH_TIMEOUT_S = 90  # Start: Ordner anlegen, Lauf abkoppeln, bis 5 s auf die pid warten
POLL_INTERVAL_S = 10
# Nach Ablauf der Wartezeit schaut das Dashboard noch gedrosselt nach: ein
# Lauf, der spaeter fertig wird, soll nicht als Fehler stehen bleiben.
LATE_POLL_INTERVAL_S = 60
LATE_FOLLOW_S = 2 * 3600 + 15 * 60  # zusammen mit der Wartezeit rund 3 Stunden
POLL_TIMEOUT_S = 30
UNKNOWN_GRACE_S = 60  # so lange darf ein gerade gestarteter Lauf noch "unbekannt" sein
# Ist schon der Start gescheitert, muss sich der Server innerhalb dieser Zeit wenigstens
# einmal melden -- sonst ist er aus oder nicht erreichbar.
REACH_GRACE_S = 3 * 60
START_FAILED_PREFIX = "Start fehlgeschlagen:"  # steht schon waehrend des Wartens an der Lauf-Zeile (siehe `_resume`)
RESUME_DELAY_S = 5  # nach dem Start kurz warten, bis Netz und SSH wieder da sind
RESUME_MIN_WAIT_S = 5 * 60  # nach einem Neustart mindestens so lange nachfragen
BUSY_MESSAGE = "Auf diesem Server läuft schon ein Update-Lauf."
LOST_MESSAGE = ("Der Update-Lauf wurde auf dem Server abgebrochen (Neustart oder Absturz?). "
                "Bitte dort „sudo dpkg --configure -a“ ausführen und neu prüfen.")
MAX_PARALLEL = 4
# Der Zeitplaner ueberspringt einen Lauf, wenn das Dashboard zu dieser Zeit nicht lief (Neustart,
# Update). Ein Server gilt als "Pruefung ausgeblieben", wenn sein letzter Versuch vor dem zuletzt
# faelligen Zeitpunkt des Zeitplans liegt (`last_due`).
CATCH_UP_DELAY_S = 2 * 60  # nach dem Start kurz warten, bis Netz und SSH da sind
# Der Hinweis auf der Seite erscheint erst so lange nach dem Zeitpunkt: die Tagespruefung selbst
# braucht fuer mehrere Server einige Minuten, in der ein Server noch auf seine Reihe wartet.
OVERDUE_HINT_GRACE_S = 15 * 60
DEFAULT_ZONE = "Europe/Berlin"  # wie im Kern, wenn weder eine Einstellung noch die Umgebung eine Zone nennt
# Nach "Neu starten" (shutdown -r +1) den neuen Stand selbst nachholen, statt bis zur
# Tagespruefung "Neustart noetig" stehen zu lassen: nach 4 Minuten, bei
# Bedarf noch zweimal.
RECHECK_AFTER_REBOOT_S = 4 * 60
RECHECK_TRIES = 3
# So lange gilt ein ausgeloester Neustart als "unterwegs", wenn der Server laut
# Laufzeit noch gar nicht neu gestartet hat.
REBOOT_PENDING_MAX_S = 30 * 60


def _cron_values(field: str, low: int, high: int) -> set[int] | None:
    """Werte eines Cron-Felds (Minute oder Stunde): `*`, Zahlen, Bereiche, Listen, Schritte
    (`*/6`, `10-20/5`). Ungueltig: None."""
    values: set[int] = set()
    for part in field.split(","):
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            if not (step_text.isascii() and step_text.isdigit()) or int(step_text) < 1:
                return None
            step = int(step_text)
        if base == "*":
            first, last = low, high
        elif "-" in base:
            a, _, b = base.partition("-")
            if not (a.isascii() and a.isdigit() and b.isascii() and b.isdigit()):
                return None
            first, last = int(a), int(b)
        elif base.isascii() and base.isdigit():
            first = int(base)
            last = high if slash else first
        else:
            return None
        if not low <= first <= last <= high:
            return None
        values.update(range(first, last + 1, step))
    return values


def last_due(cron: str, now: float, zone: tzinfo) -> float | None:
    """Der zuletzt faellige Zeitpunkt (Unix-Zeit, nicht nach `now`) eines Zeitplans, der mindestens
    taeglich laeuft (Tag, Monat und Wochentag `*`; nur Minute und Stunde zaehlen), in der Zone der
    Zeitplaene. Anderes oder Ungueltiges: None."""
    fields = cron.split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return None
    minutes, hours = _cron_values(fields[0], 0, 59), _cron_values(fields[1], 0, 23)
    if not minutes or not hours:
        return None
    today = datetime.fromtimestamp(now, zone)
    for back in range(3):
        day = today - timedelta(days=back)
        due = [t for h in hours for m in minutes
               if (t := datetime(day.year, day.month, day.day, h, m, tzinfo=zone).timestamp()) <= now]
        if due:
            return max(due)
    return None


def check_overdue(state: dict[str, Any] | None, due: float | None) -> bool:
    """Liegt der letzte Pruefversuch (auch ein fehlgeschlagener zaehlt) vor dem zuletzt faelligen
    Zeitpunkt (`last_due`)? Ohne gespeicherten Stand oder ohne Zeitpunkt: nein (noch nie geprueft
    ist etwas anderes)."""
    if not state or due is None:
        return False
    stamps = [t for t in (state.get("attempted_at"), state.get("checked_at")) if isinstance(t, int | float)]
    return bool(stamps) and max(stamps) < due


def overdue_applies(settings: dict[str, Any]) -> bool:
    """Gilt `check_overdue` ueberhaupt? Nur wenn die Tagespruefung an ist und mindestens taeglich
    laeuft (Tag, Monat und Wochentag im Zeitplan "*"). Bei einem woechentlichen Zeitplan oder
    ausgeschalteter Pruefung ist ein alter Stand gewollt: kein Hinweis, kein Nachholen."""
    if not settings.get("updates_check_enabled", True):
        return False
    fields = str(settings.get("updates_check_cron") or DEFAULT_UPDATE_CRONS["check"]).split()
    return len(fields) == 5 and fields[2:] == ["*", "*", "*"]


def _zone(name: object) -> ZoneInfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except Exception:  # noqa: BLE001 - unbekannter Name: naechste Quelle
        return None


async def schedule_zone(ctx: ExtensionContext) -> tzinfo:
    """Die Zone, in der die Zeitplaene laufen: die Kern-Einstellung `system.timezone`, sonst wie im
    Kern `NODVARD_DECK_TIMEZONE`, `TZ`, zuletzt `Europe/Berlin`."""
    try:
        stored = await ctx.settings.core("system.timezone")
    except Exception:  # noqa: BLE001 - aeltere Kerne kennen den Zugriff nicht
        stored = None
    if isinstance(stored, dict):
        stored = stored.get("value")  # der Kern speichert jede Einstellung als {"value": ...}
    env = os.environ
    for name in (stored, env.get("NODVARD_DECK_TIMEZONE"), env.get("LATTICE_TIMEZONE"), env.get("TZ"), DEFAULT_ZONE):
        zone = _zone(name)
        if zone is not None:
            return zone
    return UTC


def run_out(r: UpdateRunRecord) -> dict[str, Any]:
    from .defender import _ts

    return {
        "id": r.id, "host_id": r.host_id, "host_name": r.host_name, "mode": r.mode, "mode_label": up.MODE_LABEL.get(r.mode, r.mode),
        "trigger": r.trigger, "status": r.status, "upgraded": r.upgraded, "summary": r.summary, "output_tail": r.output_tail,
        "started_at": _ts(r.started_at), "finished_at": _ts(r.finished_at),
    }


async def save_baseline(ctx: ExtensionContext, host_id: str, kind: str, data: dict[str, Any]) -> None:
    from .defender import _now

    async with ctx.db.session() as session:
        row = await session.get(BaselineRecord, f"{host_id}:{kind}")
        if row is None:
            session.add(BaselineRecord(id=f"{host_id}:{kind}", host_id=host_id, kind=kind, data=data, updated_at=_now()))
        else:
            row.data = data
            row.updated_at = _now()


async def load_baselines(ctx: ExtensionContext, kind: str) -> dict[str, dict[str, Any]]:
    async with ctx.db.session() as session:
        rows = (await session.execute(select(BaselineRecord).where(BaselineRecord.kind == kind))).scalars()
        return {r.host_id: dict(r.data or {}) for r in rows}


async def load_baseline(ctx: ExtensionContext, host_id: str, kind: str) -> dict[str, Any] | None:
    async with ctx.db.session() as session:
        row = await session.get(BaselineRecord, f"{host_id}:{kind}")
        return dict(row.data or {}) if row else None


class UpdateCenter:
    def __init__(self, ctx: ExtensionContext, defender: Defender) -> None:
        self._ctx = ctx
        self._defender = defender
        self._checking: set[str] = set()
        self._busy: dict[str, str] = {}  # host_id -> mode, solange ein Einspiel-Lauf laeuft
        self._rechecks: set[asyncio.Task[None]] = set()  # Nachpruefungen nach einem Neustart
        self._late: set[asyncio.Task[None]] = set()  # Nachfragen nach Ablauf der Wartezeit
        self._catch_up: asyncio.Task[None] | None = None
        # Laeuft gerade die Tagespruefung? Sie nimmt sich alle Server vor, auch die, die noch auf ihre
        # Reihe warten (`MAX_PARALLEL`) und deshalb nicht in `_checking` stehen.
        self._daily_running = 0

    async def abort_interrupted_runs(self) -> int:
        """Beim Start: Einspiel-Laeufe, die beim letzten Beenden noch liefen, als
        abgebrochen markieren (wie `Defender.abort_interrupted_scans`).
        Ausgenommen sind Laeufe, die entkoppelt auf dem Server laufen (`remote_id`)
        -- die nimmt `resume_interrupted` wieder auf."""
        from .defender import ABORTED_BY_RESTART, _now

        async with self._ctx.db.session() as session:
            result = await session.execute(
                update(UpdateRunRecord)
                .where(UpdateRunRecord.status == "running", UpdateRunRecord.remote_id.is_(None))
                .values(status="error", summary=ABORTED_BY_RESTART, finished_at=_now())
            )
            return result.rowcount or 0

    async def resume_interrupted(self) -> int:
        """Beim Start (als Hintergrund-Aufgabe): entkoppelte Laeufe, die beim letzten
        Beenden noch liefen, weiter abfragen und abschliessen wie `execute` -- dazu eine
        Push-Nachricht mit dem Ergebnis, weil der Ausloeser die Antwort nie bekommen hat.

        Die Server werden sofort als belegt eingetragen; nur das Abfragen wartet kurz
        (RESUME_DELAY_S), sonst koennte in der Zwischenzeit ein zweiter Lauf starten."""
        async with self._ctx.db.session() as session:
            rows = [
                (r.id, r.host_id, r.host_name, r.mode, r.trigger, r.started_at, r.remote_id, r.summary)
                for r in (await session.execute(
                    select(UpdateRunRecord).where(UpdateRunRecord.status == "running", UpdateRunRecord.remote_id.is_not(None))
                )).scalars()
            ]
        claimed = []
        for row in rows:
            # Ein Server, der schon belegt ist (oder zweimal vorkommt): nicht doppelt abfragen.
            owned = row[1] not in self._busy
            if owned:
                self._busy[row[1]] = row[3]
            claimed.append((row, owned))
        await asyncio.sleep(RESUME_DELAY_S)
        await asyncio.gather(*(self._resume(*row, owned=owned) for row, owned in claimed))
        return len(rows)

    async def _resume(self, run_id: str, host_id: str, host_name: str, mode: str, trigger: str, started_at: Any, remote_id: str,
                      summary: str | None = None, *, owned: bool = True) -> None:
        from .defender import ABORTED_BY_RESTART, _describe, _now

        held = owned  # wir halten die Belegung des Servers, bis sie freigegeben ist
        try:
            host = await self._ctx.hosts.get(host_id)
            if host is None or not owned:
                await self._close_run(run_id, up.UpgradeResult(ok=False, summary=ABORTED_BY_RESTART), "")
                await self._ctx.ws.broadcast("defender", {"update_run": run_id, "status": "error"})
                return
            await self._ctx.ws.broadcast("defender", {"update_run": run_id, "status": "running"})
            output = ""
            try:
                started = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
                left = started.timestamp() + UPGRADE_TIMEOUT - _now().timestamp()
                # Der Start war schon gescheitert (`_run_detached` hat es an der Zeile vermerkt):
                # dieselbe kurze Schonfrist wie vor dem Neustart, sonst haengt der Lauf 45 Minuten
                # und sperrt den Server danach noch 2 h 15 min.
                start_failed = summary if summary and summary.startswith(START_FAILED_PREFIX) else None
                result, output, _rc = await self._wait_detached(
                    host, remote_id, deadline=time.monotonic() + max(left, RESUME_MIN_WAIT_S),
                    # Nichts auf dem Server: der Lauf wurde nie gestartet (oder stammt aus
                    # der Zeit vor den entkoppelten Laeufen) -- dasselbe wie bisher beim Neustart.
                    unknown_grace_s=0, unknown_summary=start_failed or ABORTED_BY_RESTART,
                    reach_grace_s=REACH_GRACE_S if start_failed else None,
                )
            except Exception as exc:  # noqa: BLE001 - Fehler landet sichtbar am Lauf
                result = up.UpgradeResult(ok=False, summary=_describe(exc))
            if not result.timed_out:
                self._busy.pop(host.id, None)
                held = False
            elif held:
                held = False  # die Nachfrage (`_settle`) uebernimmt die Belegung
            await self._settle(host, run_id, mode, trigger, result, output)
            name = host.display_name or host.name or host_name
            await self._ctx.notify.send(Notification(
                title=f"{up.MODE_LABEL.get(mode, mode)} auf {name}: " + ("fertig" if result.ok else "fehlgeschlagen"),
                body=f"{result.public}\nDas Dashboard wurde währenddessen neu gestartet.",
                severity=Severity.INFO if result.ok else Severity.WARNING,
                payload={"path": f"{SOC_PATH}?tab=updates", "tags": ["package"], **host_scope([host.id])},
            ))
        except Exception:
            logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_update_resume_failed run_id=%s", run_id)
        finally:
            if held:
                self._busy.pop(host_id, None)

    async def _settle(self, host: Host, run_id: str, mode: str, trigger: str, result: up.UpgradeResult, output: str) -> None:
        """Lauf abschliessen (`_finish`). Hat das Dashboard nur aufgehoert zu warten, bleibt
        der Server belegt und die Nachfrage laeuft gedrosselt weiter (`_follow_late`)."""
        late = result.timed_out
        try:
            await self._finish(host, run_id, mode, trigger, result, output)
            if late:
                task = asyncio.ensure_future(self._follow_late(host, run_id, mode, trigger))
                self._late.add(task)  # Referenz halten, sonst raeumt der GC den Task weg
                task.add_done_callback(self._late.discard)
                late = False
        finally:
            if late:  # Abschluss fehlgeschlagen: den Server nicht dauerhaft belegt lassen
                self._busy.pop(host.id, None)

    async def _follow_late(self, host: Host, run_id: str, mode: str, trigger: str) -> None:
        """Nach dem Ablauf der Wartezeit weiter nachfragen (gedrosselt). Wird der Lauf
        doch noch fertig, wird der Eintrag berichtigt und das Ergebnis gemeldet."""
        held = True
        try:
            result, output, _rc = await self._wait_detached(
                host, run_id, deadline=time.monotonic() + LATE_FOLLOW_S, interval_s=LATE_POLL_INTERVAL_S,
                unknown_grace_s=float("inf"), unknown_summary="",
            )
            self._busy.pop(host.id, None)
            held = False
            if result.timed_out:
                return  # weiter nichts zu berichten: der Eintrag nennt schon das Protokoll
            await self._finish(host, run_id, mode, trigger, result, output)
            name = host.display_name or host.name
            await self._ctx.notify.send(Notification(
                title=f"{up.MODE_LABEL.get(mode, mode)} auf {name}: " + ("fertig" if result.ok else "fehlgeschlagen"),
                body=f"{result.public}\nDer Lauf hat länger gedauert, als das Dashboard gewartet hat.",
                severity=Severity.INFO if result.ok else Severity.WARNING,
                payload={"path": f"{SOC_PATH}?tab=updates", "tags": ["package"], **host_scope([host.id])},
            ))
        except Exception:
            logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_update_follow_failed run_id=%s", run_id)
        finally:
            if held:
                self._busy.pop(host.id, None)

    # --- Pruefen ---------------------------------------------------------------

    async def check_host(self, host: Host, *, refresh: bool = True) -> dict[str, Any]:
        from .defender import _describe, _now

        self._checking.add(host.id)
        try:
            try:
                res = await self._ctx.exec.run(host, up.status_command(refresh=refresh), timeout_s=CHECK_TIMEOUT)
                status = up.parse_status((res.stdout or "") + "\n" + (res.stderr or ""))
            except Exception as exc:  # noqa: BLE001 - Fehler landet sichtbar am Server
                status = up.UpdateStatus(manager=None, error=_describe(exc))
            now = _now().timestamp()
            old = await load_baseline(self._ctx, host.id, "updates")
            if status.error and not status.unsupported:
                # Letzten guten Stand samt Zeitpunkt der letzten erfolgreichen Pruefung (`checked_at`)
                # behalten; dazu kommen der Fehler und der Zeitpunkt dieses Versuchs (`attempted_at`).
                # Ohne guten Stand gibt es keine erfolgreiche Pruefung.
                if old and old.get("manager"):
                    data = {**old, "error": status.error, "attempted_at": now}
                else:
                    data = {**status.as_dict(), "checked_at": None, "attempted_at": now}
            else:
                # "Kein Paketmanager" (`unsupported`) ist ein gueltiges Ergebnis: geprueft, nur nichts zu tun.
                data = {**status.as_dict(), "checked_at": now, "attempted_at": now}
                # Gerade "Neu starten" ausgeloest, der Server laeuft aber noch (Laufzeit
                # laenger als seit dem Ausloesen): den Hinweis behalten, sonst stuende
                # sofort wieder "Neustart noetig" samt Knopf da.
                since = (old or {}).get("reboot_pending_since")
                if (since and status.reboot_required and now - since < REBOOT_PENDING_MAX_S
                        and status.uptime_s is not None and status.uptime_s > now - since):
                    data["reboot_pending_since"] = since
            await save_baseline(self._ctx, host.id, "updates", data)
            return data
        finally:
            self._checking.discard(host.id)

    async def check(self, hosts: list[Host], *, refresh: bool = True) -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def one(h: Host) -> dict[str, Any]:
            from .defender import _describe, _now

            async with sem:
                try:
                    return await self.check_host(h, refresh=refresh)
                except Exception as exc:  # noqa: BLE001 - ein Server darf die Pruefung der anderen nicht abbrechen
                    logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_update_check_failed host=%s", h.id)
                    now = _now().timestamp()
                    return {**up.UpdateStatus(manager=None, error=_describe(exc)).as_dict(), "checked_at": None, "attempted_at": now}

        results = await asyncio.gather(*(one(h) for h in hosts if h.id not in self._checking))
        await self._ctx.ws.broadcast("defender", {"updates": len(results)})
        return list(results)

    def schedule_catch_up(self) -> None:
        """Beim Start (und nach Einstellungsaenderungen): Server, deren Tagespruefung ausgeblieben
        ist -- das Dashboard lief zur Pruefzeit nicht --, kurz nach dem Start nachholen. Prueft
        nichts, solange die Tagespruefung aus ist."""
        if self._catch_up is not None and not self._catch_up.done():
            return

        async def run() -> None:
            await asyncio.sleep(CATCH_UP_DELAY_S)
            from .defender import _now

            now = _now().timestamp()
            due = await self._last_due(await self._ctx.settings.get(), now)
            if due is None or self._daily_running:
                return  # laeuft die Tagespruefung gerade, kommt jeder Server dort dran
            states = await load_baselines(self._ctx, "updates")
            late = [h for h in await self._defender.target_hosts() if check_overdue(states.get(h.id), due) and h.id not in self._checking]
            if late:
                await self.check(late)

        def done(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                logging.getLogger("nodvard_deck.ext.nexus-soc").error("nexus_soc_update_catch_up_failed", exc_info=task.exception())

        self._catch_up = asyncio.ensure_future(run())
        self._catch_up.add_done_callback(done)

    async def _last_due(self, settings: dict[str, Any], now: float) -> float | None:
        """Wann die Tagespruefung zuletzt faellig war; None, wenn "ausgeblieben" nichts bedeutet
        (Pruefung aus, woechentlich oder kein lesbarer Zeitplan)."""
        if not overdue_applies(settings):
            return None
        return last_due(str(settings.get("updates_check_cron") or DEFAULT_UPDATE_CRONS["check"]), now, await schedule_zone(self._ctx))

    def cancel_catch_up(self) -> None:
        """Beim Beenden der Erweiterung: ein noch wartendes Nachholen nicht mehr starten."""
        if self._catch_up is not None and not self._catch_up.done():
            self._catch_up.cancel()

    def start_check(self, hosts: list[Host]) -> int:
        todo = [h for h in hosts if h.id not in self._checking]
        for h in todo:
            self._checking.add(h.id)  # sofort sichtbar "prueft ..."
        if todo:
            async def run() -> None:
                for h in todo:
                    self._checking.discard(h.id)
                await self.check(todo)

            asyncio.ensure_future(run())
        return len(todo)

    def _recheck_after_reboot(self, host: Host) -> None:
        async def run() -> None:
            for _ in range(RECHECK_TRIES):
                await asyncio.sleep(RECHECK_AFTER_REBOOT_S)
                if host.id in self._checking or host.id in self._busy:
                    continue
                st = await self.check_host(host, refresh=False)
                await self._ctx.ws.broadcast("defender", {"updates": 1})
                if not st.get("error") and not st.get("reboot_pending_since"):
                    return  # wieder da und neu gestartet

        task = asyncio.ensure_future(run())
        self._rechecks.add(task)  # Referenz halten, sonst raeumt der GC den Task weg
        task.add_done_callback(self._rechecks.discard)

    # --- Uebersicht --------------------------------------------------------------

    async def overview(self) -> dict[str, Any]:
        from .defender import _now

        hosts = await self._defender.target_hosts()
        states = await load_baselines(self._ctx, "updates")
        async with self._ctx.db.session() as session:
            runs = [run_out(r) for r in (await session.execute(
                select(UpdateRunRecord).order_by(UpdateRunRecord.started_at.desc()).limit(30)
            )).scalars()]
        rows = []
        now = _now().timestamp()
        settings = await self._ctx.settings.get()
        due = await self._last_due(settings, now)
        # Gerade nach dem Zeitpunkt laeuft die Tagespruefung noch: erst nach einer Schonfrist melden,
        # und nie, solange sie laeuft.
        hint = due is not None and now - due > OVERDUE_HINT_GRACE_S and not self._daily_running
        for h in hosts:
            st = states.get(h.id)
            rows.append({
                "host_id": h.id, "host_name": h.display_name or h.name, "host_status": h.status.value,
                "status": st, "checking": h.id in self._checking, "busy": self._busy.get(h.id),
                "check_overdue": hint and h.id not in self._checking and check_overdue(st, due),
                "last_run": next((r for r in runs if r["host_id"] == h.id), None),
            })
        checked = [r["status"] for r in rows if r["status"] and not r["status"].get("error")]
        return {
            "hosts": rows,
            "summary": {
                "hosts": len(rows),
                "checked": len(checked),
                "up_to_date": sum(1 for s in checked if s.get("count") == 0),
                "packages": sum(int(s.get("count") or 0) for s in checked),
                "security": sum(int(s.get("security_count") or 0) for s in checked),
                # Server mit gerade ausgeloestem Neustart zaehlen nicht mehr mit.
                "reboot": sum(1 for s in checked if s.get("reboot_required") and not s.get("reboot_pending_since")),
                "errors": sum(1 for r in rows if r["status"] and r["status"].get("error") and not r["status"].get("unsupported")),
            },
            "runs": runs,
            "config": {
                "check_enabled": settings.get("updates_check_enabled", True),
                "check_cron": settings.get("updates_check_cron") or DEFAULT_UPDATE_CRONS["check"],
                "auto_enabled": settings.get("auto_updates_enabled", False),
                "auto_mode": settings.get("auto_updates_mode") or "security",
                "auto_cron": settings.get("auto_updates_cron") or DEFAULT_UPDATE_CRONS["auto"],
                "auto_reboot": settings.get("auto_reboot", False),
                "auto_tag": (settings.get("auto_updates_host_tag") or "").strip() or None,
                # "Alle Updates" von Hand laeuft auf Proxmox als dist-upgrade: die Seite
                # sagt das in der Rueckfrage, bevor sie freigibt.
                "proxmox_dist_upgrade": bool(settings.get("proxmox_dist_upgrade")),
            },
        }

    # --- Einspielen --------------------------------------------------------------

    async def plan(self, host: Host, mode: str) -> tuple[str, list[str]]:
        """Paketmanager und (nur fuer "security") die Sicherheitspakete aus dem zuletzt
        gespeicherten Stand; ohne Stand wird vorher geprueft. Diese Angaben landen im
        Payload der Aktion, der Befehl wird daraus gebaut (defender_api.ActionCommands)."""
        st = await load_baseline(self._ctx, host.id, "updates")
        if not st or not st.get("manager"):
            st = await self.check_host(host, refresh=True)
        if not st.get("manager"):
            raise ValueError(st.get("error") or "Update-Stand unbekannt.")
        security = [p["name"] for p in st.get("packages", []) if p.get("security")] if mode == "security" else []
        return st["manager"], security

    async def command_for(self, host: Host, mode: str) -> str:
        """Der Befehl zum aktuellen Stand (siehe `plan`), immer ohne dist-upgrade. Der Weg
        der Oberflaeche (Vorschlag, Freigabe, Ausfuehrung) laeuft nicht hierueber, sondern
        ueber `defender_api.ActionCommands`; dort steht `dist_upgrade` im Plan."""
        if mode == "reboot":
            return up.REBOOT_COMMAND
        manager, packages = await self.plan(host, mode)
        return up.upgrade_command(manager, mode, packages)

    async def execute(self, host: Host, mode: str, *, command: str, trigger: str) -> tuple[bool, str, str, int | None]:
        """Fuehrt einen Einspiel-Lauf aus, protokolliert ihn und prueft danach neu.
        Rueckgabe: (ok, Zusammenfassung, Ausgabe, Rueckgabecode). Die Zusammenfassung kann eine Zeile
        vom Server enthalten und gehoert nur in das Ergebnis der Aktion, nicht in Meldungen (dafuer
        `_execute` und `UpgradeResult.public`)."""
        result, output, exit_code = await self._execute(host, mode, command=command, trigger=trigger)
        return result.ok, result.summary, output, exit_code

    async def _execute(self, host: Host, mode: str, *, command: str, trigger: str) -> tuple[up.UpgradeResult, str, int | None]:
        """Wie `execute`, mit dem ganzen Ergebnis statt der Zusammenfassung.

        Updates laufen entkoppelt auf dem Server (`_run_detached`), nur der
        Neustart (kurz, `shutdown -r +1`) laeuft direkt im SSH-Kanal."""
        from .defender import _describe, _new_id, _now

        if host.id in self._busy:
            return up.UpgradeResult(ok=False, summary=BUSY_MESSAGE), "", None
        # Sofort belegen, noch vor dem ersten await: zwei gleichzeitige Aufrufe (Aktion und
        # Zeitplan) duerfen nicht beide durch die Pruefung kommen.
        self._busy[host.id] = mode
        name = host.display_name or host.name
        run_id = _new_id("upd")
        detached = mode != "reboot"
        output, exit_code = "", None
        late = False
        try:
            try:
                async with self._ctx.db.session() as session:
                    session.add(UpdateRunRecord(id=run_id, host_id=host.id, host_name=name, mode=mode, trigger=trigger,
                                                status="running", upgraded=0, started_at=_now(),
                                                remote_id=run_id if detached else None))
                await self._ctx.ws.broadcast("defender", {"update_run": run_id, "status": "running"})
                if detached:
                    result, output, exit_code = await self._run_detached(host, run_id, command)
                else:
                    res = await self._ctx.exec.run(host, as_root(command), timeout_s=60)
                    output = (res.stdout or "") + (res.stderr or "")
                    exit_code = res.exit_code
                    if NO_ROOT in output:
                        result = up.UpgradeResult(ok=False, summary=NO_ROOT_MESSAGE)
                    else:
                        if exit_code == 0:
                            result = up.UpgradeResult(ok=True, summary="Neustart in einer Minute.")
                        else:
                            result = up.UpgradeResult(
                                ok=False, summary=output.strip()[-200:] or "Neustart fehlgeschlagen",
                                public_summary=f"Neustart fehlgeschlagen. {up.DETAILS_HINT}",
                            )
            except Exception as exc:  # noqa: BLE001
                result = up.UpgradeResult(ok=False, summary=_describe(exc))
            late = result.timed_out
        finally:
            if not late:
                self._busy.pop(host.id, None)
        await self._settle(host, run_id, mode, trigger, result, output)
        return result, output, exit_code

    async def _run_detached(self, host: Host, run_id: str, command: str) -> tuple[up.UpgradeResult, str, int | None]:
        """Startet den Lauf entkoppelt auf dem Server und wartet per kurzer Abfragen
        auf das Ergebnis (`detached.py`)."""
        from .defender import _describe

        launch = as_root(dt.launch_command(run_id, command))
        unknown = "Der Update-Lauf wurde auf dem Server nicht gefunden – er ist wohl nicht gestartet."
        reach_grace_s = None  # nur nach einem gescheiterten Start begrenzt (siehe unten)
        try:
            res = await self._ctx.exec.run(host, launch, timeout_s=LAUNCH_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            # Der Start kann trotzdem geklappt haben (Verbindung erst danach weg) --
            # nachsehen statt einen laufenden Lauf als gescheitert zu melden. Meldet
            # sich der Server aber gar nicht mehr, nicht 45 Minuten lang "laeuft" zeigen.
            unknown = f"{START_FAILED_PREFIX} {_describe(exc)}"
            reach_grace_s = REACH_GRACE_S
            # Festhalten, falls das Dashboard in der Wartezeit neu startet (`_resume`).
            try:
                async with self._ctx.db.session() as session:
                    row = await session.get(UpdateRunRecord, run_id)
                    if row is not None and row.status == "running":
                        row.summary = unknown[:400]
            except Exception:  # noqa: BLE001 - nur ein Vermerk, der Lauf geht normal weiter
                logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_update_start_mark_failed run_id=%s", run_id)
        else:
            out = (res.stdout or "") + (res.stderr or "")
            if NO_ROOT in out:
                return up.UpgradeResult(ok=False, summary=NO_ROOT_MESSAGE), out, res.exit_code
            info = dt.parse_launch(out)
            if not info.started:
                if info.method is None:  # schon der Ordner scheiterte (oder gar keine Antwort): nichts gestartet
                    lines = [ln for ln in out.strip().splitlines() if not ln.startswith("@@")]
                    reason = lines[-1][:200] if lines else "keine Rückmeldung"
                    return up.UpgradeResult(
                        ok=False, summary=f"Update-Lauf konnte nicht gestartet werden: {reason}",
                        public_summary=f"Update-Lauf konnte nicht gestartet werden. {up.DETAILS_HINT}",
                    ), out, res.exit_code
                # Gestartet, aber die pid-Datei kam nicht innerhalb von 5 s (langsamer Pi):
                # der Lauf kann trotzdem laufen -- nachsehen wie im Ausnahmefall.
                unknown = "Der Update-Lauf wurde auf dem Server nicht gefunden – er ist wohl nicht gestartet."
        return await self._wait_detached(host, run_id, deadline=time.monotonic() + UPGRADE_TIMEOUT,
                                         unknown_grace_s=UNKNOWN_GRACE_S, unknown_summary=unknown,
                                         reach_grace_s=reach_grace_s)

    async def _wait_detached(self, host: Host, remote_id: str, *, deadline: float, unknown_grace_s: float,
                             unknown_summary: str, interval_s: float | None = None,
                             reach_grace_s: float | None = None) -> tuple[up.UpgradeResult, str, int | None]:
        """Fragt alle POLL_INTERVAL_S Sekunden kurz nach, bis der Lauf fertig ist oder
        `deadline` (time.monotonic) erreicht ist. Verbindungsfehler zwischendurch
        (z. B. waehrend Docker neu startet) und Antworten ohne jede Auskunft werden
        ausgesessen. Der Lauf auf dem Server wird nie beendet.

        Mit `reach_grace_s` (Start war gescheitert): hat bis dahin keine einzige Nachfrage
        eine Antwort vom Server gebracht, gilt der Server als nicht erreichbar -- Ergebnis
        ist dann `unknown_summary`, ohne `timed_out` (der Server wird sofort wieder frei)."""
        from .defender import _describe

        command = as_root(dt.poll_command(remote_id))  # ValueError bei ungueltiger ID: sofort
        unknown_until = time.monotonic() + unknown_grace_s
        reach_until = None if reach_grace_s is None else time.monotonic() + reach_grace_s
        reached = False
        lost = 0
        last_error: str | None = None
        output = ""
        while True:
            try:
                res = await self._ctx.exec.run(host, command, timeout_s=POLL_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - Verbindung weg: weiter nachfragen
                last_error = _describe(exc)
            else:
                out = (res.stdout or "") + (res.stderr or "")
                if NO_ROOT in out:
                    return up.UpgradeResult(ok=False, summary=NO_ROOT_MESSAGE), out, res.exit_code
                poll = dt.parse_poll(out)
                if poll.state == "noreply":
                    # Kanal mitten im Aufruf verloren (leere Ausgabe): wie ein Verbindungsfehler,
                    # nicht als "Lauf unbekannt" zaehlen.
                    last_error = f"keine Antwort vom Server (Rückgabecode {res.exit_code})"
                else:
                    last_error = None
                    reached = True
                    output = poll.output or output
                    if poll.state == "done":
                        return up.parse_upgrade_output(poll.output, poll.rc), poll.output, poll.rc
                    lost = lost + 1 if poll.state == "lost" else 0
                    if lost >= 2:  # zweimal hintereinander: kein Zufall beim Nachsehen
                        return up.UpgradeResult(ok=False, summary=LOST_MESSAGE), poll.output, None
                    if poll.state == "unknown" and time.monotonic() >= unknown_until:
                        return up.UpgradeResult(ok=False, summary=unknown_summary), "", None
            if reach_until is not None and not reached and time.monotonic() >= reach_until:
                return up.UpgradeResult(ok=False, summary=unknown_summary), output, None
            if time.monotonic() >= deadline:
                summary = (f"Keine Rückmeldung mehr – der Lauf kann auf dem Server noch weiterlaufen "
                           f"(Protokoll: {dt.paths(remote_id)['log']}). Das Dashboard schaut noch eine Weile nach und meldet das Ergebnis.")
                if last_error:
                    summary += f" Letzter Fehler: {last_error}"
                return up.UpgradeResult(ok=False, summary=summary[:400], timed_out=True), output, None
            await asyncio.sleep(POLL_INTERVAL_S if interval_s is None else interval_s)

    async def _close_run(self, run_id: str, result: up.UpgradeResult, output: str) -> None:
        from .defender import _now

        async with self._ctx.db.session() as session:
            row = await session.get(UpdateRunRecord, run_id)
            if row is not None:
                row.status = "ok" if result.ok else "error"
                row.upgraded = result.upgraded
                row.summary = result.summary
                row.output_tail = output[-6000:] if output else None
                row.finished_at = _now()

    async def _finish(self, host: Host, run_id: str, mode: str, trigger: str, result: up.UpgradeResult, output: str) -> None:
        """Lauf abschliessen: speichern, Audit, Neustart-Merker bzw. neu pruefen, melden."""
        from .defender import _now

        name = host.display_name or host.name
        await self._close_run(run_id, result, output)
        await self._ctx.audit.log(
            action=f"nexus_soc.updates_{mode}", outcome="success" if result.ok else "failure", target_type="host",
            target_id=host.id, reason=f"{up.MODE_LABEL.get(mode, mode)} auf {name}: {result.public}",
            detail={"run_id": run_id, "trigger": trigger, "upgraded": result.upgraded}, correlation_id=run_id,
        )
        if mode == "reboot":
            st = await load_baseline(self._ctx, host.id, "updates")
            if st and result.ok:
                st["reboot_pending_since"] = _now().timestamp()
                await save_baseline(self._ctx, host.id, "updates", st)
            if result.ok:
                self._recheck_after_reboot(host)
        else:
            await self.check_host(host, refresh=False)
        await self._ctx.ws.broadcast("defender", {"update_run": run_id, "status": "ok" if result.ok else "error"})

    # --- Zeitplaene ----------------------------------------------------------------

    async def scheduled_check(self) -> dict[str, Any]:
        self._daily_running += 1
        try:
            return await self._scheduled_check()
        finally:
            self._daily_running -= 1

    async def _scheduled_check(self) -> dict[str, Any]:
        hosts = await self._defender.target_hosts()
        # Server, die gerade schon geprueft werden, ueberspringt check() -- sie kommen mit
        # ihrem zuletzt gespeicherten Stand in den Bericht, damit keiner fehlt.
        todo = [h for h in hosts if h.id not in self._checking]
        results = await self.check(todo, refresh=True)
        fresh = dict(zip((h.id for h in todo), results, strict=True))
        pairs = [(h, fresh[h.id] if h.id in fresh else await load_baseline(self._ctx, h.id, "updates") or {}) for h in hosts]
        settings = await self._ctx.settings.get()
        title, body, level = build_update_report(pairs, zone=await schedule_zone(self._ctx))
        if settings.get("updates_notify", True) and level != "none":
            sev = Severity.WARNING if level == "warning" else Severity.INFO
            await self._ctx.notify.send(Notification(
                title=title, body=body, severity=sev,
                payload={"path": f"{SOC_PATH}?tab=updates", "tags": ["package"], **host_scope(h.id for h, _ in pairs)},
            ))
        return {"hosts": len(pairs), "title": title}

    async def auto_update(self) -> dict[str, Any]:
        settings = await self._ctx.settings.get()
        mode = settings.get("auto_updates_mode") or "security"
        tag = (settings.get("auto_updates_host_tag") or "").strip()
        hosts = await self._defender.target_hosts()
        if tag:
            hosts = [h for h in hosts if tag in (h.tags or [])]
        lines: list[str] = []
        failed = 0
        skipped = 0
        for host in hosts:  # nacheinander: nie alle Server gleichzeitig mitten im Update
            name = host.display_name or host.name
            st = await self.check_host(host, refresh=True)
            if st.get("unsupported"):
                skipped += 1
                continue  # kein bekannter Paketmanager: nichts zu tun und nichts zu melden
            if st.get("error") or not st.get("manager"):
                lines.append(f"- {name}: Prüfung fehlgeschlagen ({st.get('error')})")
                failed += 1
                continue
            todo = st.get("security_count") if mode == "security" else st.get("count")
            if todo:
                try:
                    # Automatische Laeufe nehmen nie dist-upgrade: das darf Pakete
                    # ersetzen/entfernen und gehoert nur in eine Aktion, die jemand freigibt.
                    command = up.upgrade_command(st["manager"], mode, [p["name"] for p in st.get("packages", []) if p.get("security")])
                except ValueError as exc:
                    lines.append(f"- {name}: {exc}")
                    continue
                # Die Meldung nennt nur `public`: eine Zeile vom Server steht nicht in der Push-Nachricht.
                run, _out, _rc = await self._execute(host, mode, command=command, trigger="schedule")
                failed += 0 if run.ok else 1
                lines.append(f"- {name}: {run.public}" if run.ok else f"- {name}: FEHLGESCHLAGEN – {run.public}")
                st = await load_baseline(self._ctx, host.id, "updates") or st
            else:
                lines.append(f"- {name}: aktuell")
            if settings.get("auto_reboot", False) and st.get("reboot_required") and not st.get("reboot_pending_since"):
                run, _out, _rc = await self._execute(host, "reboot", command=up.REBOOT_COMMAND, trigger="schedule")
                lines.append(f"  ↳ Neustart: {run.public}")
        if lines:
            title = f"Automatische Updates: {len(hosts) - skipped} Server" + (f", {failed} Fehler" if failed else "")
            await self._ctx.notify.send(Notification(
                title=title, body="\n".join(lines), severity=Severity.WARNING if failed else Severity.INFO,
                payload={"path": f"{SOC_PATH}?tab=updates", "tags": ["package"], **host_scope(h.id for h in hosts)},
            ))
        return {"hosts": len(hosts), "failed": failed}


DEFAULT_UPDATE_CRONS = {"check": "0 6 * * *", "auto": "30 3 * * *"}


def _stamp(ts: float, zone: tzinfo) -> str:
    text = datetime.fromtimestamp(ts, zone).strftime("%d.%m.%Y %H:%M")
    return text + " UTC" if zone is UTC else text


def build_update_report(pairs: list[tuple[Any, dict[str, Any]]], zone: tzinfo = UTC) -> tuple[str, str, str]:
    """Kurzbericht nach der taeglichen Pruefung. Stufe "none" = nichts zu melden.

    Ein Server, dessen Pruefung fehlgeschlagen ist, steht als Fehler da -- auch wenn noch ein
    Stand einer frueheren Pruefung gespeichert ist (`manager` gesetzt); dessen Zahlen zaehlen
    nicht mit, die Zeile nennt nur, von wann er stammt."""
    lines: list[str] = []
    security = total = reboot = errors = 0
    for host, st in pairs:
        name = host.display_name or host.name
        if st.get("unsupported"):
            continue  # z. B. ein NAS mit eigener Firmware: kein Fehler, der jeden Tag gemeldet werden müsste
        if st.get("error"):
            errors += 1
            checked = st.get("checked_at")
            since = f", Stand von {_stamp(checked, zone)}" if isinstance(checked, int | float) else ""
            lines.append(f"- {name}: Prüfung fehlgeschlagen ({st['error']}){since}")
            continue
        count, sec = int(st.get("count") or 0), int(st.get("security_count") or 0)
        total += count
        security += sec
        parts = []
        if count:
            parts.append(f"{count} Update(s)" + (f", davon {sec} Sicherheit" if sec else ""))
        if st.get("reboot_required"):
            reboot += 1
            parts.append("Neustart nötig")
        if parts:
            lines.append(f"- {name}: " + ", ".join(parts))
    if not lines:
        return "Alle Server aktuell", "Keine Updates offen.", "none"
    if security:
        title = f"{security} Sicherheitsupdate(s) offen"
    elif total:
        title = f"{total} Update(s) offen"
    elif reboot:
        title = f"{reboot} Server brauchen einen Neustart"
    else:
        title = f"Update-Prüfung: {errors} Fehler"
    level = "warning" if (security or errors) else "info"
    return title, "\n".join(lines), level
