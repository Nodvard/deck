"""Taegliche Pruefung: laeuft ein Zertifikat im Nginx Proxy Manager in den naechsten
14 Tagen ab (oder ist schon abgelaufen), gibt es eine Meldung -- hoechstens EINE je
Zertifikat und Tag, auch wenn der Job manuell oder nach einem Neustart erneut laeuft.
Der Stand liegt in `data_dir/cert-notified.json` (`{zertifikat-id: "JJJJ-MM-TT"}`).

Normalerweise erneuert NPM Let's-Encrypt-Zertifikate selbst ~30 Tage vor Ablauf --
eine Meldung hier heisst also meist, dass die Erneuerung klemmt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from nodvard_sdk import Notification, Severity

from .widgets import date_text

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

    from .service import NetworkService

STATE_FILE = "cert-notified.json"
SCHEDULE = "20 7 * * *"
PAGE_PATH = "/ext/network/network"


def _load(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


async def run_cert_watch(ctx: ExtensionContext, service: NetworkService, *, now: datetime | None = None) -> dict[str, Any]:
    config = await service.npm_settings()
    if config is None or not config.notify_expiring:
        return {"checked": 0, "notified": 0, "skipped": "nicht eingerichtet" if config is None else "abgeschaltet"}
    moment = now or datetime.now(timezone.utc)
    status = await service.npm_status(now=moment)
    if status["state"] != "ok":
        return {"checked": 0, "notified": 0, "skipped": status["message"]}

    path = Path(str(ctx.data_dir)) / STATE_FILE
    notified = _load(path)
    today = moment.date().isoformat()
    sent = 0
    urgent_ids: set[str] = set()
    for cert in status["certificates"]:
        if cert["status"] not in ("warn", "expired"):
            continue
        key = str(cert["id"])
        urgent_ids.add(key)
        if notified.get(key) == today:
            continue
        expired = cert["status"] == "expired"
        domains = ", ".join(cert["domains"]) or cert["name"]
        when = date_text(cert["expires_at"])
        body = (
            f"Das Zertifikat für {domains} ist am {when} abgelaufen – Besucher sehen eine Sicherheitswarnung."
            if expired
            else f"Das Zertifikat für {domains} läuft am {when} ab ({cert['days_text']})."
        )
        body += " Im Nginx Proxy Manager unter „SSL-Zertifikate“ erneuern, falls das nicht von selbst passiert."
        await ctx.notify.send(Notification(
            title=f"Zertifikat {'abgelaufen' if expired else 'läuft bald ab'}: {cert['name']}",
            body=body,
            severity=Severity.CRITICAL if expired else Severity.WARNING,
            correlation_id=f"network-cert:{key}",
            payload={"path": PAGE_PATH, "tags": ["lock"]},
        ))
        notified[key] = today
        sent += 1
    # Erneuerte oder geloeschte Zertifikate vergessen.
    notified = {k: v for k, v in notified.items() if k in urgent_ids}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notified, indent=1, sort_keys=True), encoding="utf-8")
    return {"checked": len(status["certificates"]), "notified": sent}


class CertWatchJob:
    """`JobSpec` fuer `ctx.scheduler.register_job()` (wie backups' Waechter)."""

    id = "cert-watch"
    name = "Zertifikats-Warnung (Nginx Proxy Manager)"
    schedule = SCHEDULE
    params: ClassVar[dict[str, Any]] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext, service: NetworkService) -> None:
        self._ctx = ctx
        self._service = service

    async def handler(self, **_: Any) -> dict[str, Any]:
        return await run_cert_watch(self._ctx, self._service)
