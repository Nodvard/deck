"""Schmale Prüfung von Werten gegen ein JSON-Schema, bewusst ohne `jsonschema`-Abhängigkeit."""

from __future__ import annotations

import json
import math
import re
from typing import Any

PATTERN_MAX_LENGTH = 2000
"""Längere Werte werden vor der Musterprüfung abgelehnt (die Prüfung läuft synchron)."""


def js_anchors(pattern: str) -> str:
    """`$` heißt in JavaScript „Ende des Textes“, in Python auch „vor einem `\\n` am Ende“, darum wird es zu `\\Z`."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "$":
            out.append("\\Z")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def pattern_matches(pattern: str, value: str) -> bool:
    try:
        return re.search(js_anchors(pattern), value) is not None
    except re.error:
        return True  # ein kaputtes Muster im Schema darf nie eine gültige Eingabe verhindern


def bound(schema: dict[str, Any], key: str) -> float | None:
    value = schema.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # `math.isfinite` wirft bei einer riesigen ganzen Zahl (`10**400`) `OverflowError`, nur ein `float` kann NaN sein.
    return value if isinstance(value, int) or math.isfinite(value) else None


_SHORT_DIGITS = 20
"""Ganze Zahlen mit mehr Stellen erscheinen in Meldungen gekürzt."""


def number_text(value: float) -> str:
    """Zahl für eine Meldung: ganze Zahlen ohne Nachkommastellen, sonst mit deutschem Komma, riesige gekürzt."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        try:
            digits = str(abs(value))
        except ValueError:  # `str()` verweigert ganze Zahlen mit über 4300 Stellen
            return "eine sehr große Zahl"
        if len(digits) > _SHORT_DIGITS:
            return f"{'-' if value < 0 else ''}{digits[0]},{digits[1:4]} × 10^{len(digits) - 1}"
    return str(value).replace(".", ",")


def range_text(low: float | None, high: float | None) -> str:
    """Nur mit mindestens einer Grenze aufrufen."""
    if low is not None and high is not None:
        return f"Erlaubt sind Werte von {number_text(low)} bis {number_text(high)}."
    if low is not None:
        return f"Erlaubt sind Werte ab {number_text(low)}."
    return f"Erlaubt sind Werte bis {number_text(high)}."


class SchemaError(ValueError):
    """Ein Wert passt nicht zum Schema; der Text ist für Anwender gedacht (deutsch, nennt das Feld und was falsch
    ist)."""


_TYPE_LABEL = {
    "string": "Text",
    "integer": "ganze Zahl",
    "number": "Zahl",
    "boolean": "Ja/Nein",
    "array": "Liste",
    "object": "Objekt",
    "null": "leer",
}


def _matches_type(value: Any, name: str) -> bool:  # noqa: ANN401 - jeder JSON-Wert
    if name == "null":
        return value is None
    if isinstance(value, bool):  # `True` ist in Python auch ein `int`, in JSON aber keine Zahl
        return name == "boolean"
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float))
    if name == "boolean":
        return False
    if name == "array":
        return isinstance(value, list)
    return isinstance(value, dict)


def _value_type_label(value: Any) -> str:  # noqa: ANN401 - jeder JSON-Wert
    if value is None:
        return _TYPE_LABEL["null"]
    if isinstance(value, bool):
        return _TYPE_LABEL["boolean"]
    if isinstance(value, str):
        return _TYPE_LABEL["string"]
    if isinstance(value, int):
        return _TYPE_LABEL["integer"]
    if isinstance(value, float):
        return _TYPE_LABEL["number"]
    if isinstance(value, list):
        return _TYPE_LABEL["array"]
    return _TYPE_LABEL["object"]


def _declared_types(schema: dict[str, Any]) -> list[str]:
    declared = schema.get("type")
    names = [declared] if isinstance(declared, str) else declared if isinstance(declared, list) else []
    if not names or any(n not in _TYPE_LABEL for n in names):
        return []
    return names


def _label(schema: dict[str, Any], path: str) -> str:
    title = schema.get("title")
    return title if isinstance(title, str) and title else path or "Angaben"


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _show(value: Any) -> str:  # noqa: ANN401 - jeder JSON-Wert
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _in_enum(value: Any, allowed: list[Any]) -> bool:  # noqa: ANN401 - jeder JSON-Wert
    # `True == 1` gilt in Python, in JSON aber nicht.
    return any(isinstance(item, bool) == isinstance(value, bool) and item == value for item in allowed)


# Geprüft werden nur `type`, `enum`, `pattern`, `minLength`/`maxLength`, `minimum`/`maximum`, `properties`, `required`,
# `additionalProperties`, `items` und `minItems`/`maxItems`. Alles andere (`oneOf`, `$ref`, `format`, ...) wird weder
# geprüft noch abgelehnt: lieber eine Angabe durchlassen als eine gültige Aktion blockieren. Verbindlich prüft immer
# der Ausführer der Aktion.
def check_value(value: Any, schema: Any, path: str = "") -> None:  # noqa: ANN401 - jeder JSON-Wert, jedes Schema
    """Wirft `SchemaError` mit dem ersten gefundenen Fehler, wenn `value` nicht zu `schema` passt (`path` ist der
    Feldname)."""
    if not isinstance(schema, dict):
        return
    name = _label(schema, path)
    types = _declared_types(schema)
    if types and not any(_matches_type(value, n) for n in types):
        expected = " oder ".join(_TYPE_LABEL[n] for n in types)
        raise SchemaError(
            f"Das Feld „{name}“ hat den falschen Typ (erwartet: {expected}, erhalten: {_value_type_label(value)})."
        )
    allowed = schema.get("enum")
    if isinstance(allowed, list) and not _in_enum(value, allowed):
        raise SchemaError(
            f"Das Feld „{name}“ muss einer dieser Werte sein: {', '.join(_show(v) for v in allowed)}."
        )
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, str):
        _check_string(value, schema, name)
    elif isinstance(value, (int, float)):
        _check_number(value, schema, name)
    elif isinstance(value, list):
        _check_array(value, schema, name, path)
    elif isinstance(value, dict):
        _check_object(value, schema, path)


def _int_limit(schema: dict[str, Any], key: str) -> int | None:
    value = schema.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _check_string(value: str, schema: dict[str, Any], name: str) -> None:
    shortest, longest = _int_limit(schema, "minLength"), _int_limit(schema, "maxLength")
    if shortest is not None and len(value) < shortest:
        raise SchemaError(f"Das Feld „{name}“ ist zu kurz (mindestens {shortest} Zeichen).")
    if longest is not None and len(value) > longest:
        raise SchemaError(f"Das Feld „{name}“ ist zu lang (höchstens {longest} Zeichen).")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        # Ein sehr langer Wert wird nicht mehr gegen das Muster geprüft (die Prüfung läuft synchron).
        limit = longest if longest else PATTERN_MAX_LENGTH
        if len(value) > limit:
            raise SchemaError(f"Das Feld „{name}“ ist zu lang (höchstens {limit} Zeichen).")
        if not pattern_matches(pattern, value):
            hint = schema.get("x-pattern-message")
            raise SchemaError(f"Das Feld „{name}“: {hint if isinstance(hint, str) and hint else 'Das Format stimmt nicht.'}")


def _check_number(value: float, schema: dict[str, Any], name: str) -> None:
    # NaN und unendlich nimmt der JSON-Leser von Python an, sie sind aber keine gültigen JSON-Zahlen. Nur ein `float`
    # kann das sein: bei einer riesigen ganzen Zahl (`10**400`) würfe `math.isfinite` einen `OverflowError` (also 500
    # statt 422). Der Vergleich mit `minimum`/`maximum` ist für ganze Zahlen beliebiger Größe dagegen exakt.
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaError(f"Das Feld „{name}“: Bitte eine Zahl angeben.")
    low, high = bound(schema, "minimum"), bound(schema, "maximum")
    if (low is not None and value < low) or (high is not None and value > high):
        raise SchemaError(f"Das Feld „{name}“: {range_text(low, high)} Angegeben ist {number_text(value)}.")


def _check_array(value: list[Any], schema: dict[str, Any], name: str, path: str) -> None:
    fewest, most = _int_limit(schema, "minItems"), _int_limit(schema, "maxItems")
    if fewest is not None and len(value) < fewest:
        raise SchemaError(f"Das Feld „{name}“: mindestens {fewest} Einträge.")
    if most is not None and len(value) > most:
        raise SchemaError(f"Das Feld „{name}“: höchstens {most} Einträge.")
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value, start=1):
            check_value(item, item_schema, f"{path}[{index}]")


def _check_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    raw_props = schema.get("properties")
    props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
    required = schema.get("required")
    for key in required if isinstance(required, list) else []:
        # `null` zählt als „nicht angegeben“; ein leerer Text dagegen ist ein Wert, über den Muster und Länge entscheiden.
        if isinstance(key, str) and value.get(key) is None:
            sub = props.get(key)
            label = _label(sub if isinstance(sub, dict) else {}, _join(path, key))
            raise SchemaError(f"Das Feld „{label}“ fehlt (Pflichtfeld).")
    for key, sub in props.items():
        if key in value:
            check_value(value[key], sub, _join(path, key))
    extra = schema.get("additionalProperties")
    if extra is False or isinstance(extra, dict):
        for key, item in value.items():
            if key in props:
                continue
            if extra is False:
                raise SchemaError(f"Das Feld „{_join(path, key)}“ ist hier nicht vorgesehen.")
            check_value(item, extra, _join(path, key))
