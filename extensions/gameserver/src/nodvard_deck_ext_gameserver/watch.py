"""Gameserver-Wächter: meldet einen neuen Join-Code, einen gestoppten Dienst bei laufendem Host und seine Rückkehr."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

# Bei diesen Host-Zuständen wird der Server per SSH abgefragt; nur `down` (oder Wartung) bleibt ungefragt. `unknown` gehört
# dazu: Ein von Hand angelegter Server (ohne Proxmox) hat nie einen anderen Zustand, solange niemand „Verbindung prüfen“
# gedrückt hat, und Seite, Widget und Wächter täten für ihn nie etwas. Ist er nicht erreichbar, steht das als `error` in der
# Antwort (der Wächter meldet dann keinen Ausfall des Dienstes).
LIVE_HOST_STATUSES = frozenset({"up", "unknown"})

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Zwei Prüfungen in Folge: Ein Neustart über Nodvard Deck oder ein geplanter Neustart bleibt so still.
DOWN_STREAK = 2

AFTER_WINDOW_TITLE = " (seit dem Wartungsfenster)"
IN_WINDOW_TITLE = " (im Wartungsfenster)"
AFTER_WINDOW_NOTE = "Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) und hält noch an."


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# Wartungsfenster: Alle Meldungen tragen die Host-ID. Hat ein Fenster eine Meldung stumm geschaltet (`ctx.notify.send()` sagt
# das), merkt sich der Wächter „stumm gemeldet“ und fragt danach nur `ctx.notify.would_suppress()` (kein Verlaufseintrag je
# Prüfung). Ist das Fenster vorbei und der Zustand noch da (Dienst steht, Join-Code gilt noch), kommt die Meldung einmal
# hörbar nach. Die Entwarnung richtet sich nach dem Ausfall: War er hörbar und fällt nur „läuft wieder“ ins Fenster, kommt sie
# danach einmal hörbar nach; ein gehörter Alarm bekommt immer eine gehörte Entwarnung. Beim Join-Code wichtig: Er wechselt
# beim nächtlichen Neustart, also meist mitten im Fenster.
async def _send(ctx: "ExtensionContext", notification: Notification) -> bool:
    """True, wenn ein Wartungsfenster den Push unterdrückt hat; ältere Kerne liefern kein Ergebnis, dann gilt die Meldung
    als zugestellt."""
    result = await ctx.notify.send(notification)
    return getattr(result, "suppressed", False) is True


async def _window_over(ctx: "ExtensionContext", host_id: str) -> bool:
    """Ob das Wartungsfenster für diesen Host vorbei ist; nur fragen, nichts anlegen."""
    # Im Zweifel True: lieber einmal zu viel melden als eine Meldung verlieren.
    try:
        return await ctx.notify.would_suppress(host_id=host_id) is not True
    except Exception:  # noqa: BLE001 - Nachmeldung ist ein Extra, der Wächter muss weiterlaufen
        return True


def _join_code_notification(name: str, host_id: str, code: str, *, after_window: bool = False) -> Notification:
    body = f"Der Server hat neu gestartet und einen neuen Join-Code vergeben: {code}"
    return Notification(
        title=f"{name}: neuer Join-Code {code}" + (AFTER_WINDOW_TITLE if after_window else ""),
        body=f"{body}\nDer Neustart lag im Wartungsfenster (dort nur im Verlauf, ohne Push)." if after_window else body,
        severity=Severity.INFO,
        correlation_id=f"gameserver-joincode:{host_id}",
        payload={"host_id": host_id, "join_code": code},
    )


def _down_notification(name: str, host_id: str, service_state: Any, *, after_window: bool = False) -> Notification:
    body = f"Der Dienst ist '{service_state}', obwohl der Host läuft. Starten über die Gameserver-Seite."
    return Notification(
        title=f"{name}: Gameserver läuft nicht" + (AFTER_WINDOW_TITLE if after_window else ""),
        body=f"{body}\n{AFTER_WINDOW_NOTE}" if after_window else body,
        severity=Severity.WARNING, correlation_id=f"gameserver-down:{host_id}", payload={"host_id": host_id},
    )


def _up_notification(name: str, host_id: str, body: str, *, after_window: bool = False) -> Notification:
    return Notification(
        title=f"{name} läuft wieder" + (IN_WINDOW_TITLE if after_window else ""),
        body=f"{body}\nSchon im Wartungsfenster wieder gestartet (dort nur im Verlauf, ohne Push)." if after_window else body,
        severity=Severity.INFO, correlation_id=f"gameserver-down:{host_id}", payload={"host_id": host_id},
    )


async def run_gameserver_watch(ctx: "ExtensionContext", statuses: Any) -> dict[str, int]:
    # Der Zustand überlebt einen Neustart von Nodvard Deck: Der aktuelle Code wird nicht erneut gemeldet.
    path = Path(str(ctx.data_dir)) / "watch-state.json"
    state = _load(path)
    settings = await ctx.settings.get()
    sent = 0
    checked = 0
    for host in await ctx.hosts.list(tag=settings.get("host_tag") or "gameserver"):
        if host.status.value not in LIVE_HOST_STATUSES:
            continue  # Server aus (bzw. in Wartung): das meldet die Seite des Servers, nicht der Spiel-Dienst
        checked += 1
        data = await statuses.get(host, fresh=True)
        entry = state.setdefault(host.id, {"join_code": None, "down_streak": 0, "down_alerted": False})
        name = host.display_name or host.name

        code = data.get("join_code")
        if code and data.get("running") and code != entry.get("join_code"):
            muted = False
            # Beim allerersten Lauf wird der Code nur gemerkt: Sonst käme bei jedem frischen Setup sofort eine Meldung für einen
            # alten Code.
            if entry.get("join_code") is not None:
                muted = await _send(ctx, _join_code_notification(name, host.id, code))
                sent += 1
            entry.update(join_code=code, join_code_muted=muted)
        elif code and data.get("running") and entry.get("join_code_muted") and await _window_over(ctx, host.id):
            # Neuer Code kam im Fenster nur still in den Verlauf und gilt noch: jetzt einmal hörbar.
            entry["join_code_muted"] = await _send(ctx, _join_code_notification(name, host.id, code, after_window=True))
            sent += 1

        if data.get("error"):
            continue  # nicht erreichbar heißt nicht „Dienst gestoppt“
        if data.get("running"):
            if entry.get("down_alerted"):
                body = "Der Gameserver-Dienst ist wieder gestartet."
                if entry.get("down_muted"):
                    body += " Der Ausfall begann im Wartungsfenster (dort nur im Verlauf, ohne Push)."
                muted = await _send(ctx, _up_notification(name, host.id, body))
                # Den Ausfall hat man gehört, nur die Entwarnung fiel ins Fenster: nach dem Fenster einmal hörbar nachholen,
                # damit der Alarm nicht offen bleibt.
                entry["down_ok_pending"] = bool(muted and not entry.get("down_muted"))
                sent += 1
            elif entry.get("down_ok_pending") and await _window_over(ctx, host.id):
                muted = await _send(ctx, _up_notification(
                    name, host.id, "Der Gameserver-Dienst ist wieder gestartet.", after_window=True,
                ))
                entry["down_ok_pending"] = muted
                sent += 1
            entry.update(down_streak=0, down_alerted=False, down_muted=False)
        else:
            entry["down_streak"] = int(entry.get("down_streak") or 0) + 1
            if entry["down_streak"] >= DOWN_STREAK and not entry.get("down_alerted"):
                muted = await _send(ctx, _down_notification(name, host.id, data.get("service_state")))
                entry.update(down_alerted=True, down_muted=muted, down_ok_pending=False)
                sent += 1
            elif entry.get("down_alerted") and entry.get("down_muted") and await _window_over(ctx, host.id):
                # Im Fenster nur still gemeldet, Fenster vorbei, Dienst steht noch: jetzt einmal hörbar.
                entry["down_muted"] = await _send(
                    ctx, _down_notification(name, host.id, data.get("service_state"), after_window=True)
                )
                sent += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    return {"checked": checked, "notified": sent}
