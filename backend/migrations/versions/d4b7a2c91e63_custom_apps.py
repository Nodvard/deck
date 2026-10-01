"""custom_apps (eigene App-Kacheln im Cockpit: Name, Adresse, Symbol, Gruppe, optional ein Server)

Revision ID: d4b7a2c91e63
Revises: e7a1c4b93d52
Create Date: 2026-10-01 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4b7a2c91e63'
down_revision: Union[str, Sequence[str], None] = 'e7a1c4b93d52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('custom_apps',
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=False),
    sa.Column('icon', sa.String(length=32), nullable=True),
    sa.Column('color', sa.String(length=7), nullable=True),
    sa.Column('group_name', sa.String(length=40), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('open_in_new_tab', sa.Boolean(), nullable=False),
    sa.Column('host_id', sa.String(length=36), nullable=True),
    sa.Column('created_by_user_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], name=op.f('fk_custom_apps_host_id_hosts'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_apps'))
    )
    op.create_index(op.f('ix_custom_apps_host_id'), 'custom_apps', ['host_id'], unique=False)
    op.create_index(op.f('ix_custom_apps_sort_order'), 'custom_apps', ['sort_order'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_custom_apps_sort_order'), table_name='custom_apps')
    op.drop_index(op.f('ix_custom_apps_host_id'), table_name='custom_apps')
    op.drop_table('custom_apps')
