"""Globale Einstellungen -- docs/03-DATA-MODEL.md §9, reservierte Schluessel.

**Gap-Fill-Entscheidung, analog zu `client_type` (WP-1) und dem Zugangsdaten-Endpunkt
(WP-4):** docs/04-API.md nennt `GET /settings` / `PUT /settings/{key}`, aber kein
bisheriges WP hat sie gebaut. Das Gate (WP-5) braucht `autonomy.mode`/
`autonomy.max_risk`/`security.deny_patterns` als ECHTE, ueber die API veraenderbare
Werte -- ohne irgendeinen Schreibweg waere "volle Autonomie" nie live vorfuehrbar,
sondern nur durch direkte DB-Manipulation in Tests simulierbar. Deshalb hier ein
bewusst minimaler, generischer Store (kein Schema, keine Validierung ausser den
Typpruefungen unten) statt eines vollen Settings-Verwaltungs-Features -- letzteres
bleibt einer spaeteren UI-Runde (WP-7) vorbehalten.

Nur `scope="global", user_id=""` in dieser Runde -- benutzerspezifische Einstellungen
(docs/03 §9 nennt `scope (global|user)`) sind kein Gate-Bedarf und bleiben offen.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import Setting

_GLOBAL_SCOPE = "global"
_NO_USER = ""


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
    """`value` wird als `{"value": ...}` gespeichert -- die Spalte ist `sa.JSON` als
    Dict typisiert (docs/00 D-02: kein dialektspezifischer Spaltentyp), ein
    Skalar-Setting wie `autonomy.mode = "full"` braucht deshalb einen Wrapper."""
    row = await session.get(Setting, (key, _GLOBAL_SCOPE, _NO_USER))
    if row is None:
        row = Setting(key=key, scope=_GLOBAL_SCOPE, user_id=_NO_USER)
        session.add(row)
    row.value = {"value": value}
    row.updated_at = utcnow()
    row.updated_by_user_id = updated_by_user_id
    await session.flush()
    if key == "system.timezone":
        # Prozess-Cache der Zeitzone verwerfen, egal ueber welchen Weg geschrieben wurde.
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
    """Alle Einstellungen eines Benutzers entfernen (wenn das Konto geloescht wird)."""
    await session.execute(delete(Setting).where(Setting.scope == "user", Setting.user_id == user_id))


async def list_global(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        select(Setting).where(Setting.scope == _GLOBAL_SCOPE, Setting.user_id == _NO_USER)
    )
    # Nicht jede globale Zeile hat das `{"value": ...}`-Format: Branding speichert
    # seine Felder roh (Strings, Farb-Dicts). Die gehoeren nicht in diese Liste.
    return {
        row.key: row.value.get("value")
        for row in result.scalars().all()
        if isinstance(row.value, dict) and "value" in row.value
    }
