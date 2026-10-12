"""Hosts, Gruppen, Zugangsdaten, Host-Keys.

Hosts gehören dem Kern; Erweiterungen entdecken sie und tragen sie über `provider_ext_id` und `provider_ref` hier ein."""

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
    """Fremdschlüssel beim Anbieter; der Kern liest ihn nie inhaltlich."""

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
    """Markierung eines Hosts: eine Extension fragt "alle Hosts mit Tag docker", statt eine eigene Liste zu pflegen."""

    __tablename__ = "host_tags"

    host_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    managed_by_ext_id: Mapped[str | None] = mapped_column(String(64))
    """Welche Extension den Tag gesetzt hat; leer bei manuell gesetzten Tags.

    `upsert_discovered()` entfernt beim Abgleich nur Tags mit der eigenen `ext_id`, nie manuelle oder fremde."""


class HostGroup(Base, IdMixin, TimestampMixin):
    """Zielgruppen für Skripte."""

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
    """SSH-Host-Key eines Servers: TOFU beim ersten Kontakt, danach Pinning.

    Eine Abweichung lässt die Verbindung sichtbar scheitern, statt sie stillschweigend anzunehmen."""

    __tablename__ = "known_hosts"

    host_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True
    )
    key_type: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(36))
