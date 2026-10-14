"""Ausgabe und Fehlertexte von Aktionen: wer sie sehen darf und was ins Protokoll kommt (alles ohne Datenbank)."""

from __future__ import annotations

from typing import Any

# Ausgabe und Befehl (`payload`) einer Aktion können Serverinhalte, Passwörter oder Adressen enthalten und sind nur für
# Nutzer mit diesem Recht sichtbar (Owner und Admin über `*`). Für alle anderen bleiben `output`, `error` und `detail`
# als leere Felder (`null`, `null`, `{}`), damit Aufrufer (auch die App) nicht an einem fehlenden Feld scheitern.
OUTPUT_PERMISSION = "hosts.execute"

# Felder des `payload`, die nur Kennungen oder kurze Namen tragen. Bewusst eine Erlaubnisliste: Payloads sind je
# Aktionstyp frei (Erweiterungen bringen eigene mit), und `command`, `params`, `changes`, `values` oder ein künftiges
# Feld könnten Zugangsdaten tragen. Nur einfache Werte (Text, Zahl, Ja/Nein) kommen durch, nie Listen oder Unterobjekte.
_SAFE_PAYLOAD_KEYS = frozenset({
    "host_id", "vmid", "node", "connection", "job_id", "script_id", "unit",
    "container", "project", "service", "image", "snapname", "storage", "minutes",
})
_SIMPLE_TYPES = (str, int, float, bool)
_MAX_SAFE_VALUE_LENGTH = 200

# Felder eines Ergebnisses, die keinen Text vom Server tragen.
_SAFE_RESULT_KEYS = frozenset({"success", "exit_code", "duration_ms"})
# Zusätzlich in der Kurzfassung fürs Protokoll. `gate_error` ist ein fester Text des Gates selbst (Zeitüberschreitung,
# abgebrochen, kein Executor ...), nie ein Text vom Server.
_SAFE_AUDIT_RESULT_KEYS = _SAFE_RESULT_KEYS | {"output_length", "error_length", "gate_error"}
# Schlüssel, die in beliebigen Protokoll-Details Server-Ausgabe tragen können.
_OUTPUT_KEYS = frozenset({"output", "stdout", "stderr"})
# Dazu Befehle (z. B. ein von der Sperrliste abgefangener Befehl, `exec.denied`): Sie können wie der `payload`
# Zugangsdaten enthalten.
_HIDDEN_DETAIL_KEYS = _OUTPUT_KEYS | {"command"}


def _filled(value: Any) -> bool:
    return value not in (None, "", {}, [])


def audit_result(result: dict[str, Any], *, gate_error: str | None = None) -> dict[str, Any]:
    """Was vom Ergebnis ins Protokoll kommt: Erfolg, Exitcode, Dauer und die Länge von Ausgabe und Fehlertext, nie der
    Text selbst. `gate_error` ist der feste Text, wenn das Gate die Ausführung selbst beendet hat; er darf nie Text vom
    Server enthalten."""
    summary: dict[str, Any] = {k: result[k] for k in _SAFE_RESULT_KEYS if k in result}
    for key, name in (("output", "output_length"), ("error", "error_length")):
        value = result.get(key)
        if not (isinstance(value, str) and value):
            continue
        # Steht der Text selbst im Protokoll, braucht es die Länge nicht noch einmal.
        if key == "error" and value == gate_error:
            continue
        summary[name] = len(value)
    if gate_error:
        summary["gate_error"] = gate_error
    return summary


def hide_result(result: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Ergebnis ohne Text vom Server (alle Felder bleiben, leer); der zweite Wert sagt, ob etwas weggelassen wurde."""
    hidden = False
    out: dict[str, Any] = {}
    for key, value in result.items():
        if key in _SAFE_RESULT_KEYS:
            out[key] = value
            continue
        hidden = hidden or _filled(value)
        out[key] = {} if key == "detail" else None
    return out, hidden


def _scrub(value: Any) -> tuple[Any, bool]:
    """Leert rekursiv alle Felder `output`/`stdout`/`stderr` und `command` (`null`, das Feld bleibt)."""
    if isinstance(value, dict):
        hidden = False
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _HIDDEN_DETAIL_KEYS:
                hidden = hidden or _filled(item)
                out[key] = None
                continue
            out[key], inner = _scrub(item)
            hidden = hidden or inner
        return out, hidden
    if isinstance(value, list):
        hidden = False
        items = []
        for item in value:
            cleaned, inner = _scrub(item)
            items.append(cleaned)
            hidden = hidden or inner
        return items, hidden
    return value, False


def hide_audit_detail(action: str, detail: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Details eines Protokolleintrags ohne Server-Ausgabe. Bei `action.executed` bleibt vom Ergebnis nur die
    Kurzfassung (gespeicherte Einträge können noch Ausgabe und Fehlertext tragen); überall werden
    `output`/`stdout`/`stderr` und `command` geleert."""
    detail = dict(detail or {})
    hidden = False
    result = detail.get("result")
    if action == "action.executed" and isinstance(result, dict):
        kept: dict[str, Any] = {}
        for key, value in result.items():
            if key in _SAFE_AUDIT_RESULT_KEYS:
                kept[key] = value
                continue
            hidden = hidden or _filled(value)
            kept[key] = {} if key == "detail" else None
        # Wie bei `hide_result`: die drei Felder sind immer da. Gespeicherte Einträge kennen die Längen teils nicht, sie
        # zu ergänzen wäre erfunden.
        kept.setdefault("output", None)
        kept.setdefault("error", None)
        kept.setdefault("detail", {})
        detail["result"] = kept
    cleaned, inner = _scrub(detail)
    return cleaned, hidden or inner


def visible_payload(payload: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Der `payload` einer Aktion nur mit harmlosen Kennungen (siehe `_SAFE_PAYLOAD_KEYS`).
    Der zweite Wert sagt, ob etwas weggelassen wurde."""
    out: dict[str, Any] = {}
    hidden = False
    for key, value in (payload or {}).items():
        if (
            key in _SAFE_PAYLOAD_KEYS
            and isinstance(value, _SIMPLE_TYPES)
            and not (isinstance(value, str) and len(value) > _MAX_SAFE_VALUE_LENGTH)
        ):
            out[key] = value
        else:
            hidden = hidden or _filled(value)
    return out, hidden


def redact_action_event(data: dict[str, Any]) -> dict[str, Any]:
    """Daten eines `action.*`-Ereignisses ohne Befehl, für Empfänger ohne Server-Recht; ohne `payload` unverändert."""
    raw = data.get("payload")
    if not isinstance(raw, dict):
        return data
    payload, hidden = visible_payload(raw)
    return {**data, "payload": payload, "payload_hidden": hidden}
