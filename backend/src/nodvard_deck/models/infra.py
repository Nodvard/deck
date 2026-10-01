"""Hosts, Gruppen, Zugangsdaten, Host-Keys. Siehe docs/03-DATA-MODEL.md §2.

Die Host-Identitaet gehoert dem Kern. Extensions *entdecken* Hosts und schreiben sie hier
hinein (provider_ext_id + provider_ref), besitzen sie aber nicht. Ohne diese Regel haette
jede Extension ihre eigene Host-Liste — genau der Ist-Zustand mit vier
auseinanderdriftenden Wahrheiten.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow

host_group_members = Table(
    "host_group_members",
    Base.metadata,
    Column("group_id", String(36), ForeignKey("host_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("host_id", String(36), ForeignKey("hosts.id", ondelete="CASCADE"), primary_key=True),
)


class Host(Base, IdMixin, TimestampMixin):
    __tablename__ = "hosts"
    __table_args__ = (
        UniqueConstraint("provider_ext_id", "provider_ref", name="provider_ref"),
    )

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    address: Mapped[str] = mapped_column(String(255))
    os_family: Mapped[str] = mapped_column(String(16), default="linux")
    kind: Mapped[str | None] = mapped_column(String(32))

    provider_ext_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    """Fremdschluessel beim Anbieter. Der Kern liest ihn nie inhaltlich."""

    is_managed: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    host_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    tags: Mapped[list["HostTag"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    credentials: Mapped[list["HostCredential"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class HostTag(Base):
    """Tags ersetzen die Sonderlisten des Bestands (DOCKER_HOSTS, CRITICAL_CONTAINERS).

    Eine Extension fragt "alle Hosts mit Tag docker" statt eine eigene Liste zu pflegen,
    die unbemerkt veraltet — und die, wie im Bestand beobachtet, sogar korrekt sein kann,
    ohne dass der Code sie liest.
    """

    __tablename__ = "host_tags"

    host_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    managed_by_ext_id: Mapped[str | None] = mapped_column(String(64))
    """Live gefunden (WP-12, gameserver-Extension): `HostsHandle.upsert_discovered()`s
    Tag-Abgleich (WP-8) behandelte den frisch entdeckten Tag-Satz als VOLLSTAENDIG und
    entfernte JEDEN Tag, der nicht darin vorkam -- auch einen, den ein Admin oder eine
    ANDERE Extension (z. B. gameserver: "diesen Host mit 'gameserver' taggen") gesetzt
    hatte. Ergebnis: proxmoxs 5-minuetiger Discovery-Zyklus loeschte einen manuell
    gesetzten Tag beim naechsten Lauf wieder -- exakt das "entdeckt, nicht gepflegt"-
    Versprechen (docs/02-EXTENSION-API.md Paragraph 6) in sein Gegenteil verkehrt,
    live im Browser reproduziert. Dieses Feld macht Tag-Eigentuemerschaft explizit:
    `upsert_discovered()` entfernt nur noch Tags, die SIE SELBST zuvor gesetzt hat
    (`managed_by_ext_id == eigene ext_id`) -- ein Tag mit `NULL` (manuell) oder einer
    ANDEREN `ext_id` bleibt unberuehrt, unabhaengig davon, ob der neu entdeckte
    Tag-Satz ihn enthaelt."""


class HostGroup(Base, IdMixin, TimestampMixin):
    """Zielgruppen fuer Skripte."""

    __tablename__ = "host_groups"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class HostCredential(Base, IdMixin, TimestampMixin):
    __tablename__ = "host_credentials"

    host_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))  # ssh_key | ssh_password | api_token
    username: Mapped[str] = mapped_column(String(64), default="root")
    port: Mapped[int] = mapped_column(Integer, default=22)
    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="RESTRICT")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)


class KnownHostKey(Base, IdMixin):
    """Ersetzt StrictHostKeyChecking=no aus dem Bestand.

    TOFU beim ersten Kontakt, danach Pinning. Eine Abweichung laesst die Verbindung
    sichtbar scheitern, statt sie stillschweigend anzunehmen.
    """

    __tablename__ = "known_hosts"

    host_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    key_type: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(36))
