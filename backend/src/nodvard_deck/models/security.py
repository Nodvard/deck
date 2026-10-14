"""Vault, Audit-Log, Aktions-Journal."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class Secret(Base, IdMixin, TimestampMixin):
    """Mit Fernet verschlüsseltes Secret; der Master-Key liegt nie in der Datenbank."""

    # Kein API-Endpunkt gibt `ciphertext` oder Klartext zurück, auch nicht dem Owner (schreiben ja, lesen nein).
    # Extensions bekommen ein Handle; Klartext gibt es nur in `ctx.vault_use(...)`. Jede Materialisierung wird
    # protokolliert (ohne Wert), gebündelt auf eine Zeile je Stunde und Verwender, weil Module regelmäßig abfragen.
    __tablename__ = "secrets"

    label: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    """Ermöglicht Rotation ohne Big-Bang: neue Secrets mit v2, alte werden beim nächsten Zugriff migriert."""

    owner_ext_id: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_by_user_id: Mapped[str | None] = mapped_column(String(36))
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    secret_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    """Nie sensibel — z. B. der Fingerprint eines Public Keys."""
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """Wann der Zugang abläuft oder erneuert werden soll (z. B. Token mit Ablaufdatum); nur eine Angabe,
    das Dashboard sperrt danach nichts."""


class SecretUsage(Base):
    """Wer ein Secret tatsächlich benutzt hat und wann zuletzt, je Verwender eine Zeile.

    Geschrieben von `core.vault.vault_use()`, höchstens einmal je Stunde und Verwender wie `Secret.last_used_at`."""

    # Feste Bindungen (Besitzer-Modul, Server-Zugang, Zwei-Faktor) stehen nicht hier, sondern werden beim Lesen aus
    # `owner_ext_id`, `host_credentials` und `users` abgeleitet.
    __tablename__ = "secret_usages"

    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="CASCADE"), primary_key=True
    )
    consumer_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # extension | user | ...
    consumer_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    first_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SecretGrant(Base):
    __tablename__ = "secret_grants"

    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="CASCADE"), primary_key=True
    )
    grantee_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # ext | role
    grantee_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class AuditEntry(Base, IdMixin):
    """Append-only: kein UPDATE und kein DELETE aus der Anwendung, nur der Retention-Job löscht (`audit.retention_days`)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_ts_action", "ts", "action"),
        Index("ix_audit_correlation", "correlation_id"),
    )

    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(16))  # user|extension|ai|scheduler|system
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    # "proposed" (die KI wollte) ist nicht "success" (die KI tat): ohne die Trennung würden Aktionen als geschehen
    # gemeldet, die nie stattgefunden haben.
    outcome: Mapped[str] = mapped_column(String(16))  # success|failure|denied|proposed

    reason: Mapped[str | None] = mapped_column(Text)
    """Die Begründung; jede ausgeführte und jede abgelehnte Aktion wird mit Begründung protokolliert."""

    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class Action(Base, IdMixin):
    """Das Gate-Journal: jede vorgeschlagene Aktion ist ein Datensatz, die Bestätigungskarte eine Zeile mit 'proposed'.

    Bei voller Autonomie setzt das Gate sie direkt auf 'approved', derselbe Datensatz, nur ohne Klick."""

    __tablename__ = "actions"
    __table_args__ = (
        Index("ix_actions_status_created", "status", "created_at"),
        Index("ix_actions_correlation", "correlation_id"),
    )

    ext_id: Mapped[str] = mapped_column(String(64), index=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    host_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="SET NULL")
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)

    proposed_by_type: Mapped[str] = mapped_column(String(16))
    proposed_by_id: Mapped[str] = mapped_column(String(128))

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    """NOT NULL: die Begründungspflicht steht im Schema, nicht nur in der Anwendungslogik."""

    gate_decision: Mapped[dict] = mapped_column(JSON, default=dict)
    """Welche Regel griff: deny_pattern, flap_limit, autonomy, rbac …"""

    approved_by_user_id: Mapped[str | None] = mapped_column(String(36))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )


class ActionFlapHistory(Base, IdMixin):
    """Anti-Flapping über alle Extensions: Fingerprint aus (host, action_type, normalisierter Payload).

    Weil es im Kern sitzt, kann die autonome Remediation es nicht über einen anderen Codepfad umgehen."""

    __tablename__ = "action_flap_history"
    __table_args__ = (Index("ix_flap_fingerprint_ts", "fingerprint", "ts"),)

    host_id: Mapped[str | None] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
