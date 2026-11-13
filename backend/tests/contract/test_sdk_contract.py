"""Schutz der oeffentlichen SDK-Namen (`nodvard_sdk`), siehe docs/02-EXTENSION-API.md §10.

Entfernte Namen oder Parameter und neue Pflicht-Parameter sind Brueche; Neues ist erlaubt,
muss aber mit `python scripts/update_api_contract.py` in `sdk_public.json` nachgezogen werden.
"""

from __future__ import annotations

import json

import contract_lib as cl
import pytest


def _saved() -> dict:
    return json.loads(cl.SDK_SNAPSHOT.read_text(encoding="utf-8"))


def test_sdk_bricht_die_abwaertskompatibilitaet_nicht():
    breaks = cl.uncovered(cl.find_sdk_breaks(_saved(), cl.build_sdk_snapshot()), cl.load_exceptions())
    assert not breaks, "\n\n" + cl.format_breaks(breaks)


def test_sdk_schnappschuss_ist_aktuell():
    saved, current = _saved(), cl.build_sdk_snapshot()
    assert cl.dump_json(current) == cl.dump_json(saved), "\n\n" + cl.stale_message("SDK", saved, current, "names")


@pytest.mark.parametrize("name", ["nodvard_sdk.ActionSpec", "nodvard_sdk.actions.ActionSpec"])
def test_fingerprint_ausnahmen_sind_optional_im_vertrag(name):
    for snapshot in (_saved(), cl.build_sdk_snapshot()):
        assert snapshot["names"][name]["params"]["fingerprint_exclude_fields"] is False


def test_ssh_sitzung_ist_im_vertrag():
    for snapshot in (_saved(), cl.build_sdk_snapshot()):
        names = snapshot["names"]
        assert names["nodvard_sdk.context.ExecHandle"]["methoden"]["read_access"] == {
            "host": True, "timeout_s": False,
        }
        assert names["nodvard_sdk.context.ExecHandle"]["methoden"]["ssh_session"] == {
            "credential_id": False, "host": True, "key_credential_id": False,
        }
        assert names["nodvard_sdk.context.SshSession"]["methoden"] == {
            "run": {"command": True, "timeout_s": False}, "verify_key": {},
        }


@pytest.mark.parametrize("method", ["run", "run_argv", "stream", "stream_argv", "open_shell", "sftp"])
def test_ssh_runtime_contract_has_a_scoped_exception(method):
    change = cl.Break("nodvard_sdk.context.ExecHandle", "SDK", "sdk_verhalten_geaendert", method)
    exceptions = cl.load_exceptions()
    assert cl.is_excepted(change, exceptions)
    assert "Laufzeitverhalten" in change.describe()
    assert not cl.is_excepted(
        cl.Break(change.pfad, "SDK", "sdk_parameter_entfernt", method), exceptions,
    )
