"""Neustart des Prozesses auf Wunsch (`POST /system/restart`, nach dem Vormerken einer Wiederherstellung).

Nodvard Deck startet sich nicht selbst neu -- es BEENDET sich mit dem Rueckgabewert 75
(`EX_TEMPFAIL`), und wer den Prozess gestartet hat, startet ihn neu: im Container die
Neustart-Regel (`restart: unless-stopped` in den mitgelieferten Compose-Dateien; mit
`on-failure` greift sie wegen des Wertes ungleich 0 ebenfalls). Ohne eine solche Regel
bleibt der Dienst danach aus -- die Oberflaeche weist darauf hin.

Ablauf: Merker setzen, nach einer kurzen Wartezeit (damit die Antwort noch rausgeht) `SIGTERM`
an den eigenen Prozess. uvicorn faehrt dann ordentlich herunter (Lifespan-Ende: Planer stoppen,
Datenbank schliessen); ganz am Ende des Lifespans beendet `exit_if_requested` den Prozess mit
75, bevor uvicorn ihn mit dem Signal-Rueckgabewert beendet. Haengt das Herunterfahren
(z. B. offene Verbindungen), beendet ein Zeitgeber den Prozess nach `HARD_EXIT_AFTER_S` trotzdem.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("nodvard_deck.restart")

EXIT_CODE = 75
SIGNAL_DELAY_S = 0.7
HARD_EXIT_AFTER_S = 20.0


def _send_sigterm() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def _hard_exit() -> None:
    logger.warning("restart_hard_exit")
    os._exit(EXIT_CODE)


def request_restart(app: FastAPI, *, terminate: Callable[[], None] | None = None) -> None:
    """Merkt den Neustart vor und beendet den Prozess gleich nach der Antwort. `terminate`
    ersetzt das Signal (Tests: nie den Testprozess beenden)."""
    if getattr(app.state, "restart_requested", False):
        return
    app.state.restart_requested = True
    logger.warning("restart_requested")
    action = terminate or _send_sigterm

    def _go() -> None:
        action()
        if terminate is None:
            timer = threading.Timer(HARD_EXIT_AFTER_S, _hard_exit)
            timer.daemon = True
            timer.start()

    asyncio.get_running_loop().call_later(SIGNAL_DELAY_S, _go)


def exit_if_requested(app: FastAPI) -> None:
    """Am Ende des Lifespans aufrufen: war ein Neustart verlangt, endet der Prozess hier mit 75."""
    if getattr(app.state, "restart_requested", False):
        logging.shutdown()
        os._exit(EXIT_CODE)
