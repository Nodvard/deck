"""Welche Menschen der Kern gerade hinter einem Aufruf kennt: den Nutzer der laufenden Anfrage (aus der Prüfung des
Zugangs-Tokens) und, während er eine Aktion ausführt, wer sie vorgeschlagen und wer sie freigegeben hat."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# Ein Task übernimmt den Wert beim Anlegen: `asyncio.create_task` aus einer Anfrage kennt deren Nutzer weiter.
_KNOWN: ContextVar[frozenset[str]] = ContextVar("nodvard_known_users", default=frozenset())


def add_user(user_id: str) -> None:
    """Merkt sich den Nutzer für den Rest der laufenden Anfrage; nur nach geprüftem Zugangs-Token aufrufen."""
    _KNOWN.set(_KNOWN.get() | {user_id})


def forget() -> None:
    """Ab hier kennt der laufende Task niemanden; für Hintergrundarbeit, die nicht im Namen ihres Anlegers läuft."""
    _KNOWN.set(frozenset())


@contextmanager
def known_while(actors: Iterable[tuple[str, str]]) -> Iterator[None]:
    """Im Block gelten genau die Menschen aus `actors` (Art, Kennung) als bekannt; andere Arten zählen nicht."""
    token = _KNOWN.set(frozenset(actor_id for actor_type, actor_id in actors if actor_type == "user"))
    try:
        yield
    finally:
        _KNOWN.reset(token)


def is_known(actor_type: str, actor_id: str) -> bool:
    return actor_type == "user" and actor_id in _KNOWN.get()
