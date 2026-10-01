"""Herkunft des Images aus einer Datei im Image (`nodvard_deck.image_info`) statt aus einer Umgebungsvariable.

Vorrang fuer `Settings.image`/`Settings.build`: nicht leere Variable `NODVARD_DECK_*` (Rueckfall `LATTICE_*`) >
Datei > nichts (`None`, nie `""`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from nodvard_deck import config, image_info
from nodvard_deck.config import Settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for key in list(os.environ):
        if key.upper().startswith(("NODVARD_DECK_", "LATTICE_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_warned_legacy", set())


def _info(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "image-info.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def _settings(path: Path, **kwargs: object) -> Settings:
    return Settings(image_info_path=path, **kwargs)


# --- Lesen ----------------------------------------------------------------------------


def test_file_fills_image_and_build(tmp_path):
    path = _info(tmp_path, {"image": "ghcr.io/nodvard/deck", "version": "0.6.0-rc1"})
    s = _settings(path)
    assert s.image == "ghcr.io/nodvard/deck" and s.build == "0.6.0-rc1"


def test_without_file_and_variables_both_stay_none(tmp_path):
    s = _settings(tmp_path / "gibt-es-nicht.json")
    assert s.image is None and s.build is None


def test_default_path_is_in_the_app_folder():
    assert Settings.model_fields["image_info_path"].default == image_info.PATH == Path("/app/image-info.json")


@pytest.mark.parametrize(
    "content",
    [
        "{kaputt",
        "[]",
        json.dumps({"image": 5, "version": None}),
        json.dumps({"image": "   ", "version": ""}),
        json.dumps({"image": "a\nb", "version": "0.6.0\x00"}),
        json.dumps({"image": "x" * 500}),
        json.dumps({"image": "ghcr.io/nodvard/deck", "pad": "x" * 5000}),
    ],
)
def test_broken_file_counts_as_missing(tmp_path, content):
    s = _settings(_info(tmp_path, content))
    assert s.image is None and s.build is None


def test_only_known_fields_are_read(tmp_path):
    assert image_info.read(_info(tmp_path, {"version": " 0.6.0 ", "image": "ghcr.io/nodvard/deck", "other": "x"})) == {
        "image": "ghcr.io/nodvard/deck", "version": "0.6.0",
    }


# --- Vorrang --------------------------------------------------------------------------


def test_variable_beats_the_file(tmp_path, monkeypatch):
    path = _info(tmp_path, {"image": "ghcr.io/nodvard/deck", "version": "0.6.0"})
    monkeypatch.setenv("NODVARD_DECK_BUILD", "abc123")
    s = _settings(path)
    assert s.build == "abc123" and s.image == "ghcr.io/nodvard/deck"


def test_old_variable_beats_the_file(tmp_path, monkeypatch):
    path = _info(tmp_path, {"image": "ghcr.io/nodvard/deck", "version": "0.6.0"})
    monkeypatch.setenv("LATTICE_IMAGE", "eigenes/image")
    assert _settings(path).image == "eigenes/image"


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_variable_counts_as_unset(tmp_path, monkeypatch, empty):
    path = _info(tmp_path, {"image": "ghcr.io/nodvard/deck", "version": "0.6.0"})
    monkeypatch.setenv("NODVARD_DECK_BUILD", empty)
    monkeypatch.setenv("NODVARD_DECK_IMAGE", empty)
    s = _settings(path)
    assert s.build == "0.6.0" and s.image == "ghcr.io/nodvard/deck"


def test_empty_new_variable_does_not_hide_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setenv("NODVARD_DECK_BUILD", "")
    monkeypatch.setenv("LATTICE_BUILD", "a156da5")
    assert _settings(tmp_path / "fehlt.json").build == "a156da5"


def test_empty_variable_in_dotenv_counts_as_unset(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("NODVARD_DECK_BUILD=\nLATTICE_BUILD=\n", encoding="utf-8")
    path = _info(tmp_path, {"version": "0.6.0"})
    assert Settings(_env_file=str(env_file), image_info_path=path).build == "0.6.0"


def test_empty_values_never_come_out_as_empty_text(tmp_path, monkeypatch):
    monkeypatch.setenv("NODVARD_DECK_BUILD", "")
    s = _settings(tmp_path / "fehlt.json", image="  ")
    assert s.build is None and s.image is None


# --- Schreiben beim Bauen des Images ---------------------------------------------------


def test_main_writes_the_build_arguments_as_json(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE", "ghcr.io/nodvard/deck")
    monkeypatch.setenv("VERSION", '0.6.0-rc1"}, "x": "y')  # Anfuehrungszeichen bleiben Text, kein JSON-Einschub
    target = tmp_path / "image-info.json"
    assert image_info.main([str(target)]) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"image": "ghcr.io/nodvard/deck", "version": '0.6.0-rc1"}, "x": "y'}
    assert image_info.read(target) == data


def test_main_without_build_arguments_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE", "")
    monkeypatch.delenv("VERSION", raising=False)
    target = tmp_path / "image-info.json"
    assert image_info.main([str(target)]) == 0
    assert not target.exists()


def test_main_refuses_unusable_values(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE", "ghcr.io/nodvard/deck")
    monkeypatch.setenv("VERSION", "0.6.0\n0.7.0")
    target = tmp_path / "image-info.json"
    assert image_info.main([str(target)]) == 1
    assert not target.exists()


def test_module_runs_with_python_dash_m_like_in_the_dockerfile(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("IMAGE", "VERSION")}
    env.update(IMAGE="ghcr.io/nodvard/deck", VERSION="0.6.0")
    target = tmp_path / "image-info.json"
    done = subprocess.run(
        [sys.executable, "-m", "nodvard_deck.image_info", str(target)], env=env, capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert image_info.read(target) == {"image": "ghcr.io/nodvard/deck", "version": "0.6.0"}
