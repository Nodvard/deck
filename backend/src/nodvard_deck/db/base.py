"""SQLAlchemy-Basis.

Dialektneutral. Die Regeln aus docs/00-DECISIONS.md D-02 gelten hier woertlich:
kein JSONB, kein ARRAY, kein ILIKE, keine Dialekt-Funktionen. Listen als
Assoziationstabelle, Zeitstempel UTC-aware und in Python gesetzt.
"""

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
    """UUIDv7-artig: zeitsortierter Praefix + Zufall.

    Sortierbar (gut fuer Index-Lokalitaet), dialektneutral als String(36), in Logs lesbar.
    TODO(phase1): auf uuid.uuid7() umstellen, sobald in der Standardbibliothek.
    """
    ts = int(datetime.now(UTC).timestamp() * 1000)
    rand = uuid.uuid4().hex
    return f"{ts:012x}-{rand[0:4]}-{rand[4:8]}-{rand[8:12]}-{rand[12:24]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """`DateTime(timezone=True)` mit SQLite-Rundreise-Garantie.

    **Gefunden live beim WP-1-Boot-Test, nicht beim Lesen:** SQLite hat keinen nativen
    `timestamptz`-Typ. Ein als UTC-aware geschriebener Wert wird korrekt gespeichert,
    kommt beim naechsten Lesen ueber eine NEUE Query aber als *naive* `datetime` zurueck
    (SQLite kennt nur Text/Zahlen, die Zeitzone geht beim Zwischenspeichern verloren).
    Jeder Vergleich einer so gelesenen Zeit gegen `datetime.now(UTC)` wirft
    `TypeError: can't compare offset-naive and offset-aware datetimes` -- exakt das
    Bild, das `RefreshToken.expires_at < utcnow()` beim Refresh-Rotation-Test lieferte.

    Auf Postgres (echtes `timestamptz`) sind `process_result_value`/`process_bind_param`
    Kein-Op, weil dort bereits aware datetimes ankommen -- die Behandlung ist also
    dialektneutral im Sinne von D-02, kein SQLite-Spezialpfad im Anwendungscode.

    Ersetzt `DateTime(timezone=True)` in JEDER Modelldatei (identity/infra/security/
    platform) -- nicht nur an der einen Stelle, die live gecrasht ist. Der Fehler war
    strukturell, nicht lokal: jede kuenftige "vergleiche DB-Zeit gegen jetzt"-Stelle
    (Audit-Retention in WP-2, Aktions-Ablauf in WP-5, Job-Terminierung in WP-6) haette
    ihn sonst einzeln neu gefunden.
    """

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
    """Pflicht nach `session.add(obj)` + Flush, BEVOR eine `relationship()` auf `obj`
    gelesen ODER beschrieben wird -- siehe docs/00-DECISIONS.md D-12 fuer die volle
    Herleitung. Kurzfassung: ein frisch per `session.add()` angelegtes Objekt hat seine
    `lazy="selectin"`-Collections nicht automatisch geladen (das passiert nur bei
    Objekten, die aus einer awaiteten Query kommen); jeder Zugriffsversuch loest einen
    synchronen Lazy-Load aus, der im Async-Kontext mit `MissingGreenlet` crasht.

    Dreimal live gefunden, nicht beim Lesen: `ensure_builtin_roles()` (WP-1, `Role.
    permissions`), ein eigener Test dafuer (WP-1, `User.roles`), `HostsHandle.
    upsert_discovered()` (WP-3, `Host.tags`). Diese Funktion macht den Fix an einer
    Stelle benennbar und grep-bar (`refresh_relationships`), ersetzt aber nicht das
    Nachdenken darueber, ob eine neu angelegte Zeile ueberhaupt eine kurz danach
    gelesene Relationship hat -- ein automatischer Check dafuer wurde bewusst NICHT
    gebaut (D-12 nennt den Grund: Daten-/Kontrollfluss-Analyse noetig, hohe Falsch-
    Positiv-/Negativ-Rate bei nur vier betroffenen Feldern im gesamten Schema)."""
    await session.refresh(obj, attribute_names=list(names))


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
