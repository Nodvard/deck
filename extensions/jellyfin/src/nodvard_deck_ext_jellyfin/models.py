"""Antwortformen der Erweiterung; alle Texte stammen aus Jellyfin und sind gekürzt."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Media(BaseModel):
    title: str = Field(description="Titel; bei einer Folge der Name der Folge.")
    kind: str = Field(description="movie | episode | audio | video | other")
    series: str | None = Field(description="Name der Serie, nur bei einer Folge.")
    season: int | None = Field(description="Staffel, nur bei einer Folge.")
    episode: int | None = Field(description="Folge, nur bei einer Folge.")
    artist: str | None = Field(description="Künstler, nur bei Musik.")
    year: int | None = Field(description="Erscheinungsjahr, wenn Jellyfin eines nennt.")


class Playback(Media):
    user: str | None = Field(description="Benutzername in Jellyfin.")
    device: str | None
    client: str | None = Field(description="Name der App oder des Browsers.")
    play_method: str | None = Field(description="direct_play | direct_stream | transcode; leer, wenn Jellyfin es nicht meldet.")
    transcoding: bool = Field(description="Ob Jellyfin gerade Bild oder Ton umrechnet.")
    progress_percent: int | None = Field(description="Fortschritt von 0 bis 100; leer, wenn die Länge unbekannt ist.")
    paused: bool


class LatestItem(Media):
    added_at: datetime | None = Field(description="Wann Jellyfin den Eintrag angelegt hat.")


class LibraryCounts(BaseModel):
    movies: int | None
    series: int | None
    episodes: int | None
    albums: int | None
    songs: int | None
    music_videos: int | None
    box_sets: int | None
    books: int | None


class Connection(BaseModel):
    name: str
    url: str | None
    state: str = Field(description="ok | not_configured | auth_failed | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem Fehler der Grund, sonst leer.")
    server_name: str | None = Field(description="Name des Servers, so wie er in Jellyfin eingetragen ist.")
    version: str | None = Field(description="Version von Jellyfin, z. B. 10.10.7.")
    playback_count: int | None = Field(description="Alle laufenden Wiedergaben; leer, wenn sie sich nicht lesen ließen.")
    playbacks: list[Playback] | None = Field(description="Laufende Wiedergaben, gekürzt; die Gesamtzahl steht in playback_count.")
    counts: LibraryCounts | None
    latest: list[LatestItem] | None = Field(description="Zuletzt hinzugefügte Filme und Folgen, neueste zuerst.")
    problems: list[str] = Field(description="Abschnitte, die sich nicht lesen ließen, als deutsche Sätze; sonst leer.")


class Summary(BaseModel):
    connections: int
    playing: int
    transcoding: int


class Overview(BaseModel):
    connections: list[Connection]
    summary: Summary
