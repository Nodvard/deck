"""Nutzer, Rollen, Berechtigungen, Sitzungen."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", String(36), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))

    totp_secret_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="SET NULL")
    )
    """Das 2FA-Secret liegt im Vault, nicht in dieser Tabelle."""

    totp_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """Erst nach bestätigter Einrichtung gesetzt (POST /me/totp/confirm); solange leer, verlangt der Login keinen Code.

    Nicht aus `totp_secret_id` ableiten: das Secret existiert schon beim Scannen des QR-Codes."""

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    """Genau einer; umgeht RBAC und kann sich das nicht selbst entziehen, sonst sperrt eine Rollenänderung
    die Installation aus."""

    locale: Mapped[str] = mapped_column(String(8), default="de")
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    roles: Mapped[list["Role"]] = relationship(secondary=user_roles, lazy="selectin")


class Role(Base, IdMixin, TimestampMixin):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)

    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )


class RolePermission(Base):
    """Berechtigungen sind Strings ohne eigene Tabelle, damit Extensions eigene mitbringen können, ohne Migration.

    Format: <domäne>.<aktion>[:<scope>], Suffix-Wildcard erlaubt (z. B. secrets.read:ssh-*)."""

    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(128), primary_key=True)

    role: Mapped[Role] = relationship(back_populates="permissions")


class RefreshToken(Base, IdMixin):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    """SHA-256 des opaken Werts. Der Wert selbst wird nie gespeichert."""

    client_type: Mapped[str] = mapped_column(String(16), default="web")
    """web | android | cli: entscheidet Cookie oder Body. Der Access-Token-Pfad ist für alle Clients gleich."""

    device_name: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replaced_by_id: Mapped[str | None] = mapped_column(String(36))
    """Nachfolger-Sitzung, wenn der Token durch Rotation (Refresh) widerrufen wurde; sonst leer.

    Nur daran erkennt die Gnadenfrist in `services.auth.refresh_access_token`, welche Widerrufe sie verzeihen darf."""
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RecoveryCode(Base, IdMixin):
    """Einmalige Wiederherstellungs-Codes für die Zwei-Faktor-Anmeldung (je Nutzer 10), nur als Argon2id-Hash."""

    # Argon2id statt SHA-256 wie beim Token: ein Code hat nur etwa 50 Bit Zufall, ein schneller Hash ließe sich nach
    # einem Datenbank-Leck offline durchprobieren. Geprüft wird nur nach richtigem Passwort und hinter der Drosselung,
    # die Kosten (10 Hashes je Versuch) fallen also nicht ins Gewicht.
    __tablename__ = "recovery_codes"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(255))
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """Gesetzt beim Einlösen; ein benutzter Code gilt nie wieder."""
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApiToken(Base, IdMixin, TimestampMixin):
    """Token für Automatisierung und Langzeitnutzung durch die App."""

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
