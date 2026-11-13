"""Hand-Server und erkannte Server zuordnen."""

import sqlalchemy as sa
from alembic import op

from nodvard_deck.db.host_merge_v1 import merge_matches, normalize_address, restore_merges

revision = "c8e42a91d6b3"
down_revision = "6d9e2a4f8b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_merges",
        sa.Column("source_id", sa.String(36), primary_key=True),
        sa.Column("host_id", sa.String(36), sa.ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
    )
    connection = op.get_bind()
    hosts = sa.Table("hosts", sa.MetaData(), autoload_with=connection)
    identities = {}
    rows = list(connection.execute(sa.select(hosts).where(hosts.c.provider_ext_id == "proxmox")).mappings())
    node_addresses = {normalize_address(host["address"]) for host in rows if host["kind"] == "hypervisor"}
    for host in rows:
        metadata = host["metadata"] or {}
        addresses = []
        if host["kind"] == "hypervisor":
            addresses = [host["address"], metadata.get("node", "")]
        elif host["kind"] in ("vm", "lxc"):
            addresses = [host["display_name"]]
            if metadata.get("address_source") in ("guest-agent", "container") or (
                metadata.get("address_source") != "placeholder" and normalize_address(host["address"]) not in node_addresses
            ):
                addresses.append(host["address"])
        identities[host["id"]] = addresses
    merge_matches(connection, identities)


def downgrade() -> None:
    restore_merges(op.get_bind())
    op.drop_table("host_merges")
