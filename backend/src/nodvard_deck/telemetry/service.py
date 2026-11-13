"""Lokaler Zustand, begrenzter Versand und ausdrückliche Löschwünsche."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import hmac
import logging
import os
import platform
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import edition
from ..config import Settings, get_settings
from ..db import utcnow
from ..db.session import session_scope
from ..ext.runtime import get_extension_runtime
from ..models import ExtensionRecord, Host, User
from ..services import settings as store
from .codes import ErrorCode
from .schema import Basis, Choices, Errors, Report, Usage, VOCABULARY

KEY = "telemetry.state"
INTERVAL = timedelta(hours=24)
_lock = asyncio.Lock()
logger = logging.getLogger("nodvard_deck.telemetry")


def blocked(settings: Settings) -> str | None:
    if os.environ.get("NODVARD_TELEMETRIE", "").strip().lower() == "aus" or settings.telemetrie.strip().lower() == "aus":
        return "admin"
    if not settings.telemetry_privacy_ready:
        return "privacy"
    try:
        url = urlsplit(settings.telemetry_url)
        if url.port == 0:
            return "endpoint"
    except ValueError:
        return "endpoint"
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
            or any(char.isspace() or ord(char) < 32 for char in settings.telemetry_url)):
        return "endpoint"
    return None


def _identity() -> dict[str, str]:
    return {"id": str(uuid4()), "secret": secrets.token_hex(32)}


async def load(session: AsyncSession) -> dict[str, Any]:
    raw = await store.get_global(session, KEY)
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    state = {**_identity(), "choices": Choices().model_dump(), "last_attempt": None,
             "next_due": None, "errors": {}, "history": [], "pending_deletions": [], "attempted": False}
    await save(session, state)
    return state


async def save(session: AsyncSession, state: dict[str, Any]) -> None:
    await store.set_global(session, KEY, state)
    await session.commit()


def count_class(count: int, limits: tuple[int, ...], labels: tuple[str, ...]) -> str:
    return next((label for limit, label in zip(limits, labels) if count <= limit), labels[-1])


async def report(session: AsyncSession, settings: Settings, state: dict[str, Any], choices: Choices) -> dict[str, Any]:
    result = Report(id=state["id"])
    if choices.basis:
        owner = await session.scalar(select(User).where(User.is_owner.is_(True)))
        arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
        if arch is None:
            raise ValueError("unsupported_arch")
        installation = settings.telemetry_installation
        if installation not in VOCABULARY["installation"]:
            installation = "sonstiges"
        result.basis = Basis(produkt=edition.of(settings).id, installation=installation, arch=arch,
                             sprache="en" if owner is not None and owner.locale == "en" else "de")
    if choices.nutzung:
        runtime = get_extension_runtime()
        rows = (await session.scalars(select(ExtensionRecord.id).where(ExtensionRecord.state == "enabled"))).all()
        enabled = {runtime.canonical(row_id) for row_id in rows if not runtime.is_stale_twin(row_id)}
        known = set(VOCABULARY["module"])
        servers = await session.scalar(select(func.count()).select_from(Host)) or 0
        users = await session.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
        mfa = await session.scalar(select(func.count()).select_from(User).where(
            User.is_active.is_(True), User.totp_confirmed_at.is_not(None))) or 0
        backup = await store.get_global(session, "backup.config", {})
        result.nutzung = Usage(
            module=sorted(enabled & known),
            fremd=count_class(len(enabled - known), (0, 1, 5), ("0", "1", "2-5", "6+")),
            server=count_class(servers, (0, 1, 5, 20), ("0", "1", "2-5", "6-20", "21+")),
            benutzer=count_class(users, (1, 5), ("1", "2-5", "6+")), zwei_faktor=mfa > 0,
            sicherungen_aktiv=isinstance(backup, dict) and backup.get("enabled") is True,
        )
    if choices.fehler:
        result.fehler = Errors(arten={code: max(0, min(99, count)) for code, count in state["errors"].items()
                                     if code in ErrorCode._value2member_map_ and type(count) is int})
    return result.model_dump(mode="json", exclude_none=True, by_alias=True)


async def _status(session: AsyncSession, settings: Settings, *, choices: Choices | None = None) -> dict[str, Any]:
    state = await load(session)
    selected = Choices.model_validate(state["choices"])
    preview_error = None
    try:
        preview = await report(session, settings, state, choices if choices is not None else selected)
    except ValueError:
        preview = None
        preview_error = "unsupported_arch"
    return {"choices": selected.model_dump(), "blocked": blocked(settings), "preview_error": preview_error,
            "preview": preview,
            "history": state["history"], "next_send_at": _next_send(state),
            "pending_deletions": len(state["pending_deletions"])}


async def status(session: AsyncSession, settings: Settings, *, choices: Choices | None = None) -> dict[str, Any]:
    async with _lock:
        return await _status(session, settings, choices=choices)


def _next_send(state: dict[str, Any]) -> str | None:
    if state["last_attempt"] is None:
        return None
    return (datetime.fromisoformat(state["last_attempt"]) + INTERVAL).isoformat()


async def configure(session: AsyncSession, settings: Settings, choices: Choices) -> dict[str, Any]:
    async with _lock:
        state = await load(session)
        if blocked(settings) and any(active and not state["choices"][key] for key, active in choices.model_dump().items()):
            raise ValueError("sharing_blocked")
        if choices.basis:
            await report(session, settings, state, choices)
        was_enabled = any(state["choices"].values())
        state["choices"] = choices.model_dump()
        if not was_enabled and any(state["choices"].values()):
            state["next_due"] = (utcnow() + timedelta(seconds=secrets.randbelow(86400))).isoformat()
        if not choices.fehler:
            state["errors"] = {}
        await save(session, state)
        return await _status(session, settings)


async def _request(settings: Settings, method: str, identity: dict[str, Any], body: dict[str, Any] | None = None) -> bool:
    key = hmac.new(bytes.fromhex(identity["secret"]), identity["id"].encode(), hashlib.sha256).hexdigest()
    path = "/v1/bericht" if method == "POST" else f"/v1/id/{identity['id']}"
    try:
        async with asyncio.timeout(10):
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, settings.telemetry_url.rstrip("/") + path,
                                         headers={"X-Nodvard-Delete-Key": key}, json=body) as response:
                    return 200 <= response.status_code < 300
    except (httpx.HTTPError, TimeoutError, OSError):
        logger.info("Nutzungsdaten: Empfänger nicht erreichbar.")
        return False


def _log(state: dict[str, Any], operation: str, outcome: str, body: dict[str, Any]) -> None:
    state["history"] = [{"at": utcnow().isoformat(), "operation": operation, "outcome": outcome, "report": body},
                        *state["history"]][:30]


async def send(session: AsyncSession, settings: Settings, *, scheduled: bool = False) -> str:
    async with _lock:
        state = await load(session)
        if blocked(settings) or not any(state["choices"].values()):
            return "disabled"
        now = utcnow()
        if state["last_attempt"] and now < datetime.fromisoformat(state["last_attempt"]) + INTERVAL:
            return "rate_limited"
        if scheduled and (not state["next_due"] or now < datetime.fromisoformat(state["next_due"])):
            return "not_due"
        try:
            body = await report(session, settings, state, Choices.model_validate(state["choices"]))
        except ValueError:
            await session.commit()
            return "unsupported_arch"
        state["last_attempt"] = now.isoformat()
        state["next_due"] = (now + INTERVAL + timedelta(seconds=secrets.randbelow(3600))).isoformat()
        recipients = state.setdefault("recipients", [None] if state["attempted"] else [])
        url = settings.telemetry_url.rstrip("/")
        if url not in recipients:
            recipients.append(url)
        state["attempted"] = True
        _log(state, "report", "sending", body)
        await save(session, state)
        ok = await _request(settings, "POST", state, body)
        state["history"][0]["outcome"] = "sent" if ok else "failed"
        state["errors"] = {}
        await save(session, state)
        return "sent" if ok else "failed"


async def delete_data(session: AsyncSession, settings: Settings) -> str:
    async with _lock:
        state = await load(session)
        pending = state["pending_deletions"]
        if state["attempted"]:
            pending.extend({"id": state["id"], "secret": state["secret"], "url": url}
                           for url in state.get("recipients", [None]))
        state.update(_identity())
        state.update(choices=Choices().model_dump(), errors={}, attempted=False, next_due=None, recipients=[])
        # Die 24-Stunden-Sperre gilt auch nach einem Identitätswechsel.
        await save(session, state)
        remaining = []
        for identity in pending:
            target = settings.model_copy(update={"telemetry_url": identity.get("url") or ""})
            if blocked(target):
                remaining.append(identity)
                continue
            ok = await _request(target, "DELETE", identity)
            _log(state, "delete", "deleted" if ok else "failed", {"id": identity["id"]})
            if not ok:
                remaining.append(identity)
        state["pending_deletions"] = remaining
        await save(session, state)
        return "delete_pending" if remaining else "deleted"


async def record_error(code: ErrorCode) -> None:
    try:
        settings = get_settings()
        if blocked(settings) or not isinstance(code, ErrorCode):
            return
        async with _lock:
            async with session_scope() as session:
                raw = await store.get_global(session, KEY)
                if not raw or not raw["choices"]["fehler"]:
                    return
                state = copy.deepcopy(raw)
                state["errors"][code.value] = min(99, state["errors"].get(code.value, 0) + 1)
                await save(session, state)
    except Exception:
        logger.info("Nutzungsdaten: Fehlerzähler konnte nicht gespeichert werden.")


async def run_daily() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            async with session_scope() as session:
                await send(session, get_settings(), scheduled=True)
        except Exception:
            logger.info("Nutzungsdaten: Geplanter Bericht konnte nicht verarbeitet werden.")


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
