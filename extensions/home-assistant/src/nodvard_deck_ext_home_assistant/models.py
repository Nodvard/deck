"""Antwortformen der Erweiterung; alle Texte stammen aus Home Assistant und sind gekürzt."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UpdateItem(BaseModel):
    entity_id: str
    name: str
    installed_version: str | None
    latest_version: str | None


class UnavailableItem(BaseModel):
    entity_id: str
    name: str


class EntityValue(BaseModel):
    entity_id: str
    name: str
    status: str = Field(description="ok | unavailable | unknown | missing (die Entität gibt es in Home Assistant nicht)")
    state: str | None = Field(description="Zustand als Text, z. B. 21.5 oder on; leer bei unavailable, unknown und missing.")
    unit: str | None = Field(description="Einheit, z. B. °C; nur wenn Home Assistant eine meldet.")


class Counts(BaseModel):
    total: int = Field(description="Alle Entitäten, die Home Assistant für den Zugang meldet.")
    updates: int = Field(description="Entitäten update.* mit Zustand on: hier steht ein Update bereit.")
    unavailable: int
    unknown: int


class Connection(BaseModel):
    name: str
    url: str | None
    state: str = Field(description="ok | not_configured | auth_failed | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem Fehler der Grund, sonst leer.")
    version: str | None = Field(description="Version von Home Assistant, z. B. 2026.10.1.")
    location_name: str | None = Field(description="Name des Standorts, so wie er in Home Assistant eingetragen ist.")
    counts: Counts | None
    updates: list[UpdateItem] = Field(description="Bereitstehende Updates, gekürzt; die Gesamtzahl steht in counts.updates.")
    unavailable: list[UnavailableItem] = Field(description="Nicht verfügbare Entitäten, gekürzt; die Gesamtzahl steht in counts.unavailable.")
    entities: list[EntityValue] = Field(description="Die in den Einstellungen gewählten Entitäten in dieser Reihenfolge.")


class Summary(BaseModel):
    connections: int
    updates: int
    unavailable: int
    unknown: int


class Overview(BaseModel):
    connections: list[Connection]
    summary: Summary
