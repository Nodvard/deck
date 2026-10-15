"""Antwortformen der Erweiterung; alle Texte stammen aus Plex und sind gekürzt."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Media(BaseModel):
    title: str = Field(description="Titel; bei einer Folge der Name der Folge, bei einer Staffel deren Bezeichnung.")
    kind: str = Field(description="movie | episode | season | show | audio | video | other")
    series: str | None = Field(description="Name der Serie, nur bei einer Folge oder Staffel.")
    season: int | None = Field(description="Staffel, nur bei einer Folge oder Staffel.")
    episode: int | None = Field(description="Folge, nur bei einer Folge.")
    artist: str | None = Field(description="Künstler, nur bei Musik.")
    year: int | None = Field(description="Erscheinungsjahr, wenn Plex eines nennt.")


class Playback(Media):
    user: str | None = Field(description="Benutzername in Plex.")
    device: str | None = Field(description="Name des Geräts, auf dem abgespielt wird.")
    client: str | None = Field(description="Name der App oder des Browsers.")
    play_method: str | None = Field(description="direct_play | direct_stream | transcode; leer, wenn Plex es nicht eindeutig meldet.")
    transcoding: bool = Field(description="Ob Plex gerade Bild oder Ton umrechnet.")
    progress_percent: int | None = Field(description="Fortschritt von 0 bis 100; leer, wenn die Länge unbekannt ist.")
    paused: bool


class LatestItem(Media):
    added_at: datetime | None = Field(description="Wann der Eintrag zu Plex hinzugekommen ist.")


class Library(BaseModel):
    title: str
    kind: str = Field(description="movie | show | artist | photo | other")
    count: int | None = Field(description="Einträge der Hauptart (Filme, Serien, Künstler); leer, wenn sich die Zahl nicht lesen ließ.")
    scanning: bool = Field(description="Ob Plex die Bibliothek gerade neu einliest.")


class Connection(BaseModel):
    name: str
    url: str | None
    state: str = Field(description="ok | not_configured | auth_failed | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem Fehler der Grund, sonst leer.")
    server_name: str | None = Field(description="Name des Servers, so wie er in Plex eingetragen ist.")
    version: str | None = Field(description="Version von Plex Media Server, z. B. 1.41.3.9314-a0bfb8370.")
    playback_count: int | None = Field(description="Alle laufenden Wiedergaben; leer, wenn sie sich nicht lesen ließen.")
    playbacks: list[Playback] | None = Field(description="Laufende Wiedergaben, gekürzt; die Gesamtzahl steht in playback_count.")
    libraries: list[Library] | None = Field(description="Bibliotheken in der Reihenfolge von Plex, höchstens 30.")
    latest: list[LatestItem] | None = Field(description="Zuletzt hinzugefügte Medien, neueste zuerst.")
    problems: list[str] = Field(description="Abschnitte, die sich nicht lesen ließen, als deutsche Sätze; sonst leer.")


class Summary(BaseModel):
    connections: int
    playing: int
    transcoding: int


class Overview(BaseModel):
    connections: list[Connection]
    summary: Summary
