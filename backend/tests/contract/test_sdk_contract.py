"""Schutz der oeffentlichen SDK-Namen (`nodvard_sdk`), siehe docs/02-EXTENSION-API.md §10.

Entfernte Namen oder Parameter und neue Pflicht-Parameter sind Brueche; Neues ist erlaubt,
muss aber mit `python scripts/update_api_contract.py` in `sdk_public.json` nachgezogen werden.
"""

from __future__ import annotations

import json

import contract_lib as cl


def _saved() -> dict:
    return json.loads(cl.SDK_SNAPSHOT.read_text(encoding="utf-8"))


def test_sdk_bricht_die_abwaertskompatibilitaet_nicht():
    breaks = cl.uncovered(cl.find_sdk_breaks(_saved(), cl.build_sdk_snapshot()), cl.load_exceptions())
    assert not breaks, "\n\n" + cl.format_breaks(breaks)


def test_sdk_schnappschuss_ist_aktuell():
    saved, current = _saved(), cl.build_sdk_snapshot()
    assert cl.dump_json(current) == cl.dump_json(saved), "\n\n" + cl.stale_message("SDK", saved, current, "names")
