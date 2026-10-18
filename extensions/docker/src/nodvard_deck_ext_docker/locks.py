"""Ein Stack, eine Änderung zur Zeit: die gemeinsame Sperre aller Ausführer, die ein Compose-Projekt ändern."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager


def busy_text(what: str) -> str:
    return f"An diesem Stack läuft gerade schon etwas ({what}). Versuch es danach noch einmal."


class StackLocks:
    """Ein Stack, eine Änderung zur Zeit, gemeinsam für alle Ausführer des Moduls."""

    def __init__(self) -> None:
        self._held: dict[tuple[str, str], str] = {}
        self.outside: Callable[[str, str], str | None] | None = None
        """Was außerhalb der Stack-Aktionen gerade an einem Projekt läuft, etwa das Image-Update eines Containers."""

    def held(self, host_id: str, project: str) -> str | None:
        return self._held.get((host_id, project))

    def holder(self, host_id: str, project: str) -> str | None:
        return self.held(host_id, project) or (self.outside(host_id, project) if self.outside is not None else None)

    @contextmanager
    def hold(self, host_id: str, project: str, what: str) -> Iterator[str | None]:
        """Hält den Stack für `what` und liefert `None`; ist er belegt, hält es nichts und liefert, was dort gerade läuft."""
        if (busy := self.holder(host_id, project)) is not None:
            yield busy
            return
        self._held[(host_id, project)] = what
        try:
            yield None
        finally:
            self._held.pop((host_id, project), None)
