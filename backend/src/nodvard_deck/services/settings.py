"""Einstellungen als schlichter Schlüssel-Wert-Speicher, global oder je Benutzer (docs/03-DATA-MODEL.md §9)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import Setting

_GLOBAL_SCOPE = "global"
_NO_USER = ""

DESIGN_NOTICE_KEY = "ui.design_notice_dismissed"
"""Je Benutzer: Hinweis „Deck hat ein neues Aussehen.“ erledigt. Neue Konten bekommen ihn gleich als erledigt, sie
kennen das alte Aussehen nicht; wer es schon vor dem Update gab, hat den Wert nicht und sieht den Hinweis einmal."""


async def get_global(session: AsyncSession, key: str, default: Any = None) -> Any:
    result = await session.execute(
        select(Setting).where(
            Setting.key == key, Setting.scope == _GLOBAL_SCOPE, Setting.user_id == _NO_USER
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return default
    return row.value.get("value", default)


async def set_global(
    session: AsyncSession, key: str, value: Any, *, updated_by_user_id: str | None = None
) -> None:
    """Speichert `value` als `{"value": ...}`: Die Spalte ist ein JSON-Dict, Skalare brauchen einen Wrapper."""
    row = await session.get(Setting, (key, _GLOBAL_SCOPE, _NO_USER))
    if row is None:
        row = Setting(key=key, scope=_GLOBAL_SCOPE, user_id=_NO_USER)
        session.add(row)
    row.value = {"value": value}
    row.updated_at = utcnow()
    row.updated_by_user_id = updated_by_user_id
    await session.flush()
    if key == "system.timezone":
        # Prozess-Cache der Zeitzone verwerfen, egal über welchen Weg geschrieben wurde.
        from ..core import timezone as timezone_service

        timezone_service.reset_timezone_cache()


async def get_user(session: AsyncSession, user_id: str, key: str, default: Any = None) -> Any:
    """Einstellung eines einzelnen Benutzers (`scope="user"`)."""
    row = await session.get(Setting, (key, "user", user_id))
    if row is None:
        return default
    return row.value.get("value", default)


async def set_user(session: AsyncSession, user_id: str, key: str, value: Any) -> None:
    row = await session.get(Setting, (key, "user", user_id))
    if row is None:
        row = Setting(key=key, scope="user", user_id=user_id)
        session.add(row)
    row.value = {"value": value}
    row.updated_at = utcnow()
    row.updated_by_user_id = user_id
    await session.flush()


async def delete_user_settings(session: AsyncSession, user_id: str) -> None:
    """Alle Einstellungen eines Benutzers entfernen (beim Löschen des Kontos)."""
    await session.execute(delete(Setting).where(Setting.scope == "user", Setting.user_id == user_id))


async def list_global(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(Setting).where(Setting.scope == _GLOBAL_SCOPE, Setting.user_id == _NO_USER)
    )
    # Branding speichert seine Felder roh (Strings, Farb-Dicts) statt als `{"value": ...}`; die gehören nicht hierher.
    return {
        row.key: row.value.get("value")
        for row in result.scalars().all()
        if isinstance(row.value, dict) and "value" in row.value
    }
