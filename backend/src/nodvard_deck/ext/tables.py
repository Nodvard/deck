"""Praefix-Erzwingung fuer Extension-Tabellen (docs/02-EXTENSION-API.md §7).

`ExtensionManifest.table_prefix` (nodvard_sdk) legt den erwarteten Praefix bereits fest
(`ext_<id>_`, `-` -> `_`); diese Funktion ist der Kern-seitige Torwaechter, der eine
Extension beim Laden zurueckweist, wenn ihre eigene SQLAlchemy-Metadata Tabellen
ausserhalb dieses Praefixes deklariert.

Seit der `inventory`-Extension (eigene Tabellen `ext_inventory_*`) real verifiziert:
`Extension.setup()` ruft `ctx.db.declare_tables(Base.metadata)` (siehe
`ext/context.py::DbHandle.declare_tables`), was diese Funktion mit dem
`table_prefix` aus dem Manifest aufruft. Der Alembic-Branch-pro-Extension-Mechanismus
(separates `alembic upgrade heads`, docs/02 §7) ist ebenfalls gebaut --
`nodvard_deck.migrate.upgrade_heads()` discovert `extensions/*/migrations/versions/`
dynamisch und migriert alle Branches (eigener `down_revision=None` +
`branch_labels` pro Extension) in einem Lauf; siehe `backend/tests/test_migrate.py`.
"""

from __future__ import annotations

from collections.abc import Iterable


class TablePrefixViolation(Exception):
    pass


def validate_table_prefix(
    ext_id: str, table_names: Iterable[str], *, expected_prefix: str | tuple[str, ...]
) -> None:
    """`expected_prefix`: ein Praefix oder mehrere (nach einer Umbenennung zusaetzlich die der alten
    Kennungen, siehe `ExtensionManifest.table_prefixes`); jede Tabelle muss mit einem davon beginnen."""
    prefixes = (expected_prefix,) if isinstance(expected_prefix, str) else tuple(expected_prefix)
    violations = [name for name in table_names if not name.startswith(prefixes)]
    if violations:
        allowed = " oder ".join(f"'{p}'" for p in prefixes)
        raise TablePrefixViolation(
            f"Extension '{ext_id}': Tabellen {sorted(violations)} verletzen den "
            f"Pflicht-Präfix {allowed} (docs/02-EXTENSION-API.md §7)."
        )
