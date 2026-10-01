"""recovery_codes (einmalige Wiederherstellungs-Codes fuer die Zwei-Faktor-Anmeldung)

Revision ID: e7a1c4b93d52
Revises: c3f1a7d92b64
Create Date: 2026-09-30 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a1c4b93d52'
down_revision: Union[str, Sequence[str], None] = 'c3f1a7d92b64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('recovery_codes',
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('code_hash', sa.String(length=255), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_recovery_codes_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recovery_codes'))
    )
    op.create_index(op.f('ix_recovery_codes_user_id'), 'recovery_codes', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_recovery_codes_user_id'), table_name='recovery_codes')
    op.drop_table('recovery_codes')
