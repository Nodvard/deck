"""Vergleich von Zieladressen. Die Beispiele liegen in `vectors/same_target.json`; die Oberflaeche
(`frontend/src/lib/targetAddress.test.ts`) prueft dieselben, damit Anzeige und Server gleich urteilen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import nodvard_sdk as sdk
from nodvard_sdk.addresses import same_target, target_form

VECTORS = json.loads((Path(__file__).parent / "vectors" / "same_target.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("value,expected", VECTORS["forms"])
def test_target_form(value, expected):
    assert target_form(value) == expected


@pytest.mark.parametrize("a,b", VECTORS["same"])
def test_same_target(a, b):
    assert same_target(a, b) and same_target(b, a)


@pytest.mark.parametrize("a,b", VECTORS["different"])
def test_different_target(a, b):
    assert not same_target(a, b) and not same_target(b, a)


def test_empty_values_are_all_the_same_and_other_types_stay_as_they_are():
    assert same_target(None, "") and same_target(None, "  ")
    assert target_form(8080) == 8080
    assert not same_target(None, "http://a")


def test_same_target_is_part_of_the_public_sdk():
    assert sdk.same_target is same_target
    assert "same_target" in sdk.__all__
