"""Liest den aktuellen Join-Code aus Log-Text: reine Textverarbeitung ohne `ctx`, wie `nodvard_deck_ext_shield.parsing`."""
from __future__ import annotations

import re

# Valheims PlayFab-Crossplay-Join-Code ändert sich bei jedem Neustart des Server-Prozesses (nicht auf einem Timer), und der
# Prozess überlebt den nächtlichen Host-Reboot nicht. Der Server schreibt den neuen Code beim Start in sein Log
# (`C:\valheim\logs\service-out.log`, NSSM-Windows-Dienst); es gibt keine API dafür, nur die Log-Zeile. Muster und Befehl sind
# gegen ein echtes Log geprüft (Regressionstest), `settings.join_code_pattern` bleibt überschreibbar, falls eine künftige
# Version das Log-Format ändert.
DEFAULT_LOG_COMMAND = (
    "powershell -NoProfile -Command "
    "\"Get-Content -Path 'C:\\valheim\\logs\\service-out.log' -ErrorAction SilentlyContinue "
    "| Select-String -Pattern 'join code' -SimpleMatch "
    "| Select-Object -Last 1 | ForEach-Object { $_.Line }\""
)

DEFAULT_JOIN_CODE_PATTERN = r"join code:?\s*([A-Za-z0-9]{4,10})"


def extract_join_code(log_text: str, *, pattern: str = DEFAULT_JOIN_CODE_PATTERN) -> str | None:
    """Gibt `None` zurück, wenn nichts gefunden wurde; kein Fehler, der Server kann gerade erst gestartet sein und das Log noch
    nicht geschrieben haben."""
    if not log_text.strip():
        return None
    match = re.search(pattern, log_text, re.IGNORECASE)
    return match.group(1) if match else None
