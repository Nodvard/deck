"""Fehlertypen des SDK lassen sich kopieren und picklen (Test-Werkzeuge, Weitergabe zwischen Prozessen)."""

from __future__ import annotations

import copy
import inspect
import pickle

import pytest

from nodvard_sdk.errors import PermissionDenied, SecretNotOwned, error_message


@pytest.mark.parametrize(
    "error",
    [
        PermissionDenied("demo", "hosts.write"),
        SecretNotOwned("proxmox", "proxmox-token:pve9"),
        SecretNotOwned("demo", "totp:nutzer:1", login_secret=True),
    ],
    ids=["permission", "fremdes-geheimnis", "anmelde-geheimnis"],
)
def test_errors_survive_copy_and_pickle(error):
    for clone in (copy.copy(error), copy.deepcopy(error), pickle.loads(pickle.dumps(error))):  # noqa: S301 - eigene Daten
        assert type(clone) is type(error)
        assert str(clone) == str(error)
        assert clone.__dict__ == error.__dict__
    assert isinstance(SecretNotOwned("a", "b"), PermissionDenied)


def test_error_message_without_traceback_logs_caller_without_values(caplog):
    error = RuntimeError("vertraulich-fehler /srv/private/key")
    private_value = "vertraulich-lokal"
    line = inspect.currentframe().f_lineno + 1
    detail = error_message(error)

    assert error.__traceback__ is None
    record, = caplog.records
    logged = record.getMessage()
    assert record.name == "nodvard_deck.errors"
    assert detail == f"Die Anfrage ist fehlgeschlagen. Fehlerkennung: {record.args[0]}."
    assert "type=RuntimeError" in logged
    assert f"{__file__}:{line} in test_error_message_without_traceback_logs_caller_without_values" in logged
    assert " in error_message" not in logged
    for hidden in (str(error), private_value, "detail = error_message(error)"):
        assert hidden not in logged + detail
    assert record.exc_info is None
    assert record.stack_info is None
