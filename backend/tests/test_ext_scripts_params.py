"""`nodvard_deck_ext_scripts.params.substitute_params`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "scripts" / "src"))

from nodvard_deck_ext_scripts.params import ParamError, substitute_params  # noqa: E402


def test_substitutes_known_placeholder_with_shell_quoting():
    result = substitute_params("echo $greeting", {"greeting": {"type": "string"}}, {"greeting": "hi there"})
    assert result == "echo 'hi there'"


def test_falls_back_to_default_when_value_missing():
    result = substitute_params(
        "echo $level", {"level": {"type": "string", "default": "info"}}, {}
    )
    assert result == "echo info"


def test_missing_value_without_default_raises():
    with pytest.raises(ParamError, match="fehlt"):
        substitute_params("echo $level", {"level": {"type": "string"}}, {})


def test_unknown_value_key_is_rejected():
    """Sicherheitsrelevant: ein Wert fuer einen Namen, den das Skript gar nicht
    deklariert hat, darf nicht still durchgereicht werden."""
    with pytest.raises(ParamError, match="Unbekannte Parameter"):
        substitute_params("echo hi", {}, {"injected": "$(rm -rf /)"})


def test_shell_quoting_neutralizes_command_injection_attempt():
    result = substitute_params(
        "echo $name", {"name": {"type": "string"}}, {"name": "$(rm -rf /); echo pwned"}
    )
    assert "$(rm -rf /)" not in result or result.count("'") >= 2
    assert result == "echo '$(rm -rf /); echo pwned'"


def test_unknown_placeholder_in_script_content_raises():
    with pytest.raises(ParamError, match="Platzhalter"):
        substitute_params("echo $typo", {"typo_other": {"type": "string", "default": "x"}}, {})
