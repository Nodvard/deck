"""Tabellen der Erweiterung (eigene `Base`, Präfix `ext_nexus_soc_`); Spalten stehen 1:1 in den Migrationen."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nodvard_deck.db.base import UTCDateTime
from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IncidentRecord(Base):
    __tablename__ = "ext_nexus_soc_incidents"

    # Dieselbe ID steht als `correlation_id` im Kern-Audit-Log.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str | None] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(255), index=True)
    target: Mapped[str] = mapped_column(String(255), index=True)
    message: Mapped[str] = mapped_column(Text())
    is_crash: Mapped[bool] = mapped_column(Boolean(), default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
    # open | proposed | reviewed | resolved | dismissed
    status: Mapped[str] = mapped_column(String(20), index=True)
    ai_summary: Mapped[str | None] = mapped_column(Text())
    action_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class IncidentQueueRecord(Base):
    """Noch nicht gemeldeter Vorfall des Sammelfensters; wird nach einem Neustart wieder aufgenommen."""

    __tablename__ = "ext_nexus_soc_incident_queue"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # = Vorfall-ID
    host_id: Mapped[str | None] = mapped_column(String(64))
    host_name: Mapped[str] = mapped_column(String(255))
    target: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text())
    details: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
    # offen | verarbeitet | fehlgeschlagen
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    # Für das Aufräumen alter Einträge.
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Nach MAX_ATTEMPTS Fehlversuchen wird der Eintrag "fehlgeschlagen".
    attempts: Mapped[int] = mapped_column(Integer(), default=0)
    # Ein erneuter Versuch schlägt nichts ein zweites Mal vor, sondern meldet mit diesem Stand weiter.
    action_id: Mapped[str | None] = mapped_column(String(64))
    ai_summary: Mapped[str | None] = mapped_column(Text())
    # Wird vor dem Vorschlag gesetzt (bei voller Autonomie führt das Gate ihn sofort aus). Fehlt danach
    # `action_id`, ist unklar, ob er entstand: dann nur melden, nie ein zweiter Vorschlag.
    proposal_started: Mapped[bool] = mapped_column(Boolean(), default=False)


# Virenschutz: Scans, Funde, Härtungs-Audits.


class ScanRecord(Base):
    __tablename__ = "ext_nexus_soc_scans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(255))
    # quick | deep | watch | custom
    kind: Mapped[str] = mapped_column(String(16), index=True)
    paths: Mapped[list[str]] = mapped_column(JSON(), default=list)
    # manual | schedule
    trigger: Mapped[str] = mapped_column(String(16))
    # running | clean | infected | error
    status: Mapped[str] = mapped_column(String(16), index=True)
    files_scanned: Mapped[int | None] = mapped_column(Integer())
    infected: Mapped[int] = mapped_column(Integer(), default=0)
    error: Mapped[str | None] = mapped_column(Text())
    output_tail: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class FindingRecord(Base):
    __tablename__ = "ext_nexus_soc_findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scan_id: Mapped[str | None] = mapped_column(String(64), index=True)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text())
    signature: Mapped[str] = mapped_column(String(255))
    # detected | quarantined | restored | deleted | ignored
    status: Mapped[str] = mapped_column(String(16), index=True)
    quarantine_path: Mapped[str | None] = mapped_column(Text())
    original_mode: Mapped[str | None] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(Text())
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditRecord(Base):
    __tablename__ = "ext_nexus_soc_audits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(255))
    # ok | error
    status: Mapped[str] = mapped_column(String(16))
    hardening_index: Mapped[int | None] = mapped_column(Integer())
    warnings: Mapped[list[str]] = mapped_column(JSON(), default=list)
    suggestions: Mapped[list[str]] = mapped_column(JSON(), default=list)
    error: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


# Update-Zentrale, Einbruchschutz und Datei-Wächter.


class UpdateRunRecord(Base):
    """Ein Einspiel-Lauf (Updates, Aufräumen, Neustart), manuell oder automatisch."""

    __tablename__ = "ext_nexus_soc_update_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(255))
    # all | security | cleanup | reboot
    mode: Mapped[str] = mapped_column(String(16))
    # manual | schedule
    trigger: Mapped[str] = mapped_column(String(16))
    # running | ok | error
    status: Mapped[str] = mapped_column(String(16), index=True)
    upgraded: Mapped[int] = mapped_column(Integer(), default=0)
    summary: Mapped[str | None] = mapped_column(Text())
    output_tail: Mapped[str | None] = mapped_column(Text())
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Name des entkoppelten Laufs auf dem Server (`detached.py`); gesetzt heißt: nach einem Neustart wieder aufnehmen.
    remote_id: Mapped[str | None] = mapped_column(String(64))


class BaselineRecord(Base):
    """Zuletzt gesehener oder bestätigter Stand je Server und Art (updates, ports, logins, files, guard)."""

    __tablename__ = "ext_nexus_soc_baselines"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<host_id>:<kind>"
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SecurityEventRecord(Base):
    """Sicherheitsereignis aus Einbruchschutz oder Datei-Wächter."""

    __tablename__ = "ext_nexus_soc_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # info | warning | critical
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
    acknowledged: Mapped[bool] = mapped_column(Boolean(), default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
