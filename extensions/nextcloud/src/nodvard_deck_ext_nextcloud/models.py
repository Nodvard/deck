"""Antwortformen der Kennzahlen aus der Nextcloud-App „Server-Info“; jede Zahl darf fehlen (`None`)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UpdateInfo(BaseModel):
    core_available: bool | None = Field(
        default=None, description="Ob eine neuere Nextcloud-Version bereitsteht; leer, wenn die Nextcloud das nicht meldet."
    )
    core_version: str | None = Field(default=None, description="Die Versionsnummer des Updates, falls eines bereitsteht.")
    core_checked_at: datetime | None = Field(
        default=None, description="Wann die Nextcloud zuletzt nach einer neuen Version gesucht hat; leer: noch nie."
    )
    app_updates: int | None = Field(default=None, description="Anzahl der Apps mit einem Update.")


class StorageInfo(BaseModel):
    free_bytes: int | None = Field(default=None, description="Freier Platz im Datenordner in Bytes.")
    files: int | None = Field(default=None, description="Anzahl der Dateien (die Nextcloud zählt sie alle paar Stunden neu).")
    users: int | None = Field(default=None, description="Anzahl der Konten, die sich schon einmal angemeldet haben.")


class ActiveUsers(BaseModel):
    last_5_minutes: int | None = None
    last_hour: int | None = None
    last_24_hours: int | None = None


class LoadInfo(BaseModel):
    load_1: float | None = Field(default=None, description="CPU-Last der letzten 1 Minute (Anzahl wartender Prozesse).")
    load_5: float | None = None
    load_15: float | None = None
    cpu_cores: int | None = Field(default=None, description="Anzahl der Prozessorkerne (Threads) des Servers.")
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = Field(
        default=None, description="Arbeitsspeicher, der noch frei oder leicht freizugeben ist, in Bytes."
    )


class ServerInfo(BaseModel):
    state: str = Field(description="ok | not_configured | auth_failed | app_inactive | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem Problem der Grund, sonst leer.")
    auth: str | None = Field(description="Womit angemeldet wurde: token | password; leer ohne Anfrage.")
    version: str | None = Field(default=None, description="Die installierte Nextcloud-Version.")
    updates: UpdateInfo
    storage: StorageInfo
    active_users: ActiveUsers
    load: LoadInfo
