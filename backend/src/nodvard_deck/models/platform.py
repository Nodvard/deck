"""Extension-Registry, Connectoren, Zeitplan, Läufe, Benachrichtigungen, Einstellungen."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..config import LOCAL_TIMEZONE
from ..db.base import Base, IdMixin, TimestampMixin, UTCDateTime, utcnow


class ExtensionRecord(Base, TimestampMixin):
    __tablename__ = "extensions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    api_version: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="disabled")
    """enabled | disabled | error | incompatible | uninstalling.

    Bei `error` wird die Extension angezeigt, aber nicht geladen: Eine defekte darf das Dashboard nicht lahmlegen."""

    source: Mapped[str] = mapped_column(String(16), default="local")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    granted_permissions: Mapped[list] = mapped_column(JSON, default=list)
    """Was der Admin bestätigt hat; kann weniger sein als beantragt."""

    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ConnectorInstance(Base, IdMixin, TimestampMixin):
    """Vom Nutzer angelegte Instanz eines Typs, den eine Extension registriert hat."""

    __tablename__ = "connector_instances"

    ext_id: Mapped[str] = mapped_column(String(64), index=True)
    type_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    """Secrets stehen hier nur als Referenz: {"api_key": {"$secret": "ollama-token"}}"""

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")
    health_message: Mapped[str | None] = mapped_column(String(255))
    last_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Job(Base, IdMixin, TimestampMixin):
    """Eine Tabelle für alle geplanten Arbeiten; die Gesamtansicht des Zeitplans ist damit ein einfaches SELECT."""

    __tablename__ = "jobs"
    __table_args__ = (Index("uq_jobs_ext_key", "ext_id", "ext_job_key", unique=True),)

    ext_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ext_job_key: Mapped[str | None] = mapped_column(String(128))
    """Stabiler Schlüssel aus `JobSpec.id` (von der Extension gewählt, nicht die generierte `Job.id`).
    `ctx.scheduler.trigger()` adressiert darüber, denn die Extension kennt ihre UUID nie. Eindeutig je `ext_id`,
    damit `register_job()` ein Upsert ist und ein erneutes Aktivieren nichts doppelt anlegt."""
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(64))
    schedule: Mapped[str] = mapped_column(String(64))  # Cron
    timezone: Mapped[str] = mapped_column(String(64), default=LOCAL_TIMEZONE)
    target: Mapped[dict] = mapped_column(JSON, default=dict)  # host_id / group_id / "all"
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36))


class JobRun(Base, IdMixin):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_runs_job_started", "job_id", "started_at"),)

    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    ext_id: Mapped[str | None] = mapped_column(String(64), index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # manual | schedule | ai | event
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    """running | succeeded | failed | interrupted | skipped.

    `interrupted` macht einen Neustart mitten im Lauf sichtbar: Beim Start setzt der Kern alle `running`-Läufe darauf.
    Ein Lauf, den das Herunterfahren abbricht, endet ebenfalls so."""

    host_id: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output_ref: Mapped[str | None] = mapped_column(String(255))
    """Zeigt auf /data/runs/<id>.log: Skriptausgaben sind unbegrenzt groß, Datenbankzeilen sollten es nicht sein."""

    error: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(36))


class Notification(Base, IdMixin):
    __tablename__ = "notifications"

    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    source_ext_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class NotificationDelivery(Base, IdMixin):
    """Protokolliert, ob der Versand geklappt hat, damit ein Kanal, der ins Leere meldet, auffällt."""

    __tablename__ = "notification_deliveries"

    notification_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notifications.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), primary_key=True, default="global")
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default="")
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(36))


class DashboardLayout(Base, IdMixin, TimestampMixin):
    __tablename__ = "dashboard_layouts"

    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128), default="Standard")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    items: Mapped[list] = mapped_column(JSON, default=list)
    """[{widget_id, ext_id, x, y, w, h, config}], für Web und App identisch."""
    groups: Mapped[list | None] = mapped_column(JSON, nullable=True)
    """Gruppen der Startseite in ihrer Reihenfolge, [{key, title, collapsed}]; leer heißt Vorgabe."""


class CustomApp(Base, IdMixin, TimestampMixin):
    """Eine eigene App-Kachel im Cockpit: ein von Hand angelegter Link ohne Container-Erkennung.

    Der Kern ruft die Adresse nie selbst ab (kein SSRF); Prüfung und Grenzen stehen in `services/custom_apps.py`."""

    __tablename__ = "custom_apps"

    name: Mapped[str] = mapped_column(String(60))
    url: Mapped[str] = mapped_column(String(1000))
    """Nur http:// oder https://, ohne Zugangsdaten und Steuerzeichen."""

    icon: Mapped[str | None] = mapped_column(String(32))
    """Name aus der festen Symbol-Liste (`services.custom_apps.APP_ICONS`) oder ein einzelnes Emoji,
    nie eine Bild-Adresse (Tracking, Mixed Content)."""

    color: Mapped[str | None] = mapped_column(String(7))
    """`#rrggbb` oder leer (dann wählt die Oberfläche eine Farbe nach dem Namen)."""

    group_name: Mapped[str | None] = mapped_column(String(40))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    open_in_new_tab: Mapped[bool] = mapped_column(Boolean, default=True)
    host_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="SET NULL"), index=True
    )
    """Optionaler Server-Bezug (nur zur Anzeige); wird der Server gelöscht, bleibt die App."""

    created_by_user_id: Mapped[str | None] = mapped_column(String(36))
