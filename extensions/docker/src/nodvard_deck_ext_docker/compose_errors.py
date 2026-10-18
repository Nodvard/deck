"""Compose-Ausgaben ohne Inhalt aus `.env`-Dateien, bevor sie in Antworten, Verlauf, Meldungen oder Logs landen."""

from __future__ import annotations

import re

# Bei einer kaputten .env zitiert Compose die Zeile oder einen Teil des Werts, und das kann ein Passwort sein.
# Compose nennt die Datei immer mit absolutem Pfad; so bleiben "error reading from server" und Ähnliches unberührt.
_READ_RE = re.compile(r"(?i)(?:failed to (?:read|load)|error reading) (?=[/~]|\.{1,2}/|[A-Za-z]:[\\/])")
_LINE_RE = re.compile(r"line (\d{1,7}): ")
_QUOTED_RE = re.compile(r"(?i)unterminated quoted value|in variable name|invalid template")
# Ein einzelnes `$` in einem Wert der .env liest Compose als Variable, ihr Name ist dann ein Stück des Werts.
UNSET_VAR_RE = re.compile(r'The \\*"?([^"\\\s]+)\\*"? variable is not set')
_MARK_RE = re.compile(r"@@(?:step=[a-z]+|rc=-?\d*|warn=[a-z-]+|tail|running|lost|unknown)")
_MAX_PATH = 512

# Nur diese festen Texte ersetzen den Rest der Meldung; was Compose dort schreibt, geht nie weiter.
_CONTENT_REASONS = (
    ("unterminated quoted value", "Ein Wert in Anführungszeichen wird nicht geschlossen"),
    ("in variable name", "Ein Name enthält ein ungültiges Zeichen"),
    ("key cannot contain a space", "Ein Name enthält ein Leerzeichen"),
    ("invalid template", "Ein Wert enthält ein unvollständiges ${…}; ein $, das im Wert stehen soll, als $$ schreiben"),
    ("is missing a value", "Eine Pflicht-Variable hat keinen Wert"),
)
_FILE_REASONS = (
    ("no such file or directory", "Die Datei fehlt"),
    ("permission denied", "Nodvard Deck darf die Datei nicht lesen"),
    ("is a directory", "Das ist ein Ordner, keine Datei"),
    ("input/output error", "Der Datenträger meldet einen Lesefehler"),
)
_HIDDEN = "Den Inhalt zeigt Nodvard Deck nicht, er kann Passwörter enthalten."
_UNSET = (
    "Eine Variable ist nicht gesetzt und bleibt leer. Den Namen zeigt Nodvard Deck nicht, er kann ein Stück eines Passworts "
    "sein; steht in einem Wert der .env ein einzelnes $, dort $$ schreiben."
)


def scrub_compose_output(text: str) -> str:
    """Ersetzt einen Lese- oder Inhaltsfehler zu einer Datei durch einen festen Grund und blendet die Zeilen danach bis
    auf die `@@`-Marken aus, soweit sie zum Inhalt gehören können; Namen nicht gesetzter Variablen fallen weg."""
    if not text:
        return text
    kept: list[str] = []
    hiding = warned = False
    for line in text.splitlines(keepends=True):
        body = line.splitlines()[0]
        if hiding and not _MARK_RE.fullmatch(body):
            continue
        hiding = False
        end = "\n" if len(body) < len(line) else ""
        read, quoted = _READ_RE.search(body), _QUOTED_RE.search(body)
        # Steht der Grund vor "failed to read", kann alles danach schon zum Wert gehören.
        if quoted is not None and (read is None or quoted.start() < read.start()):
            kept.append(f"Compose kann eine .env-Datei nicht lesen. {_HIDDEN}{end}")
            hiding = True
        elif read is not None:
            message, hiding = _read_error(body[read.end():])
            kept.append(f"{UNSET_VAR_RE.sub('Eine Variable ist nicht gesetzt', body[:read.start()])}{message}{end}")
        elif (unset := UNSET_VAR_RE.search(body)) is not None:
            if not warned:
                kept.append(f"{body[:unset.start()]}{_UNSET}{end}")
            warned = True
        else:
            kept.append(line)
    return "".join(kept)


def _read_error(rest: str) -> tuple[str, bool]:
    """Der feste Text für `<Pfad>: [line N: ]<Grund>` und ob die Zeilen danach zum Inhalt gehören können."""
    cut = rest.find(": ", 0, _MAX_PATH)
    if cut <= 0:
        return f"Compose kann eine .env-Datei nicht lesen. {_HIDDEN}", True
    path, detail = rest[:cut], rest[cut + 2:]
    line = _LINE_RE.match(detail)
    lowered = detail.lower()
    content = next(((needle, reason) for needle, reason in _CONTENT_REASONS if needle in lowered), None)
    file_reason = next((reason for needle, reason in _FILE_REASONS if needle in lowered), None)
    if content is None and file_reason is not None:
        return f"Compose kann {path} nicht lesen: {file_reason}.", False
    if content is None:
        return f"Compose kann {path} nicht lesen. Die Meldung zeigt Nodvard Deck nicht, sie kann Passwörter enthalten.", True
    needle, reason = content
    # Bei einem offenen Anführungszeichen nennt Compose die letzte Zeile der Datei, nicht die des Werts.
    where = f" (Zeile {line[1]})" if line is not None and needle != "unterminated quoted value" else ""
    return f"Compose kann {path} nicht lesen: {reason}{where}. {_HIDDEN}", True
