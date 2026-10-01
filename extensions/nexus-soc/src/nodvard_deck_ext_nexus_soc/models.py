"""Dauerhafte Vorfalls-Historie -- eigene `Base`/`MetaData` wie bei
inventory/documents, eigener Alembic-Branch (`migrations/versions/`), Pflicht-Praefix
`ext_nexus_soc_` (von `ctx.db.declare_tables()` beim `setup()` geprueft).

Vorher lebte die Historie nur in einer `deque(maxlen=200)` im Prozess: nach jedem
Neustart/Deploy war die SOC-Seite leer, und Bestaetigen/Verwerfen ging ebenso
verloren. Die Warteschlange des laufenden Batches und die Cooldowns liegen im Speicher
(`incidents.py`); damit ein Neustart im Sammelfenster keinen Vorfall verliert, steht
jeder Vorfall zusaetzlich sofort in `ext_nexus_soc_incident_queue` (`IncidentQueueRecord`)
und wird beim Start wieder aufgenommen.

Jede Spalte hier hat ihre Entsprechung 1:1 in der Migration -- beide Dateien muessen
zusammen geaendert werden.
"""

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

    # Die Vorfall-ID aus `incidents.new_incident_id()` -- dieselbe, die als
    # `correlation_id` im Kern-Audit-Log steht (Verknuepfung Vorfall <-> Audit).
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
    """Noch nicht gemeldeter Vorfall des laufenden Sammelfensters. Wird sofort beim
    Erkennen geschrieben ("offen") und erst nach der Verarbeitung des Batches auf
    "verarbeitet" gesetzt -- so geht ein Vorfall bei einem Neustart innerhalb des
    Fensters nicht verloren, sondern wird beim Start wieder aufgenommen.
    Spalten 1:1 wie in der Migration b8c9d0e1f2a3."""

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
    # Zeitpunkt, an dem der Eintrag "verarbeitet" oder "fehlgeschlagen" wurde (fuers Aufraeumen)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Fehlgeschlagene Verarbeitungsversuche; nach MAX_ATTEMPTS wird der Eintrag "fehlgeschlagen".
    attempts: Mapped[int] = mapped_column(Integer(), default=0)
    # Schon angelegter Aktionsvorschlag samt KI-Text: ein erneuter Versuch schlaegt nichts
    # ein zweites Mal vor, sondern meldet mit diesem Stand weiter.
    action_id: Mapped[str | None] = mapped_column(String(64))
    ai_summary: Mapped[str | None] = mapped_column(Text())
    # Gesetzt, BEVOR ein Aktionsvorschlag angelegt wird (bei voller Autonomie fuehrt ihn das Gate
    # sofort aus). Steht es nach einem Neustart ohne `action_id` da, ist unklar, ob der Vorschlag
    # entstanden ist: dann wird nur gemeldet, nie ein zweiter vorgeschlagen.
    proposal_started: Mapped[bool] = mapped_column(Boolean(), default=False)


# ---------------------------------------------------------------------------
# Virenschutz: Scans, Funde/Quarantaene, Haertungs-Audits.
# Spalten 1:1 wie in der Migration e5f6a7b8c9d0 -- beide Dateien zusammen aendern.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Update-Zentrale, Einbruchschutz und Datei-Waechter.
# Spalten 1:1 wie in der Migration f6a7b8c9d0e1 (+ a7b8c9d0e1f2) -- beide Dateien zusammen aendern.
# ---------------------------------------------------------------------------


class UpdateRunRecord(Base):
    """Ein Einspiel-Lauf (Updates, Aufraeumen, Neustart) -- manuell oder automatisch."""

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
    # Migration a7b8c9d0e1f2: Name des entkoppelten Laufs auf dem Server
    # (`detached.py`). Gesetzt = nach einem Dashboard-Neustart wieder aufnehmen.
    remote_id: Mapped[str | None] = mapped_column(String(64))


class BaselineRecord(Base):
    """Zuletzt gesehener/bestaetigter Stand je Server und Art: "updates" (letzte
    Update-Pruefung), "ports" (bekannte offene Ports), "logins" (bekannte Login-IPs),
    "files" (Pruefsummen ueberwachter Dateien), "guard" (letzter Einbruchschutz-Blick)."""

    __tablename__ = "ext_nexus_soc_baselines"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<host_id>:<kind>"
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON(), default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SecurityEventRecord(Base):
    """Sicherheitsereignis aus Einbruchschutz/Datei-Waechter (Brute-Force, Login von
    neuer IP, neuer offener Port, geaenderte Systemdatei ...)."""

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
