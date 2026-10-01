"""Nutzer, Rollen, Berechtigungen, Sitzungen. Siehe docs/03-DATA-MODEL.md §1."""

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
    """Gesetzt erst NACH einer erfolgreich verifizierten Setup-Bestaetigung
    (POST /me/totp/confirm) -- solange NULL, ist 2FA angelegt aber nicht aktiv, und der
    Login verlangt noch keinen Code. Getrennt von `totp_secret_id IS NOT NULL`, weil ein
    Secret sonst schon waehrend des Scan-QR-Codes im Setup-Schritt "aktiv" waere, bevor
    der Nutzer bewiesen hat, dass er es wirklich eingerichtet hat."""

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    """Genau einer. Umgeht RBAC und kann sich das nicht selbst entziehen — sonst kann
    eine unglueckliche Rollenaenderung die Installation aussperren."""

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
    """Berechtigungen sind Strings, keine Tabelle mit Fremdschluessel.

    Damit koennen Extensions eigene Berechtigungen mitbringen, ohne dass eine Migration
    noetig ist. Format: <domaene>.<aktion>[:<scope>], Suffix-Wildcard erlaubt
    (z. B. secrets.read:ssh-*).
    """

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
    """web | android | cli — entscheidet Cookie vs. Body. Der Access-Token-Pfad ist fuer
    alle identisch; nur daran haengt die Zusage 'eine API fuer Web und App'."""

    device_name: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    replaced_by_id: Mapped[str | None] = mapped_column(String(36))
    """Nachfolger-Sitzung, wenn dieser Token durch ROTATION widerrufen wurde (Refresh).
    Bleibt leer bei Abmelden/Passwortwechsel -- nur so weiss die Gnadenfrist in
    `services.auth.refresh_access_token`, welche Widerrufe sie verzeihen darf."""
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RecoveryCode(Base, IdMixin):
    """Einmalige Wiederherstellungs-Codes fuer die Zwei-Faktor-Anmeldung (je Nutzer 10).

    Gespeichert wird nur ein Argon2id-Hash (wie bei Passwoertern), nie der Code selbst. Ein
    Code hat nur ~50 Bit Zufall und ist damit deutlich schwaecher als ein Token
    (`RefreshToken.token_hash`, 256 Bit, reicht SHA-256) -- ein schneller Hash liesse sich
    bei einem Datenbank-Leck offline durchprobieren, ein langsamer nicht. Geprueft wird nur
    nach richtigem Passwort und hinter der Drosselung, die Kosten (10 Hashes je Versuch)
    fallen also nicht ins Gewicht."""

    __tablename__ = "recovery_codes"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(255))
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """Gesetzt beim Einloesen -- ein benutzter Code gilt nie wieder."""
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApiToken(Base, IdMixin, TimestampMixin):
    """Fuer Automatisierung und Langzeitnutzung durch die App.

    Ersatz fuer die heutigen ungeschuetzten Endpunkte, die per Cron angesprochen werden.
    """

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
