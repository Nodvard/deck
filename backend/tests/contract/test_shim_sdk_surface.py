"""Der alte Importname `lattice_sdk` liefert dieselbe Oberflaeche wie `nodvard_sdk` (Umbenennung Lattice -> Nodvard Deck).

Der Vertrags-Schnappschuss (`sdk_public.json`) enthaelt beide Namen. Hier steht, warum das reicht:
 * Alles, was vor der Umbenennung unter `lattice_sdk.*` stand, steht unveraendert weiter da (Entfernen = Bruch).
 * Unter dem alten Namen ist genau das erreichbar, was unter dem neuen erreichbar ist -- nicht mehr, nicht weniger.
 * Die alten Klassennamen (`LatticeExtension`, `LatticeError`) sind weiter Teil der Oberflaeche, als Aliase.

Diese Datei darf `lattice_sdk` importieren (Ausnahme im Waechter `scripts/check_legacy_names.py`: `test_shim_*`).
"""

from __future__ import annotations

import importlib
import json
import warnings

import contract_lib as cl


def _saved() -> dict:
    return json.loads(cl.SDK_SNAPSHOT.read_text(encoding="utf-8"))["names"]


def _surface(package: str) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return cl.build_sdk_snapshot_from(importlib.import_module(package), label=package)["names"]


def _strip(names: dict, package: str) -> dict:
    return {key[len(package):]: value for key, value in names.items() if key.startswith(package + ".")}


def test_the_snapshot_covers_both_import_names():
    assert cl.SDK_PACKAGES == ("nodvard_sdk", "lattice_sdk")
    saved = _saved()
    assert any(k.startswith("nodvard_sdk.") for k in saved) and any(k.startswith("lattice_sdk.") for k in saved)


def test_the_old_name_offers_exactly_the_same_surface_as_the_new_one():
    new, old = _strip(_surface("nodvard_sdk"), "nodvard_sdk"), _strip(_surface("lattice_sdk"), "lattice_sdk")
    assert new and old == new


def test_every_name_the_old_package_offered_before_the_rename_is_still_there():
    saved = _saved()
    old_names = {k: v for k, v in saved.items() if k.startswith("lattice_sdk.")}
    assert len(old_names) >= 183, "so viele Namen hatte der Schnappschuss vor der Umbenennung"
    current = _surface("lattice_sdk")
    assert not [k for k in old_names if k not in current], "entfernt"
    assert {k: v for k, v in old_names.items() if current[k] != v} == {}, "geaendert"


def test_the_old_class_names_are_part_of_the_surface_as_aliases():
    saved = _saved()
    for name in (
        "lattice_sdk.LatticeExtension", "lattice_sdk.LatticeError",
        "lattice_sdk.extension.LatticeExtension", "lattice_sdk.errors.LatticeError",
        "nodvard_sdk.LatticeExtension", "nodvard_sdk.LatticeError",
        "nodvard_sdk.extension.LatticeExtension", "nodvard_sdk.errors.LatticeError",
        "nodvard_sdk.NodvardExtension", "nodvard_sdk.NodvardError",
        "lattice_sdk.NodvardExtension", "lattice_sdk.NodvardError",
    ):
        assert name in saved, name
    assert saved["lattice_sdk.LatticeExtension"] == saved["nodvard_sdk.NodvardExtension"]
    assert saved["lattice_sdk.extension.LatticeExtension"] == saved["lattice_sdk.extension.NodvardExtension"]


def test_removing_an_old_name_is_reported_as_a_break():
    """Faellt `lattice_sdk` weg (oder ein Alias darin), meldet der Vertragstest einen Bruch statt still nichts zu finden."""
    saved = _saved()
    current = {"format": cl.FORMAT_VERSION, "names": {k: v for k, v in _surface("lattice_sdk").items() if k != "lattice_sdk.LatticeExtension"}}
    breaks = cl.find_sdk_breaks({"names": saved}, current)
    assert [(b.pfad, b.art) for b in breaks if b.pfad == "lattice_sdk.LatticeExtension"] == [("lattice_sdk.LatticeExtension", "sdk_name_entfernt")]
