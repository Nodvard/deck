"""Umgebungsvariablen: neues Praefix `NODVARD_DECK_`, alte `LATTICE_*`-Namen als Rueckfall.

Reihenfolge der Quellen (config.py `settings_customise_sources`):
init > Umgebung neu > Umgebung alt > .env neu > .env alt > Secrets.
Die Warnung ueber alte Namen nennt nur Namen, nie Werte (z. B. `JWT_SECRET`).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import types
import typing
from pathlib import Path

import pytest
import yaml
from nodvard_deck import config
from nodvard_deck.config import Settings
from pydantic import TypeAdapter

REPO = Path(__file__).resolve().parents[2]
NEW = "NODVARD_DECK_"
OLD = "LATTICE_"
FIELDS = sorted(Settings.model_fields)


def _base_type(annotation: object) -> type:
    """`bool | None` -> `bool`, `str | None` -> `str`."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        assert len(args) == 1, f"nicht unterstuetzter Feldtyp: {annotation}"
        return _base_type(args[0])
    return annotation  # type: ignore[return-value]


# Je Feldtyp zwei verschiedene Rohwerte (alt/neu), damit man sieht, welcher Wert gilt.
_RAW = {
    bool: ("true", "false"),
    int: ("11", "22"),
    float: ("1.5", "2.5"),
    Path: ("/alt/pfad", "/neu/pfad"),
    str: ("alter-wert", "neuer-wert"),
}


def _raws(field: str) -> tuple[str, str]:
    base = _base_type(Settings.model_fields[field].annotation)
    assert base in _RAW, f"Feld {field}: Testwerte fuer {base} ergaenzen"
    return _RAW[base]


def _expected(field: str, raw: str) -> object:
    return TypeAdapter(Settings.model_fields[field].annotation).validate_python(raw)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keine fremden Variablen, keine `.env` des Repositorys, Warnmerker zurueckgesetzt."""
    for key in list(os.environ):
        if key.upper().startswith((NEW, OLD)):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_warned_legacy", set())


def _dotenv(tmp_path: Path, lines: dict[str, str]) -> str:
    path = tmp_path / "test.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
    return str(path)


def test_every_field_is_covered():
    # Wenn hier etwas fehlschlaegt, hat Settings ein Feld mit einem Typ, den die Tests
    # noch nicht kennen (siehe _RAW).
    for name in FIELDS:
        _raws(name)


@pytest.mark.parametrize("field", FIELDS)
def test_only_old_name_is_used_as_fallback(field: str, monkeypatch: pytest.MonkeyPatch):
    old, _ = _raws(field)
    monkeypatch.setenv(OLD + field.upper(), old)
    assert getattr(Settings(), field) == _expected(field, old)


@pytest.mark.parametrize("field", FIELDS)
def test_only_new_name_is_used(field: str, monkeypatch: pytest.MonkeyPatch):
    _, new = _raws(field)
    monkeypatch.setenv(NEW + field.upper(), new)
    assert getattr(Settings(), field) == _expected(field, new)


@pytest.mark.parametrize("field", FIELDS)
def test_new_name_wins_over_old_name(field: str, monkeypatch: pytest.MonkeyPatch):
    old, new = _raws(field)
    monkeypatch.setenv(OLD + field.upper(), old)
    monkeypatch.setenv(NEW + field.upper(), new)
    assert getattr(Settings(), field) == _expected(field, new)


@pytest.mark.parametrize("field", FIELDS)
def test_old_name_in_dotenv_file(field: str, tmp_path: Path):
    old, _ = _raws(field)
    env_file = _dotenv(tmp_path, {OLD + field.upper(): old})
    assert getattr(Settings(_env_file=env_file), field) == _expected(field, old)


@pytest.mark.parametrize("field", FIELDS)
def test_new_name_in_dotenv_file(field: str, tmp_path: Path):
    _, new = _raws(field)
    env_file = _dotenv(tmp_path, {NEW + field.upper(): new})
    assert getattr(Settings(_env_file=env_file), field) == _expected(field, new)


@pytest.mark.parametrize("field", FIELDS)
def test_new_env_beats_old_dotenv(field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    old, new = _raws(field)
    monkeypatch.setenv(NEW + field.upper(), new)
    env_file = _dotenv(tmp_path, {OLD + field.upper(): old})
    assert getattr(Settings(_env_file=env_file), field) == _expected(field, new)


@pytest.mark.parametrize("field", FIELDS)
def test_old_env_beats_new_dotenv(field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Reihenfolge laut Plan: beide Umgebungsquellen vor beiden `.env`-Quellen."""
    old, new = _raws(field)
    monkeypatch.setenv(OLD + field.upper(), old)
    env_file = _dotenv(tmp_path, {NEW + field.upper(): new})
    assert getattr(Settings(_env_file=env_file), field) == _expected(field, old)


@pytest.mark.parametrize("field", FIELDS)
def test_new_dotenv_beats_old_dotenv(field: str, tmp_path: Path):
    old, new = _raws(field)
    env_file = _dotenv(tmp_path, {OLD + field.upper(): old, NEW + field.upper(): new})
    assert getattr(Settings(_env_file=env_file), field) == _expected(field, new)


@pytest.mark.parametrize("field", FIELDS)
def test_init_argument_beats_everything(field: str, monkeypatch: pytest.MonkeyPatch):
    old, new = _raws(field)
    monkeypatch.setenv(NEW + field.upper(), new)
    given = _expected(field, old)
    assert getattr(Settings(**{field: given}), field) == given


def test_lowercase_variable_names_still_work(monkeypatch: pytest.MonkeyPatch):
    # Wie bisher: die Namen sind nicht gross-/kleinschreibungsabhaengig (nur unter Windows
    # relevant, dort ist os.environ ohnehin unabhaengig davon).
    monkeypatch.setenv("lattice_env", "alt")
    assert Settings().env == "alt"


def test_defaults_without_any_variable():
    s = Settings()
    assert s.env == "dev" and s.database_url.endswith("/lattice.db")


# --- Warnung nur mit Namen -------------------------------------------------------------


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "nodvard_deck.config"]


def test_warning_names_old_variables_but_never_their_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    secret = "Sup3r-Geheim-Wert-42"
    monkeypatch.setenv("LATTICE_JWT_SECRET", secret)
    other = "wert-xyz-7"
    monkeypatch.setenv("LATTICE_ENV", other)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.config"):
        Settings()
    records = _warnings(caplog)
    assert len(records) == 1
    text = records[0].getMessage()
    assert "LATTICE_JWT_SECRET" in text and "LATTICE_ENV" in text
    assert "NODVARD_DECK_" in text  # sagt, wie es richtig heisst
    assert secret not in caplog.text and other not in caplog.text


def test_warning_for_old_names_in_dotenv_has_no_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    secret = "Sup3r-Geheim-Wert-43"
    env_file = _dotenv(tmp_path, {"LATTICE_JWT_SECRET": secret})
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.config"):
        Settings(_env_file=env_file)
    records = _warnings(caplog)
    assert len(records) == 1
    assert "LATTICE_JWT_SECRET" in records[0].getMessage()
    assert secret not in caplog.text


def test_env_and_dotenv_old_names_give_a_single_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv("LATTICE_ENV", "prod")
    env_file = _dotenv(tmp_path, {"LATTICE_LOG_JSON": "true"})
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.config"):
        Settings(_env_file=env_file)
    records = _warnings(caplog)
    assert len(records) == 1
    assert "LATTICE_ENV" in records[0].getMessage() and "LATTICE_LOG_JSON" in records[0].getMessage()


def test_warning_is_shown_only_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv("LATTICE_ENV", "prod")
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.config"):
        Settings()
        Settings()
        Settings()
    assert len(_warnings(caplog)) == 1


def test_no_warning_with_new_names_only(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv("NODVARD_DECK_JWT_SECRET", "neu-geheim-1")
    monkeypatch.setenv("NODVARD_DECK_ENV", "prod")
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.config"):
        Settings()
    assert _warnings(caplog) == []
    assert "neu-geheim-1" not in caplog.text


def test_unknown_old_variables_do_not_warn(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    # LATTICE_DNS_1 und LATTICE_URL sind keine Einstellungen der Anwendung.
    monkeypatch.setenv("LATTICE_DNS_1", "192.0.2.1")
    monkeypatch.setenv("LATTICE_URL", "http://localhost:8080")
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.config"):
        Settings()
    assert _warnings(caplog) == []


# --- Deploy-Dateien ---------------------------------------------------------------------


def _compose() -> dict:
    return yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_file_uses_new_names_with_nested_fallback_for_dns():
    service = _compose()["services"]["nodvard-deck"]
    assert service["dns"] == [
        "${NODVARD_DECK_DNS_1:-${LATTICE_DNS_1:-1.1.1.1}}",
        "${NODVARD_DECK_DNS_2:-${LATTICE_DNS_2:-1.0.0.1}}",
    ]
    env = service["environment"]
    assert set(env) == {
        "NODVARD_DECK_ENV",
        "NODVARD_DECK_LOG_JSON",
        "NODVARD_DECK_AUDIT_RETENTION_DAYS",
        "NODVARD_DECK_DEMO_MODE",
    }
    assert env["NODVARD_DECK_ENV"] == "prod"
    # Image umbenannt (test_deploy_compose.py prueft Service/Projekt/Volume), Volume-Schluessel bleibt.
    assert service["image"] == "nodvard-deck:latest"
    assert service["volumes"] == ["lattice_data:/app/data"]


def test_dockerfile_sets_only_new_names():
    text = (REPO / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    env_block = text[text.index("ENV PYTHONUNBUFFERED") :].split("\n\n", 1)[0]
    assert "NODVARD_DECK_ENV=prod" in env_block
    assert "NODVARD_DECK_LOG_JSON=true" in env_block
    assert "NODVARD_DECK_DATA_DIR=/app/data" in env_block
    # Alte Namen im Image waeren schlechter: sie wuerden bei jedem Start die Warnung
    # ausloesen und fuer den Rueckweg nichts bringen (das alte Image bringt seine eigenen mit).
    assert "LATTICE_" not in env_block


def test_env_example_uses_new_names_that_exist_as_settings():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE)
    assert keys and all(k.startswith(NEW) for k in keys), keys
    for key in keys:
        assert key[len(NEW) :].lower() in Settings.model_fields, key
    assert "LATTICE_" not in re.sub(r"^#.*$", "", text, flags=re.MULTILINE)


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
def compose_config(tmp_path: Path):
    """`docker compose config` mit genau den uebergebenen Variablen (keine `.env`)."""
    if not _docker_compose_available():
        pytest.skip("docker compose nicht verfuegbar (YAML-Ersatztest oben deckt die Namen ab)")
    empty_env = tmp_path / "leer.env"
    empty_env.write_text("", encoding="utf-8")

    def run(**variables: str) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith((NEW, OLD))}
        env.update(variables)
        result = subprocess.run(
            ["docker", "compose", "-f", str(REPO / "deploy" / "docker-compose.yml"), "--env-file", str(empty_env), "config", "--format", "json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        import json

        return json.loads(result.stdout)["services"]["nodvard-deck"]

    return run


def test_compose_config_without_any_dns_variable_uses_defaults(compose_config):
    service = compose_config()
    assert service["dns"] == ["1.1.1.1", "1.0.0.1"]
    assert service["environment"]["NODVARD_DECK_ENV"] == "prod"


def test_compose_config_with_only_old_dns_variable(compose_config):
    service = compose_config(LATTICE_DNS_1="192.0.2.10", LATTICE_DNS_2="192.0.2.11")
    assert service["dns"] == ["192.0.2.10", "192.0.2.11"]


def test_compose_config_with_only_dns_1_old_keeps_default_for_2(compose_config):
    assert compose_config(LATTICE_DNS_1="192.0.2.10")["dns"] == ["192.0.2.10", "1.0.0.1"]


def test_compose_config_with_both_names_new_wins(compose_config):
    service = compose_config(
        LATTICE_DNS_1="192.0.2.10",
        LATTICE_DNS_2="192.0.2.11",
        NODVARD_DECK_DNS_1="198.51.100.10",
        NODVARD_DECK_DNS_2="198.51.100.11",
    )
    assert service["dns"] == ["198.51.100.10", "198.51.100.11"]


def test_compose_config_with_only_new_names(compose_config):
    assert compose_config(NODVARD_DECK_DNS_1="198.51.100.10")["dns"] == ["198.51.100.10", "1.0.0.1"]
