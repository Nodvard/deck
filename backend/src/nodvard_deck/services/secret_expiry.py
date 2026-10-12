"""Erinnert 14 und 3 Tage vor dem Ablauf eines Tresor-Eintrags und nach dem Ablauf (Kern-Job `secret-expiry-reminder`)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nodvard_sdk import Severity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.timezone import get_timezone
from ..db import utcnow
from ..db.session import session_scope
from ..models import Host, HostCredential, Secret

logger = logging.getLogger("nodvard_deck.secret_expiry")

JOB_KEY = "secret-expiry-reminder"
JOB_NAME = "Erinnerung an ablaufende Zugänge"
SCHEDULE = "7 8 * * *"  # morgens in der eingestellten Zeitzone, nicht zur vollen Stunde

METADATA_KEY = "expiry_notice"
SOON_DAYS = 14
DUE_DAYS = 3


@dataclass(frozen=True)
class Stage:
    key: str
    rank: int
    severity: str


SOON = Stage("soon", 1, Severity.INFO.value)
DUE = Stage("due", 2, Severity.WARNING.value)
EXPIRED = Stage("expired", 3, Severity.WARNING.value)
_RANKS = {s.key: s.rank for s in (SOON, DUE, EXPIRED)}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def days_left(expires_at: datetime, now: datetime, zone: ZoneInfo) -> int:
    """Kalendertage von heute bis zum Ablauftag in der Zone (0 = heute, negativ = vorbei)."""
    return (_utc(expires_at).astimezone(zone).date() - _utc(now).astimezone(zone).date()).days


def stage_for(expires_at: datetime, now: datetime, zone: ZoneInfo) -> Stage | None:
    """Die gerade fällige Stufe oder None (mehr als 14 Tage hin). Verpasste Stufen werden nicht nachgeholt."""
    if _utc(expires_at) <= _utc(now):
        return EXPIRED
    left = days_left(expires_at, now, zone)
    if left <= DUE_DAYS:
        return DUE
    if left <= SOON_DAYS:
        return SOON
    return None


def _marker(expires_at: datetime) -> str:
    return _utc(expires_at).isoformat()


def already_sent(metadata: Any, expires_at: datetime, stage: Stage) -> bool:
    """Ob diese oder eine dringendere Stufe für genau dieses Ablaufdatum schon gemeldet wurde."""
    notice = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(notice, dict) or notice.get("for") != _marker(expires_at):
        return False
    return _RANKS.get(notice.get("stage"), 0) >= stage.rank


def _when(left: int) -> str:
    if left <= 0:
        return "heute"
    if left == 1:
        return "morgen"
    return f"in {left} Tagen"


def _date(expires_at: datetime, zone: ZoneInfo) -> str:
    return _utc(expires_at).astimezone(zone).strftime("%d.%m.%Y")


def message(secret: Secret, stage: Stage, now: datetime, zone: ZoneInfo, owner: str | None = None) -> tuple[str, str]:
    """(Titel, Text) der Meldung; `owner` ist der Satz aus `_owner`."""
    assert secret.expires_at is not None
    name = f"„{secret.label}“" + (f" ({secret.description})" if secret.description else "")
    left = days_left(secret.expires_at, now, zone)
    if stage is EXPIRED:
        title = f"Zugang abgelaufen: {secret.label}"
        when = "ist heute abgelaufen" if left == 0 else f"ist seit dem {_date(secret.expires_at, zone)} abgelaufen"
        text = f"{name} {when}. Bitte einen neuen Zugang erzeugen und im Dashboard ersetzen."
    else:
        title = f"Zugang läuft bald ab: {secret.label}"
        text = (
            f"{name} läuft {_when(left)} ab (am {_date(secret.expires_at, zone)}). "
            "Bitte vorher einen neuen Zugang erzeugen und im Dashboard ersetzen."
        )
    return title, f"{text} {owner}" if owner else text


MAX_HOST_NAMES = 3


async def _owner(session: AsyncSession, secret: Secret) -> tuple[str | None, str | None]:
    """(Satz, Link): wem der Zugang gehört und wo man ihn ersetzt, sonst (None, None) für einen freien Eintrag."""
    if secret.owner_ext_id:
        from ..ext.runtime import get_extension_runtime

        runtime = get_extension_runtime()
        ext_id = runtime.canonical(secret.owner_ext_id)
        loaded = runtime.loaded.get(ext_id)
        found = loaded if loaded is not None else runtime.discovered.get(ext_id)
        manifest = getattr(found, "manifest", None)
        name = getattr(manifest, "name", None) or ext_id
        return f"Gehört zum Modul „{name}“.", f"/settings/extensions/{ext_id}" if found is not None else None
    rows = (await session.execute(
        select(Host.id, Host.name, Host.display_name)
        .join(HostCredential, HostCredential.host_id == Host.id)
        .where(HostCredential.secret_id == secret.id)
    )).all()
    if not rows:
        return None, None
    hosts = sorted({(display_name or name, host_id) for host_id, name, display_name in rows}, key=lambda h: (h[0].lower(), h[1]))
    names = [f"„{n}“" for n, _ in hosts[:MAX_HOST_NAMES]]
    if len(hosts) > MAX_HOST_NAMES:
        names.append(f"{len(hosts) - MAX_HOST_NAMES} weitere")
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " und " + names[-1]
    sentence = f"Zugang für den Server {listed}." if len(hosts) == 1 else f"Zugang für die Server {listed}."
    return sentence, f"/settings/hosts/{hosts[0][1]}"


async def remind_one(session: AsyncSession, secret_id: str, *, now: datetime, zone: ZoneInfo) -> str | None:
    """Meldet die fällige Stufe eines Eintrags, wenn sie noch offen ist, und liefert sie (sonst None)."""
    from . import notifications as notifications_service

    secret = await session.get(Secret, secret_id)
    if secret is None or secret.expires_at is None:
        return None
    stage = stage_for(secret.expires_at, now, zone)
    if stage is None or already_sent(secret.secret_metadata, secret.expires_at, stage):
        return None
    owner, path = await _owner(session, secret)
    title, body = message(secret, stage, now, zone, owner)
    payload: dict[str, Any] = {"tags": ["warning", "key"] if stage.rank >= DUE.rank else ["key"]}
    if path:
        payload["path"] = path
    # Merker vor der Meldung: `deliver` speichert beides zusammen, so kommt nie eine doppelte.
    # Neues dict zuweisen, Änderungen im JSON-Feld bemerkt SQLAlchemy sonst nicht.
    metadata = dict(secret.secret_metadata) if isinstance(secret.secret_metadata, dict) else {}
    metadata[METADATA_KEY] = {"for": _marker(secret.expires_at), "stage": stage.key}
    secret.secret_metadata = metadata
    await session.flush()
    await notifications_service.deliver(session, title=title, body=body, severity=stage.severity, payload=payload)
    return stage.key


async def run_check(*, now: datetime | None = None) -> dict[str, int]:
    """Der Kern-Job: alle Einträge, die in den nächsten 14 Tagen ablaufen oder abgelaufen sind."""
    now = _utc(now or utcnow())
    async with session_scope() as session:
        try:
            zone = ZoneInfo(await get_timezone(session))
        except Exception:  # lieber in UTC zählen als gar nicht erinnern
            logger.warning("secret_expiry_timezone_unusable, counting in UTC", exc_info=True)
            zone = ZoneInfo("UTC")
        # Ein Tag Puffer: die Zone kann den Ablauftag gegenüber UTC verschieben.
        horizon = now + timedelta(days=SOON_DAYS + 1)
        ids = list((await session.execute(
            select(Secret.id).where(Secret.expires_at.is_not(None), Secret.expires_at <= horizon).order_by(Secret.expires_at)
        )).scalars().all())
    sent = failed = 0
    for secret_id in ids:
        try:
            async with session_scope() as session:
                if await remind_one(session, secret_id, now=now, zone=zone):
                    sent += 1
        except Exception:  # ein Fehler hält die anderen Einträge nicht auf
            failed += 1
            logger.exception("secret_expiry_reminder_failed secret_id=%s", secret_id)
    return {"checked": len(ids), "sent": sent, "failed": failed}


async def _job_handler(**_: Any) -> dict[str, int]:
    return await run_check()


async def sync_job() -> None:
    """Legt den täglichen Job an oder aktualisiert ihn und plant ihn ein."""
    from ..core.scheduler import CORE_SCHEDULER_EXT_ID, get_scheduler_service
    from ..ext.runtime import get_extension_runtime
    from . import jobs as jobs_service

    async with session_scope() as session:
        job = await jobs_service.upsert_job(
            session, ext_id=None, ext_job_key=JOB_KEY, name=JOB_NAME,
            kind="core", schedule=SCHEDULE, params={}, enabled=True,
        )
    get_extension_runtime().scheduler.register(CORE_SCHEDULER_EXT_ID, JOB_KEY, _job_handler)
    await get_scheduler_service().schedule(job, _job_handler)


__all__ = [
    "DUE",
    "DUE_DAYS",
    "EXPIRED",
    "JOB_KEY",
    "METADATA_KEY",
    "SCHEDULE",
    "SOON",
    "SOON_DAYS",
    "already_sent",
    "days_left",
    "message",
    "remind_one",
    "run_check",
    "stage_for",
    "sync_job",
]
