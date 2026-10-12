"""inventory: Homelab-Baukasten (Hersteller, Modell, Seriennummer, Produktlink, Server, Bauprojekte, Racks)

Neue Spalten an `ext_inventory_items` und zwei neue Tabellen:

- `manufacturer`, `model`, `serial_number`, `product_url`: woran man ein Teil wiedererkennt und wo man es
  nachkauft (der Preisvergleich baut daraus nur einen Such-Link, er liest keine fremde Seite aus).
- `host_id`: der Server in Nodvard Deck, zu dem das Teil gehört (oder aus dem es ausgelesen wurde).
  Bewusst ohne Fremdschlüssel auf die Kern-Tabelle `hosts`: eine Erweiterung hängt nicht am Schema des
  Kerns, und ein gelöschter Server soll das Teil nicht mitnehmen.
- `project_id` (`ext_inventory_projects`): ein Bauprojekt („NAS bauen“) mit Status, Budget und Teileliste.
- `rack_id`, `rack_position`, `rack_units` (`ext_inventory_racks`): wo ein Gerät im Rack steckt
  (unterste Höheneinheit und Höhe in HE).

`project_id` und `rack_id` sind ebenfalls ohne Fremdschlüssel angelegt: SQLite kann einen nur beim
Neuanlegen der Tabelle mitgeben, und das Umkopieren von `ext_inventory_items` würde mit
`PRAGMA foreign_keys=ON` die Bilder (`ON DELETE CASCADE`) mitlöschen. Beim Löschen eines Projekts oder
Racks setzt die Erweiterung die Verweise selbst auf NULL (`__init__.py`).

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-08 18:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _item_columns() -> list[sa.Column]:
    return [
        sa.Column("manufacturer", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("serial_number", sa.String(length=200), nullable=True),
        sa.Column("product_url", sa.String(length=2000), nullable=True),
        sa.Column("host_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("rack_id", sa.String(length=36), nullable=True),
        sa.Column("rack_position", sa.Integer(), nullable=True),
        sa.Column("rack_units", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "ext_inventory_projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="planned"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("budget_cents", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ext_inventory_racks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("height_units", sa.Integer(), nullable=False, server_default="42"),
        sa.Column(
            "location_id", sa.String(length=36),
            sa.ForeignKey("ext_inventory_locations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in _item_columns():
        op.add_column("ext_inventory_items", column)
    op.create_index("ix_ext_inventory_items_project_id", "ext_inventory_items", ["project_id"])
    op.create_index("ix_ext_inventory_items_rack_id", "ext_inventory_items", ["rack_id"])
    op.create_index("ix_ext_inventory_items_host_id", "ext_inventory_items", ["host_id"])


def downgrade() -> None:
    op.drop_index("ix_ext_inventory_items_host_id", table_name="ext_inventory_items")
    op.drop_index("ix_ext_inventory_items_rack_id", table_name="ext_inventory_items")
    op.drop_index("ix_ext_inventory_items_project_id", table_name="ext_inventory_items")
    # Spalten einzeln entfernen (SQLite ab 3.35 kann DROP COLUMN ohne Umkopieren; ein Umkopieren der
    # Tabelle würde mit PRAGMA foreign_keys=ON die Bilder über ON DELETE CASCADE mitlöschen).
    for column in reversed(_item_columns()):
        op.drop_column("ext_inventory_items", column.name)
    op.drop_table("ext_inventory_racks")
    op.drop_table("ext_inventory_projects")
