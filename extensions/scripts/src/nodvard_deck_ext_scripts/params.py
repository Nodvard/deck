"""Parametersubstitution fuer Skripte (docs/02-EXTENSION-API.md Paragraph 6).

Ersetzt werden NUR die Parameter, die das Skript in `params_schema` deklariert, als
`$name` oder `${name}` -- jedes andere `$` ist normale Shell-Syntax und bleibt, wie es
ist (`$HOME`, `"$f"`, `$(date)`, `$1`, `$?`, `${VAR:-x}`, `$name` mit einem nicht
deklarierten Namen). Frueher lief das ganze Skript durch `string.Template.substitute()`,
und jedes solche `$` liess den Lauf scheitern.

`$$` steht weiter fuer ein einzelnes `$` (so war es bei `string.Template`, und
bestehende Skripte nutzen es): Ein Skript, das bisher ohne Fehler lief, ergibt Zeichen
fuer Zeichen denselben Befehl wie vorher. Gebraucht wird `$$` nur noch fuer ein `$`
direkt vor einem Parameternamen; die Prozessnummer `$$` der Shell schreibt man `$$$$`.

Ein Name wird wie bei `string.Template` erkannt: ASCII-Buchstabe oder `_`, danach
Buchstaben, Ziffern oder `_`, so lang wie moeglich. `$hostname` ist also der Name
`hostname` und ersetzt NICHT den Parameter `host` (dafuer `${host}name`).

Bleibt bewusst reine, synchrone Logik ohne `ctx`-Abhaengigkeit (wie
`nodvard_deck_ext_shield.parsing`) -- leicht isoliert testbar, das eigentliche Aufloesen
von `type="secret"`-Werten (das `ctx.vault_use()` braucht) passiert eine Ebene hoeher.
"""

from __future__ import annotations

import re
import shlex
from typing import Any


class ParamError(Exception):
    pass


_NAME = r"[_a-zA-Z][_a-zA-Z0-9]*"
"""Derselbe Bezeichner wie `string.Template.idpattern` (dort `[_a-z][_a-z0-9]*` mit
`re.IGNORECASE`, nur ASCII). Bewusst hier festgeschrieben statt von `string.Template`
geliehen: Was an die Shell geht, darf sich nicht mit einer Python-Version aendern."""

_PLACEHOLDER = re.compile(rf"\$(?:(?P<escaped>\$)|(?P<named>{_NAME})|\{{(?P<braced>{_NAME})\}})")
"""Dieselben Alternativen in derselben Reihenfolge wie `string.Template.pattern`, nur ohne
`invalid`: Ein `$`, auf das nichts davon folgt, wird nicht gefunden und bleibt stehen."""


def escape_dollars(text: str) -> str:
    """Schreibt jedes `$` als `$$`, damit `substitute_params()` wieder genau `text`
    ergibt -- fuer Befehle, die woertlich in ein Skript uebernommen werden."""
    return text.replace("$", "$$")


def substitute_params(content: str, params_schema: dict[str, dict[str, Any]], values: dict[str, str]) -> str:
    """Ersetzt `$name`/`${name}` fuer jeden deklarierten Parameter durch den
    SHELL-QUOTIERTEN Wert und `$$` durch `$`; alles andere bleibt unveraendert.

    `params_schema` ist die Liste der ERLAUBTEN Namen -- ein Wert fuer einen Namen, den
    das Skript gar nicht deklariert, wird abgelehnt (keine Befehlsinjektion ueber ein
    Feld, das das Skript nicht erwartet). Fehlt ein Wert UND hat der Parameter keinen
    `default`, ist das ein Fehler statt einer stillen Leerstring-Substitution.
    """
    unknown = set(values) - set(params_schema)
    if unknown:
        raise ParamError(f"Unbekannte Parameter: {sorted(unknown)}")

    resolved: dict[str, str] = {}
    for name, spec in params_schema.items():
        if name in values:
            raw = values[name]
        elif "default" in spec:
            raw = spec["default"]
        else:
            raise ParamError(f"Parameter '{name}' fehlt (kein Default in params_schema).")
        resolved[name] = shlex.quote(str(raw))

    def _replace(match: re.Match[str]) -> str:
        if match.group("escaped") is not None:
            return "$"
        name = match.group("named") or match.group("braced")
        # Nicht deklariert: gehoert der Shell (z. B. `$HOME`), bleibt unveraendert.
        return resolved.get(name, match.group(0))

    return _PLACEHOLDER.sub(_replace, content)
