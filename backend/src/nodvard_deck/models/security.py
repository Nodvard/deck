"""Vault, Audit-Log, Aktions-Journal. Siehe docs/03-DATA-MODEL.md §§3-5."""

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
    """Verschluesselt mit Fernet. Invarianten (im Code erzwungen, nicht nur dokumentiert):

    1. Kein API-Endpunkt gibt `ciphertext` oder Klartext zurueck. Nie, auch nicht fuer den
       Owner. Schreiben und Ersetzen ja, Lesen nein.
    2. Extensions bekommen ein Handle; der Klartext lebt nur in `ctx.vault_use(...)`.
    3. Jede Materialisierung schreibt eine Audit-Zeile (Label + Verwender, ohne Wert).
    4. Der Master-Key liegt nie in der Datenbank.
    """

    __tablename__ = "secrets"

    label: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    """Ermoeglicht Rotation ohne Big-Bang: neue Secrets mit v2, alte werden beim naechsten
    Zugriff migriert."""

    owner_ext_id: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    created_by_user_id: Mapped[str | None] = mapped_column(String(36))
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    secret_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    """Nie sensibel — z. B. der Fingerprint eines Public Keys."""


class SecretGrant(Base):
    __tablename__ = "secret_grants"

    secret_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("secrets.id", ondelete="CASCADE"), primary_key=True
    )
    grantee_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # ext | role
    grantee_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class AuditEntry(Base, IdMixin):
    """Append-only. Kein UPDATE, kein DELETE aus der Anwendung — nur der Retention-Job
    loescht jenseits von `audit.retention_days`.

    `outcome='proposed'` von `outcome='success'` zu trennen ist der Unterschied zwischen
    "die KI wollte" und "die KI tat". Genau diese Unterscheidung fehlte im Bestand, und
    genau daraus entstanden Meldungen ueber Aktionen, die nie stattgefunden haben.
    """

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
    outcome: Mapped[str] = mapped_column(String(16))  # success|failure|denied|proposed

    reason: Mapped[str | None] = mapped_column(Text)
    """Hier landet die BEGRUENDUNG. Jede ausgefuehrte UND jede abgelehnte
    Aktion wird mit Begruendung geloggt."""

    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class Action(Base, IdMixin):
    """Das Gate-Journal — jede vorgeschlagene Aktion wird ein nachvollziehbarer Datensatz.

    Die Bestaetigungs-Karte in der UI *ist* eine Zeile mit status='proposed'.
    "Volle Autonomie" heisst, dass das Gate direkt auf 'approved' setzt: derselbe
    Datensatz, dieselbe Nachvollziehbarkeit, nur ohne Klick.
    """

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
    """NOT NULL. Die Begruendungspflicht ist im Schema verankert, nicht nur in der
    Anwendungslogik."""

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
    """Generalisiertes Anti-Flapping.

    Fingerprint ueber (host, action_type, normalisierter Payload) — greift damit bei jedem
    wiederholten Befehl, nicht nur bei Docker-Restarts wie in der alten Fassung. Und es
    gilt fuer *alle* Extensions, weil es im Kern sitzt: die autonome Remediation kann es
    nicht mehr umgehen, indem sie einen anderen Codepfad nimmt.
    """

    __tablename__ = "action_flap_history"
    __table_args__ = (Index("ix_flap_fingerprint_ts", "fingerprint", "ts"),)

    host_id: Mapped[str | None] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
