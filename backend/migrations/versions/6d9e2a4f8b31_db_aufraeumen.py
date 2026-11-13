"""Ungenutzte Tabelle und redundante Indizes entfernen, Filterindizes ergänzen.

Revision ID: 6d9e2a4f8b31
Revises: b5d1e9a37c42
Create Date: 2026-11-07 10:00:00.000000
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6d9e2a4f8b31"
down_revision: Union[str, Sequence[str], None] = "b5d1e9a37c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    if sa.inspect(connection).has_table("secret_grants"):
        grants = sa.table("secret_grants", sa.column("secret_id", sa.String(36)))
        if connection.execute(sa.select(grants.c.secret_id).limit(1)).first() is not None:
            logging.getLogger("alembic.runtime.migration").warning(
                "secret_grants enthält Daten und bleibt erhalten."
            )
        else:
            op.drop_table("secret_grants")

    op.drop_index("ix_action_flap_history_fingerprint", table_name="action_flap_history")
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
    op.drop_index("ix_jobs_ext_id", table_name="jobs")
    op.drop_index("ix_actions_status", table_name="actions")
    op.create_index("ix_actions_host_id", "actions", ["host_id"], unique=False)
    op.create_index("ix_notifications_read_at_ts", "notifications", ["read_at", "ts"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notifications_read_at_ts", table_name="notifications")
    op.drop_index("ix_actions_host_id", table_name="actions")
    op.create_index("ix_actions_status", "actions", ["status"], unique=False)
    op.create_index("ix_jobs_ext_id", "jobs", ["ext_id"], unique=False)
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"], unique=False)
    op.create_index("ix_action_flap_history_fingerprint", "action_flap_history", ["fingerprint"], unique=False)

    if not sa.inspect(op.get_bind()).has_table("secret_grants"):
        op.create_table(
            "secret_grants",
            sa.Column("secret_id", sa.String(36), nullable=False),
            sa.Column("grantee_type", sa.String(16), nullable=False),
            sa.Column("grantee_id", sa.String(64), nullable=False),
            sa.ForeignKeyConstraint(
                ["secret_id"], ["secrets.id"],
                name=op.f("fk_secret_grants_secret_id_secrets"), ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("secret_id", "grantee_type", "grantee_id", name=op.f("pk_secret_grants")),
        )
