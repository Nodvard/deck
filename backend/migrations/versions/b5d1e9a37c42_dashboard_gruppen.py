"""dashboard_gruppen (Gruppen der Startseite an `dashboard_layouts`)

`dashboard_layouts.groups`: Reihenfolge, Namen und Zuklappen der Widget-Gruppen, leer bei allen bestehenden
Layouts (die Oberfläche nimmt dann die Vorgabe).

Rein additiv, bestehende Zeilen bleiben unverändert. Zurück auf eine ältere Version geht wie bei jeder
Kern-Migration über die Kopie von vor dem Update (`boot` legt sie an; ein älteres Image kennt diese
Revision nicht). `downgrade` entfernt die Spalte wieder.

Revision ID: b5d1e9a37c42
Revises: a6c2e8d41f57
Create Date: 2026-10-17 21:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b5d1e9a37c42'
down_revision: Union[str, Sequence[str], None] = 'a6c2e8d41f57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('dashboard_layouts', sa.Column('groups', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('dashboard_layouts') as batch_op:
        batch_op.drop_column('groups')
