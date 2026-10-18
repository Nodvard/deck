"""Der schmale Schema-Prüfer (`core/schema_check.py`): prüft die Parameter einer Aktion vor dem Gate und nennt in einem
deutschen Satz, welches Feld nicht passt. Unbekannte Schlüsselwörter lässt er durch (verbindlich prüft der Ausführer)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nodvard_deck.core.schema_check import SchemaError, check_value, number_text

EXTENSIONS = Path(__file__).resolve().parents[2] / "extensions"


def _message(value, schema) -> str:
    with pytest.raises(SchemaError) as info:
        check_value(value, schema)
    return str(info.value)


def test_valid_values_pass():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "count": {"type": "integer"}, "ratio": {"type": "number"}},
        "required": ["name"],
    }
    check_value({"name": "x"}, schema)
    check_value({"name": "x", "count": 3, "ratio": 0.5, "unbekannt": True}, schema)
    check_value({"name": "x", "count": 3.0}, schema)  # 3.0 ist in JSON eine ganze Zahl


@pytest.mark.parametrize("schema", [None, True, False, [], "x", {}, {"type": "object"}])
def test_missing_or_trivial_schema_accepts_everything(schema):
    check_value({"command": 7, "irgendwas": [1, {"a": None}]}, schema)


@pytest.mark.parametrize(
    ("value", "type_name", "expected_word"),
    [
        (1, "string", "Text"),
        ("1", "integer", "ganze Zahl"),
        (1.5, "integer", "ganze Zahl"),
        ("1", "number", "Zahl"),
        ("true", "boolean", "Ja/Nein"),
        ("a", "array", "Liste"),
        ([], "object", "Objekt"),
        (0, "null", "leer"),
    ],
)
def test_wrong_type_is_named(value, type_name, expected_word):
    text = _message({"feld": value}, {"type": "object", "properties": {"feld": {"type": type_name}}})
    assert text.startswith("Das Feld „feld“ hat den falschen Typ (erwartet: " + expected_word), text


def test_bool_is_not_a_number_and_a_number_is_not_a_bool():
    assert "falschen Typ" in _message({"n": True}, {"properties": {"n": {"type": "integer"}}})
    assert "falschen Typ" in _message({"n": True}, {"properties": {"n": {"type": "number"}}})
    assert "falschen Typ" in _message({"b": 1}, {"properties": {"b": {"type": "boolean"}}})
    check_value({"b": False}, {"properties": {"b": {"type": "boolean"}}})


def test_type_list_and_unknown_type_name():
    schema = {"properties": {"v": {"type": ["string", "null"]}}}
    check_value({"v": None}, schema)
    check_value({"v": "x"}, schema)
    assert "Text oder leer" in _message({"v": 1}, schema)
    # Ein Typname, den der Prüfer nicht kennt, wird nicht geprüft.
    check_value({"v": 1}, {"properties": {"v": {"type": "secret"}}})


def test_text_names_the_wrong_value_type():
    assert "erhalten: Liste" in _message({"v": [1]}, {"properties": {"v": {"type": "string"}}})
    assert "erhalten: leer" in _message({"v": None}, {"properties": {"v": {"type": "string"}}})
    assert "erhalten: Objekt" in _message({"v": {}}, {"properties": {"v": {"type": "string"}}})
    assert "erhalten: Zahl" in _message({"v": 1.5}, {"properties": {"v": {"type": "string"}}})
    assert "erhalten: Ja/Nein" in _message({"v": True}, {"properties": {"v": {"type": "string"}}})


def test_required_means_present_and_not_null():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    assert _message({}, schema) == "Das Feld „name“ fehlt (Pflichtfeld)."
    assert _message({"name": None}, schema) == "Das Feld „name“ fehlt (Pflichtfeld)."
    # Ein leerer Text ist ein Wert; ohne Muster oder Mindestlänge entscheidet der Ausführer.
    check_value({"name": ""}, schema)


def test_title_is_used_as_field_name():
    schema = {"properties": {"snapname": {"type": "string", "title": "Name des Snapshots"}}, "required": ["snapname"]}
    assert "„Name des Snapshots“" in _message({}, schema)
    assert "„Name des Snapshots“" in _message({"snapname": 1}, schema)


def test_enum_compares_like_json():
    schema = {"properties": {"m": {"enum": ["soft", "hard", 3, True]}}}
    check_value({"m": "soft"}, schema)
    check_value({"m": 3}, schema)
    check_value({"m": True}, schema)
    assert _message({"m": "weich"}, schema) == "Das Feld „m“ muss einer dieser Werte sein: soft, hard, 3, true."
    assert "muss einer dieser Werte sein" in _message({"m": 1}, {"properties": {"m": {"enum": [True]}}})  # 1 ist nicht true
    assert "muss einer dieser Werte sein" in _message({"m": True}, {"properties": {"m": {"enum": [1]}}})


def test_pattern_uses_the_same_rules_as_the_browser():
    schema = {"properties": {"c": {"type": "string", "pattern": "^[a-z]+$"}}}
    check_value({"c": "abc"}, schema)
    assert _message({"c": "ABC"}, schema) == "Das Feld „c“: Das Format stimmt nicht."
    # `$` heißt Ende des Textes, auch in Python (dort ließe es sonst ein `\n` am Ende durch).
    assert "Format" in _message({"c": "abc\n"}, schema)
    # Ein leerer Text wird wie jeder andere geprüft.
    assert "Format" in _message({"c": ""}, schema)
    check_value({"c": ""}, {"properties": {"c": {"pattern": "^[a-z]*$"}}})


def test_pattern_message_hint_and_broken_pattern():
    schema = {"properties": {"c": {"pattern": "^[a-z]+$", "x-pattern-message": "Nur Kleinbuchstaben."}}}
    assert _message({"c": "A"}, schema) == "Das Feld „c“: Nur Kleinbuchstaben."
    check_value({"c": "A"}, {"properties": {"c": {"pattern": "([unclosed"}}})  # kaputtes Muster blockiert nichts


def test_length_limits():
    schema = {"properties": {"s": {"type": "string", "minLength": 2, "maxLength": 4}}}
    check_value({"s": "ab"}, schema)
    check_value({"s": "abcd"}, schema)
    assert _message({"s": "a"}, schema) == "Das Feld „s“ ist zu kurz (mindestens 2 Zeichen)."
    assert _message({"s": "abcde"}, schema) == "Das Feld „s“ ist zu lang (höchstens 4 Zeichen)."


def test_very_long_value_is_not_run_through_the_pattern():
    text = _message({"s": "a" * 5000}, {"properties": {"s": {"pattern": "^a+$"}}})
    assert text == "Das Feld „s“ ist zu lang (höchstens 2000 Zeichen)."
    check_value({"s": "a" * 5000}, {"properties": {"s": {"pattern": "^a+$", "maxLength": 6000}}})


def test_number_range_includes_the_limits():
    schema = {"properties": {"n": {"type": "integer", "minimum": 1, "maximum": 5}}}
    check_value({"n": 1}, schema)
    check_value({"n": 5}, schema)
    assert _message({"n": 0}, schema) == "Das Feld „n“: Erlaubt sind Werte von 1 bis 5. Angegeben ist 0."
    assert _message({"n": 6}, schema).endswith("Angegeben ist 6.")
    assert _message({"n": 2.5}, {"properties": {"n": {"type": "number", "minimum": 3}}}) == (
        "Das Feld „n“: Erlaubt sind Werte ab 3. Angegeben ist 2,5."
    )
    assert _message({"n": 9}, {"properties": {"n": {"maximum": 5}}}) == "Das Feld „n“: Erlaubt sind Werte bis 5. Angegeben ist 9."


def test_not_a_finite_number_is_rejected():
    assert "Bitte eine Zahl" in _message({"n": float("nan")}, {"properties": {"n": {"type": "number"}}})
    assert "Bitte eine Zahl" in _message({"n": float("inf")}, {"properties": {"n": {"type": "number"}}})


HUGE = 10**400
"""Der JSON-Leser liefert so etwas als Python-`int`; in ein `float` passt es nicht."""


def test_huge_integer_without_limits_passes():
    check_value({"n": HUGE}, {"properties": {"n": {"type": "integer"}}})
    check_value({"n": -HUGE}, {"properties": {"n": {"type": "number"}}})
    check_value({"n": HUGE}, {"properties": {"n": {}}})
    # Die Aufzählung vergleicht exakt, ohne Umweg über `float`.
    check_value({"n": HUGE}, {"properties": {"n": {"enum": [1, HUGE]}}})
    assert "muss einer dieser Werte sein" in _message({"n": HUGE}, {"properties": {"n": {"enum": [1.5, 3]}}})


def test_huge_integer_is_checked_against_limits_without_crashing():
    both = {"properties": {"n": {"type": "integer", "minimum": 1, "maximum": 5}}}
    assert _message({"n": HUGE}, both) == "Das Feld „n“: Erlaubt sind Werte von 1 bis 5. Angegeben ist 1,000 × 10^400."
    assert _message({"n": -HUGE}, both).endswith("Angegeben ist -1,000 × 10^400.")
    # Nur eine Grenze, die verletzt wird, und eine, die nicht verletzt wird.
    assert _message({"n": HUGE}, {"properties": {"n": {"maximum": 5}}}).startswith("Das Feld „n“: Erlaubt sind Werte bis 5.")
    check_value({"n": HUGE}, {"properties": {"n": {"type": "integer", "minimum": 1}}})
    check_value({"n": -HUGE}, {"properties": {"n": {"type": "integer", "maximum": 5}}})
    # Kleine Dezimalgrenzen vergleichen sich mit dem riesigen Wert ohne Fehler.
    check_value({"n": HUGE}, {"properties": {"n": {"type": "number", "minimum": 0.5}}})
    assert "Erlaubt sind Werte bis 5,5" in _message({"n": HUGE}, {"properties": {"n": {"type": "number", "maximum": 5.5}}})


def test_huge_limit_in_the_schema_does_not_crash():
    check_value({"n": 5}, {"properties": {"n": {"type": "integer", "maximum": HUGE}}})
    text = _message({"n": 5}, {"properties": {"n": {"type": "integer", "minimum": HUGE}}})
    assert text == "Das Feld „n“: Erlaubt sind Werte ab 1,000 × 10^400. Angegeben ist 5."
    # NaN und unendlich als Grenze gelten weiter als „keine Grenze“.
    check_value({"n": 5}, {"properties": {"n": {"minimum": float("inf"), "maximum": float("nan")}}})


def test_number_text_shortens_huge_integers_only():
    assert number_text(2**63) == "9223372036854775808"
    assert number_text(10**19) == "10000000000000000000"  # 20 Stellen stehen noch voll da
    assert number_text(10**20) == "1,000 × 10^20"
    assert number_text(-(10**20)) == "-1,000 × 10^20"
    assert number_text(HUGE) == "1,000 × 10^400"
    assert number_text(1.5e300) == "1,500 × 10^300"  # eine ganzzahlige Dezimalzahl wird wie eine ganze behandelt
    assert number_text(2.5) == "2,5"
    assert number_text(3.0) == "3"
    # Über 4300 Stellen verweigert `str()` die Wandlung: auch das darf nicht abstürzen.
    assert number_text(10**5000) == "eine sehr große Zahl"


def test_nested_objects_and_paths():
    schema = {
        "type": "object",
        "properties": {"changes": {"type": "object", "properties": {"cores": {"type": "integer"}}, "required": ["cores"]}},
        "required": ["changes"],
    }
    check_value({"changes": {"cores": 2}}, schema)
    assert _message({"changes": {}}, schema) == "Das Feld „changes.cores“ fehlt (Pflichtfeld)."
    assert "„changes.cores“ hat den falschen Typ" in _message({"changes": {"cores": "2"}}, schema)
    assert "„changes“ hat den falschen Typ" in _message({"changes": []}, schema)


def test_arrays_items_and_counts():
    schema = {"properties": {"ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2}}}
    check_value({"ids": [1, 2]}, schema)
    assert "„ids[2]“ hat den falschen Typ" in _message({"ids": [1, "x"]}, schema)
    assert _message({"ids": []}, schema) == "Das Feld „ids“: mindestens 1 Einträge."
    assert _message({"ids": [1, 2, 3]}, schema) == "Das Feld „ids“: höchstens 2 Einträge."


def test_additional_properties():
    closed = {"properties": {"a": {"type": "string"}}, "additionalProperties": False}
    check_value({"a": "x"}, closed)
    assert _message({"a": "x", "b": 1}, closed) == "Das Feld „b“ ist hier nicht vorgesehen."
    typed = {"properties": {"a": {"type": "string"}}, "additionalProperties": {"type": "integer"}}
    check_value({"a": "x", "b": 1}, typed)
    assert "„b“ hat den falschen Typ" in _message({"a": "x", "b": "1"}, typed)
    check_value({"a": "x", "b": "egal"}, {"properties": {"a": {}}, "additionalProperties": True})


def test_unknown_keywords_are_ignored_not_rejected():
    schema = {
        "type": "object",
        "properties": {"u": {"type": "string", "format": "uri", "contentEncoding": "base64"}},
        "oneOf": [{"required": ["nope"]}],
        "$ref": "#/nirgendwo",
        "dependentRequired": {"u": ["v"]},
    }
    check_value({"u": "kein uri"}, schema)


def test_broken_schema_parts_are_ignored():
    check_value({"a": 1}, {"properties": ["a"], "required": "a", "enum": "x", "items": [1], "minLength": "2"})
    check_value({"a": 1}, {"properties": {"a": "kein Schema"}, "required": [3, None]})


def _load_extension(monkeypatch, name: str):
    monkeypatch.syspath_prepend(str(EXTENSIONS / name / "src"))
    yield
    for module in list(sys.modules):
        if module.startswith(f"nodvard_deck_ext_{name}"):
            del sys.modules[module]


@pytest.fixture
def docker_ext(monkeypatch):
    yield from _load_extension(monkeypatch, "docker")


@pytest.fixture
def proxmox_ext(monkeypatch):
    yield from _load_extension(monkeypatch, "proxmox")


def test_real_docker_schemas_accept_the_values_the_executor_accepts(docker_ext):
    from nodvard_deck_ext_docker.capabilities import container_action_specs
    from nodvard_deck_ext_docker.resources import resource_action_specs

    specs = {s.action_type: s for s in [*container_action_specs(), *resource_action_specs()] if s.params_schema}
    assert set(specs) == {
        "container.start", "container.stop", "container.restart", "container.remove", "docker.stack_restart",
        "docker.image_remove", "docker.volume_remove", "docker.network_remove",
    }
    good = {
        "container": "web-1.prod", "project": "nextcloud_prod", "volume": "data_volume",
        "image": "sha256:" + "a" * 64, "network": "b" * 64,
    }
    for spec in specs.values():
        (field,) = spec.params_schema["required"]
        check_value({field: good[field]}, spec.params_schema)
        assert f"„{field}“ fehlt" in _message({}, spec.params_schema)
        assert f"„{field}“ hat den falschen Typ" in _message({field: 5}, spec.params_schema)
        for bad in ("", "; rm -rf /", "-x", "mit leerzeichen", good[field] + "\n"):
            assert "Format stimmt nicht" in _message({field: bad}, spec.params_schema), (spec.action_type, bad)


def test_real_proxmox_schemas_accept_the_values_the_executor_accepts(proxmox_ext):
    from nodvard_deck_ext_proxmox.capabilities import _VM_ACTION_SPECS

    specs = {s.action_type: s for s in _VM_ACTION_SPECS if s.params_schema}
    assert set(specs) == {"vm.snapshot_rollback", "vm.snapshot_delete", "vm.config_set"}
    check_value({"snapname": "vor-update"}, specs["vm.snapshot_rollback"].params_schema)
    check_value({"snapname": "vor-update"}, specs["vm.snapshot_delete"].params_schema)
    check_value({"changes": {"cores": 4}}, specs["vm.config_set"].params_schema)
    for action_type, field in (("vm.snapshot_rollback", "snapname"), ("vm.snapshot_delete", "snapname"), ("vm.config_set", "changes")):
        assert f"„{field}“ fehlt" in _message({}, specs[action_type].params_schema)
    assert "„snapname“ hat den falschen Typ" in _message({"snapname": 7}, specs["vm.snapshot_delete"].params_schema)
    assert "„changes“ hat den falschen Typ" in _message({"changes": "cores=4"}, specs["vm.config_set"].params_schema)
