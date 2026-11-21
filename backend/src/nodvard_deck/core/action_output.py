"""Ausgabe und Fehlertexte von Aktionen: wer sie sehen darf und was ins Protokoll kommt (alles ohne Datenbank)."""

from __future__ import annotations

import re
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
# Schlüssel in Protokoll-Details, deren Text auch ohne `hosts.execute` sichtbar bleibt: Kennungen, Namen und feste
# Zustände. Bewusst eine Erlaubnisliste wie beim `payload`: Jeder andere Text (Fehlermeldungen, Notizen, Pfade, Adressen)
# kann vom Server stammen. Zahlen, Ja/Nein und leere Werte bleiben unter jedem Schlüssel.
_SAFE_DETAIL_TEXT_KEYS = _SAFE_PAYLOAD_KEYS | {
    "action_type", "account_id", "notification_type", "ext_id", "host", "host_ids", "host_name", "credential_id",
    "previous_credential_id", "key_id", "previous_key_id", "key_type", "finding_id", "scan_id", "run_id", "request_id",
    "task_id", "status", "mode", "trigger", "via", "rule", "check", "code", "gate_error", "kind", "id",
    "role", "roles", "username", "label", "on_behalf_of", "template", "group", "step", "protocol", "app_version",
    "version", "scopes", "created_at", "fingerprint", "name", "key",
}
# Der Titel einer Meldung kann einen Servernamen oder Text eines Moduls tragen. Sichtbar bleibt er nur beim Löschen einer
# Meldung (fremde Kontomeldungen blendet `services.audit` dort aus).
_TITLE_ACTIONS = frozenset({"notifications.deleted"})
# Unter `task_status` steht bei einem Fehlschlag teils der Ausgang von Proxmox als freier Text. Sichtbar bleiben nur
# diese festen Zustände.
_TASK_STATES = frozenset({
    "OK", "ok", "running", "failed", "timeout", "lost", "finished", "unfinished", "rejected", "given_up",
})
# Unter diesen Schlüsseln schreibt der Kern feste Codes (`"renewed"`, `"ok"`, `"bootstrap"`), Module teils auch freien
# Text. Sichtbar bleibt deshalb nur, was wie ein Code aussieht.
_CODE_DETAIL_KEYS = frozenset({"reason", "result", "source", "aktion", "change"})
_CODE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}")
# Begründungen dieser Einträge sind feste Sätze des Kerns; Erweiterungen dürfen unter diesen Namen nicht schreiben
# (`ext.context.CORE_AUDIT_PREFIXES`). Jede andere Begründung kann Text vom Server tragen. `system.` fehlt bewusst:
# Dort steht teils der Text einer Ausnahme, etwa beim Einspielen einer Sicherung.
FIXED_REASON_PREFIXES = ("auth.", "login.", "mfa.", "user.")


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


def _scrub(value: Any, *, text_ok: bool = False, text_keys: frozenset[str] = _SAFE_DETAIL_TEXT_KEYS) -> tuple[Any, bool]:
    """Leert rekursiv `output`/`stdout`/`stderr` und `command` sowie jeden Text, dessen Schlüssel nicht in
    `text_keys` steht oder der zu lang ist (`null`, das Feld bleibt)."""
    if isinstance(value, dict):
        hidden = False
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "task_status":
                known = isinstance(item, str) and item in _TASK_STATES
                hidden = hidden or (not known and _filled(item))
                out[key] = item if known else None
                continue
            if key in _HIDDEN_DETAIL_KEYS:
                hidden = hidden or _filled(item)
                out[key] = None
                continue
            if key in _CODE_DETAIL_KEYS and isinstance(item, str) and _CODE.fullmatch(item):
                out[key] = item
                continue
            out[key], inner = _scrub(item, text_ok=key in text_keys)
            hidden = hidden or inner
        return out, hidden
    if isinstance(value, list):
        hidden = False
        items = []
        for item in value:
            cleaned, inner = _scrub(item, text_ok=text_ok)
            items.append(cleaned)
            hidden = hidden or inner
        return items, hidden
    if isinstance(value, str) and not (text_ok and len(value) <= _MAX_SAFE_VALUE_LENGTH):
        return None, _filled(value)
    return value, False


def hide_audit_detail(action: str, detail: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Details eines Protokolleintrags ohne Server-Ausgabe. Bei `action.executed` bleibt vom Ergebnis nur die
    Kurzfassung (gespeicherte Einträge können noch Ausgabe und Fehlertext tragen); überall bleibt Text nur unter
    Schlüsseln der Erlaubnisliste."""
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
    text_keys = _SAFE_DETAIL_TEXT_KEYS | {"title"} if action in _TITLE_ACTIONS else _SAFE_DETAIL_TEXT_KEYS
    cleaned, inner = _scrub(detail, text_keys=text_keys)
    return cleaned, hidden or inner


def hide_audit_reason(action: str, reason: str | None) -> tuple[str | None, bool]:
    """Begründung eines Protokolleintrags ohne `hosts.execute`: nur die festen Sätze des Kerns, sonst `null`."""
    if not reason or action.startswith(FIXED_REASON_PREFIXES):
        return reason, False
    return None, True


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
