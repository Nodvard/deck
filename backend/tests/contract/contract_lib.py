"""Vergleichslogik fuer den oeffentlichen API-/SDK-Vertrag (Nodvard Link).

Regel (docs/04-API.md "Kompatibilitaet"): Aenderungen an
`/api/v1` und am SDK `nodvard_sdk` (alter Name `lattice_sdk`, ein Alias) sind nur abwaertskompatibel.
Dieses Modul baut aus `app.openapi()` bzw. `nodvard_sdk` einen kompakten, stabil sortierten Schnappschuss und
vergleicht zwei Staende. Es kennt weder die echte App noch pytest -- dadurch lassen sich
alle Regeln mit kleinen Fake-Schemas testen (`test_contract_lib.py`).

Ein Schnappschuss-Knoten (`node`) beschreibt einen Wert grob:
    {"type": "string" | "integer" | "a|b" (Vereinigung, "null" inklusive) | "any",
     "enum": [...], "fields": {name: node}, "items": node}
Nur auf der Anfrageseite (Parameter, Body) traegt ein Knoten zusaetzlich `"required": true`.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTRACT_DIR = Path(__file__).resolve().parent
API_SNAPSHOT = CONTRACT_DIR / "api_v1.json"
SDK_SNAPSHOT = CONTRACT_DIR / "sdk_public.json"
EXCEPTIONS_FILE = CONTRACT_DIR / "breaking_exceptions.toml"

UPDATE_COMMAND = "python scripts/update_api_contract.py"
FORMAT_VERSION = 1
MAX_DEPTH = 6
HTTP_METHODS = ("get", "put", "post", "delete", "patch")
SDK_METHOD = "SDK"

# Art des Bruchs -> verstaendliche Beschreibung (fuer Fehlermeldungen).
KINDS: dict[str, str] = {
    "endpunkt_entfernt": "Endpunkt (Pfad + Methode) entfernt oder umbenannt",
    "antwortfeld_entfernt": "Antwortfeld entfernt oder umbenannt",
    "antwortstatus_entfernt": "Erfolgsantwort (2xx) entfällt",
    "enum_wert_entfernt": "Wert einer festen Werteliste entfernt",
    "typ_geaendert": "Typ geändert",
    "parameter_entfernt": "Parameter entfernt oder umbenannt",
    "parameter_pflicht": "Neuer Pflicht-Parameter (oder Parameter jetzt Pflicht)",
    "body_feld_entfernt": "Feld im Anfrage-Body entfernt oder umbenannt",
    "body_feld_pflicht": "Neues Pflichtfeld im Anfrage-Body (oder Feld jetzt Pflicht)",
    "body_pflicht": "Anfrage braucht jetzt einen Body, der vorher fehlte oder optional war",
    "sdk_name_entfernt": "Öffentlicher SDK-Name entfernt oder umbenannt",
    "sdk_parameter_entfernt": "SDK: Parameter / Feld entfernt oder umbenannt",
    "sdk_parameter_pflicht": "SDK: neuer Parameter ohne Vorgabewert",
    "sdk_art_geaendert": "SDK: Art geändert (z. B. Klasse wird Funktion)",
}


@dataclass(frozen=True)
class Break:
    pfad: str  # API-Pfad bzw. SDK-Name
    methode: str  # GET/POST/... bzw. "SDK"
    art: str  # Schluessel aus KINDS
    ziel: str = ""  # wo genau (Feldpfad, Parametername); leer = ganzer Endpunkt
    detail: str = ""

    def describe(self) -> str:
        wo = f" -> {self.ziel}" if self.ziel else ""
        extra = f" ({self.detail})" if self.detail else ""
        return f"{self.methode} {self.pfad}{wo}: {KINDS[self.art]}{extra}"


# --------------------------------------------------------------------------- OpenAPI


def _resolve(schema: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    node: Any = doc
    for part in ref.removeprefix("#/").split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _types_of(schema: dict[str, Any]) -> set[str]:
    t = schema.get("type")
    if isinstance(t, list):
        return set(t)
    if isinstance(t, str):
        return {t}
    return set()


def schema_node(
    schema: dict[str, Any],
    doc: dict[str, Any],
    *,
    track_required: bool,
    depth: int = 0,
    seen: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Uebersetzt ein OpenAPI-Schema (mit aufgeloesten `$ref`) in einen Knoten."""
    ref = schema.get("$ref")
    if ref:
        if ref in seen:  # rekursives Modell: hier abbrechen
            return {"type": "object"}
        seen = (*seen, ref)
        schema = _resolve(schema, doc)

    types = _types_of(schema)
    enum = list(schema["enum"]) if "enum" in schema else None
    if "const" in schema:
        enum = [schema["const"]]
    fields: dict[str, Any] = {}
    required = set(schema.get("required", []))
    items: dict[str, Any] | None = None

    def add_fields(src: dict[str, Any], req: set[str]) -> None:
        for name, sub in (src.get("properties") or {}).items():
            child = schema_node(sub, doc, track_required=track_required, depth=depth + 1, seen=seen)
            if track_required and name in req:
                child["required"] = True
            fields.setdefault(name, child)

    # allOf: Felder zusammenfuehren; anyOf/oneOf: Vereinigung (Pydantic: Optional[X] = anyOf[X, null]).
    parts = list(schema.get("allOf", []))
    alternatives = list(schema.get("anyOf", [])) + list(schema.get("oneOf", []))
    for alt in alternatives:
        sub = schema_node(alt, doc, track_required=track_required, depth=depth + 1, seen=seen)
        types |= set(sub["type"].split("|"))
        for name, child in sub.get("fields", {}).items():
            # In einer Vereinigung ist ein Feld nie sicher vorhanden.
            child = {k: v for k, v in child.items() if k != "required"}
            fields.setdefault(name, child)
        if "items" in sub and items is None:
            items = sub["items"]
        if sub.get("enum"):
            enum = sorted({*(enum or []), *sub["enum"]}, key=lambda v: json.dumps(v))
    for part in parts:
        sub = schema_node(part, doc, track_required=track_required, depth=depth + 1, seen=seen)
        if sub["type"] != "any":
            types |= set(sub["type"].split("|"))
        for name, child in sub.get("fields", {}).items():
            fields.setdefault(name, child)
        if "items" in sub and items is None:
            items = sub["items"]
        if sub.get("enum") and enum is None:
            enum = sub["enum"]

    if "properties" in schema:
        types.add("object")
        if depth < MAX_DEPTH:
            add_fields(schema, required)
    if "items" in schema and isinstance(schema["items"], dict):
        types.add("array")
        if depth < MAX_DEPTH:
            items = schema_node(schema["items"], doc, track_required=False, depth=depth + 1, seen=seen)
    if depth >= MAX_DEPTH:
        fields, items = {}, None

    if "any" in types:
        types = {"any"}
    node: dict[str, Any] = {"type": "|".join(sorted(types)) if types else "any"}
    if enum is not None:
        node["enum"] = sorted(enum, key=lambda v: json.dumps(v))
    if fields:
        node["fields"] = dict(sorted(fields.items()))
    if items is not None:
        node["items"] = items
    return node


def _body_node(op: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any] | None:
    body = op.get("requestBody")
    if not body:
        return None
    body = _resolve(body, doc)
    content = body.get("content") or {}
    if not content:
        return {"required": bool(body.get("required"))}
    ctype = "application/json" if "application/json" in content else min(content)
    node = schema_node(content[ctype].get("schema", {}), doc, track_required=True)
    out: dict[str, Any] = {"content_type": ctype, "required": bool(body.get("required"))}
    out["schema"] = node
    return out


def build_api_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    """Kompakter Vertrag aus einem OpenAPI-Dokument. Der Titel wird bewusst ignoriert."""
    endpoints: dict[str, Any] = {}
    for path, item in sorted((doc.get("paths") or {}).items()):
        shared = item.get("parameters", [])
        for method in HTTP_METHODS:
            op = item.get(method)
            if op is None:
                continue
            params: dict[str, Any] = {}
            for raw in [*shared, *op.get("parameters", [])]:
                p = _resolve(raw, doc)
                node = schema_node(p.get("schema", {}), doc, track_required=True)
                if p.get("required"):
                    node["required"] = True
                params[f"{p['in']}:{p['name']}"] = node
            entry: dict[str, Any] = {}
            if params:
                entry["params"] = dict(sorted(params.items()))
            body = _body_node(op, doc)
            if body is not None:
                entry["body"] = body
            responses: dict[str, Any] = {}
            for status, resp in sorted((op.get("responses") or {}).items()):
                if not str(status).startswith("2"):
                    continue
                resp = _resolve(resp, doc)
                content = resp.get("content") or {}
                ctype = "application/json" if "application/json" in content else (min(content) if content else None)
                responses[str(status)] = (
                    schema_node(content[ctype].get("schema", {}), doc, track_required=False) if ctype else None
                )
            entry["responses"] = responses
            endpoints[f"{method.upper()} {path}"] = entry
    return {"format": FORMAT_VERSION, "endpoints": endpoints}


# --------------------------------------------------------------------------- Vergleich


def _types(node: dict[str, Any]) -> set[str]:
    return set(node.get("type", "any").split("|"))


def _type_changed(old: dict[str, Any], new: dict[str, Any], *, direction: str) -> bool:
    """Antwort (`response`): neue Typen muessen eine Teilmenge der alten sein (enger ist
    sicher). Anfrage (`request`): neue Typen muessen die alten umfassen (weiter ist sicher).
    Ein unbekannter Typ (`any`) ist nie ein Bruch, wenn er schon vorher `any` war."""
    o, n = _types(old), _types(new)
    if o == n:
        return False
    if direction == "response":
        if "any" in o:
            return False
        return "any" in n or not n <= o
    if "any" in n:
        return False
    return "any" in o or not o <= n


def _compare_node(
    old: dict[str, Any], new: dict[str, Any], *, direction: str, ziel: str, pfad: str, methode: str, out: list[Break],
    feld_entfernt: str, feld_pflicht: str | None,
) -> None:
    def brk(art: str, where: str, detail: str = "") -> None:
        out.append(Break(pfad, methode, art, where, detail))

    if _type_changed(old, new, direction=direction):
        brk("typ_geaendert", ziel, f"{old.get('type', 'any')} -> {new.get('type', 'any')}")
    # Enum: entfernter Wert. Anfrage: Wert, den der Server nicht mehr nimmt. Antwort: Wert,
    # den der Server nicht mehr liefert (gilt laut Regel ebenfalls als Bruch).
    if "enum" in old:
        for value in old["enum"]:
            if "enum" in new and value not in new["enum"]:
                brk("enum_wert_entfernt", ziel, f"{value!r}")
    old_fields, new_fields = old.get("fields", {}), new.get("fields", {})
    for name, child in old_fields.items():
        sub = f"{ziel}.{name}" if ziel else name
        if name not in new_fields:
            brk(feld_entfernt, sub)
            continue
        _compare_node(child, new_fields[name], direction=direction, ziel=sub, pfad=pfad, methode=methode, out=out,
                      feld_entfernt=feld_entfernt, feld_pflicht=feld_pflicht)
        if feld_pflicht and new_fields[name].get("required") and not child.get("required"):
            brk(feld_pflicht, sub, "war optional")
    if feld_pflicht:
        for name, child in new_fields.items():
            if name not in old_fields and child.get("required"):
                brk(feld_pflicht, f"{ziel}.{name}" if ziel else name, "neu")
    if "items" in old and "items" in new:
        _compare_node(old["items"], new["items"], direction=direction, ziel=f"{ziel}[]", pfad=pfad, methode=methode,
                      out=out, feld_entfernt=feld_entfernt, feld_pflicht=feld_pflicht)


def find_api_breaks(old: dict[str, Any], new: dict[str, Any]) -> list[Break]:
    out: list[Break] = []
    new_eps = new.get("endpoints", {})
    for key, o in sorted(old.get("endpoints", {}).items()):
        methode, pfad = key.split(" ", 1)
        n = new_eps.get(key)
        if n is None:
            out.append(Break(pfad, methode, "endpunkt_entfernt"))
            continue
        # Parameter
        o_params, n_params = o.get("params", {}), n.get("params", {})
        for name, node in o_params.items():
            if name not in n_params:
                out.append(Break(pfad, methode, "parameter_entfernt", name))
                continue
            _compare_node(node, n_params[name], direction="request", ziel=name, pfad=pfad, methode=methode, out=out,
                          feld_entfernt="body_feld_entfernt", feld_pflicht="body_feld_pflicht")
            if n_params[name].get("required") and not node.get("required"):
                out.append(Break(pfad, methode, "parameter_pflicht", name, "war optional"))
        for name, node in n_params.items():
            if name not in o_params and node.get("required"):
                out.append(Break(pfad, methode, "parameter_pflicht", name, "neu"))
        # Body
        o_body, n_body = o.get("body"), n.get("body")
        if n_body and n_body.get("required") and not (o_body and o_body.get("required")):
            out.append(Break(pfad, methode, "body_pflicht", "", "war optional" if o_body else "fehlte vorher"))
        if o_body and "schema" in o_body and n_body and "schema" in n_body:
            _compare_node(o_body["schema"], n_body["schema"], direction="request", ziel="", pfad=pfad, methode=methode,
                          out=out, feld_entfernt="body_feld_entfernt", feld_pflicht="body_feld_pflicht")
        elif o_body and "schema" in o_body and not n_body:
            out.append(Break(pfad, methode, "body_feld_entfernt", "", "ganzer Body entfällt"))
        # Antworten
        for status, o_resp in o.get("responses", {}).items():
            if status not in n.get("responses", {}):
                out.append(Break(pfad, methode, "antwortstatus_entfernt", status))
                continue
            n_resp = n["responses"][status]
            if o_resp is not None and n_resp is not None:
                _compare_node(o_resp, n_resp, direction="response", ziel=status, pfad=pfad, methode=methode, out=out,
                              feld_entfernt="antwortfeld_entfernt", feld_pflicht=None)
            elif o_resp is not None and n_resp is None:
                out.append(Break(pfad, methode, "antwortfeld_entfernt", status, "Antwort hat keinen Inhalt mehr"))
    return out


# --------------------------------------------------------------------------- SDK


def _params_of(obj: Any) -> dict[str, bool] | None:
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    params = {
        name: p.default is inspect.Parameter.empty
        for name, p in sig.parameters.items()
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) and name not in ("self", "cls")
    }
    return dict(sorted(params.items()))


def _describe_sdk_obj(obj: Any) -> dict[str, Any]:
    if inspect.isclass(obj):
        entry: dict[str, Any] = {"art": "klasse"}
        params = _params_of(obj)
        if params:
            entry["params"] = params
        methods: dict[str, Any] = {}
        for name, member in vars(obj).items():
            if name.startswith("_"):
                continue
            func = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
            if inspect.isfunction(func):
                methods[name] = _params_of(func) or {}
        if methods:
            entry["methoden"] = dict(sorted(methods.items()))
        return entry
    if callable(obj):
        return {"art": "funktion", "params": _params_of(obj) or {}}
    return {"art": "konstante"}


SDK_PACKAGES = ("nodvard_sdk", "lattice_sdk")
"""Importnamen des SDK: der neue und der alte (`lattice_sdk` ist ein Alias, siehe sdk/python/lattice_sdk/).
Beide kommen in den Schnappschuss: Der alte Name muss dieselbe Oberflaeche liefern wie vor der Umbenennung,
und eine versehentlich entfernte Alias-Zeile faellt als Bruch auf (`sdk_name_entfernt`)."""


def build_sdk_snapshot(packages: tuple[str, ...] = SDK_PACKAGES) -> dict[str, Any]:
    """Oeffentliche Namen: `__all__` des Pakets sowie je Top-Level-Modul die dort
    definierten oeffentlichen Klassen/Funktionen und GROSS geschriebene Konstanten --
    unter jedem der Importnamen (`nodvard_sdk` und der alte Name `lattice_sdk`)."""
    names: dict[str, Any] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # der alte Importname warnt absichtlich
        for package in packages:
            names.update(build_sdk_snapshot_from(importlib.import_module(package), label=package)["names"])
    return {"format": FORMAT_VERSION, "names": dict(sorted(names.items()))}


def _legacy_aliases(obj: Any) -> dict[str, str]:
    """`_LEGACY_NAMES` eines SDK-Moduls: alter Name -> neuer Name (per Modul-`__getattr__`, nicht in `vars()`)."""
    legacy = getattr(obj, "_LEGACY_NAMES", None)
    return dict(legacy) if isinstance(legacy, dict) else {}


def _silently(obj: Any, name: str) -> Any:
    with warnings.catch_warnings():  # die alten Namen warnen absichtlich (DeprecationWarning)
        warnings.simplefilter("ignore")
        return getattr(obj, name)


def build_sdk_snapshot_from(pkg: Any, label: str | None = None) -> dict[str, Any]:
    """`label`: unter welchem Importnamen das Paket aufgelistet wird (Standard: sein eigener Name). Beim Alias
    `lattice_sdk` ist `pkg.__name__` der echte Name (`nodvard_sdk`), die Schluessel tragen aber den Alias-Namen:
    So steht im Schnappschuss, was ueber den alten Importnamen erreichbar ist."""
    label = label or pkg.__name__
    names: dict[str, Any] = {}
    for name in pkg.__all__:
        names[f"{label}.{name}"] = _describe_sdk_obj(_silently(pkg, name))
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):  # privater Unterbau (z. B. `_legacy`) ist nicht Teil des Vertrags
            continue
        mod = importlib.import_module(f"{label}.{info.name}")  # beim Alias geht das ueber den Alias-Finder
        for name, obj in vars(mod).items():
            if name.startswith("_"):
                continue
            own = getattr(obj, "__module__", None) == mod.__name__
            if (inspect.isclass(obj) or inspect.isfunction(obj)) and own or (name.isupper() and not inspect.ismodule(obj)):
                names[f"{label}.{info.name}.{name}"] = _describe_sdk_obj(obj)
        for old in _legacy_aliases(mod):
            names[f"{label}.{info.name}.{old}"] = _describe_sdk_obj(_silently(mod, old))
    return {"format": FORMAT_VERSION, "names": dict(sorted(names.items()))}


def _compare_members(pfad: str, where: str, old: dict[str, bool], new: dict[str, bool], out: list[Break]) -> None:
    for name in old:
        if name not in new:
            out.append(Break(pfad, SDK_METHOD, "sdk_parameter_entfernt", f"{where}{name}"))
    for name, required in new.items():
        if name not in old and required:
            out.append(Break(pfad, SDK_METHOD, "sdk_parameter_pflicht", f"{where}{name}"))


def find_sdk_breaks(old: dict[str, Any], new: dict[str, Any]) -> list[Break]:
    out: list[Break] = []
    new_names = new.get("names", {})
    for name, o in old.get("names", {}).items():
        n = new_names.get(name)
        if n is None:
            out.append(Break(name, SDK_METHOD, "sdk_name_entfernt"))
            continue
        if o["art"] != n["art"]:
            out.append(Break(name, SDK_METHOD, "sdk_art_geaendert", "", f"{o['art']} -> {n['art']}"))
            continue
        _compare_members(name, "", o.get("params", {}), n.get("params", {}), out)
        for meth, o_params in o.get("methoden", {}).items():
            if meth not in n.get("methoden", {}):
                out.append(Break(name, SDK_METHOD, "sdk_parameter_entfernt", f"{meth}()"))
            else:
                _compare_members(name, f"{meth}(): ", o_params, n["methoden"][meth], out)
    return out


# --------------------------------------------------------------------------- Ausnahmen + Ausgabe


def load_exceptions(path: Path = EXCEPTIONS_FILE) -> list[dict[str, str]]:
    """Liest die Ausnahmeliste. Jeder Eintrag braucht pfad, methode, art und begruendung."""
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("ausnahmen", [])
    for entry in entries:
        for key in ("pfad", "methode", "art", "begruendung"):
            if not str(entry.get(key, "")).strip():
                raise ValueError(f"Ausnahmeliste {path.name}: Eintrag ohne '{key}': {entry}")
        if str(entry["begruendung"]).strip().upper().startswith("TODO"):
            raise ValueError(f"Ausnahmeliste {path.name}: Begründung fehlt noch (steht noch TODO): {entry}")
        if entry["art"] not in KINDS:
            raise ValueError(f"Ausnahmeliste {path.name}: unbekannte Art '{entry['art']}' (erlaubt: {', '.join(KINDS)})")
    return entries


def is_excepted(brk: Break, exceptions: list[dict[str, str]]) -> bool:
    """Eine Ausnahme passt auf Pfad + Methode + Art; mit `ziel` nur auf genau dieses Ziel."""
    return any(
        e["pfad"] == brk.pfad
        and e["methode"].upper() == brk.methode
        and e["art"] == brk.art
        and e.get("ziel", brk.ziel) == brk.ziel
        for e in exceptions
    )


def uncovered(breaks: list[Break], exceptions: list[dict[str, str]]) -> list[Break]:
    return [b for b in breaks if not is_excepted(b, exceptions)]


def exception_snippet(breaks: list[Break]) -> str:
    """TOML-Vorlage zum Kopieren in die Ausnahmeliste (Begruendung muss von Hand kommen)."""
    blocks = []
    for b in breaks:
        ziel = f'ziel = "{b.ziel}"\n' if b.ziel else ""
        blocks.append(
            f'[[ausnahmen]]\npfad = "{b.pfad}"\nmethode = "{b.methode}"\nart = "{b.art}"\n{ziel}'
            'begruendung = "TODO: warum ist dieser Bruch nötig?"\n'
        )
    return "\n".join(blocks)


RULE_TEXT = (
    "Regel (Nodvard Link): Änderungen an /api/v1 und am SDK (nodvard_sdk) sind nur "
    "abwärtskompatibel. Erlaubt ist Hinzufügen (neue Endpunkte, neue optionale Felder, neue "
    "Antwortfelder). Nicht erlaubt: Endpunkte oder Antwortfelder entfernen/umbenennen, neue "
    "Pflichtfelder in Anfragen, Typwechsel. Umbenennen geht nur mit Übergangslösung: der alte "
    "Name bleibt mehrere Releases parallel und ist als deprecated markiert (docs/04-API.md, "
    "Abschnitt „Kompatibilität“)."
)


def format_breaks(breaks: list[Break]) -> str:
    lines = [f"{len(breaks)} Bruch/Brüche der Abwärtskompatibilität gefunden:", ""]
    lines += [f"  - {b.describe()}" for b in breaks]
    lines += [
        "",
        RULE_TEXT,
        "",
        (
            "Ist der Bruch wirklich unvermeidbar: Eintrag mit Begründung in "
            "backend/tests/contract/breaking_exceptions.toml anlegen und dann "
            f"`{UPDATE_COMMAND} --allow-breaking` ausführen. Vorlage:"
        ),
        "",
        exception_snippet(breaks),
    ]
    return "\n".join(lines)


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def changed_keys(old: dict[str, Any], new: dict[str, Any], section: str) -> tuple[list[str], list[str], list[str]]:
    """(hinzugekommen, entfernt, geaendert) auf Ebene der Endpunkte bzw. SDK-Namen."""
    o, n = old.get(section, {}), new.get(section, {})
    return (
        sorted(set(n) - set(o)),
        sorted(set(o) - set(n)),
        sorted(k for k in set(o) & set(n) if o[k] != n[k]),
    )


def stale_message(label: str, old: dict[str, Any], new: dict[str, Any], section: str) -> str:
    added, removed, changed = changed_keys(old, new, section)
    lines = [f"Der Schnappschuss des {label}-Vertrags ist veraltet."]
    for title, keys in (("Neu", added), ("Entfernt", removed), ("Geändert", changed)):
        if keys:
            shown = ", ".join(keys[:15]) + (f" … (+{len(keys) - 15})" if len(keys) > 15 else "")
            lines.append(f"  {title}: {shown}")
    lines += [
        "",
        "Neues Hinzufügen ist erlaubt – der Schnappschuss muss nur nachgezogen werden:",
        f"    {UPDATE_COMMAND}",
        "und die geänderte JSON-Datei mit einchecken.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- Echte App


async def collect_openapi(app: Any, session: Any, settings: Any, extensions_dir: Path) -> dict[str, Any]:
    """`app.openapi()` MIT allen mitgelieferten Extensions. Extension-Routen haengen erst
    nach `enable_extension()` an der App (services/extensions.py) -- ohne dieses Laden
    fehlt ein grosser Teil von /api/v1/ext/... im Schema. Schlaegt das Laden einer
    Extension fehl, bricht das hier laut ab: ein Schnappschuss ohne sie waere still
    unvollstaendig und wuerde je nach Umgebung anders aussehen."""
    from nodvard_deck.services import extensions as svc

    settings.extensions_dir = extensions_dir
    found = await svc.discover_and_sync(session, settings)
    loaded: list[str] = []
    try:
        failed: list[str] = []
        for ext_id in sorted(found):
            await svc.enable_extension(app, session, settings, ext_id)
            record = await session.get(svc.ExtensionRecord, ext_id)
            if record.state != "enabled":
                failed.append(f"{ext_id}: {record.last_error}")
            loaded.append(ext_id)
        if failed:
            raise RuntimeError("Extensions für den API-Vertrag nicht ladbar: " + "; ".join(failed))
        app.openapi_schema = None
        doc = app.openapi()
        app.openapi_schema = None
        return doc
    finally:
        for ext_id in reversed(loaded):
            try:
                await svc.disable_extension(app, session, ext_id)
            except Exception:  # noqa: BLE001, S110 - Aufraeumen darf den eigentlichen Fehler nicht verdecken
                pass
