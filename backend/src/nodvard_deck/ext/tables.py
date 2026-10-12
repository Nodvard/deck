"""Präfix-Prüfung für Extension-Tabellen: Tabellen außerhalb des Manifest-Präfixes lehnen das Laden ab."""

from __future__ import annotations

from collections.abc import Iterable


class TablePrefixViolation(Exception):
    pass


def validate_table_prefix(
    ext_id: str, table_names: Iterable[str], *, expected_prefix: str | tuple[str, ...]
) -> None:
    """Jede Tabelle muss mit einem erlaubten Präfix beginnen (nach einer Umbenennung auch die der alten Kennungen)."""
    prefixes = (expected_prefix,) if isinstance(expected_prefix, str) else tuple(expected_prefix)
    violations = [name for name in table_names if not name.startswith(prefixes)]
    if violations:
        allowed = " oder ".join(f"'{p}'" for p in prefixes)
        raise TablePrefixViolation(
            f"Extension '{ext_id}': Tabellen {sorted(violations)} verletzen den "
            f"Pflicht-Präfix {allowed} (docs/02-EXTENSION-API.md §7)."
        )
