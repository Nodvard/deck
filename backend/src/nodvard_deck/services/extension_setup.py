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

from nodvard_sdk.addresses import same_target
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


def _path_value(values: Any, path: str) -> Any:
    """Wert hinter einem Pfad wie `server_url` oder `pihole.url` (nur Objekte, kein Raten)."""
    cur = values
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _default_for(schema: dict[str, Any] | None, path: str, item_schema: dict[str, Any] | None = None) -> Any:
    node: Any = item_schema if item_schema is not None else {"properties": (schema or {}).get("properties") or {}}
    for part in path.split("."):
        node = ((node or {}).get("properties") or {}).get(part)
        if node is None:
            return None
    return node.get("default") if isinstance(node, dict) else None


def _bound_paths(spec: dict[str, Any]) -> list[str]:
    return [p for p in spec.get("x-secret-bound-to") or [] if isinstance(p, str) and p]


def _target_at(
    values: Any, path: str, schema: dict[str, Any] | None, item_schema: dict[str, Any] | None = None
) -> Any:
    """Das Ziel hinter `path`: der gespeicherte Wert, sonst der Standardwert aus dem Schema
    (ohne eingetragene Adresse gilt der Standard); leer ist immer `""`."""
    value = _path_value(values, path)
    if value is None:
        value = _default_for(schema, path, item_schema)
    return "" if _is_empty(value) else value


def _targets_moved(
    bound: list[str], before: Any, after: Any, schema: dict[str, Any] | None, item_schema: dict[str, Any] | None = None
) -> bool:
    return any(
        not same_target(_target_at(before, p, schema, item_schema), _target_at(after, p, schema, item_schema))
        for p in bound
    )


def secrets_to_clear(schema: dict[str, Any] | None, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Labels der Geheimnisse, die nach einer Aenderung der Einstellungen nicht mehr
    stehen bleiben duerfen: Ein Geheimnis gehoert zu dem Ziel, bei dem es gesetzt wurde.
    Aendert sich dieses Ziel (Schema: `x-secrets[].x-secret-bound-to`, Feldnamen oder
    Pfade wie `pihole.url`), wuerde das Geheimnis sonst an die neue Adresse geschickt.
    Das gilt fuer JEDE Aenderung eines der genannten Felder, auch fuer eine erste Adresse
    (leer -> Wert): ein Geheimnis kann an mehrere Felder gebunden sein (ein zweiter Server
    mit demselben Schluessel), und eines, das schon im Tresor liegt, ohne dass ein Ziel
    eingetragen war, gehoert zu keinem Server. Neue Geheimnisse nimmt der Tresor erst an,
    wenn ein Ziel gespeichert ist (`secret_target_missing`), so geht beim ersten Einrichten
    nichts verloren. Bei Geheimnissen je Listeneintrag (`per_item`) gelten die Feldnamen
    innerhalb des Eintrags; faellt der Eintrag weg (oder wird umbenannt), faellt sein Geheimnis
    ebenfalls weg. Ein NEU angelegter Eintrag startet ohne Geheimnis: liegt unter seinem Namen
    noch eines (etwa von einer frueher entfernten Verbindung), gehoerte es zu einem anderen
    Ziel. Die Liste nennt Labels, die geloescht werden muessen, falls es sie gibt."""
    out: list[str] = []
    for spec in (schema or {}).get("x-secrets") or []:
        bound = _bound_paths(spec)
        per_item = spec.get("per_item")
        if per_item:
            items_schema = (((schema or {}).get("properties") or {}).get(per_item) or {}).get("items")
            new_by_name = {
                str((i or {}).get("name") or "").strip(): i for i in new.get(per_item) or [] if isinstance(i, dict)
            }
            old_names = {
                str((i or {}).get("name") or "").strip() for i in old.get(per_item) or [] if isinstance(i, dict)
            }
            for name in new_by_name:
                if name and name not in old_names:
                    out.append(spec["label"].replace("{name}", name))
            for item in old.get(per_item) or []:
                name = str((item or {}).get("name") or "").strip()
                if not name:
                    continue
                label = spec["label"].replace("{name}", name)
                current = new_by_name.get(name)
                if current is None or _targets_moved(bound, item, current, schema, items_schema):
                    out.append(label)
        elif _targets_moved(bound, old, new, schema):
            out.append(spec["label"])
    return list(dict.fromkeys(out))


def secret_target_missing(schema: dict[str, Any] | None, values: dict[str, Any], slot: dict[str, Any]) -> bool:
    """Ist das Geheimnis `slot` (ein Eintrag aus `secret_slots_spec`) an Felder gebunden, die alle
    noch leer sind (auch ohne Standardwert im Schema)? Dann gibt es noch keinen Server, zu dem es
    gehoeren koennte: erst die Adresse speichern, dann das Geheimnis ablegen."""
    bound = _bound_paths(slot)
    if not bound:
        return False
    per_item = slot.get("per_item")
    if per_item:
        items_schema = (((schema or {}).get("properties") or {}).get(per_item) or {}).get("items")
        item = next(
            (i for i in values.get(per_item) or [] if isinstance(i, dict) and str(i.get("name") or "").strip() == slot.get("item")),
            {},
        )
        return all(same_target(_target_at(item, p, schema, items_schema), "") for p in bound)
    return all(same_target(_target_at(values, p, schema), "") for p in bound)


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
