r"""Reine Funktionen, kein I/O, keine Nebenwirkung. "PARSEN" ist hier zu Ende,
"VORSCHLAGEN" (per `ctx.actions.propose()`) und "MELDEN" (aus dem Ergebnis, nie aus
der Absicht) passieren danach, ausserhalb dieses Moduls.

Ersetzt `parse_and_execute_remediation()` aus dem Vorgaengersystem, das
Parsen UND Ausfuehren in einem Zug machte. Der neue Name `parse_ai_response`
signalisiert die Trennung selbst.

**Zwei im Vorgaengersystem gefundene, hier gezielt behobene Fehler:**
- Das Vorgaengersystem suchte `BEGRUENDUNG:` nur zeilen-verankert (`line.startswith(...)`) --
  ein Modell, das seine Antwort einzeilig ausgibt (kein Zeilenumbruch), oder den
  Tippfehler `BEGRUNDUNG` (fehlendes zweites E) schreibt, wurde nie erkannt.
  Hier: ein Regex-Scan ueber den GESAMTEN Text (`re.search`, nicht pro Zeile), mit
  `BEGR\S{0,4}NDUNG` als Toleranz fuer Tippfehler/Umlaut-Variante.
- Das Vorgaengersystem extrahierte den Befehl mit `(.+)` gierig bis zum Zeilenende
  (`re.search(..., clean_line)`) -- bei einzeiliger Modellausgabe landete der Text
  DANACH (Status-Emoji, naechster Abschnitt) im auszufuehrenden Befehl. Hier: ein
  Lookahead auf bekannte Abschnittsmarker (🟢/📌/⚡/BEGRUENDUNG/AKTION) oder Textende
  beendet die Erfassung, nicht das Zeilenende.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .remediation import looks_like_host_not_container

_SECTION_BOUNDARY = r"(?=\s*(?:🟢|📌|⚡|BEGR\S{0,4}NDUNG\s*:|AKTION\s*:|$))"

_BEGRUENDUNG_RE = re.compile(
    r"BEGR\S{0,4}NDUNG\s*:\s*(?P<reason>.+?)" + _SECTION_BOUNDARY,
    re.IGNORECASE | re.DOTALL,
)
_AKTION_NONE_RE = re.compile(r"AKTION\s*:\s*(?:KEINE|NONE)\b", re.IGNORECASE)
_AKTION_EXEC_RE = re.compile(
    r"AKTION\s*:\s*EXEC\s+(?P<host>\S+)\s+(?P<command>.+?)" + _SECTION_BOUNDARY,
    re.IGNORECASE | re.DOTALL,
)

_PLACEHOLDER_MARKERS = ("<host>", "<target>", "<befehl>", "<command>")


class ParsedActionKind(str, Enum):
    NONE = "none"
    """Kein AKTION-Feld gefunden, oder explizit 'AKTION: KEINE'/'AKTION: NONE'."""
    EXEC = "exec"
    """Ein auszufuehrender Befehl auf einem Host -- geht als Vorschlag ans Gate."""
    REJECTED = "rejected"
    """Erkannt, aber von einer Extension-eigenen Pruefung verworfen (aktuell nur
    `looks_like_host_not_container` -- die Sperrlisten-Pruefung selbst macht das
    Kern-Gate anhand von `command_field`, nicht dieses Modul)."""


@dataclass(frozen=True)
class ParsedAction:
    kind: ParsedActionKind
    host: str | None = None
    command: str | None = None
    reason: str | None = None
    rejection_reason: str | None = None


def _clean(text: str) -> str:
    return text.replace("**", "").replace("`", "").strip()


def parse_ai_response(
    ai_text: str, *, forbidden_keywords: tuple[str, ...] | None = None
) -> list[ParsedAction]:
    """Liefert `[]` bei 'AKTION: KEINE' oder wenn gar kein AKTION-Feld erkennbar ist
    (konservativ -- ein nicht verstandenes Format schlaegt NICHTS vor,
    anders als ein Format-Fehler, der eine Aktion vortaeuscht). Sonst genau EIN
    Eintrag -- das Vorgaengersystem kannte nie mehr als ein AKTION-Feld pro Modellantwort, die
    Listenform ist der dokumentierte Vertrag, nicht ein beobachtetes
    Mehrfach-Vorkommen."""
    text = ai_text or ""

    reason_match = _BEGRUENDUNG_RE.search(text)
    reason = _clean(reason_match.group("reason")) if reason_match else None

    if _AKTION_NONE_RE.search(text):
        return []

    exec_match = _AKTION_EXEC_RE.search(text)
    if exec_match is None:
        return []

    host = exec_match.group("host").strip("<>:").lower()
    command = _clean(exec_match.group("command"))

    if not command or any(marker in command.lower() for marker in _PLACEHOLDER_MARKERS):
        return []  # Platzhalter-Verbot, SYSTEM_PROMPT Regel 4 (aus dem Vorgaengersystem)

    forbidden_target = looks_like_host_not_container(command, forbidden_keywords=forbidden_keywords)
    if forbidden_target is not None:
        return [
            ParsedAction(
                kind=ParsedActionKind.REJECTED,
                host=host,
                command=command,
                reason=reason,
                rejection_reason=(
                    f"'{forbidden_target}' sieht wie ein geschützter Host aus, nicht wie ein Docker-Container."
                ),
            )
        ]

    return [
        ParsedAction(
            kind=ParsedActionKind.EXEC,
            host=host,
            command=command,
            reason=reason or "(keine Begründung angegeben)",
        )
    ]


_DECISION_BLOCK_RE = re.compile(
    r"(?is)⚡?\s*\*{0,2}(?:NODVARD|NEXUS)-(?:Aktion|Entscheidung)\s*:?\s*\*{0,2}"
    r"|BEGR\S{0,4}NDUNG\s*:.*?(?=AKTION\s*:|🟢|$)"
    r"|AKTION\s*:.*?(?=🟢|$)"
)
_WHITESPACE_RUNS_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINE_RUNS_RE = re.compile(r"\n{3,}")


def strip_decision_block(ai_text: str) -> str:
    """Entfernt den rohen BEGRUENDUNG/AKTION-Block aus einem Modelltext fuer die
    Anzeige (ntfy/Widget) -- woertlich portiert aus `flush_incident_batch()` des Vorgaengersystems.
    Was tatsaechlich passiert ist, steht separat in der Aktions-Zeile, die aus dem
    ECHTEN `ActionResult` gebaut wird (die Meldung kann nicht luegen, weil ihr Text
    den `actions`-Datensatz als einzige Quelle hat), NICHT aus diesem bereinigten
    Modelltext."""
    text = _DECISION_BLOCK_RE.sub("", ai_text or "")
    text = _WHITESPACE_RUNS_RE.sub(" ", text)
    text = _BLANK_LINE_RUNS_RE.sub("\n\n", text)
    return text.strip()
