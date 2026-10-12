"""Zustandswächter: meldet von selbst, wenn etwas kippt (Verbindung, Knoten, Datenträger, Speicher, ZFS, Temperatur) und
wenn es wieder in Ordnung ist, über das Meldungs-Center des Kerns. Gemeldet werden nur Zustandswechsel."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

from .config import build_connectors
from .connector import ProxmoxApiError
from .node_health import (
    TEMP_HYSTERESIS,
    collect_disks,
    collect_zfs,
    temperature_limit,
    temperature_setting,
)
from .storage import format_bytes, is_shared

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_log = logging.getLogger("nodvard_deck.ext.proxmox")

# Knoten, die planmäßig neu starten, sind je ~2-4 Minuten weg: Erreichbarkeit wird erst nach so vielen Fehlprüfungen in
# Folge gemeldet (bei 5-Minuten-Takt ab 10 Minuten), ein normaler Neustart bleibt still.
UNREACHABLE_STREAK = 3
DISK_STREAK = 1
# Zwei Prüfungen in Folge, damit ein kurzer Spitzenwert (etwa während ein Backup auf einen lokalen Speicher läuft) still bleibt.
STORAGE_STREAK = 2
# Die Temperatur schwankt: erst nach drei Prüfungen in Folge (10 bis 15 Minuten) melden, wieder normal erst
# `TEMP_HYSTERESIS` Grad unter der Schwelle.
DISK_TEMP_STREAK = 3
# Ein ZFS-Pool wechselt seinen Zustand nicht von selbst hin und her: sofort melden.
ZFS_STREAK = 1
# Ein fehlender Pool wird erst nach zwei Prüfungen in Folge gemeldet (kurz nach dem Start des Knotens kann er noch beim
# Einbinden sein).
ZFS_GONE_STREAK = 2
# So lange ohne einen bekannten Pool, dann gilt er als absichtlich entfernt und wird vergessen.
ZFS_FORGET_SECONDS = 7 * 86400

PAGE_PATH = "/ext/proxmox/nodes"
STORAGE_WARN_DEFAULT = 90
STORAGE_WARN_MIN = 50
STORAGE_WARN_MAX = 99
STORAGE_CRITICAL = 98  # ab hier "kritisch" statt "Warnung"
STORAGE_HYSTERESIS = 5  # so viele Punkte unter der Schwelle gilt der Speicher wieder als in Ordnung

AFTER_WINDOW_TITLE = " (seit dem Wartungsfenster)"
AFTER_WINDOW_NOTE = "Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) und hält noch an."
RECOVERED = "Wieder in Ordnung."
RECOVERED_AFTER_WINDOW = "Wieder in Ordnung. Die Störung begann im Wartungsfenster (dort nur im Verlauf, ohne Push)."
IN_WINDOW_TITLE = " (im Wartungsfenster)"
RECOVERED_IN_WINDOW = "Schon im Wartungsfenster wieder in Ordnung (dort nur im Verlauf, ohne Push)."


class AlertState:
    """Pro Schlüssel: wie oft in Folge schlecht, ob schon gemeldet wurde, ob die Meldung ein Wartungsfenster stumm
    geschaltet hat (`muted`, dazu `host`: der Host, für den das Fenster galt) und ob nach einem hörbaren Ausfall eine im
    Fenster stumme Entwarnung aussteht (`ok_pending`: Host-ID)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._data: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1, sort_keys=True), encoding="utf-8")

    def observe(self, key: str, bad: bool, threshold: int) -> str | None:
        """-> "alert" (gerade gekippt), "recovered" (war gemeldet, jetzt gut) oder None."""
        entry = self._data.setdefault(key, {"streak": 0, "alerted": False})
        if bad:
            entry["streak"] += 1
            if not entry["alerted"] and entry["streak"] >= threshold:
                entry["alerted"] = True
                entry.pop("ok_pending", None)  # neue Störung: die ausstehende Entwarnung ist überholt
                return "alert"
            return None
        was_alerted = entry["alerted"]
        if entry.get("ok_pending") and not was_alerted:
            entry["streak"] = 0  # kurzer Aussetzer unter der Schwelle: die Entwarnung steht weiter aus
            return None
        self._data.pop(key, None)
        return "recovered" if was_alerted else None

    def muted(self, key: str) -> bool:
        return bool(self._data.get(key, {}).get("muted"))

    def muted_host(self, key: str) -> str | None:
        """Host, für den das Fenster die Meldung stumm geschaltet hat. Damit bleibt die Frage "Fenster vorbei?" richtig,
        auch wenn die Host-Zuordnung gerade nicht lesbar ist, sonst kämen alle stummen Meldungen sofort hörbar, womöglich
        mitten im Fenster."""
        return self._data.get(key, {}).get("host")

    def set_muted(self, key: str, muted: bool, host_id: str | None) -> None:
        if key not in self._data:
            return
        self._data[key]["muted"] = muted
        if muted and host_id:
            self._data[key]["host"] = host_id
        else:
            self._data[key].pop("host", None)

    def alerted(self, key: str) -> bool:
        return bool(self._data.get(key, {}).get("alerted"))

    def critical(self, key: str) -> bool:
        """Die Meldung steht auf "kritisch" (Speicher-Pools und ZFS, siehe `set_critical`)."""
        return bool(self._data.get(key, {}).get("crit"))

    def set_critical(self, key: str, critical: bool) -> None:
        """Merkt sich, ob ein gemeldeter Zustand schon als "kritisch" gemeldet wurde. Nur für gemeldete Zustände, sonst gibt
        es nichts zu merken."""
        entry = self._data.get(key)
        if entry is None or not entry.get("alerted"):
            return
        if critical:
            entry["crit"] = True
        else:
            entry.pop("crit", None)

    def rearm(self, key: str, threshold: int) -> None:
        """Ein schon gemeldeter Zustand ist schlimmer geworden: die nächste schlechte Prüfung meldet ihn noch einmal
        (`observe` liefert wieder "alert")."""
        entry = self._data.get(key)
        if entry is not None and entry.get("alerted"):
            entry["alerted"] = False
            entry["streak"] = threshold - 1

    def seen(self, key: str) -> dict[str, float]:
        """Was der Wächter unter `key` zuletzt gesehen hat: Name -> Zeitpunkt (Sekunden). Kein Meldungszustand."""
        raw = self._data.get(key, {}).get("seen")
        if not isinstance(raw, dict):
            return {}
        return {str(name): float(at) for name, at in raw.items() if isinstance(at, (int, float)) and not isinstance(at, bool)}

    def set_seen(self, key: str, seen: dict[str, float]) -> None:
        if seen:
            self._data[key] = {"seen": seen}
        else:
            self._data.pop(key, None)

    def keys(self, prefix: str) -> list[str]:
        return [key for key in self._data if key.startswith(prefix)]

    def forget(self, key: str) -> None:
        self._data.pop(key, None)

    def ok_pending(self, key: str) -> str | None:
        return self._data.get(key, {}).get("ok_pending")

    def set_ok_pending(self, key: str, host_id: str | None) -> None:
        """Entwarnung im Fenster stumm, der Ausfall davor war hörbar: nach dem Fenster einmal nachholen (`host_id`).
        Mit None erledigt."""
        if host_id:
            self._data[key] = {"streak": 0, "alerted": False, "ok_pending": host_id}
        elif self._data.get(key, {}).get("ok_pending") and not self._data[key].get("alerted"):
            self._data.pop(key, None)


def _now() -> float:
    return time.time()


async def _node_host_ids(ctx: "ExtensionContext") -> dict[str, str]:
    """`provider_ref` ("<Verbindung>/node/<Knoten>") -> Host-ID der von Proxmox entdeckten Knoten. Der Kern unterdrückt
    Meldungen im Wartungsfenster nur über `payload.host_id`; ein Fehler hier darf die Meldung selbst nie verhindern, sie
    geht dann ohne Host-Bezug raus."""
    try:
        hosts = await ctx.hosts.list(tag="node")
    except Exception:  # noqa: BLE001 - Wartungsfenster sind ein Extra, der Wächter muss weitermelden
        _log.warning("Proxmox-Wächter: Knoten-Hosts nicht lesbar, Meldungen ohne Host-Bezug.", exc_info=True)
        return {}
    return {h.provider_ref: h.id for h in hosts if h.provider_ext_id == "proxmox" and h.provider_ref}


def _connection_host_id(node_hosts: dict[str, str], conn_name: str) -> str | None:
    """Host der Verbindung, nur eindeutig, wenn sie genau einen Knoten hat. Bei einem Cluster steht kein einzelner Host
    für die Verbindung, dann bleibt die Meldung ohne Host-Bezug (nie fälschlich still)."""
    ids = [host_id for ref, host_id in node_hosts.items() if ref.startswith(f"{conn_name}/node/")]
    return ids[0] if len(ids) == 1 else None


async def _send(
    ctx: "ExtensionContext", severity: Severity, title: str, body: str, key: str, host_id: str | None = None,
    path: str | None = None,
) -> bool:
    """-> True, wenn ein Wartungsfenster den Push unterdrückt hat (nur im Verlauf). Ältere Kerne liefern kein Ergebnis,
    dann gilt die Meldung als zugestellt. `path`: Seite, die ein Tipp auf die Meldung öffnet (Konvention im `payload`)."""
    payload: dict[str, Any] = {"host_id": host_id} if host_id else {}
    if path:
        payload["path"] = path
    result = await ctx.notify.send(Notification(
        title=title, body=body, severity=severity, correlation_id=f"proxmox-watch:{key}", payload=payload,
    ))
    return getattr(result, "suppressed", False) is True


async def _still_silenced(ctx: "ExtensionContext", host_id: str | None) -> bool:
    """Läuft das Wartungsfenster für diesen Host noch? Nur fragen (`would_suppress`), nichts anlegen, damit nicht je Prüfung
    ein Verlaufseintrag entsteht. Im Zweifel (Fehler) False: lieber einmal zu viel melden als eine Meldung verlieren."""
    if not host_id:
        return False
    try:
        return await ctx.notify.would_suppress(host_id=host_id) is True
    except Exception:  # noqa: BLE001 - Nachmeldung ist ein Extra, der Wächter muss weiterlaufen
        _log.warning("Proxmox-Wächter: Wartungsfenster nicht prüfbar, Meldung kommt hörbar.", exc_info=True)
        return False


def _whole(value: float) -> int:
    """Kaufmännisch gerundet, denn die Zahl in der Meldung entscheidet auch."""
    return int(value + 0.5)


def _setting_int(raw: Any, default: int, low: int, high: int) -> int:
    """Ganze Zahl im erlaubten Bereich; alles andere (Text, Bruch, außerhalb, nichts) gilt als nicht gesetzt und ergibt
    den Standard, denn ein falscher Wert darf den Wächter nie lahmlegen."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    try:
        number = int(raw)
    except (OverflowError, ValueError):  # unendlich oder keine Zahl
        return default
    return number if number == raw and low <= number <= high else default


def _skip_names(raw: Any) -> set[str]:
    """Namen aus `storage_skip` (klein geschrieben): diese Speicher sind ganz ausgenommen, z. B. eine absichtlich volle ISO-Ablage."""
    if not isinstance(raw, list):
        return set()
    return {item.strip().lower() for item in raw if isinstance(item, str) and item.strip()}


def _pool_reading(pool: dict[str, Any]) -> tuple[int, float, float] | None:
    """(ganze Prozent, belegt, gesamt) oder None, wenn der Speicher gerade nichts Verlässliches sagt: nicht aktiv,
    abgeschaltet oder ohne Größe (ein nicht erreichbares NFS-Ziel meldet keine Zahlen). Solche Speicher gelten als
    "unbekannt", nie als "voll"."""
    for field in ("active", "enabled"):
        if str(pool.get(field, 1)).strip().lower() in ("0", "false", "none", ""):
            return None
    try:
        total, used = float(pool.get("total")), float(pool.get("used"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(total) and math.isfinite(used)) or total <= 0 or used < 0:
        return None
    return _whole(100.0 * used / total), used, total


def _pool_alert(
    name: str, where: str, percent: int, used: float, total: float, warn: int
) -> tuple[Severity, str, str]:
    free = format_bytes(max(total - used, 0))
    detail = f"{format_bytes(used)} von {format_bytes(total)} belegt, noch {free} frei. Warnung ab {warn} %."
    if percent >= STORAGE_CRITICAL:
        return (
            Severity.CRITICAL, f"Speicher {name} {where} kritisch voll: {percent} %",
            f"{detail} Ist er ganz voll, schlagen Schreibvorgänge fehl (VMs, Backups). Schaffe jetzt Platz.",
        )
    return (
        Severity.WARNING, f"Speicher {name} {where} fast voll: {percent} %",
        f"{detail} Auf der Proxmox-Seite siehst du, was den Platz belegt.",
    )


async def run_watch(ctx: "ExtensionContext") -> dict[str, int]:
    # Der Zustand liegt in einer Datei, damit ein Deploy oder Neustart von Nodvard Deck keine Meldungsflut auslöst.
    state = AlertState(Path(str(ctx.data_dir)) / "watch-state.json")
    settings = await ctx.settings.get()
    storage_warn = _setting_int(settings.get("storage_warn_percent"), STORAGE_WARN_DEFAULT, STORAGE_WARN_MIN, STORAGE_WARN_MAX)
    storage_skip = _skip_names(settings.get("storage_skip"))
    temp_setting = temperature_setting(settings.get("disk_temp_warn_c"))
    now = _now()
    sent = 0
    checked = 0
    node_hosts = await _node_host_ids(ctx)

    async def handle(
        key: str, bad: bool, threshold: int, alert: tuple[Severity, str, str], ok_title: str, host_id: str | None,
        late_body: str | None = None, ok_note: str | None = None, path: str | None = None,
    ) -> None:
        """Verbucht eine Prüfung und meldet den Wechsel. Fällt eine Meldung in ein Wartungsfenster, ist sie stumm (nur im
        Verlauf) und kommt nach dem Fenster einmal hörbar nach, wenn der Zustand noch besteht. `late_body`: Text dafür,
        `ok_note`: Zusatz zur Entwarnung, `path`: Seite hinter der Meldung."""
        nonlocal sent, checked
        note = f" {ok_note}" if ok_note else ""
        checked += 1
        was_muted = state.muted(key)
        # Host der stummen Meldung bzw. der ausstehenden Entwarnung, vor observe(), das den Eintrag bei "wieder gut" entfernt.
        scope = host_id or state.muted_host(key)
        pending_ok = state.ok_pending(key)
        change = state.observe(key, bad, threshold)
        if change == "alert":
            state.set_muted(key, await _send(ctx, alert[0], alert[1], alert[2], key, host_id, path), host_id)
            sent += 1
        elif change == "recovered":
            muted = await _send(
                ctx, Severity.INFO, ok_title, (RECOVERED_AFTER_WINDOW if was_muted else RECOVERED) + note, key, scope, path,
            )
            if muted and not was_muted:
                # Den Ausfall hat man gehört, nur die Entwarnung fiel ins Fenster: nach dem Fenster einmal hörbar nachholen,
                # damit der Alarm nicht offen bleibt.
                state.set_ok_pending(key, scope)
            sent += 1
        elif bad and was_muted and not await _still_silenced(ctx, state.muted_host(key) or host_id):
            # Im Wartungsfenster nur still gemeldet, Fenster vorbei, Zustand noch da: jetzt einmal hörbar. Fällt auch das in
            # ein Fenster, bleibt es "stumm".
            state.set_muted(key, await _send(
                ctx, alert[0], alert[1] + AFTER_WINDOW_TITLE, f"{late_body or alert[2]}\n{AFTER_WINDOW_NOTE}", key, scope, path,
            ), scope)
            sent += 1
        elif not bad and pending_ok and not await _still_silenced(ctx, pending_ok):
            # Entwarnung aus dem Fenster, Zustand weiter gut: jetzt einmal hörbar.
            muted = await _send(
                ctx, Severity.INFO, ok_title + IN_WINDOW_TITLE, RECOVERED_IN_WINDOW + note, key, host_id or pending_ok, path,
            )
            state.set_ok_pending(key, pending_ok if muted else None)
            sent += 1

    async def check_pool(
        conn_name: str, node_name: str, node_host_id: str | None, key: str, name: str, shared: bool,
        reading: tuple[int, float, float],
    ) -> None:
        percent, used, total = reading
        where = f"(geteilt, {conn_name})" if shared else f"auf {node_name}"
        # Einmal gemeldet, gilt der Speicher erst als in Ordnung, wenn er STORAGE_HYSTERESIS Punkte unter der Schwelle liegt
        # (sonst Meldungsflut um die Grenze). Dazwischen bleibt er "schlecht", ohne neue Meldung.
        was_alerted = state.alerted(key)
        bad = percent >= storage_warn or (was_alerted and percent > storage_warn - STORAGE_HYSTERESIS)
        was_critical = state.critical(key)
        critical = percent >= STORAGE_CRITICAL or (was_critical and percent > STORAGE_CRITICAL - STORAGE_HYSTERESIS)
        if was_alerted and critical and not was_critical:
            state.rearm(key, STORAGE_STREAK)  # schon gemeldet, jetzt kritisch: noch einmal, diesmal als "kritisch"
        await handle(
            key, bad, STORAGE_STREAK, _pool_alert(name, where, percent, used, total, storage_warn),
            f"Speicher {name} {where} wieder im grünen Bereich: {percent} %", node_host_id,
            ok_note=f"Nur noch zu {percent} % belegt (Warnung ab {storage_warn} %).",
            path=f"{PAGE_PATH}?tab=storage&host={node_host_id}" if node_host_id else f"{PAGE_PATH}?tab=storage",
        )
        state.set_critical(key, critical)

    def node_path(node_host_id: str | None) -> str:
        return f"{PAGE_PATH}?host={node_host_id}" if node_host_id else PAGE_PATH

    async def check_temperature(conn_name: str, node_name: str, node_host_id: str | None, disk: dict[str, Any]) -> None:
        temp = disk.get("temperature_c")
        if not isinstance(temp, int) or isinstance(temp, bool):
            return  # kein Messwert: nichts ändert sich
        key = f"disktemp:{conn_name}/{node_name}/{disk['devpath']}"
        limit = temperature_limit(disk.get("type"), temp_setting)
        bad = temp >= limit or (state.alerted(key) and temp > limit - TEMP_HYSTERESIS)
        await handle(
            key, bad, DISK_TEMP_STREAK,
            (Severity.WARNING, f"Datenträger auf {node_name} zu heiß: {temp} °C",
             (f"{disk['model']} ({disk['devpath']}) ist {temp} °C warm (Warnung ab {limit} °C). Lüftung und Staub prüfen – "
              "dauerhaft zu heiß verkürzt die Lebensdauer.")),
            f"Datenträger auf {node_name} wieder normal warm: {temp} °C", node_host_id,
            ok_note=f"Jetzt {temp} °C (Warnung ab {limit} °C).", path=node_path(node_host_id),
        )

    async def check_zfs(conn_name: str, node_name: str, node_host_id: str | None, pool: dict[str, Any]) -> None:
        if pool.get("health") is None:
            return  # Zustand nicht lesbar: weder Meldung noch Entwarnung
        key = f"zfs:{conn_name}/{node_name}/{pool['name']}"
        critical = pool["tone"] == "danger"
        if state.alerted(key) and critical and not state.critical(key):
            state.rearm(key, ZFS_STREAK)  # schon gemeldet, jetzt ausgefallen: noch einmal, diesmal als "kritisch"
        where = (f"Welche Platte betroffen ist, steht in Proxmox unter dem Knoten → Disks → ZFS "
                 f"(oder `zpool status {pool['name']}` auf dem Knoten).")
        advice = (
            "Der Pool liefert gerade keine verlässlichen Daten. Platten und Kabel prüfen, bevor du etwas neu startest, "
            "und die Sicherungen bereithalten." if critical
            else "Daten sichern und die defekte Platte bald tauschen."
        )
        await handle(
            key, pool["health"] != "ONLINE", ZFS_STREAK,
            (Severity.CRITICAL if critical else Severity.WARNING, f"ZFS-Pool {pool['name']} auf {node_name}: {pool['badge']}",
             f"Der Pool ist {pool['summary']}. {where} {advice}"),
            f"ZFS-Pool {pool['name']} auf {node_name} wieder ONLINE", node_host_id, path=node_path(node_host_id),
        )
        state.set_critical(key, critical)

    async def check_zfs_gone(conn_name: str, node_name: str, node_host_id: str | None, present: set[str]) -> None:
        """Pools, die der Wächter auf diesem Knoten schon gesehen hat und die jetzt fehlen (nicht eingebunden, Platten weg).
        Nur nach einer erfolgreichen Abfrage aufrufen: eine gescheiterte Abfrage und ein Knoten offline ändern nichts;
        nach `ZFS_FORGET_SECONDS` gilt ein fehlender Pool als absichtlich entfernt."""
        seen_key = f"zfsseen:{conn_name}/{node_name}"
        seen = state.seen(seen_key)
        for name in present:
            seen[name] = now
        for name in sorted(seen):
            key = f"zfsgone:{conn_name}/{node_name}/{name}"
            gone = name not in present
            if gone and now - seen[name] > ZFS_FORGET_SECONDS:
                del seen[name]  # lange weg: absichtlich entfernt
                state.forget(key)
                state.forget(f"zfs:{conn_name}/{node_name}/{name}")
                continue
            await handle(
                key, gone, ZFS_GONE_STREAK,
                (Severity.CRITICAL, f"ZFS-Pool {name} auf {node_name} fehlt",
                 (f"Proxmox listet den Pool {name} nicht mehr – nach einem Neustart nicht eingebunden, oder die Platten "
                  "sind weg. Was darauf liegt, ist gerade nicht erreichbar. In Proxmox unter dem Knoten → Disks → ZFS "
                  "nachsehen (oder `zpool import` auf dem Knoten). Hast du ihn absichtlich entfernt, vergisst Nodvard "
                  "Deck ihn nach 7 Tagen.")),
                f"ZFS-Pool {name} auf {node_name} wieder da", node_host_id, path=node_path(node_host_id),
            )
        state.set_seen(seen_key, seen)

    for conn_name, connector in (await build_connectors(ctx)).items():
        try:
            nodes = await connector.list_nodes()
            conn_error = None
        except ProxmoxApiError as exc:
            nodes, conn_error = [], str(exc)
        pools_complete = conn_error is None  # nur dann ist "Speicher nicht mehr da" verlässlich
        pools_seen: set[str] = set()
        shared_done: set[str] = set()
        await handle(
            f"conn:{conn_name}", conn_error is not None, UNREACHABLE_STREAK,
            (Severity.CRITICAL, f"Proxmox '{conn_name}' nicht erreichbar",
             f"Seit mindestens {UNREACHABLE_STREAK} Prüfungen (~{(UNREACHABLE_STREAK - 1) * 5} Minuten) keine Antwort: {conn_error}"),
            f"Proxmox '{conn_name}' wieder erreichbar",
            _connection_host_id(node_hosts, conn_name),
            late_body=f"Keine Antwort: {conn_error}",
        )
        for node in nodes:
            node_name = node.get("node")
            if not node_name:
                continue
            online = node.get("status") in (None, "online")
            node_host_id = node_hosts.get(f"{conn_name}/node/{node_name}")
            await handle(
                f"node:{conn_name}/{node_name}", not online, UNREACHABLE_STREAK,
                (Severity.CRITICAL, f"Knoten {node_name} offline", f"Proxmox meldet den Knoten {node_name} ({conn_name}) als '{node.get('status')}'."),
                f"Knoten {node_name} wieder online", node_host_id,
            )
            if not online:
                pools_complete = False
                continue
            try:
                raw_pools = await connector.node_storage(node_name)
            except ProxmoxApiError:
                raw_pools = []  # die Speicher-Abfrage allein ist kein Alarmgrund
                pools_complete = False
            for pool in raw_pools:
                name = str(pool.get("storage") or "")
                if not name or name.lower() in storage_skip:
                    continue
                shared = is_shared(pool)
                pool_key = f"storage:{conn_name}/{name}" if shared else f"storage:{conn_name}/{node_name}/{name}"
                pools_seen.add(pool_key)
                reading = _pool_reading(pool)
                if reading is None or pool_key in shared_done:
                    continue
                if shared:
                    shared_done.add(pool_key)  # derselbe Speicher steht bei jedem Knoten, nur einmal zählen
                await check_pool(conn_name, node_name, node_host_id, pool_key, name, shared, reading)
            try:
                disks = await collect_disks(connector, conn_name, node_name, temp_setting)
            except ProxmoxApiError:
                disks = []  # Datenträger-Abfrage allein ist kein Alarmgrund
            for disk in disks:
                # Temperatur nicht hier: sie schwankt und hat eine eigene Prüfung mit Abstand (`check_temperature`).
                bad = disk["tone"] in ("danger", "warn") and not disk["badge"].endswith("°C")
                await handle(
                    f"disk:{conn_name}/{node_name}/{disk['devpath']}", bad, DISK_STREAK,
                    (Severity.CRITICAL if disk["tone"] == "danger" else Severity.WARNING,
                     f"Datenträger auf {node_name}: {disk['badge']}",
                     f"{disk['model']} ({disk['devpath']}) -- {disk['summary']}. Einzige Platte des Knotens? Dann Backup prüfen."),
                    f"Datenträger auf {node_name} wieder unauffällig", node_host_id,
                )
                await check_temperature(conn_name, node_name, node_host_id, disk)
            try:
                pools = await collect_zfs(connector, node_name)
            except ProxmoxApiError:
                continue  # die ZFS-Abfrage allein ist kein Alarmgrund, und ohne sie fehlt kein Pool
            for pool in pools:
                await check_zfs(conn_name, node_name, node_host_id, pool)
            await check_zfs_gone(conn_name, node_name, node_host_id, {pool["name"] for pool in pools})
        if pools_complete:
            # Gibt es einen Speicher nicht mehr (oder steht er jetzt auf der Ausnahmeliste), fällt sein Zustand weg, aber nur,
            # wenn alle Knoten der Verbindung geantwortet haben.
            for stale in [k for k in state.keys(f"storage:{conn_name}/") if k not in pools_seen]:
                state.forget(stale)
    state.save()
    return {"checked": checked, "notified": sent}
