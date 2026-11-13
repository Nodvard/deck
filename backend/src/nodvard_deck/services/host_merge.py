from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.host_merge_v1 import merge_matches, normalize_address
from ..models import Host


async def merge_discovered(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Host))).scalars().all()
    addresses = {normalize_address(host.address) for host in rows if host.provider_ext_id is None}
    if not any(
        addresses.intersection(
            normalize_address(value) for value in (host.host_metadata or {}).get("identity_addresses", [])
            if isinstance(value, str) and value.strip()
        )
        for host in rows if host.provider_ext_id is not None
    ):
        return {}
    await session.execute(text("UPDATE hosts SET name = name WHERE provider_ext_id IS NULL"))
    connection = await session.connection()
    merged = await connection.run_sync(merge_matches)
    if merged:
        from .hosts import drop_pooled_connections

        for host in rows:
            if host.id in merged:
                await drop_pooled_connections(session, host.id)
                session.expunge(host)
        refreshed = await session.execute(select(Host).where(Host.id.in_(merged.values())).execution_options(populate_existing=True))
        refreshed.scalars().all()
    return merged
