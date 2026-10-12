"""Backup-Waechter: meldet von selbst, wenn ein Backup fehlschlaegt -- und wenn derselbe
Job danach wieder erfolgreich lief. Landet im Kern-Benachrichtigungs-Center
("Meldungen").

Nur Zustandswechsel, nie derselbe Fehlschlag bei jeder Pruefung erneut. Der Zustand liegt
in `data_dir/watch-state.json` (ein Neustart von Nodvard Deck loest keine Meldungsflut aus).
"Laeuft" und "unbekannt" aendern nichts: ein laufender Wiederholungsversuch ist noch kein
"wieder in Ordnung".

Hintergrund: ein fehlschlagender VZDump-Job (z. B. "Broken pipe" zum NFS-Ziel) kann
wochenlang unbemerkt bleiben, wenn niemand aktiv nachschaut.

Ursache in Klartext: Bevor eine Fehlschlag-Meldung rausgeht, liest der Waechter das Protokoll des Laufs
(`ProxmoxBackupProvider.failure_cause`, failure_text.py) und schreibt "Ursache" und "Was tun" dazu -- nur die feste
Erklaerung, nie die Zeile von Proxmox (die kann Pfade und Adressen enthalten und ginge mit dem Push nach aussen; sie
steht auf der Seite). Laesst sich das nicht lesen, geht die Meldung ohne diesen Zusatz raus. Gelesen wird nur, wenn
die Meldung wirklich rausgeht, nicht bei jeder Pruefung. Ein Lauf mit "WARNINGS: n" gilt als erfolgreich.

Backup zu alt: Dass eine VM gar nicht mehr gesichert wird (Job geloescht oder abgeschaltet,
Ziel weg, Knoten lange aus), faellt in keinem Fehlschlag auf. Darum meldet der Waechter je VM
einmal, wenn die juengste ERFOLGREICHE Sicherung aelter als ihre Frist ist, und einmal Entwarnung,
sobald eine neue gelingt. Die Frist ist `backup_max_age_days` (Standard 3, mindestens 2) -- oder, wenn der
Backup-Job der VM seltener laeuft (z. B. nur samstags), dessen Abstand aus dem Zeitplan (`schedule.py`) -- plus
`AGE_GRACE_SECONDS` Luft, damit ein Lauf, der gerade erst startet oder spaeter fertig wird, keinen Alarm samt
Entwarnung ausloest. Gehoert eine VM zu mehreren Jobs, zaehlt der kuerzeste Abstand; abgeschaltete Jobs zaehlen
nicht, und ein nicht lesbarer Zeitplan heisst: nur die Einstellung. Solange fuer die VM eine Sicherung laeuft,
kommt keine neue Meldung und der bisherige Zustand bleibt stehen. Geprueft werden VMs mit einem Backup-Job
(auch einem abgeschalteten) und VMs ohne Job, fuer die noch Sicherungen auf einem Speicher liegen
(Job geloescht). VMs ohne Job UND ohne Sicherung gehen den Waechter nichts an (die Seite zeigt
sie als "ungeschuetzt"), ebenso VMs, die dort als "bewusst ohne Backup" markiert sind, und die
unter `backup_age_skip` (VMID, `Verbindung/VMID` oder Name). Als Zeitpunkt zaehlt der neuere von
beiden: der letzte erfolgreiche VZDump-Lauf der VM und die neueste Sicherungsdatei auf den Speichern.
Ein Wert nur aus dem Lauf-Verlauf reicht zum Entwarnen, zum Melden muss auch der Blick auf die
Speicher moeglich gewesen sein -- eine nicht lesbare Verbindung meldet nie "zu alt". Die Speicher werden
hoechstens alle `INVENTORY_CACHE_SECONDS` gelesen (`InventoryCache`); vor einer NEUEN Meldung wird frisch gelesen.
Die Entwarnung, die nur eine neue Datei auf einem Speicher bringt, kann deshalb bis zu drei Stunden dauern; ein neuer
erfolgreicher Lauf im Verlauf wirkt sofort.
Hat dieselbe VM schon eine offene Fehlschlag-Meldung (ein Job schlaegt fehl), kommt "zu alt" nicht
zusaetzlich: es ist dieselbe Stoerung. Sobald die Fehlschlag-Meldung zu ist und die Sicherung noch
immer zu alt waere, meldet der Waechter es dann. Die Meldungen laufen durch dieselbe Fensterlogik.

Wartungsfenster: beide Meldungen tragen die Host-ID der VM. Hat ein Fenster den
Fehlschlag stumm geschaltet (`ctx.notify.send()` sagt das), merkt sich der Waechter
"stumm gemeldet" und fragt danach nur `ctx.notify.would_suppress()` (kein Verlaufseintrag
je Pruefung). Ist das Fenster vorbei und der Job noch fehlgeschlagen, kommt die Meldung
einmal hoerbar nach; lief er im Fenster schon wieder erfolgreich, kommt nichts nach.
Die Entwarnung richtet sich nach dem Fehlschlag: War er hoerbar und faellt nur die
Entwarnung ins Fenster, kommt sie nach dem Fenster einmal hoerbar nach -- einen gehoerten
Alarm schliesst immer eine gehoerte Entwarnung, aber nicht mitten in der Nacht
(docs/02-EXTENSION-API.md §2, "Meldungen und Wartungsfenster").
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

from .schedule import schedule_gap_days

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

    from .capabilities import ProxmoxBackupProvider

AFTER_WINDOW_TITLE = " (seit dem Wartungsfenster)"
AFTER_WINDOW_NOTE = "Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) und hält noch an."
IN_WINDOW_TITLE = " (im Wartungsfenster)"
RECOVERED_IN_WINDOW = "Schon im Wartungsfenster wieder erfolgreich (dort nur im Verlauf, ohne Push)."
RECOVERED_AFTER_WINDOW = "Der Fehlschlag lag im Wartungsfenster (dort nur im Verlauf, ohne Push)."

AGE_MAX_DAYS_DEFAULT = 3
AGE_MAX_DAYS_MIN = 2
AGE_MAX_DAYS_MAX = 60
AGE_GRACE_SECONDS = 6 * 3600
"""Luft auf jede Frist: ein Job, der gerade erst startet oder etwas spaeter fertig wird, soll keinen Alarm samt
Entwarnung ausloesen."""
AGE_RUNNING_MAX_SECONDS = 24 * 3600
"""Eine Sicherung, die laenger als das laeuft, gilt als haengend und haelt "Backup zu alt" nicht mehr zurueck."""
INVENTORY_CACHE_SECONDS = 3 * 3600
AGE_KEY = "age:"
"""Praefix der Zustandseintraege "Backup zu alt" (`age:<Verbindung>--<VMID>`). Der Eintrag eines Jobs
beginnt mit dem Verbindungsnamen und der darf kein ":" enthalten -- die beiden Arten verwechseln sich nie."""
AGE_RECOVERED_AFTER_WINDOW = "Die Meldung lag im Wartungsfenster (dort nur im Verlauf, ohne Push)."
AGE_RECOVERED_IN_WINDOW = "Schon im Wartungsfenster wieder aktuell (dort nur im Verlauf, ohne Push)."
BACKUPS_PATH = "/ext/backups/backups"


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _when(epoch: Any) -> str:
    if not isinstance(epoch, (int, float)):
        return "unbekannt"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _remember(last_run_at: Any, muted: bool, host_id: str | None = None) -> Any:
    """Eintrag im Zustand: wie bisher der Zeitpunkt des gemeldeten Laufs; war die Meldung
    im Wartungsfenster stumm, ein kleines Objekt mit `muted` und dem Host, fuer den das
    Fenster galt (alte Dateien bleiben lesbar). Den Host merken wir uns, damit die Frage
    "Fenster vorbei?" auch stimmt, wenn er in der Zeile gerade fehlt."""
    if not muted:
        return last_run_at
    return {"run": last_run_at, "muted": True, **({"host": host_id} if host_id else {})}


def _is_muted(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("muted") is True


def _muted_host(entry: Any) -> str | None:
    return entry.get("host") if isinstance(entry, dict) else None


def _ok_pending(entry: Any) -> bool:
    """Der Fehlschlag war hoerbar, die Entwarnung fiel ins Fenster und steht noch aus."""
    return isinstance(entry, dict) and entry.get("ok_pending") is True


async def _send(ctx: "ExtensionContext", notification: Notification) -> bool:
    """-> True, wenn ein Wartungsfenster den Push unterdrueckt hat. Aeltere Kerne liefern
    kein Ergebnis -- dann gilt die Meldung als zugestellt."""
    result = await ctx.notify.send(notification)
    return getattr(result, "suppressed", False) is True


async def _still_silenced(ctx: "ExtensionContext", host_id: str | None) -> bool:
    """Laeuft das Wartungsfenster fuer diesen Host noch? Nur fragen, nichts anlegen. Im
    Zweifel False: lieber einmal zu viel melden als eine Meldung verlieren."""
    if not host_id:
        return False
    try:
        return await ctx.notify.would_suppress(host_id=host_id) is True
    except Exception:  # noqa: BLE001 - Nachmeldung ist ein Extra, der Waechter muss weiterlaufen
        return False


def _failed_notification(
    job: dict[str, Any], key: str, *, after_window: bool = False, cause: dict[str, Any] | None = None,
) -> Notification:
    """`cause`: Ergebnis von `failure_cause` (oder None). Bei einer bekannten Ursache kommen ihre zwei festen Saetze dazu."""
    host_id = job.get("host_id")
    body = (
        f"Letzter Lauf {_when(job.get('last_run_at'))} auf {job.get('node') or '?'}, "
        f"Ziel {job.get('storage') or '?'} ({job['connection']}). "
        "Details und 'Erneut versuchen' auf der Seite „Proxmox-Backups“."
    )
    if cause and cause.get("key") not in (None, "unknown"):
        body = f"Ursache: {cause['what']}\nWas tun: {cause['todo']}\n{body}"
    return Notification(
        title=f"Backup fehlgeschlagen: {job['name']}" + (AFTER_WINDOW_TITLE if after_window else ""),
        body=f"{body}\n{AFTER_WINDOW_NOTE}" if after_window else body,
        severity=Severity.CRITICAL,
        correlation_id=f"backups-watch:{key}",
        payload={"host_id": host_id} if host_id else {},
    )


def _recovered_notification(job: dict[str, Any], key: str, variant: str) -> Notification:
    """`variant`: "plain" (normal), "after_window" (der Fehlschlag lag im Fenster) oder
    "in_window" (die Entwarnung fiel ins Fenster und kommt jetzt nach)."""
    host_id = job.get("host_id")
    note = {"plain": "", "after_window": RECOVERED_AFTER_WINDOW, "in_window": RECOVERED_IN_WINDOW}[variant]
    body = f"Letzter Lauf {_when(job.get('last_run_at'))}."
    return Notification(
        title=f"Backup wieder erfolgreich: {job['name']}" + (IN_WINDOW_TITLE if variant == "in_window" else ""),
        body=f"{body} {note}" if note else body,
        severity=Severity.INFO,
        correlation_id=f"backups-watch:{key}",
        # Wie der Fehlschlag zum Host, damit das Fenster beide gleich behandelt (docs/03 §9).
        payload={"host_id": host_id} if host_id else {},
    )


async def _step(
    ctx: "ExtensionContext",
    alerted: dict[str, Any],
    key: str,
    verdict: str | None,
    host_id: str | None,
    value: Any,
    alert: Callable[[bool], Notification],
    recovered: Callable[[str], Notification],
) -> int:
    """Ein Eintrag gegen seinen Zustand (Fehlschlag je Job, "zu alt" je VM -- dieselbe Logik).

    `verdict`: "bad" (Stoerung besteht), "good" (in Ordnung) oder None (unbekannt, nichts aendern).
    `value`: was im Zustand steht, solange die Meldung hoerbar war. `alert(after_window)` und
    `recovered(variant)` bauen die Meldungen. -> Anzahl gesendeter Meldungen (0 oder 1)."""
    if verdict == "bad" and (key not in alerted or _ok_pending(alerted[key])):
        # Neue Stoerung (auch nach einer noch ausstehenden Entwarnung: die ist ueberholt)
        alerted[key] = _remember(value, await _send(ctx, alert(False)), host_id)
        return 1
    if verdict == "bad" and _is_muted(alerted[key]) and not await _still_silenced(
        ctx, host_id or _muted_host(alerted[key])
    ):
        # Im Wartungsfenster nur still gemeldet, Fenster vorbei, noch immer so:
        # jetzt einmal hoerbar. Faellt auch das in ein Fenster, bleibt es "stumm".
        scope = host_id or _muted_host(alerted[key])
        alerted[key] = _remember(value, await _send(ctx, alert(True)), scope)
        return 1
    if verdict == "good" and _ok_pending(alerted.get(key)):
        # Entwarnung aus dem Fenster, weiter in Ordnung: jetzt einmal hoerbar.
        if await _still_silenced(ctx, host_id or _muted_host(alerted[key])):
            return 0
        muted = await _send(ctx, recovered("in_window"))
        if muted:
            alerted[key] = {"ok_pending": True, "host": host_id or _muted_host(alerted[key])}
        else:
            del alerted[key]
        return 1
    if verdict == "good" and key in alerted:
        was_muted = _is_muted(alerted[key])
        muted = await _send(ctx, recovered("after_window" if was_muted else "plain"))
        if muted and not was_muted:
            # Den Fehlschlag hat man gehoert, nur die Entwarnung fiel ins Fenster: nach
            # dem Fenster einmal hoerbar nachholen, damit der Alarm nicht offen bleibt.
            alerted[key] = {"ok_pending": True, **({"host": host_id} if host_id else {})}
        else:
            del alerted[key]
        return 1
    return 0


def _now() -> float:
    return time.time()


def _setting_int(raw: Any, default: int, low: int, high: int) -> int:
    """Ganze Zahl im erlaubten Bereich; alles andere (Text, Bruch, ausserhalb, nichts) gilt als
    nicht gesetzt und ergibt den Standard -- ein falscher Wert darf den Waechter nie lahmlegen."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    try:
        number = int(raw)
    except (OverflowError, ValueError):  # unendlich oder keine Zahl
        return default
    return number if number == raw and low <= number <= high else default


def _setting_days(raw: Any) -> int:
    """Die Frist aus der Einstellung. Ein gespeicherter Wert 1 war frueher erlaubt und gilt wie die neue
    Untergrenze (nicht als ungueltig und damit als Standard)."""
    return max(_setting_int(raw, AGE_MAX_DAYS_DEFAULT, 1, AGE_MAX_DAYS_MAX), AGE_MAX_DAYS_MIN)


def _job_gap_days(jobs: list[dict[str, Any]]) -> int | None:
    """Abstand der Backup-Jobs einer VM in Tagen: der kuerzeste unter den eingeschalteten (der haeufigste Job sichert
    sie). Kein eingeschalteter Job oder ein Zeitplan, den wir nicht lesen koennen (der koennte der haeufigste sein):
    `None`, dann gilt nur die Einstellung."""
    gaps = []
    for job in jobs:
        if not job.get("enabled"):
            continue
        gap = schedule_gap_days(job.get("schedule"))
        if gap is None:
            return None
        gaps.append(gap)
    return min(gaps) if gaps else None


def _is_running(job: dict[str, Any], now: float) -> bool:
    """Laeuft fuer diese Zeile (Job und Gast) gerade eine Sicherung? Eine, die schon laenger als
    `AGE_RUNNING_MAX_SECONDS` laeuft, gilt als haengend."""
    if job.get("last_status") != "running":
        return False
    started = job.get("last_run_at")
    return not isinstance(started, (int, float)) or now - started <= AGE_RUNNING_MAX_SECONDS


class InventoryCache:
    """Die Sicherungen auf den Speichern, im Arbeitsspeicher und hoechstens `INVENTORY_CACHE_SECONDS` alt: der Waechter
    laeuft alle 15 Minuten, die Speicher sollen nicht ebenso oft gelesen werden. Nur ein fehlerfreies Ergebnis kommt
    hinein; nach einem Neustart ist er leer."""

    def __init__(self, seconds: float = INVENTORY_CACHE_SECONDS) -> None:
        self._seconds = seconds
        self._value: dict[str, Any] | None = None
        self._stored_at = 0.0

    def get(self, now: float) -> dict[str, Any] | None:
        if self._value is not None and 0 <= now - self._stored_at < self._seconds:
            return self._value
        return None

    def put(self, value: dict[str, Any], now: float) -> None:
        self._value, self._stored_at = value, now

    def clear(self) -> None:
        self._value = None


def _skip_names(raw: Any) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {item.strip().lower() for item in raw if isinstance(item, str) and item.strip()}


def _is_skipped(skip: set[str], connection: str, vmid: str, name: str) -> bool:
    return bool(skip & {vmid.lower(), f"{connection}/{vmid}".lower(), name.strip().lower()})


def _age_notification(vm: dict[str, Any], key: str, last_ok: float, days: int, *, after_window: bool = False) -> Notification:
    host_id = vm.get("host_id")
    body = (
        f"Die letzte erfolgreiche Sicherung ist vom {_when(last_ok)} und damit älter als {days} Tage. "
        "Häufige Gründe: der Backup-Job wurde gelöscht oder abgeschaltet, "
        "das Ziel ist nicht erreichbar oder der Server war lange aus. Details auf der Backup-Seite."
    )
    payload: dict[str, Any] = {"host_id": host_id} if host_id else {}
    payload["path"] = f"{BACKUPS_PATH}?host={host_id}" if host_id else BACKUPS_PATH
    return Notification(
        title=f"Backup zu alt: {vm['name']}" + (AFTER_WINDOW_TITLE if after_window else ""),
        body=f"{body}\n{AFTER_WINDOW_NOTE}" if after_window else body,
        severity=Severity.WARNING,
        correlation_id=f"backups-watch:{key}",
        payload=payload,
    )


def _age_recovered_notification(vm: dict[str, Any], key: str, last_ok: float, variant: str) -> Notification:
    host_id = vm.get("host_id")
    note = {"plain": "", "after_window": AGE_RECOVERED_AFTER_WINDOW, "in_window": AGE_RECOVERED_IN_WINDOW}[variant]
    body = f"Letzte erfolgreiche Sicherung: {_when(last_ok)}."
    payload: dict[str, Any] = {"host_id": host_id} if host_id else {}
    payload["path"] = f"{BACKUPS_PATH}?host={host_id}" if host_id else BACKUPS_PATH
    return Notification(
        title=f"Backup wieder aktuell: {vm['name']}" + (IN_WINDOW_TITLE if variant == "in_window" else ""),
        body=f"{body} {note}" if note else body,
        severity=Severity.INFO,
        correlation_id=f"backups-watch:{key}",
        payload=payload,
    )


@dataclass
class _AgeCase:
    """Eine VM im Lauf der Pruefung "zu alt". `verdict`: "good", "bad" oder None (unbekannt, nichts aendern)."""

    key: str
    conn: str
    vmid: str
    vm: dict[str, Any]
    last_ok: float
    verdict: str | None


def _limit_seconds(vm: dict[str, Any]) -> float:
    return vm["days"] * 86400 + AGE_GRACE_SECONDS


def _acts_on_bad(alerted: dict[str, Any], key: str) -> bool:
    """Wuerde "zu alt" jetzt etwas ausloesen (neue Meldung, Nachmeldung nach dem Fenster)? Eine offene, schon gehoerte
    Meldung bleibt einfach stehen."""
    return key not in alerted or _ok_pending(alerted[key]) or _is_muted(alerted[key])


async def _read_files(
    ctx: "ExtensionContext", provider: "ProxmoxBackupProvider", cache: InventoryCache, now: float, *, fresh: bool
) -> tuple[dict[tuple[str, str], float], set[str], bool, bool]:
    """Neueste Sicherungsdatei je Gast. -> (Zeitpunkte, Verbindungen mit Lesefehler, Abfrage ganz gescheitert,
    stammt aus dem Zwischenspeicher). Ein Ergebnis mit Fehlern oder eine gescheiterte Abfrage wird nie gemerkt."""
    inventory = None if fresh else cache.get(now)
    cached = inventory is not None
    if inventory is None:
        cache.clear()
        try:
            inventory = await provider.inventory()
        except Exception:  # ohne den Blick auf die Speicher wird nichts als "zu alt" gemeldet
            ctx.logger.warning("Backup-Wächter: Sicherungen auf den Speichern nicht lesbar, nichts wird als zu alt gemeldet.", exc_info=True)
            return {}, set(), True, False
        if not inventory["errors"]:
            cache.put(inventory, now)
    files = {
        (entry["connection"], str(entry["vmid"])): entry["newest_at"]
        for entry in inventory["guests"]
        if isinstance(entry.get("newest_at"), (int, float))
    }
    return files, {e["connection"] for e in inventory["errors"]}, False, cached


async def _running_without_job(
    ctx: "ExtensionContext", provider: "ProxmoxBackupProvider", now: float
) -> tuple[set[tuple[str, str]], set[str]] | None:
    """Fuer welche Gaeste (Verbindung, VMID) laeuft gerade eine Sicherung, und auf welchen Verbindungen liess sich das
    nicht lesen? `None`, wenn die Abfrage ganz scheiterte (dann meldet der Waechter vorsichtshalber nichts)."""
    try:
        answer = await provider.running_backups()
    except Exception:
        ctx.logger.warning("Backup-Wächter: laufende Sicherungen nicht lesbar, nichts wird als zu alt gemeldet.", exc_info=True)
        return None
    running = {
        (entry["connection"], str(entry["vmid"]))
        for entry in answer["running"]
        if not isinstance(entry.get("started_at"), (int, float)) or now - entry["started_at"] <= AGE_RUNNING_MAX_SECONDS
    }
    return running, {e["connection"] for e in answer["errors"]}


async def _check_age(
    ctx: "ExtensionContext",
    provider: "ProxmoxBackupProvider",
    alerted: dict[str, Any],
    jobs: list[dict[str, Any]],
    unreachable: set[str],
    inventory_cache: InventoryCache,
) -> int:
    """Pruefung "Backup zu alt" fuer alle VMs (siehe Modul-Docstring). -> Anzahl Meldungen."""
    settings = await ctx.settings.get()
    setting_days = _setting_days(settings.get("backup_max_age_days"))
    skip = _skip_names(settings.get("backup_age_skip"))
    now = _now()

    # Wer hat noch eine offene Fehlschlag-Meldung? Fuer diese VM waere "zu alt" dieselbe Stoerung.
    open_failure = {
        (job["connection"], str(job["vmid"]))
        for job in jobs
        if job["job_ref"] in alerted and not _ok_pending(alerted[job["job_ref"]])
    }

    # VMs mit Backup-Job: Name, Host, der letzte erfolgreiche Lauf aus dem Verlauf, ob gerade eine Sicherung laeuft
    # und die Frist (Einstellung oder, wenn groesser, der Abstand der Jobs).
    vms: dict[tuple[str, str], dict[str, Any]] = {}
    for job in jobs:
        vm = vms.setdefault(
            (job["connection"], str(job["vmid"])),
            {"name": job["name"], "host_id": job.get("host_id"), "task_ok": None, "running": False, "jobs": []},
        )
        vm["jobs"].append(job)
        vm["running"] = vm["running"] or _is_running(job, now)
        ok_at = job.get("last_ok_at")
        if isinstance(ok_at, (int, float)):
            vm["task_ok"] = max(vm["task_ok"] or 0, ok_at)
    for vm in vms.values():
        vm["days"] = max(setting_days, _job_gap_days(vm["jobs"]) or 0)

    # Wo wir gerade nicht lesen konnten, darf nichts als "zu alt" gelten und kein Zustand wegfallen:
    # `unsure` je Verbindung; faellt eine ganze Abfrage aus, bleibt der Zustand liegen (`keep_state`),
    # und fehlt der Blick auf die Speicher ganz, meldet auch keine Verbindung "zu alt" (`unsure_all`).
    unsure = set(unreachable)
    keep_state = False

    # VMs ohne Job: nur dabei, wenn noch Sicherungen auf einem Speicher liegen (siehe unten). Ohne Job gibt es
    # keinen Zeitplan, also gilt die Einstellung.
    jobless: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        unprotected = await provider.unprotected()
        unsure.update(e["connection"] for e in unprotected["errors"])
        for guest in unprotected["guests"]:
            if not guest.get("acknowledged"):  # "bewusst ohne Backup" gehoert nicht hierher
                jobless[(guest["connection"], str(guest["vmid"]))] = {
                    "name": guest["name"], "host_id": guest.get("host_id"), "task_ok": None, "running": False,
                    "jobs": [], "days": setting_days,
                }
    except Exception:  # die Liste ist ein Extra, die Pruefung der Jobs laeuft trotzdem
        ctx.logger.warning("Backup-Wächter: Gäste ohne Job nicht lesbar, nur Gäste mit Job werden geprüft.", exc_info=True)
        keep_state = True

    # Die Speicher brauchen wir nur, wenn es etwas gibt, das der Verlauf allein nicht klaert.
    def stale(vm: dict[str, Any]) -> bool:
        return vm["task_ok"] is None or now - vm["task_ok"] > _limit_seconds(vm)

    files: dict[tuple[str, str], float] = {}
    read_unsure: set[str] = set()
    unsure_all = cached = False
    needs_files = bool(jobless) or any(stale(vm) for (conn, vmid), vm in vms.items() if not _is_skipped(skip, conn, vmid, vm["name"]))
    if needs_files:
        files, read_unsure, unsure_all, cached = await _read_files(ctx, provider, inventory_cache, now, fresh=False)
        keep_state = keep_state or unsure_all

    def judge() -> tuple[list[_AgeCase], set[str]]:
        """Urteil je VM aus dem, was gerade gelesen ist. -> (Faelle, Schluessel aller geprueften VMs)."""
        candidates = dict(vms)
        for ident, vm in jobless.items():
            if ident not in vms and ident in files:  # ohne Job und ohne jede Sicherung: nicht melden
                candidates[ident] = vm
        cases: list[_AgeCase] = []
        evaluated: set[str] = set()
        for (conn, vmid), vm in candidates.items():
            if conn in unreachable or _is_skipped(skip, conn, vmid, vm["name"]):
                continue
            key = f"{AGE_KEY}{conn}--{vmid}"
            evaluated.add(key)
            known = [t for t in (vm["task_ok"], files.get((conn, vmid))) if t is not None]
            if not known:
                continue  # nie erfolgreich gesichert: bei einem Fehlschlag meldet das die andere Pruefung
            last_ok = max(known)
            verdict: str | None
            if now - last_ok <= _limit_seconds(vm):
                verdict = "good"
            elif unsure_all or conn in unsure or conn in read_unsure:
                verdict = None  # die Speicher waren nicht (vollstaendig) lesbar: nicht auf Verdacht melden
            elif vm["running"]:
                verdict = None  # gerade laeuft eine Sicherung: abwarten, der bisherige Zustand bleibt
            elif (conn, vmid) in open_failure and key not in alerted:
                verdict = None  # dieselbe Stoerung steht schon als Fehlschlag da
            else:
                verdict = "bad"
            cases.append(_AgeCase(key, conn, vmid, vm, last_ok, verdict))
        return cases, evaluated

    cases, evaluated = judge()
    if cached and any(c.verdict == "bad" and _acts_on_bad(alerted, c.key) for c in cases):
        # Eine neue Meldung stuetzt sich nie auf den Zwischenspeicher: inzwischen kann eine Sicherung dazugekommen sein.
        files, read_unsure, unsure_all, cached = await _read_files(ctx, provider, inventory_cache, now, fresh=True)
        keep_state = keep_state or unsure_all
        cases, evaluated = judge()

    # Ein Gast ohne Job taucht in der Task-Liste der Jobs nicht auf: laeuft gerade eine Sicherung von Hand, bevor wir
    # melden, nachsehen.
    pending = [c for c in cases if c.verdict == "bad" and not c.vm["jobs"] and _acts_on_bad(alerted, c.key)]
    if pending:
        answer = await _running_without_job(ctx, provider, now)
        for case in pending:
            if answer is None or (case.conn, case.vmid) in answer[0] or case.conn in answer[1]:
                case.verdict = None

    sent = 0
    for case in cases:
        sent += await _step(
            ctx, alerted, case.key, case.verdict, case.vm.get("host_id"), int(case.last_ok),
            lambda after_window, case=case: _age_notification(
                case.vm, case.key, case.last_ok, case.vm["days"], after_window=after_window),
            lambda variant, case=case: _age_recovered_notification(case.vm, case.key, case.last_ok, variant),
        )

    # Zustand von VMs, die nicht mehr zu pruefen sind (Job und Sicherungen weg, auf der Ausnahmeliste,
    # "bewusst ohne Backup"), wegraeumen -- nur nicht, wo wir gerade nicht lesen konnten.
    if not keep_state:
        for key in [k for k in alerted if k.startswith(AGE_KEY) and k not in evaluated]:
            if key[len(AGE_KEY):].split("--", 1)[0] not in unsure | read_unsure:
                del alerted[key]
    return sent


async def _would_alert(ctx: ExtensionContext, alerted: dict[str, Any], key: str, host_id: str | None) -> bool:
    """Ginge bei "fehlgeschlagen" jetzt eine Meldung raus (wie in `_step`)? Nur dann lohnt es, das Protokoll zu lesen."""
    if key not in alerted or _ok_pending(alerted[key]):
        return True
    return _is_muted(alerted[key]) and not await _still_silenced(ctx, host_id or _muted_host(alerted[key]))


async def _failure_cause(ctx: ExtensionContext, provider: ProxmoxBackupProvider, job_ref: str) -> dict[str, Any] | None:
    try:
        return await provider.failure_cause(job_ref)
    except Exception:  # die Ursache ist ein Extra: die Meldung geht auch ohne sie raus
        ctx.logger.warning("Backup-Wächter: Ursache des Fehlschlags nicht lesbar.", exc_info=True)
        return None


async def run_backup_watch(
    ctx: "ExtensionContext", provider: "ProxmoxBackupProvider", inventory_cache: InventoryCache | None = None
) -> dict[str, int]:
    """`inventory_cache`: der Zwischenspeicher fuer die Sicherungen auf den Speichern; wer immer denselben uebergibt (der
    Zeitplan-Job), liest sie hoechstens alle drei Stunden. Ohne ihn liest jeder Lauf neu."""
    inventory_cache = inventory_cache if inventory_cache is not None else InventoryCache(0)
    path = Path(str(ctx.data_dir)) / "watch-state.json"
    alerted: dict[str, Any] = _load(path)
    rows = await provider.list_jobs()
    # Eine nicht erreichbare Verbindung liefert nur eine Platzhalterzeile --
    # ihre Jobs sind nicht weg, nur gerade nicht lesbar.
    unreachable = {row["connection"] for row in rows if row.get("last_status") == "unreachable"}
    jobs = [row for row in rows if row.get("last_status") != "unreachable"]
    sent = 0
    for job in jobs:
        key = job["job_ref"]
        status = job.get("last_status")
        cause = None
        if status == "failed" and await _would_alert(ctx, alerted, key, job.get("host_id")):
            cause = await _failure_cause(ctx, provider, key)
        sent += await _step(
            ctx, alerted, key, "bad" if status == "failed" else "good" if status == "ok" else None,
            job.get("host_id"), job.get("last_run_at"),
            lambda after_window, job=job, key=key, cause=cause: _failed_notification(
                job, key, after_window=after_window, cause=cause),
            lambda variant, job=job, key=key: _recovered_notification(job, key, variant),
        )
    # Jobs, die es nicht mehr gibt, vergessen (sonst bliebe ihr Zustand ewig liegen) --
    # aber nur auf erreichbaren Verbindungen, sonst kaeme dieselbe Meldung erneut.
    known = {job["job_ref"] for job in jobs}
    for key in [k for k in alerted if not k.startswith(AGE_KEY) and k not in known and k.split("--", 1)[0] not in unreachable]:
        del alerted[key]
    try:
        sent += await _check_age(ctx, provider, alerted, jobs, unreachable, inventory_cache)
    except Exception:  # "zu alt" ist ein Extra: die Fehlschlag-Meldungen und der Zustand duerfen nie darunter leiden
        ctx.logger.warning("Backup-Wächter: Prüfung auf zu alte Sicherungen fehlgeschlagen.", exc_info=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(alerted, indent=1, sort_keys=True), encoding="utf-8")
    return {"checked": len(jobs), "notified": sent}
