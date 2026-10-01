"""Parametersubstitution fuer Skripte (docs/02-EXTENSION-API.md Paragraph 6).

`string.Template` (`$name`-Syntax) statt einer neuen Template-Sprache -- dieselbe
Zurueckhaltung wie `nodvard_sdk.widgets`s bewusst winzige `{{ }}`-Ausdruecke ohne
Bedingungen/Schleifen.

Bleibt bewusst reine, synchrone Logik ohne `ctx`-Abhaengigkeit (wie
`nodvard_deck_ext_nexus_soc.parsing`) -- leicht isoliert testbar, das eigentliche Aufloesen
von `type="secret"`-Werten (das `ctx.vault_use()` braucht) passiert eine Ebene hoeher.
"""

from __future__ import annotations

import shlex
from string import Template
from typing import Any


class ParamError(Exception):
    pass


def substitute_params(content: str, params_schema: dict[str, dict[str, Any]], values: dict[str, str]) -> str:
    """Ersetzt `$name` im Skriptinhalt durch den SHELL-QUOTIERTEN Wert.

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

    try:
        return Template(content).substitute(resolved)
    except KeyError as exc:
        raise ParamError(f"Skript verwendet unbekannten Platzhalter: {exc}") from exc
    except ValueError as exc:
        raise ParamError(f"Ungültiger Platzhalter im Skript: {exc}") from exc
