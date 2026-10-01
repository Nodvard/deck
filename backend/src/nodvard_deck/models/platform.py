"""Extension-Registry, Connectoren, Zeitplan, Laeufe, Benachrichtigungen, Einstellungen.

Siehe docs/03-DATA-MODEL.md §§6-9.
"""

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

    Eine Extension im Zustand `error` wird angezeigt, aber nicht geladen — eine defekte
    Erweiterung darf nie das Dashboard lahmlegen.
    """

    source: Mapped[str] = mapped_column(String(16), default="local")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    granted_permissions: Mapped[list] = mapped_column(JSON, default=list)
    """Was der Admin bestaetigt hat — kann weniger sein als beantragt."""

    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ConnectorInstance(Base, IdMixin, TimestampMixin):
    """Vom Nutzer angelegte Instanz eines von einer Extension registrierten Typs.

    Ersetzt strukturell die von Hand gepflegte infra.json/WEB_URLS-Wartung: ein neuer
    Gameserver ist eine Instanz bzw. ein entdeckter Host mit Tag, kein JSON-Eintrag.
    """

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
    """EINE Tabelle fuer alle geplanten Arbeiten.

    Die "Fleet-weiter Zeitplan"-Ansicht ist damit ein SELECT. Der doppelte, ins Leere
    laufende /action/briefing-Cron des Bestands waere darin eine sichtbare Doppelzeile
    gewesen statt ein Zufallsfund im Log.
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("uq_jobs_ext_key", "ext_id", "ext_job_key", unique=True),)

    ext_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ext_job_key: Mapped[str | None] = mapped_column(String(128))
    """`JobSpec.id` -- der stabile, von der Extension selbst gewaehlte Schluessel
    (docs/02 §2 `nodvard_sdk.context.JobSpec.id`), NICHT die server-generierte
    `Job.id` (UUID). Eine Extension kennt ihre eigene DB-UUID nie (`register_job()`
    gibt laut SDK-Vertrag nichts zurueck) -- `ctx.scheduler.trigger(job_id, ...)`
    adressiert deshalb ZWANGSLAEUFIG ueber diesen Schluessel, nicht die UUID. Eindeutig
    je `ext_id` (fuer Kern-Jobs mit `ext_id=None`: eindeutig unter allen Kern-Jobs) --
    macht `register_job()` idempotent (Upsert statt Blind-Insert), was WP-3 noch nicht
    war (unschaedlich dort, weil nichts real ausfuehrte; ab WP-6 haette ein erneutes
    Aktivieren sonst denselben Job dupliziert -- genau der doppelte-Cron-Fehler des
    Bestands, den D-08 eigentlich beheben sollte)."""
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

    `interrupted` existiert, damit ein Neustart mitten im Lauf sichtbar wird statt zu
    verschwinden — beim Start markiert der Kern alle noch als `running` gefuehrten Laeufe um.
    """

    host_id: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output_ref: Mapped[str | None] = mapped_column(String(255))
    """Zeigt auf /data/runs/<id>.log — Skriptausgaben sind unbegrenzt gross,
    Datenbankzeilen sollten es nicht sein."""

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
    """Protokolliert, ob der Versand geklappt hat.

    Im Bestand fehlt genau diese Rueckmeldung — ein Kanal, der ins Leere meldet, faellt
    dort wochenlang nicht auf.
    """

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
    """[{widget_id, ext_id, x, y, w, h, config}] — identisch fuer Web und App, damit die
    Anordnung auf dem Telefon nicht neu erfunden wird."""


class CustomApp(Base, IdMixin, TimestampMixin):
    """Eine eigene App-Kachel im Cockpit ("+ App hinzufuegen"): ein Link, den jemand von Hand
    angelegt hat -- Router, NAS-Oberflaeche, Pi-hole, alles ohne Service-Matrix oder Container-
    Erkennung. Der Kern ruft die Adresse NIE selbst ab (keine Statusabfrage, kein SSRF); sie ist
    nur ein Link im Browser. Pruefung und Begrenzungen: `services/custom_apps.py`.
    """

    __tablename__ = "custom_apps"

    name: Mapped[str] = mapped_column(String(60))
    url: Mapped[str] = mapped_column(String(1000))
    """Nur http:// oder https://, ohne Zugangsdaten und Steuerzeichen."""

    icon: Mapped[str | None] = mapped_column(String(32))
    """Name aus der festen Symbol-Liste (`services.custom_apps.APP_ICONS`) oder ein einzelnes Emoji --
    nie eine Bild-Adresse (Tracking, Mixed Content)."""

    color: Mapped[str | None] = mapped_column(String(7))
    """`#rrggbb` oder leer (dann waehlt die Oberflaeche eine Farbe nach dem Namen)."""

    group_name: Mapped[str | None] = mapped_column(String(40))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    open_in_new_tab: Mapped[bool] = mapped_column(Boolean, default=True)
    host_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("hosts.id", ondelete="SET NULL"), index=True
    )
    """Optionaler Server-Bezug (nur zur Anzeige). Wird der Server geloescht, bleibt die App."""

    created_by_user_id: Mapped[str | None] = mapped_column(String(36))
