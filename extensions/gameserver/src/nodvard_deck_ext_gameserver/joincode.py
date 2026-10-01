"""Extrahiert den aktuellen Join-Code aus Log-Text -- reine, testbare
Textverarbeitung ohne `ctx`-Abhaengigkeit, wie
`nodvard_deck_ext_nexus_soc.parsing`.

**Konkreter Anlass:** Valheims PlayFab-Crossplay-Join-Code aendert sich bei JEDEM
Neustart des Server-*Prozesses* (nicht auf einem Timer) -- und der Prozess ueberlebt
den naechtlichen Host-Reboot nicht. Der Server schreibt den neuen Code beim Start in
sein eigenes Log (`C:\\valheim\\logs\\service-out.log`, NSSM-Windows-Dienst); es gibt
keine API dafuer, nur die Log-Zeile.

**Gegen ein echtes `service-out.log` verifiziert.** `DEFAULT_JOIN_CODE_PATTERN`/
`DEFAULT_LOG_COMMAND` waren beim urspruenglichen Schreiben eine Annahme (kein
echtes Log-Beispiel lag vor). Per SSH gegen die echte Datei geprueft (drei echte
Zeilen, `test_ext_gameserver_joincode.py`s Regressionstest haelt sie fest) --
Muster UND Powershell-Befehl passen unveraendert. `settings.join_code_pattern`
bleibt trotzdem ueberschreibbar (siehe `settings.schema.json`), falls eine
kuenftige Valheim-Version das Log-Format aendert."""

from __future__ import annotations

import re

DEFAULT_LOG_COMMAND = (
    "powershell -NoProfile -Command "
    "\"Get-Content -Path 'C:\\valheim\\logs\\service-out.log' -ErrorAction SilentlyContinue "
    "| Select-String -Pattern 'join code' -SimpleMatch "
    "| Select-Object -Last 1 | ForEach-Object { $_.Line }\""
)

DEFAULT_JOIN_CODE_PATTERN = r"join code:?\s*([A-Za-z0-9]{4,10})"


def extract_join_code(log_text: str, *, pattern: str = DEFAULT_JOIN_CODE_PATTERN) -> str | None:
    """Gibt `None` zurueck, wenn nichts gefunden wurde -- KEIN Fehler, der Server
    koennte gerade erst gestartet sein und das Log noch nicht geschrieben haben."""
    if not log_text.strip():
        return None
    match = re.search(pattern, log_text, re.IGNORECASE)
    return match.group(1) if match else None
