"""Vorgegebener Einrichtungscode: Mindestlänge 12 und keine Beispielwerte aus Anleitung und Oberfläche."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from nodvard_deck.core import setup_code

ROOT = Path(__file__).resolve().parents[2]
URL = "/api/v1/auth/bootstrap"
BODY = {"username": "nico", "password": "correct-horse-battery"}
EXAMPLE_LINE = re.compile(r"NODVARD_DECK_SETUP_CODE=([^\s<`\"']+)")


def _example_values() -> set[str]:
    files = [*ROOT.glob("docs/**/*.md"), *ROOT.glob("*.md"), *ROOT.glob("deploy/**/*.md")]
    files += [p for p in ROOT.glob("frontend/src/**/*.tsx") if ".test." not in p.name]
    found = set()
    for path in files:
        found.update(EXAMPLE_LINE.findall(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize(("preset", "expected"), [("abcd-efgh-jkm", None), ("abcd-efgh-jkmn", "ABCDEFGHJKMN")])
def test_preset_needs_twelve_letters_or_digits(preset, expected):
    assert setup_code.valid_preset(preset) == expected


@pytest.mark.parametrize("preset", ["abcd1234", "ABCD-EFGH-JKM", "a-b-c-d-e-f-g-h-j-k-m"])
def test_preset_with_eight_to_eleven_characters_is_ignored(tmp_path, preset):
    code, created = setup_code.ensure(tmp_path, preset)
    assert created is True
    assert setup_code.normalize(code) != setup_code.normalize(preset)
    assert not setup_code.verify(tmp_path, preset, preset)


def test_every_documented_example_is_rejected():
    values = _example_values()
    assert {setup_code.normalize(v) for v in values} >= {"HIEREIGENENCODEEINTRAGEN", "PUTYOUROWNCODEHERE"}
    assert [v for v in values if setup_code.valid_preset(v) is not None] == []


@pytest.mark.parametrize("preset", ["HIER-EIGENEN-CODE-EINTRAGEN", "put-your-own-code-here"])
def test_example_is_rejected_although_long_enough(tmp_path, preset):
    assert len(setup_code.normalize(preset)) >= 12
    assert setup_code.valid_preset(preset) is None
    code, created = setup_code.ensure(tmp_path, preset)
    assert created is True
    assert setup_code.normalize(code) != setup_code.normalize(preset)
    assert not setup_code.verify(tmp_path, preset, preset)


COMPOSE_FILES = [
    "deploy/docker-compose.yml",
    "deploy/compose.standalone.yml",
    "deploy/compose.standalone-mit-helfer.yml",
    "deploy/nodvard-dock/compose.yml",
]
COMPOSE_HINT = re.compile(r"mindestens (\d+) Buchstaben/Ziffern")


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_compose_hint_names_minimum_length(name):
    assert COMPOSE_HINT.findall((ROOT / name).read_text(encoding="utf-8")) == [str(setup_code.MIN_PRESET_LEN)]


@pytest.mark.parametrize("preset", ["mein-eigener-code-7392", "my-own-setup-code-7392", "dein eigener code"])
def test_earlier_examples_stay_rejected(preset):
    assert setup_code.valid_preset(preset) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["abcd-efgh-jkm", "ABCD1234"])
async def test_startup_falls_back_on_short_preset(client, db_session, test_settings, capsys, preset):
    from nodvard_deck.services import auth as auth_service

    test_settings.setup_code = preset
    code = await auth_service.prepare_setup_code(db_session, test_settings)
    out = capsys.readouterr().out
    assert "NODVARD_DECK_SETUP_CODE ist zu kurz (mindestens 12 Buchstaben oder Ziffern) und wird ignoriert." in out
    assert preset not in out and setup_code.normalize(preset) not in out
    assert setup_code.code_file(test_settings.data_dir).exists()
    assert (await client.post(URL, json={**BODY, "setup_code": preset})).status_code == 403
    assert (await client.post(URL, json={**BODY, "setup_code": code})).status_code == 201


@pytest.mark.asyncio
async def test_startup_falls_back_on_example(client, db_session, test_settings, capsys):
    from nodvard_deck.services import auth as auth_service

    preset = min(_example_values())
    test_settings.setup_code = preset
    code = await auth_service.prepare_setup_code(db_session, test_settings)
    out = capsys.readouterr().out
    assert "NODVARD_DECK_SETUP_CODE ist ein Beispielwert aus der Anleitung und wird ignoriert." in out
    assert "zu kurz" not in out
    assert preset not in out and setup_code.normalize(preset) not in out
    assert setup_code.normalize(code) != setup_code.normalize(preset)
    assert (await client.post(URL, json={**BODY, "setup_code": preset})).status_code == 403
    assert (await client.post(URL, json={**BODY, "setup_code": code})).status_code == 201


@pytest.mark.asyncio
async def test_startup_without_preset_is_unchanged(db_session, test_settings, capsys):
    from nodvard_deck.services import auth as auth_service

    test_settings.setup_code = None
    code = await auth_service.prepare_setup_code(db_session, test_settings)
    out = capsys.readouterr().out
    assert "Hinweis" not in out
    assert f"Einrichtungscode: {code}" in out
    assert setup_code.read_file(test_settings.data_dir) == setup_code.normalize(code)


@pytest.mark.asyncio
async def test_valid_preset_is_still_used(db_session, test_settings, capsys):
    from nodvard_deck.services import auth as auth_service

    test_settings.setup_code = "abcd-efgh-jkmn"
    code = await auth_service.prepare_setup_code(db_session, test_settings)
    assert code == "ABCD-EFGH-JKMN"
    assert "Hinweis" not in capsys.readouterr().out
    assert not setup_code.code_file(test_settings.data_dir).exists()
