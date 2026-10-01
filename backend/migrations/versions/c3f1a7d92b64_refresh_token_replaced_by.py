"""refresh token replaced_by_id (Gnadenfrist bei der Rotation)

Revision ID: c3f1a7d92b64
Revises: 53b6fedfa84e
Create Date: 2026-09-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f1a7d92b64'
down_revision: Union[str, Sequence[str], None] = '53b6fedfa84e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('refresh_tokens', sa.Column('replaced_by_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('refresh_tokens', 'replaced_by_id')
