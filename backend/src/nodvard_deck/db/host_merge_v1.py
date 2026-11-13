"""Zusammenführung und Rückweg für Server-Zuordnungen."""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime

import sqlalchemy as sa


def normalize_address(value: str) -> str:
    value = value.strip().lower().removesuffix(".")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value


def _key(table, row):
    return {column.name: row[column.name] for column in table.primary_key}


def _where(table, values):
    return sa.and_(*(table.c[name] == value for name, value in values.items()))


def _values(table, values):
    return {
        name: datetime.fromisoformat(value) if isinstance(table.c[name].type, sa.DateTime) and isinstance(value, str) else value
        for name, value in values.items()
    }


def merge_matches(connection, identities: dict[str, list[str]] | None = None) -> dict[str, str]:
    schema = sa.MetaData()
    schema.reflect(connection)
    hosts = schema.tables["hosts"]
    journal = schema.tables["host_merges"]
    rows = [
        row for row in connection.execute(sa.select(hosts).order_by(hosts.c.id)).mappings()
        if not (row["metadata"] or {}).get("gone_since")
    ]
    manual = [row for row in rows if row["provider_ext_id"] is None]
    discovered = [row for row in rows if row["provider_ext_id"] is not None]
    addresses = {}
    for row in discovered:
        values = identities.get(row["id"], []) if identities is not None else (row["metadata"] or {}).get("identity_addresses", [])
        addresses[row["id"]] = {normalize_address(value) for value in values if isinstance(value, str) and value.strip()}
    matches = {}
    for host in manual:
        address = normalize_address(host["address"])
        if not address:
            continue
        candidates = [row for row in discovered if address in addresses[row["id"]]]
        if len(candidates) == 1:
            matches[host["id"]] = candidates[0]
    merged = {}
    for host in manual:
        source = matches.get(host["id"])
        if source is None or sum(row["id"] == source["id"] for row in matches.values()) != 1:
            continue
        if connection.execute(sa.select(journal.c.source_id).where(journal.c.host_id == source["id"])).first():
            continue
        actions = schema.tables.get("actions")
        if actions is not None and connection.execute(sa.select(actions.c.id).where(
            actions.c.host_id.in_([host["id"], source["id"]]), actions.c.status == "executing",
        )).first():
            continue
        settings = schema.tables["settings"]
        if connection.execute(sa.select(settings.c.key).where(
            settings.c.key == "ssh.host_key_confirm_required", settings.c.scope == "host",
            settings.c.user_id == host["id"],
        )).first():
            continue
        known_hosts = schema.tables["known_hosts"]
        keys = {host["id"]: set(), source["id"]: set()}
        for row in connection.execute(sa.select(known_hosts).where(
            known_hosts.c.host_id.in_([host["id"], source["id"]]),
        )).mappings():
            keys[row["host_id"]].add((row["key_type"], row["fingerprint"]))
        # Ein Schlüssel der Quelle, den der von Hand angelegte Server nicht schon kennt, würde sonst ungeprüft übernommen.
        if keys[source["id"]] - keys[host["id"]]:
            continue
        changes = []
        has_default = connection.execute(sa.select(schema.tables["host_credentials"].c.id).where(
            schema.tables["host_credentials"].c.host_id == host["id"],
            schema.tables["host_credentials"].c.is_default.is_(True),
        )).first() is not None
        for table in schema.sorted_tables:
            if table.name == "host_merges":
                continue
            if "host_id" in table.c:
                column, condition = "host_id", table.c.host_id == source["id"]
            elif table.name == "settings":
                column = "user_id"
                condition = sa.and_(table.c.scope == "host", table.c.user_id == source["id"])
            elif table.name == "secret_usages":
                column = "consumer_id"
                condition = sa.and_(table.c.consumer_type == "host", table.c.consumer_id == source["id"])
            else:
                continue
            for row in connection.execute(sa.select(table).where(condition)).mappings():
                old_key = _key(table, row)
                values = {column: host["id"]}
                if table.name == "host_credentials" and has_default:
                    values["is_default"] = False
                if "id" in old_key and "kind" in table.c and row["id"] == f"{source['id']}:{row['kind']}":
                    values["id"] = f"{host['id']}:{row['kind']}"
                new_key = {name: values.get(name, value) for name, value in old_key.items()}
                collision = new_key != old_key and connection.execute(sa.select(table).where(_where(table, new_key))).first()
                if table.name == "known_hosts":
                    collision = connection.execute(sa.select(table).where(
                        table.c.host_id == host["id"], table.c.key_type == row["key_type"],
                        table.c.fingerprint == row["fingerprint"],
                    )).first()
                if collision:
                    changes.append({"table": table.name, "deleted": dict(row)})
                    connection.execute(table.delete().where(_where(table, old_key)))
                else:
                    changes.append({"table": table.name, "key": new_key, "before": {name: row[name] for name in values}})
                    connection.execute(table.update().where(_where(table, old_key)).values(**values))
        jobs = schema.tables.get("jobs")
        if jobs is not None:
            for row in connection.execute(sa.select(jobs)).mappings():
                if (row["target"] or {}).get("host_id") == source["id"]:
                    key = _key(jobs, row)
                    changes.append({"table": jobs.name, "key": key, "before": {"target": row["target"]}})
                    connection.execute(jobs.update().where(_where(jobs, key)).values(
                        target={**row["target"], "host_id": host["id"]},
                    ))
        metadata = {**(host["metadata"] or {}), **(source["metadata"] or {})}
        for name in ("login_ok", "setup_run", "sudo_full_wanted"):
            if name in (host["metadata"] or {}):
                metadata[name] = host["metadata"][name]
        metadata["manual_entry"] = {name: host[name] for name in ("id", "name", "display_name", "address")}
        before = {name: host[name] for name in ("provider_ext_id", "provider_ref", "kind", "status", "metadata")}
        snapshot = json.loads(json.dumps({"host": before, "source": dict(source), "changes": changes}, default=str))
        connection.execute(journal.insert().values(source_id=source["id"], host_id=host["id"], snapshot=snapshot))
        connection.execute(hosts.delete().where(hosts.c.id == source["id"]))
        connection.execute(hosts.update().where(hosts.c.id == host["id"]).values(
            provider_ext_id=source["provider_ext_id"], provider_ref=source["provider_ref"],
            kind=source["kind"], status=source["status"], metadata=metadata,
        ))
        merged[source["id"]] = host["id"]
    return merged


def restore_merges(connection) -> None:
    schema = sa.MetaData()
    schema.reflect(connection)
    hosts = schema.tables["hosts"]
    journal = schema.tables["host_merges"]
    for merge in connection.execute(sa.select(journal)).mappings():
        snapshot = merge["snapshot"]
        current = connection.execute(sa.select(hosts).where(hosts.c.id == merge["host_id"])).mappings().first()
        if current is None:
            continue
        connection.execute(hosts.update().where(hosts.c.id == merge["host_id"]).values(**_values(hosts, snapshot["host"])))
        connection.execute(hosts.insert().values(**_values(hosts, snapshot["source"])))
        for change in reversed(snapshot["changes"]):
            table = schema.tables.get(change["table"])
            if table is None:
                continue
            if "deleted" in change:
                connection.execute(table.insert().values(**_values(table, change["deleted"])))
            else:
                connection.execute(table.update().where(_where(table, _values(table, change["key"]))).values(
                    **_values(table, change["before"]),
                ))
