"""Antwortformen der Erweiterung."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Summary(BaseModel):
    total: int
    up: int
    down: int
    pending: int
    maintenance: int


class Monitor(BaseModel):
    name: str
    type: str | None
    url: str | None = Field(description="Adresse oder Rechner:Port, ohne Zugangsdaten und ohne Suchteil.")
    status: str = Field(description="up | down | pending | maintenance | unknown")
    response_ms: int | None
    uptime_24h: float | None = Field(description="Verfügbarkeit der letzten 24 Stunden in Prozent; nur mit Status-Seite.")
    cert_days_left: int | None
    cert_valid: bool | None


class Outage(BaseModel):
    monitor: str
    time: datetime | None
    message: str


class Connection(BaseModel):
    name: str
    url: str | None
    state: str = Field(description="ok | not_configured | auth_failed | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem Fehler der Grund, sonst leer.")
    monitors: list[Monitor]
    summary: Summary | None
    outages: list[Outage]
    status_page: bool = Field(description="Ob für diese Verbindung eine Status-Seite eingetragen ist.")


class Overview(BaseModel):
    connections: list[Connection]
    summary: Summary
