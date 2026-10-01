"""„Einrichtung nötig“ für Erweiterungen: fehlen Pflichtfelder oder Pflicht-Zugangsdaten,
oder ist der letzte Verbindungstest fehlgeschlagen?

Alles hier ist generisch und liest nur das Einstellungs-Schema (`required`,
`x-secrets`) und die gespeicherten Werte -- der Kern kennt keine einzelne Erweiterung.

Das Ergebnis des letzten Verbindungstests liegt als globale Einstellung
`extension.test.<id>` (`{"ok", "message", "at"}`). Es wird gelöscht, sobald Einstellungen
oder Zugangsdaten der Erweiterung geändert werden -- ein alter Fehlschlag soll nicht
stehen bleiben, nachdem der Nutzer das Problem behoben hat.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import Secret, Setting

TEST_KEY_PREFIX = "extension.test."


def secret_slots_spec(schema: dict[str, Any] | None, values: dict[str, Any]) -> list[dict[str, Any]]:
    """`x-secrets` im Schema, z. B.
    `[{"label": "proxmox-token:{name}", "title": "API-Token", "per_item": "connections"}]`
    -- `per_item` erzeugt ein Geheimnis je Eintrag der genannten Liste (über dessen `name`).
    `"optional": true` markiert ein Geheimnis, ohne das die Erweiterung trotzdem arbeitet."""
    slots: list[dict[str, Any]] = []
    for spec in (schema or {}).get("x-secrets") or []:
        per_item = spec.get("per_item")
        if per_item:
            for item in values.get(per_item) or []:
                name = str((item or {}).get("name") or "").strip()
                if name:
                    slots.append({**spec, "label": spec["label"].replace("{name}", name), "item": name})
        else:
            slots.append({**spec, "item": None})
    return slots


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (list, dict)) and not value)


def _title(key: str, schema: dict[str, Any]) -> str:
    return str(schema.get("title") or key.replace("_", " "))


def missing_fields(schema: dict[str, Any] | None, values: dict[str, Any]) -> list[str]:
    """Pflichtfelder ohne Wert (und ohne Standardwert im Schema): oben auf der Ebene der
    Einstellungen und je Eintrag einer Liste von Einträgen (z. B. je Proxmox-Server)."""
    out: list[str] = []
    props = (schema or {}).get("properties") or {}
    for key in (schema or {}).get("required") or []:
        sub = props.get(key) or {}
        if _is_empty(values.get(key)) and sub.get("default") is None:
            if sub.get("type") == "array":
                out.append(f"Bei „{_title(key, sub)}“ fehlt noch ein Eintrag.")
            else:
                out.append(f"„{_title(key, sub)}“ ist noch nicht ausgefüllt.")
    for key, sub in props.items():
        items = sub.get("items") if sub.get("type") == "array" else None
        if not isinstance(items, dict) or items.get("type") != "object":
            continue
        item_title = str(sub.get("x-item-title") or "Eintrag")
        for index, item in enumerate(values.get(key) or [], start=1):
            if not isinstance(item, dict):
                continue
            who = str(item.get("name") or "") or str(index)
            for req in items.get("required") or []:
                if _is_empty(item.get(req)) and (items.get("properties") or {}).get(req, {}).get("default") is None:
                    out.append(f"{item_title} „{who}“: „{_title(req, (items.get('properties') or {}).get(req) or {})}“ fehlt.")
    return out


def missing_secrets(schema: dict[str, Any] | None, values: dict[str, Any], present_labels: set[str]) -> list[str]:
    out: list[str] = []
    for spec in secret_slots_spec(schema, values):
        if spec.get("optional") or spec["label"] in present_labels:
            continue
        title = str(spec.get("title") or spec["label"])
        out.append(f"Zugangsdaten fehlen: {title}" + (f" ({spec['item']})" if spec.get("item") else "") + ".")
    return out


def setup_reasons(
    schema: dict[str, Any] | None,
    values: dict[str, Any],
    present_labels: set[str],
    last_test: dict[str, Any] | None,
    *,
    with_test_message: bool = True,
) -> list[str]:
    """`with_test_message=False`: nur "Der letzte Verbindungstest ist fehlgeschlagen." ohne den Text
    (er nennt Adressen und Fehlergruende -- nur fuer Nutzer, die Erweiterungen verwalten)."""
    reasons = missing_fields(schema, values) + missing_secrets(schema, values, present_labels)
    if last_test is not None and last_test.get("ok") is False:
        detail = f": {last_test.get('message') or 'ohne Angabe'}" if with_test_message else "."
        reasons.append(f"Der letzte Verbindungstest ist fehlgeschlagen{detail}")
    return reasons


# -- Speicher für das letzte Testergebnis -------------------------------------------------


def _row_key(ext_id: str) -> str:
    return f"{TEST_KEY_PREFIX}{ext_id}"


async def save_last_test(session: AsyncSession, ext_id: str, *, ok: bool, message: str) -> dict[str, Any]:
    from . import settings as settings_service

    value = {"ok": ok, "message": message, "at": utcnow().isoformat()}
    await settings_service.set_global(session, _row_key(ext_id), value)
    return value


async def clear_last_test(session: AsyncSession, ext_id: str) -> None:
    rows = (await session.execute(select(Setting).where(Setting.key == _row_key(ext_id)))).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.flush()


async def load_last_tests(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Alle gespeicherten Testergebnisse, je Erweiterungs-ID."""
    rows = (await session.execute(select(Setting).where(Setting.key.like(f"{TEST_KEY_PREFIX}%")))).scalars().all()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = (row.value or {}).get("value")
        if isinstance(value, dict):
            out[row.key[len(TEST_KEY_PREFIX):]] = value
    return out


async def present_secret_labels(session: AsyncSession) -> set[str]:
    return set((await session.execute(select(Secret.label))).scalars().all())
