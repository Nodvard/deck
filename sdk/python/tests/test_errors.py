"""Fehlertypen des SDK lassen sich kopieren und picklen (Test-Werkzeuge, Weitergabe zwischen Prozessen)."""

from __future__ import annotations

import copy
import pickle

import pytest

from nodvard_sdk.errors import PermissionDenied, SecretNotOwned


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
