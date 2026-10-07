"""Hilfen fuer Tests mit Zwei-Faktor-Anmeldung."""

from __future__ import annotations

import time

import pyotp

STEP = 30


def setup_confirm_code(secret: str, now: float | None = None) -> str:
    """Code fuer `POST /me/totp/confirm`: aus dem Zeitschritt davor (die Pruefung nimmt einen Schritt davor und danach
    an). Der Code, der die Einrichtung bestaetigt, gilt danach nicht noch einmal; so bleibt der aktuelle Code aus der
    App fuer den Test frei (Anmelden, Abschalten, Bestaetigen).

    `now`: die feste Uhr eines Tests (`auth_service._totp_now`). Ohne sie die echte Uhr; steht gleich ein neuer Schritt
    an, wartet die Hilfe ihn ab, sonst waere der Code beim Server schon zwei Schritte alt."""
    if now is None:
        now = time.time()
        if now % STEP > STEP - 0.5:
            time.sleep(STEP - now % STEP + 0.05)
            now = time.time()
    return pyotp.TOTP(secret).at(now - STEP)
