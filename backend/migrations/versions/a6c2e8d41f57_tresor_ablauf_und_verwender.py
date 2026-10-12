"""tresor_ablauf_und_verwender (Ablaufdatum an `secrets`, neue Tabelle `secret_usages`)

`secrets.expires_at`: wann ein Zugang ablaeuft oder erneuert werden soll (nur Angabe, leer bei allen
bestehenden Eintraegen).

`secret_usages`: wer ein Geheimnis tatsaechlich benutzt hat und wann zuletzt, je Verwender eine Zeile.
Startet leer; gefuellt wird sie beim naechsten Zugriff (`core.vault.vault_use`).

Rein additiv, bestehende Zeilen bleiben unveraendert. Zurueck auf eine aeltere Version geht wie bei jeder
Kern-Migration ueber die Kopie von vor dem Update (`boot` legt sie an; ein aelteres Image kennt diese
Revision nicht). `downgrade` entfernt Spalte und Tabelle wieder.

Revision ID: a6c2e8d41f57
Revises: f3a9c6d18e24
Create Date: 2026-10-12 08:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a6c2e8d41f57'
down_revision: Union[str, Sequence[str], None] = 'f3a9c6d18e24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('secrets', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'secret_usages',
        sa.Column('secret_id', sa.String(length=36), nullable=False),
        sa.Column('consumer_type', sa.String(length=16), nullable=False),
        sa.Column('consumer_id', sa.String(length=128), nullable=False),
        sa.Column('first_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['secret_id'], ['secrets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('secret_id', 'consumer_type', 'consumer_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('secret_usages')
    with op.batch_alter_table('secrets') as batch_op:
        batch_op.drop_column('expires_at')
