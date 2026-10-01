"""Zustandswaechter: meldet von selbst, wenn etwas kippt --
Verbindung/Knoten nicht erreichbar, Datentraeger mit SMART-Befund oder fast
verschlissen -- und wenn es wieder in Ordnung ist. Landet im Kern-Benachrichtigungs-
Center ("Meldungen") und von dort in jedem aktiven Benachrichtigungskanal.

Nur Zustandswechsel werden gemeldet, nie derselbe Zustand alle 5 Minuten erneut. Der
Zustand liegt in `data_dir/watch-state.json`, damit ein Deploy/Neustart von Nodvard
Deck keine Meldungsflut ausloest.

Entprellung: Knoten, die planmaessig neu starten, sind jeweils ~2-4 Minuten weg.
Erreichbarkeit wird deshalb erst nach
`UNREACHABLE_STREAK` aufeinanderfolgenden Fehlpruefungen (bei 5-Minuten-Takt also
>= 10 Minuten) gemeldet -- ein normaler Neustart bleibt still.

Wartungsfenster: die Meldungen tragen die Host-ID des Knotens, ein Fenster des Kerns
stellt sie dann stumm (der Verlaufseintrag bleibt). `ctx.notify.send()` sagt, ob das
passiert ist; dann steht der Zustand auf "stumm gemeldet". Bei den folgenden Pruefungen
fragt der Waechter nur `ctx.notify.would_suppress()` (legt nichts an, also kein
Verlaufseintrag je Pruefung). Ist das Fenster vorbei und der Zustand noch schlecht, kommt
die Meldung einmal hoerbar nach ("... (seit dem Wartungsfenster)"). Ist er schon im
Fenster wieder gut, kommt nichts nach; die Entwarnung laeuft wie jede Meldung durch die
Fensterpruefung. Die Entwarnung richtet sich nach der Ausfallmeldung: War der Ausfall
hoerbar und faellt nur die Entwarnung ins Fenster, kommt sie nach dem Fenster einmal
hoerbar nach ("... (im Wartungsfenster)") -- einen gehoerten Alarm schliesst immer eine
gehoerte Entwarnung, aber nicht mitten in der Nacht (docs/02-EXTENSION-API.md §2,
"Meldungen und Wartungsfenster").
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

from .config import build_connectors
from .connector import ProxmoxApiError
from .node_health import collect_disks

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_log = logging.getLogger("nodvard_deck.ext.proxmox")

UNREACHABLE_STREAK = 3
DISK_STREAK = 1

AFTER_WINDOW_TITLE = " (seit dem Wartungsfenster)"
AFTER_WINDOW_NOTE = "Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) und hält noch an."
RECOVERED = "Wieder in Ordnung."
RECOVERED_AFTER_WINDOW = "Wieder in Ordnung. Die Störung begann im Wartungsfenster (dort nur im Verlauf, ohne Push)."
IN_WINDOW_TITLE = " (im Wartungsfenster)"
RECOVERED_IN_WINDOW = "Schon im Wartungsfenster wieder in Ordnung (dort nur im Verlauf, ohne Push)."


class AlertState:
    """Pro Schluessel: wie oft in Folge schlecht, ob schon gemeldet wurde, ob diese
    Meldung ein Wartungsfenster stumm geschaltet hat (`muted`, dazu `host`: der Host, fuer
    den das Fenster galt) und ob nach einem hoerbaren Ausfall eine im Fenster stumme
    Entwarnung aussteht (`ok_pending`: Host-ID)."""

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
                entry.pop("ok_pending", None)  # neue Stoerung: die ausstehende Entwarnung ist ueberholt
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
        """Host, fuer den das Fenster die Meldung stumm geschaltet hat. Damit bleibt die
        Frage "Fenster vorbei?" richtig, auch wenn die Host-Zuordnung gerade nicht lesbar
        ist -- sonst kaemen alle stummen Meldungen sofort hoerbar, womoeglich mitten im
        Fenster."""
        return self._data.get(key, {}).get("host")

    def set_muted(self, key: str, muted: bool, host_id: str | None) -> None:
        if key not in self._data:
            return
        self._data[key]["muted"] = muted
        if muted and host_id:
            self._data[key]["host"] = host_id
        else:
            self._data[key].pop("host", None)

    def ok_pending(self, key: str) -> str | None:
        return self._data.get(key, {}).get("ok_pending")

    def set_ok_pending(self, key: str, host_id: str | None) -> None:
        """Entwarnung im Fenster stumm, der Ausfall davor war hoerbar: nach dem Fenster
        einmal nachholen (`host_id`). Mit None erledigt."""
        if host_id:
            self._data[key] = {"streak": 0, "alerted": False, "ok_pending": host_id}
        elif self._data.get(key, {}).get("ok_pending") and not self._data[key].get("alerted"):
            self._data.pop(key, None)


async def _node_host_ids(ctx: "ExtensionContext") -> dict[str, str]:
    """`provider_ref` ("<Verbindung>/node/<Knoten>") -> Host-ID der von Proxmox
    entdeckten Knoten. Der Kern unterdrueckt Meldungen im Wartungsfenster nur ueber
    `payload.host_id` -- ohne den Host blieben Knoten-/Verbindungsmeldungen beim
    nächtlichen Neustart trotz Fenster hörbar. Ein Fehler hier darf die Meldung selbst
    nie verhindern: dann geht sie eben ohne Host-Bezug raus."""
    try:
        hosts = await ctx.hosts.list(tag="node")
    except Exception:  # noqa: BLE001 - Wartungsfenster sind ein Extra, der Waechter muss weitermelden
        _log.warning("Proxmox-Wächter: Knoten-Hosts nicht lesbar, Meldungen ohne Host-Bezug.", exc_info=True)
        return {}
    return {h.provider_ref: h.id for h in hosts if h.provider_ext_id == "proxmox" and h.provider_ref}


def _connection_host_id(node_hosts: dict[str, str], conn_name: str) -> str | None:
    """Host der Verbindung -- nur eindeutig, wenn sie genau einen Knoten hat (pve1 und
    pve2 sind je eine eigene Verbindung). Bei einem Cluster steht kein einzelner Host
    fuer die Verbindung, dann bleibt die Meldung ohne Host-Bezug (nie faelschlich still)."""
    ids = [host_id for ref, host_id in node_hosts.items() if ref.startswith(f"{conn_name}/node/")]
    return ids[0] if len(ids) == 1 else None


async def _send(
    ctx: "ExtensionContext", severity: Severity, title: str, body: str, key: str, host_id: str | None = None
) -> bool:
    """-> True, wenn ein Wartungsfenster den Push unterdrueckt hat (nur im Verlauf).
    Aeltere Kerne liefern kein Ergebnis -- dann gilt die Meldung als zugestellt."""
    result = await ctx.notify.send(Notification(
        title=title, body=body, severity=severity, correlation_id=f"proxmox-watch:{key}",
        payload={"host_id": host_id} if host_id else {},
    ))
    return getattr(result, "suppressed", False) is True


async def _still_silenced(ctx: "ExtensionContext", host_id: str | None) -> bool:
    """Laeuft das Wartungsfenster fuer diesen Host noch? Nur fragen, nichts anlegen. Im
    Zweifel (Fehler) False: lieber einmal zu viel melden als eine Meldung verlieren."""
    if not host_id:
        return False
    try:
        return await ctx.notify.would_suppress(host_id=host_id) is True
    except Exception:  # noqa: BLE001 - Nachmeldung ist ein Extra, der Waechter muss weiterlaufen
        _log.warning("Proxmox-Wächter: Wartungsfenster nicht prüfbar, Meldung kommt hörbar.", exc_info=True)
        return False


async def run_watch(ctx: "ExtensionContext") -> dict[str, int]:
    state = AlertState(Path(str(ctx.data_dir)) / "watch-state.json")
    sent = 0
    checked = 0
    node_hosts = await _node_host_ids(ctx)

    async def handle(
        key: str, bad: bool, threshold: int, alert: tuple[Severity, str, str], ok_title: str, host_id: str | None,
        late_body: str | None = None,
    ) -> None:
        """`late_body`: Text fuer die Nachmeldung nach dem Fenster, wenn der normale Text
        dafuer nicht passt (z. B. "~10 Minuten" nach einem einstuendigen Fenster)."""
        nonlocal sent, checked
        checked += 1
        was_muted = state.muted(key)
        # Host der stummen Meldung bzw. der ausstehenden Entwarnung -- vor observe(), das
        # den Eintrag bei "wieder gut" entfernt.
        scope = host_id or state.muted_host(key)
        pending_ok = state.ok_pending(key)
        change = state.observe(key, bad, threshold)
        if change == "alert":
            state.set_muted(key, await _send(ctx, alert[0], alert[1], alert[2], key, host_id), host_id)
            sent += 1
        elif change == "recovered":
            muted = await _send(ctx, Severity.INFO, ok_title, RECOVERED_AFTER_WINDOW if was_muted else RECOVERED, key, scope)
            if muted and not was_muted:
                # Den Ausfall hat man gehoert, nur die Entwarnung fiel ins Fenster: nach dem
                # Fenster einmal hoerbar nachholen, damit der Alarm nicht offen bleibt.
                state.set_ok_pending(key, scope)
            sent += 1
        elif bad and was_muted and not await _still_silenced(ctx, state.muted_host(key) or host_id):
            # Im Wartungsfenster nur still gemeldet, Fenster vorbei, Zustand noch da:
            # jetzt einmal hoerbar. Faellt auch das in ein Fenster, bleibt es "stumm".
            state.set_muted(key, await _send(
                ctx, alert[0], alert[1] + AFTER_WINDOW_TITLE, f"{late_body or alert[2]}\n{AFTER_WINDOW_NOTE}", key, scope,
            ), scope)
            sent += 1
        elif not bad and pending_ok and not await _still_silenced(ctx, pending_ok):
            # Entwarnung aus dem Fenster, Zustand weiter gut: jetzt einmal hoerbar.
            muted = await _send(ctx, Severity.INFO, ok_title + IN_WINDOW_TITLE, RECOVERED_IN_WINDOW, key, host_id or pending_ok)
            state.set_ok_pending(key, pending_ok if muted else None)
            sent += 1

    for conn_name, connector in (await build_connectors(ctx)).items():
        try:
            nodes = await connector.list_nodes()
            conn_error = None
        except ProxmoxApiError as exc:
            nodes, conn_error = [], str(exc)
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
                continue
            try:
                disks = await collect_disks(connector, conn_name, node_name)
            except ProxmoxApiError:
                continue  # Datentraeger-Abfrage allein ist kein Alarmgrund
            for disk in disks:
                # Temperatur bewusst NICHT: schwankt, wuerde flattern.
                bad = disk["tone"] in ("danger", "warn") and not disk["badge"].endswith("°C")
                await handle(
                    f"disk:{conn_name}/{node_name}/{disk['devpath']}", bad, DISK_STREAK,
                    (Severity.CRITICAL if disk["tone"] == "danger" else Severity.WARNING,
                     f"Datenträger auf {node_name}: {disk['badge']}",
                     f"{disk['model']} ({disk['devpath']}) -- {disk['summary']}. Einzige Platte des Knotens? Dann Backup prüfen."),
                    f"Datenträger auf {node_name} wieder unauffällig", node_host_id,
                )
    state.save()
    return {"checked": checked, "notified": sent}
