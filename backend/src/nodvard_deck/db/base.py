"""SQLAlchemy-Basis, dialektneutral: keine Dialekt-Funktionen, Listen als Assoziationstabelle, Zeitstempel UTC-aware."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_id() -> str:
    """UUIDv7-artig: zeitsortierter Präfix plus Zufall, als String(36) dialektneutral und in Logs lesbar."""
    # TODO: auf uuid.uuid7() umstellen, sobald die Standardbibliothek es hat.
    ts = int(datetime.now(UTC).timestamp() * 1000)
    rand = uuid.uuid4().hex
    return f"{ts:012x}-{rand[0:4]}-{rand[4:8]}-{rand[8:12]}-{rand[12:24]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """`DateTime(timezone=True)`, das auch unter SQLite aware zurückkommt."""

    # SQLite hat keinen timestamptz: ein aware geschriebener Wert käme beim nächsten Lesen naiv zurück, und der
    # Vergleich mit datetime.now(UTC) würde mit TypeError scheitern. Auf Postgres tun beide Methoden nichts, dort
    # kommen aware Werte an.
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


async def refresh_relationships(session: AsyncSession, obj: Any, *names: str) -> None:
    """Nach `session.add(obj)` und Flush nötig, bevor eine `relationship()` von `obj` gelesen oder beschrieben wird."""
    # Ein frisch angelegtes Objekt hat seine `lazy="selectin"`-Collections nicht geladen (das passiert nur bei
    # Objekten aus einer awaiteten Query); der Zugriff löst sonst einen synchronen Lazy-Load aus, der im
    # Async-Kontext mit MissingGreenlet scheitert.
    await session.refresh(obj, attribute_names=list(names))


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
