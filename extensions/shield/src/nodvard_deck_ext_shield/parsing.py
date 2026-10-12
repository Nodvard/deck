"""Zerlegt die Antwort der KI in Begründung und Aktion; reine Funktionen ohne I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .remediation import looks_like_host_not_container

# Die Erfassung endet am nächsten Abschnittsmarker oder am Textende, nicht am Zeilenende: Gibt ein Modell die Antwort
# einzeilig aus, würde sonst der Text danach im auszuführenden Befehl landen.
_SECTION_BOUNDARY = r"(?=\s*(?:🟢|📌|⚡|BEGR\S{0,4}NDUNG\s*:|AKTION\s*:|$))"

# Gesucht wird im ganzen Text, nicht zeilenweise; `\S{0,4}` toleriert den Tippfehler BEGRUNDUNG und Umlaut-Varianten.
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
    """Kein AKTION-Feld gefunden oder ausdrücklich „AKTION: KEINE“ bzw. „AKTION: NONE“."""
    EXEC = "exec"
    """Ein auszuführender Befehl auf einem Host; geht als Vorschlag ans Gate."""
    REJECTED = "rejected"
    """Erkannt, aber von `looks_like_host_not_container` verworfen; die Sperrliste prüft das Gate im Kern."""


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
    """Liefert `[]` bei „AKTION: KEINE“ oder ohne erkennbares AKTION-Feld, sonst genau einen Eintrag.

    Ein nicht verstandenes Format schlägt nichts vor, statt eine Aktion vorzutäuschen."""
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
        return []  # Platzhalter aus dem Prompt-Beispiel sind kein Befehl (SYSTEM_PROMPT Regel 4)

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
    """Entfernt den rohen BEGRÜNDUNG/AKTION-Block aus dem Modelltext für die Anzeige (ntfy, Widget).

    Was tatsächlich passiert ist, steht in der Aktions-Zeile, die aus dem echten `ActionResult` gebaut wird."""
    text = _DECISION_BLOCK_RE.sub("", ai_text or "")
    text = _WHITESPACE_RUNS_RE.sub(" ", text)
    text = _BLANK_LINE_RUNS_RE.sub("\n\n", text)
    return text.strip()
