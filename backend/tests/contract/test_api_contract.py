"""Schutz des oeffentlichen API-Vertrags (Nodvard Link, docs/04-API.md "Kompatibilitaet").

Vergleicht die aktuelle `app.openapi()` -- inklusive aller mitgelieferten Extensions --
mit dem eingecheckten Schnappschuss `api_v1.json`:

1. Kein Bruch (entfernt/umbenannt, neue Pflichtfelder, Typwechsel), ausser er steht mit
   Begruendung in `breaking_exceptions.toml`.
2. Der Schnappschuss ist aktuell (Neues ist erlaubt, muss aber eingecheckt werden).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import contract_lib as cl
import pytest

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[3] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


@pytest.fixture
async def current(client, db_session, test_settings):
    from nodvard_deck.main import app

    doc = await cl.collect_openapi(app, db_session, test_settings, REPO_EXTENSIONS_DIR)
    return cl.build_api_snapshot(doc)


def _saved() -> dict:
    return json.loads(cl.API_SNAPSHOT.read_text(encoding="utf-8"))


def test_ausnahmeliste_ist_gueltig():
    cl.load_exceptions()


async def test_api_bricht_die_abwaertskompatibilitaet_nicht(current):
    breaks = cl.uncovered(cl.find_api_breaks(_saved(), current), cl.load_exceptions())
    assert not breaks, "\n\n" + cl.format_breaks(breaks)


async def test_api_schnappschuss_ist_aktuell(current):
    saved = _saved()
    assert cl.dump_json(current) == cl.dump_json(saved), "\n\n" + cl.stale_message("API", saved, current, "endpoints")


async def test_extension_routen_sind_im_schnappschuss(current):
    """Der Vertrag schliesst /api/v1/ext/<id>/... ein, nicht nur den Kern."""
    assert any("/api/v1/ext/" in key for key in current["endpoints"])
