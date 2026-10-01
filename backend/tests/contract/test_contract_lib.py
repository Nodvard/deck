"""Unit-Tests der Vergleichslogik mit kleinen Fake-Schemas (ohne die echte App)."""

from __future__ import annotations

import copy
import types

import contract_lib as cl
import pytest


def doc(paths: dict, schemas: dict | None = None) -> dict:
    return {"openapi": "3.1.0", "info": {"title": "x", "version": "1"}, "paths": paths,
            "components": {"schemas": schemas or {}}}


def op(*, params=None, body=None, body_required=True, response=None, status="200") -> dict:
    out: dict = {"responses": {status: {"description": "ok"}}}
    if response is not None:
        out["responses"][status]["content"] = {"application/json": {"schema": response}}
    if params:
        out["parameters"] = params
    if body is not None:
        out["requestBody"] = {"required": body_required, "content": {"application/json": {"schema": body}}}
    return out


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S, I = {"type": "string"}, {"type": "integer"}


def arts(old: dict, new: dict) -> set[tuple[str, str]]:
    a = cl.build_api_snapshot(old)
    b = cl.build_api_snapshot(new)
    return {(x.art, x.ziel) for x in cl.find_api_breaks(a, b)}


BASE = doc({"/api/v1/things": {
    "get": op(params=[{"name": "limit", "in": "query", "required": False, "schema": I}],
              response=obj({"id": S, "count": I, "meta": obj({"a": S})}, ["id"])),
    "post": op(body=obj({"name": S, "size": I}, ["name"]), response=obj({"id": S})),
}})


def test_identisch_ist_kein_bruch():
    assert arts(BASE, copy.deepcopy(BASE)) == set()


def test_titel_wird_ignoriert():
    new = copy.deepcopy(BASE)
    new["info"]["title"] = "Nodvard Deck"
    assert cl.build_api_snapshot(BASE) == cl.build_api_snapshot(new)


def test_hinzufuegen_ist_erlaubt():
    new = copy.deepcopy(BASE)
    new["paths"]["/api/v1/neu"] = {"get": op(response=obj({"x": S}))}
    g = new["paths"]["/api/v1/things"]["get"]
    g["parameters"].append({"name": "q", "in": "query", "required": False, "schema": S})
    g["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["extra"] = S
    g["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["meta"]["properties"]["b"] = I
    p = new["paths"]["/api/v1/things"]["post"]
    p["requestBody"]["content"]["application/json"]["schema"]["properties"]["optional_new"] = S
    new["paths"]["/api/v1/things"]["delete"] = op()
    assert arts(BASE, new) == set()


def test_endpunkt_entfernt():
    new = copy.deepcopy(BASE)
    del new["paths"]["/api/v1/things"]["post"]
    assert arts(BASE, new) == {("endpunkt_entfernt", "")}
    del new["paths"]["/api/v1/things"]
    assert ("endpunkt_entfernt", "") in arts(BASE, new)


def test_antwortfeld_entfernt_auch_verschachtelt():
    new = copy.deepcopy(BASE)
    props = new["paths"]["/api/v1/things"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    del props["count"]
    del props["meta"]["properties"]["a"]
    assert arts(BASE, new) == {("antwortfeld_entfernt", "200.count"), ("antwortfeld_entfernt", "200.meta.a")}


def test_antwortstatus_entfernt():
    new = copy.deepcopy(BASE)
    resp = new["paths"]["/api/v1/things"]["get"]["responses"]
    resp["201"] = resp.pop("200")
    assert arts(BASE, new) == {("antwortstatus_entfernt", "200")}


def test_neuer_pflicht_parameter_und_parameter_jetzt_pflicht():
    new = copy.deepcopy(BASE)
    params = new["paths"]["/api/v1/things"]["get"]["parameters"]
    params.append({"name": "must", "in": "query", "required": True, "schema": S})
    params[0]["required"] = True
    assert arts(BASE, new) == {("parameter_pflicht", "query:must"), ("parameter_pflicht", "query:limit")}


def test_parameter_entfernt():
    new = copy.deepcopy(BASE)
    new["paths"]["/api/v1/things"]["get"]["parameters"] = []
    assert arts(BASE, new) == {("parameter_entfernt", "query:limit")}


def test_body_pflichtfeld_neu_und_feld_jetzt_pflicht():
    new = copy.deepcopy(BASE)
    schema = new["paths"]["/api/v1/things"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["must"] = S
    schema["required"] = ["name", "must", "size"]
    assert arts(BASE, new) == {("body_feld_pflicht", "must"), ("body_feld_pflicht", "size")}


def test_body_feld_entfernt():
    new = copy.deepcopy(BASE)
    del new["paths"]["/api/v1/things"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["size"]
    assert arts(BASE, new) == {("body_feld_entfernt", "size")}


def test_body_fehlte_vorher_und_ist_jetzt_pflicht():
    new = copy.deepcopy(BASE)
    new["paths"]["/api/v1/things"]["get"]["requestBody"] = {
        "required": True, "content": {"application/json": {"schema": obj({"a": S})}}}
    assert arts(BASE, new) == {("body_pflicht", "")}


def test_optionaler_body_wird_pflicht_aber_optional_neu_ist_erlaubt():
    old = doc({"/p": {"post": op(body=obj({"a": S}), body_required=False)}})
    new = doc({"/p": {"post": op(body=obj({"a": S}), body_required=True)}})
    assert arts(old, new) == {("body_pflicht", "")}
    assert arts(new, old) == set()


def test_typwechsel_in_antwort_und_anfrage():
    new = copy.deepcopy(BASE)
    g = new["paths"]["/api/v1/things"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    g["properties"]["count"] = S
    p = new["paths"]["/api/v1/things"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    p["properties"]["size"] = S
    assert arts(BASE, new) == {("typ_geaendert", "200.count"), ("typ_geaendert", "size")}


def test_null_erlaubt_ist_in_der_antwort_ein_bruch_in_der_anfrage_nicht():
    opt = {"anyOf": [S, {"type": "null"}]}
    a = doc({"/p": {"post": op(body=obj({"a": S}), response=obj({"r": S}))}})
    b = doc({"/p": {"post": op(body=obj({"a": opt}), response=obj({"r": opt}))}})
    assert arts(a, b) == {("typ_geaendert", "200.r")}
    assert arts(b, a) == {("typ_geaendert", "a")}  # Anfrage enger geworden


def test_enum_wert_entfernt_und_neuer_wert_erlaubt():
    a = doc({"/p": {"get": op(response=obj({"s": {"type": "string", "enum": ["a", "b"]}}))}})
    b = doc({"/p": {"get": op(response=obj({"s": {"type": "string", "enum": ["a"]}}))}})
    c = doc({"/p": {"get": op(response=obj({"s": {"type": "string", "enum": ["a", "b", "c"]}}))}})
    assert arts(a, b) == {("enum_wert_entfernt", "200.s")}
    assert arts(a, c) == set()


def test_ref_wird_aufgeloest_auch_rekursiv():
    schemas = {
        "Item": obj({"id": S, "child": {"$ref": "#/components/schemas/Item"}}, ["id"]),
        "Out": obj({"items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}}),
    }
    d = doc({"/p": {"get": op(response={"$ref": "#/components/schemas/Out"})}}, schemas)
    snap = cl.build_api_snapshot(d)
    resp = snap["endpoints"]["GET /p"]["responses"]["200"]
    assert resp["fields"]["items"]["type"] == "array"
    assert resp["fields"]["items"]["items"]["fields"]["id"] == {"type": "string"}
    assert "child" in resp["fields"]["items"]["items"]["fields"]  # Rekursion bricht ab, kein Absturz
    # Feld im aufgeloesten Modell entfernt -> Bruch
    d2 = copy.deepcopy(d)
    del d2["components"]["schemas"]["Item"]["properties"]["id"]
    assert arts(d, d2) == {("antwortfeld_entfernt", "200.items[].id")}


def test_pydantic_optional_ref_nullable():
    schemas = {"E": {"type": "string", "enum": ["x", "y"]}}
    d = doc({"/p": {"get": op(response=obj({"e": {"anyOf": [{"$ref": "#/components/schemas/E"}, {"type": "null"}]}}))}}, schemas)
    node = cl.build_api_snapshot(d)["endpoints"]["GET /p"]["responses"]["200"]["fields"]["e"]
    assert node == {"type": "null|string", "enum": ["x", "y"]}


def test_ausnahme_greift_nur_auf_passenden_bruch():
    new = copy.deepcopy(BASE)
    del new["paths"]["/api/v1/things"]["post"]
    breaks = cl.find_api_breaks(cl.build_api_snapshot(BASE), cl.build_api_snapshot(new))
    ok = [{"pfad": "/api/v1/things", "methode": "post", "art": "endpunkt_entfernt", "begruendung": "x"}]
    other_method = [{"pfad": "/api/v1/things", "methode": "GET", "art": "endpunkt_entfernt", "begruendung": "x"}]
    assert cl.uncovered(breaks, ok) == []
    assert cl.uncovered(breaks, other_method) == breaks
    assert cl.uncovered(breaks, []) == breaks


def test_ausnahme_mit_ziel_gilt_nur_fuer_dieses_ziel():
    new = copy.deepcopy(BASE)
    props = new["paths"]["/api/v1/things"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    del props["count"], props["id"]
    breaks = cl.find_api_breaks(cl.build_api_snapshot(BASE), cl.build_api_snapshot(new))
    ex = [{"pfad": "/api/v1/things", "methode": "GET", "art": "antwortfeld_entfernt", "ziel": "200.count",
           "begruendung": "x"}]
    left = cl.uncovered(breaks, ex)
    assert [b.ziel for b in left] == ["200.id"]


def test_fehlermeldung_ist_deutsch_und_vollstaendig():
    new = copy.deepcopy(BASE)
    del new["paths"]["/api/v1/things"]["post"]
    props = new["paths"]["/api/v1/things"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    del props["count"]
    text = cl.format_breaks(cl.find_api_breaks(cl.build_api_snapshot(BASE), cl.build_api_snapshot(new)))
    for part in ("POST /api/v1/things", "GET /api/v1/things -> 200.count", "abwärtskompatibel",
                 "deprecated", "breaking_exceptions.toml", "--allow-breaking"):
        assert part in text


def test_veraltet_meldung_nennt_den_befehl():
    a = cl.build_api_snapshot(BASE)
    new = copy.deepcopy(BASE)
    new["paths"]["/api/v1/neu"] = {"get": op()}
    text = cl.stale_message("API", a, cl.build_api_snapshot(new), "endpoints")
    assert "python scripts/update_api_contract.py" in text and "GET /api/v1/neu" in text


def test_ausnahmeliste_validierung(tmp_path):
    f = tmp_path / "e.toml"
    f.write_text('[[ausnahmen]]\npfad = "/x"\nmethode = "GET"\nart = "endpunkt_entfernt"\nbegruendung = ""\n')
    with pytest.raises(ValueError, match="begruendung"):
        cl.load_exceptions(f)
    f.write_text('[[ausnahmen]]\npfad = "/x"\nmethode = "GET"\nart = "endpunkt_entfernt"\nbegruendung = "TODO: warum?"\n')
    with pytest.raises(ValueError, match="TODO"):
        cl.load_exceptions(f)
    f.write_text('[[ausnahmen]]\npfad = "/x"\nmethode = "GET"\nart = "quatsch"\nbegruendung = "weil"\n')
    with pytest.raises(ValueError, match="unbekannte Art"):
        cl.load_exceptions(f)
    f.write_text('[[ausnahmen]]\npfad = "/x"\nmethode = "GET"\nart = "endpunkt_entfernt"\nbegruendung = "weil"\n')
    assert len(cl.load_exceptions(f)) == 1
    assert cl.load_exceptions(tmp_path / "gibt-es-nicht.toml") == []


# ----------------------------------------------------------------------------- SDK


@pytest.fixture
def fake_sdk(monkeypatch):
    def make(version: int) -> dict:
        mod = types.ModuleType("fake_sdk_pkg")
        mod.__path__ = []  # Paket ohne Untermodule
        if version == 1:
            class Spec:
                def __init__(self, label: str, icon: str = "") -> None: ...
                def run(self, host: str, *, force: bool = False) -> None: ...

            def helper(a, b=1): ...
            mod.__all__ = ["Spec", "helper", "LIMIT"]
            mod.Spec, mod.helper, mod.LIMIT = Spec, helper, 5
        else:
            class Spec:  # type: ignore[no-redef]
                def __init__(self, label: str, icon: str = "", extra: int = 0) -> None: ...
                def run(self, host: str, *, force: bool = False, new: int = 1) -> None: ...

            def helper(a, b=1, c=2): ...
            def neu(x): ...
            mod.__all__ = ["Spec", "helper", "LIMIT", "neu"]
            mod.Spec, mod.helper, mod.LIMIT, mod.neu = Spec, helper, 5, neu
        return cl.build_sdk_snapshot_from(mod)
    return make


def test_sdk_hinzufuegen_ist_erlaubt(fake_sdk):
    assert cl.find_sdk_breaks(fake_sdk(1), fake_sdk(2)) == []


def test_sdk_entfernte_namen_und_parameter_sind_brueche(fake_sdk):
    new = fake_sdk(1)
    del new["names"]["fake_sdk_pkg.LIMIT"]
    del new["names"]["fake_sdk_pkg.helper"]["params"]["b"]
    del new["names"]["fake_sdk_pkg.Spec"]["params"]["icon"]
    del new["names"]["fake_sdk_pkg.Spec"]["methoden"]["run"]["force"]
    found = {(b.art, b.pfad, b.ziel) for b in cl.find_sdk_breaks(fake_sdk(1), new)}
    assert found == {
        ("sdk_name_entfernt", "fake_sdk_pkg.LIMIT", ""),
        ("sdk_parameter_entfernt", "fake_sdk_pkg.helper", "b"),
        ("sdk_parameter_entfernt", "fake_sdk_pkg.Spec", "icon"),
        ("sdk_parameter_entfernt", "fake_sdk_pkg.Spec", "run(): force"),
    }


def test_sdk_neuer_pflicht_parameter_und_artwechsel(fake_sdk):
    new = fake_sdk(1)
    new["names"]["fake_sdk_pkg.helper"]["params"]["must"] = True
    new["names"]["fake_sdk_pkg.LIMIT"] = {"art": "funktion", "params": {}}
    found = {(b.art, b.pfad) for b in cl.find_sdk_breaks(fake_sdk(1), new)}
    assert found == {("sdk_parameter_pflicht", "fake_sdk_pkg.helper"), ("sdk_art_geaendert", "fake_sdk_pkg.LIMIT")}


def test_sdk_ausnahme_greift(fake_sdk):
    new = fake_sdk(1)
    del new["names"]["fake_sdk_pkg.LIMIT"]
    breaks = cl.find_sdk_breaks(fake_sdk(1), new)
    ex = [{"pfad": "fake_sdk_pkg.LIMIT", "methode": "SDK", "art": "sdk_name_entfernt", "begruendung": "x"}]
    assert cl.uncovered(breaks, ex) == []


def test_sdk_alte_namen_per_modul_getattr_und_private_module(tmp_path, monkeypatch):
    """`_LEGACY_NAMES` eines SDK-Moduls (alter Name -> neuer Name, per Modul-`__getattr__`) kommt in den Schnappschuss;
    private Module (`_intern`) gehoeren nicht zum Vertrag."""
    import importlib
    import sys

    pkg = tmp_path / "fake_alias_pkg"
    pkg.mkdir()
    getattr_code = "def __getattr__(name):\n    if name == 'Alt':\n        return Neu\n    raise AttributeError(name)\n"
    (pkg / "__init__.py").write_text(
        "from .mod import Neu\n__all__ = ['Neu', 'Alt']\n_LEGACY_NAMES = {'Alt': 'Neu'}\n" + getattr_code, encoding="utf-8")
    (pkg / "mod.py").write_text(
        "class Neu:\n    def __init__(self, a): ...\n_LEGACY_NAMES = {'Alt': 'Neu'}\n" + getattr_code, encoding="utf-8")
    (pkg / "_intern.py").write_text("def hilfsfunktion(x): ...\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        names = cl.build_sdk_snapshot_from(importlib.import_module("fake_alias_pkg"))["names"]
    finally:
        for name in [n for n in sys.modules if n.startswith("fake_alias_pkg")]:
            del sys.modules[name]
    assert sorted(names) == ["fake_alias_pkg.Alt", "fake_alias_pkg.Neu", "fake_alias_pkg.mod.Alt", "fake_alias_pkg.mod.Neu"]
    assert names["fake_alias_pkg.mod.Alt"] == names["fake_alias_pkg.mod.Neu"] == {"art": "klasse", "params": {"a": True}}
