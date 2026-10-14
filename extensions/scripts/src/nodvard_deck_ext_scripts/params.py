"""Setzt die Parameter eines Skripts als shell-quotierte Werte ein; jedes andere `$` bleibt Shell-Syntax."""

from __future__ import annotations

import re
import shlex
from typing import Any


class ParamError(Exception):
    pass


# Wie `string.Template.idpattern` (nur ASCII), aber festgeschrieben, damit sich nicht mit der Python-Version ändert, was
# an die Shell geht. `$hostname` ist der Name `hostname`, nicht der Parameter `host` (dafür `${host}name`).
_NAME = r"[_a-zA-Z][_a-zA-Z0-9]*"

# Wie `string.Template.pattern`, nur ohne `invalid`: Ein `$`, auf das nichts davon folgt, bleibt stehen.
_PLACEHOLDER = re.compile(rf"\$(?:(?P<escaped>\$)|(?P<named>{_NAME})|\{{(?P<braced>{_NAME})\}})")


def escape_dollars(text: str) -> str:
    """Schreibt jedes `$` als `$$`, damit `substitute_params()` wieder genau `text` ergibt."""
    return text.replace("$", "$$")


def substitute_params(content: str, params_schema: dict[str, dict[str, Any]], values: dict[str, str]) -> str:
    """Ersetzt `$name`/`${name}` deklarierter Parameter durch den shell-quotierten Wert und `$$` durch `$`."""
    # Nur deklarierte Namen sind erlaubt, sonst ließe sich über ein unerwartetes Feld ein Befehl einschleusen.
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
        # Quotiert, damit ein Wert nie als Shell-Syntax gelesen wird.
        resolved[name] = shlex.quote(str(raw))

    def _replace(match: re.Match[str]) -> str:
        # `$$` ergibt ein `$`; die Prozessnummer `$$` der Shell schreibt man `$$$$`.
        if match.group("escaped") is not None:
            return "$"
        name = match.group("named") or match.group("braced")
        # Nicht deklariert: gehört der Shell (z. B. `$HOME`).
        return resolved.get(name, match.group(0))

    return _PLACEHOLDER.sub(_replace, content)
