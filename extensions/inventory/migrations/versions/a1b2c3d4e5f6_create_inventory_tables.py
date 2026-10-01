"""create inventory tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-09-23 18:00:00.000000

Erste Revision der inventory-Extension -- eigener, vom Kern-Branch UNABHAENGIGER
Alembic-Kopf (`down_revision=None`, eigener `branch_labels`-Wert), wie
docs/02-EXTENSION-API.md §7 es vorsieht. `python -m nodvard_deck.migrate` (backend/src/
nodvard_deck/migrate.py) findet dieses Verzeichnis automatisch und faehrt es zusammen mit
dem Kern-Branch auf den jeweils neuesten Stand ("upgrade heads", Mehrzahl).

Tabellennamen zwingend mit `ext_inventory_` praefixiert (docs/02 §7,
`ExtensionManifest.table_prefix`) -- zusaetzlich zur Migration selbst validiert
`DbHandle.declare_tables()` das beim `setup()`-Aufruf noch einmal gegen die
tatsaechlich deklarierten ORM-Modelle (siehe nodvard_deck_ext_inventory/models.py).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = ("inventory",)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ext_inventory_categories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ext_inventory_locations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "parent_id", sa.String(length=36),
            sa.ForeignKey("ext_inventory_locations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_inventory_locations_parent_id", "ext_inventory_locations", ["parent_id"])

    op.create_table(
        "ext_inventory_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category_id", sa.String(length=36),
            sa.ForeignKey("ext_inventory_categories.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "location_id", sa.String(length=36),
            sa.ForeignKey("ext_inventory_locations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("purchase_price_cents", sa.Integer(), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_inventory_items_category_id", "ext_inventory_items", ["category_id"])
    op.create_index("ix_ext_inventory_items_location_id", "ext_inventory_items", ["location_id"])
    op.create_index("ix_ext_inventory_items_name", "ext_inventory_items", ["name"])

    op.create_table(
        "ext_inventory_item_images",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "item_id", sa.String(length=36),
            sa.ForeignKey("ext_inventory_items.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("filename", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ext_inventory_item_images_item_id", "ext_inventory_item_images", ["item_id"])


def downgrade() -> None:
    op.drop_table("ext_inventory_item_images")
    op.drop_table("ext_inventory_items")
    op.drop_table("ext_inventory_locations")
    op.drop_table("ext_inventory_categories")
