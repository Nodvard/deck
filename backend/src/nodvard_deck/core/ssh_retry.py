from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import session_scope
from ..models import Setting

KEY = "ssh.auth_retry"


@dataclass
class RetryState:
    failures: int = 0
    retry_at: float = 0

    def failed(self) -> RetryState:
        failures = min(self.failures + 1, 7)
        delay = min(1800 * 2 ** (failures - 3), 21600) if failures >= 3 else 0
        return RetryState(failures, time.time() + delay if delay else 0)


async def load(session: AsyncSession, host_id: str) -> RetryState:
    row = await session.get(Setting, (KEY, "host", host_id))
    return RetryState(**row.value) if row else RetryState()


async def clear(session: AsyncSession, host_id: str) -> None:
    await session.execute(delete(Setting).where(Setting.key == KEY, Setting.scope == "host", Setting.user_id == host_id))


async def save(host_id: str, state: RetryState) -> None:
    async with session_scope() as session:
        row = await session.get(Setting, (KEY, "host", host_id))
        if state.failures:
            if row is None:
                row = Setting(key=KEY, scope="host", user_id=host_id)
                session.add(row)
            row.value = asdict(state)
        elif row is not None:
            await session.delete(row)
