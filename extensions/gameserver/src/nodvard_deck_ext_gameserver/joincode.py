"""Liest den aktuellen Join-Code aus Log-Text: reine Textverarbeitung ohne `ctx`, wie `nodvard_deck_ext_shield.parsing`."""
from __future__ import annotations

import re

# Valheims Crossplay-Join-Code wechselt bei jedem Neustart des Server-Prozesses (also nach jedem nächtlichen Reboot) und
# steht nur beim Start im Log (`C:\valheim\logs\service-out.log`, NSSM-Dienst), eine API gibt es nicht. Muster und Befehl
# sind gegen ein echtes Log geprüft; `settings.join_code_pattern` bleibt überschreibbar, falls sich das Format ändert.
DEFAULT_LOG_COMMAND = (
    "powershell -NoProfile -Command "
    "\"Get-Content -Path 'C:\\valheim\\logs\\service-out.log' -ErrorAction SilentlyContinue "
    "| Select-String -Pattern 'join code' -SimpleMatch "
    "| Select-Object -Last 1 | ForEach-Object { $_.Line }\""
)

DEFAULT_JOIN_CODE_PATTERN = r"join code:?\s*([A-Za-z0-9]{4,10})"


def extract_join_code(log_text: str, *, pattern: str = DEFAULT_JOIN_CODE_PATTERN) -> str | None:
    """`None`, wenn nichts gefunden wurde; kein Fehler, denn der Server kann gerade erst gestartet sein (Log noch leer)."""
    if not log_text.strip():
        return None
    match = re.search(pattern, log_text, re.IGNORECASE)
    return match.group(1) if match else None
