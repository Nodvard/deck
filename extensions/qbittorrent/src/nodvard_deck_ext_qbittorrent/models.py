"""Antwortformen der Erweiterung; alle Texte stammen aus qBittorrent und sind gekürzt."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Transfer(BaseModel):
    download_speed: int = Field(description="Aktuelle Geschwindigkeit des Herunterladens in Byte pro Sekunde.")
    upload_speed: int = Field(description="Aktuelle Geschwindigkeit des Hochladens in Byte pro Sekunde.")
    connection: str = Field(description="connected | firewalled | disconnected | unknown")
    download_limit: int | None = Field(description="Tempolimit beim Herunterladen in Byte pro Sekunde; leer, wenn es keines gibt.")
    upload_limit: int | None = Field(description="Tempolimit beim Hochladen in Byte pro Sekunde; leer, wenn es keines gibt.")
    limited: bool = Field(description="Ob ein Tempolimit gilt (das normale oder das alternative).")
    alt_limits: bool | None = Field(description="Ob die alternativen Tempolimits an sind; leer, wenn qBittorrent es nicht mitteilt.")


class Torrent(BaseModel):
    name: str
    state: str = Field(
        description="downloading | stalled_downloading | uploading | stalled_uploading | paused | queued | checking | moving | error | unknown"
    )
    progress_percent: int = Field(description="Fortschritt von 0 bis 100.")
    size: int | None = Field(description="Größe der gewählten Dateien in Byte.")
    download_speed: int
    upload_speed: int
    eta_seconds: int | None = Field(description="Geschätzte Restzeit in Sekunden; leer, wenn qBittorrent keine nennt.")


class TorrentCounts(BaseModel):
    total: int
    downloading: int = Field(description="Laden gerade (auch ohne Quellen).")
    seeding: int = Field(description="Verteilen (laden hoch).")
    paused: int = Field(description="Pausiert oder gestoppt.")
    error: int = Field(description="Mit Fehler oder fehlenden Dateien.")
    other: int = Field(description="Warten in der Schlange, werden geprüft oder verschoben.")


class Connection(BaseModel):
    name: str
    url: str | None
    state: str = Field(description="ok | not_configured | auth_failed | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem Fehler der Grund, sonst leer.")
    version: str | None = Field(description="Version von qBittorrent, z. B. v5.1.4.")
    api_version: str | None = Field(description="Version der Web-API, z. B. 2.11.4.")
    transfer: Transfer | None
    active: list[Torrent] | None = Field(description="Torrents, die gerade Daten bewegen, die schnellsten zuerst; höchstens 50.")
    active_more: bool = Field(description="Ob es noch mehr aktive Torrents gibt, als in `active` stehen.")
    counts: TorrentCounts | None = Field(description="Anzahl je Zustand; leer, wenn sie sich nicht lesen ließ.")
    free_space: int | None = Field(description="Freier Platz am Speicherort in Byte; leer, wenn unbekannt.")
    notes: list[str] = Field(description="Hinweise, die kein Fehler sind, als deutsche Sätze; sonst leer.")
    problems: list[str] = Field(description="Abschnitte, die sich nicht lesen ließen, als deutsche Sätze; sonst leer.")


class Summary(BaseModel):
    connections: int
    download_speed: int
    upload_speed: int
    active: int


class Overview(BaseModel):
    connections: list[Connection]
    summary: Summary
