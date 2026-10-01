"""Backup-Waechter: meldet von selbst, wenn ein Backup fehlschlaegt -- und wenn derselbe
Job danach wieder erfolgreich lief. Landet im Kern-Benachrichtigungs-Center
("Meldungen").

Nur Zustandswechsel, nie derselbe Fehlschlag bei jeder Pruefung erneut. Der Zustand liegt
in `data_dir/watch-state.json` (ein Neustart von Nodvard Deck loest keine Meldungsflut aus).
"Laeuft" und "unbekannt" aendern nichts: ein laufender Wiederholungsversuch ist noch kein
"wieder in Ordnung".

Hintergrund: ein fehlschlagender VZDump-Job (z. B. "Broken pipe" zum NFS-Ziel) kann
wochenlang unbemerkt bleiben, wenn niemand aktiv nachschaut.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

    from .capabilities import ProxmoxBackupProvider

AFTER_WINDOW_TITLE = " (seit dem Wartungsfenster)"
AFTER_WINDOW_NOTE = "Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) und hält noch an."
IN_WINDOW_TITLE = " (im Wartungsfenster)"
RECOVERED_IN_WINDOW = "Schon im Wartungsfenster wieder erfolgreich (dort nur im Verlauf, ohne Push)."
RECOVERED_AFTER_WINDOW = "Der Fehlschlag lag im Wartungsfenster (dort nur im Verlauf, ohne Push)."


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


def _failed_notification(job: dict[str, Any], key: str, *, after_window: bool = False) -> Notification:
    host_id = job.get("host_id")
    body = (
        f"Letzter Lauf {_when(job.get('last_run_at'))} auf {job.get('node') or '?'}, "
        f"Ziel {job.get('storage') or '?'} ({job['connection']}). "
        "Details und 'Erneut versuchen' auf der Backup-Seite."
    )
    return Notification(
        title=f"Backup fehlgeschlagen: {job['name']}" + (AFTER_WINDOW_TITLE if after_window else ""),
        body=f"{body}\n{AFTER_WINDOW_NOTE}" if after_window else body,
        severity=Severity.CRITICAL,
        correlation_id=f"backups-watch:{key}",
        payload={"host_id": host_id} if host_id else {},
    )


def _recovered_notification(job: dict[str, Any], key: str, note: str, *, in_window: bool = False) -> Notification:
    host_id = job.get("host_id")
    body = f"Letzter Lauf {_when(job.get('last_run_at'))}."
    return Notification(
        title=f"Backup wieder erfolgreich: {job['name']}" + (IN_WINDOW_TITLE if in_window else ""),
        body=f"{body} {note}" if note else body,
        severity=Severity.INFO,
        correlation_id=f"backups-watch:{key}",
        # Wie der Fehlschlag zum Host, damit das Fenster beide gleich behandelt (docs/03 §9).
        payload={"host_id": host_id} if host_id else {},
    )


async def run_backup_watch(ctx: "ExtensionContext", provider: "ProxmoxBackupProvider") -> dict[str, int]:
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
        host_id = job.get("host_id")
        if status == "failed" and (key not in alerted or _ok_pending(alerted[key])):
            # Neuer Fehlschlag (auch nach einer noch ausstehenden Entwarnung: die ist ueberholt)
            alerted[key] = _remember(job.get("last_run_at"), await _send(ctx, _failed_notification(job, key)), host_id)
            sent += 1
        elif status == "failed" and _is_muted(alerted[key]) and not await _still_silenced(
            ctx, host_id or _muted_host(alerted[key])
        ):
            # Im Wartungsfenster nur still gemeldet, Fenster vorbei, noch fehlgeschlagen:
            # jetzt einmal hoerbar. Faellt auch das in ein Fenster, bleibt es "stumm".
            scope = host_id or _muted_host(alerted[key])
            muted = await _send(ctx, _failed_notification(job, key, after_window=True))
            alerted[key] = _remember(job.get("last_run_at"), muted, scope)
            sent += 1
        elif status == "ok" and _ok_pending(alerted.get(key)):
            # Entwarnung aus dem Fenster, Job weiter in Ordnung: jetzt einmal hoerbar.
            if not await _still_silenced(ctx, host_id or _muted_host(alerted[key])):
                muted = await _send(ctx, _recovered_notification(job, key, RECOVERED_IN_WINDOW, in_window=True))
                if muted:
                    alerted[key] = {"ok_pending": True, "host": host_id or _muted_host(alerted[key])}
                else:
                    del alerted[key]
                sent += 1
        elif status == "ok" and key in alerted:
            was_muted = _is_muted(alerted[key])
            muted = await _send(ctx, _recovered_notification(
                job, key, RECOVERED_AFTER_WINDOW if was_muted else "",
            ))
            if muted and not was_muted:
                # Den Fehlschlag hat man gehoert, nur die Entwarnung fiel ins Fenster: nach
                # dem Fenster einmal hoerbar nachholen, damit der Alarm nicht offen bleibt.
                alerted[key] = {"ok_pending": True, **({"host": host_id} if host_id else {})}
            else:
                del alerted[key]
            sent += 1
    # Jobs, die es nicht mehr gibt, vergessen (sonst bliebe ihr Zustand ewig liegen) --
    # aber nur auf erreichbaren Verbindungen, sonst kaeme dieselbe Meldung erneut.
    known = {job["job_ref"] for job in jobs}
    for key in [k for k in alerted if k not in known and k.split("--", 1)[0] not in unreachable]:
        del alerted[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(alerted, indent=1, sort_keys=True), encoding="utf-8")
    return {"checked": len(jobs), "notified": sent}
