"""Proxmox-Routen geben nur Sätze der Erweiterung an die Oberfläche, nie den Text einer fremden Ausnahme."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "proxmox" / "src"
SOURCE = EXT_SRC / "nodvard_deck_ext_proxmox" / "__init__.py"


@pytest.fixture(autouse=True)
def _ext_proxmox_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_proxmox"):
            del sys.modules[name]


def _extension_error(kind: str, text: str) -> Exception:
    from nodvard_deck_ext_proxmox.config import ConnectionMissing
    from nodvard_deck_ext_proxmox.connector import ProxmoxApiError

    return {"api": ProxmoxApiError, "missing": ConnectionMissing}[kind](text)


@pytest.mark.parametrize(("kind", "text"), [
    ("api", "GET /nodes/pve/qemu/100/snapshot -> Proxmox VE antwortet nicht (Verbindung abgelehnt)."),
    ("missing", "Proxmox-Verbindung 'pve2' ist nicht (mehr) konfiguriert."),
])
def test_sentences_of_the_extension_reach_the_page(kind, text):
    from nodvard_deck_ext_proxmox import bad_gateway

    err = bad_gateway(_extension_error(kind, text), logging.getLogger("test"))
    assert err.status_code == 502
    assert err.detail == text


@pytest.mark.parametrize("exc", [
    RuntimeError("Event loop is closed"),
    RuntimeError("Fehler in /app/data/ext/proxmox/geheim.json, Zeile 3"),
    RuntimeError(""),
])
def test_foreign_exception_texts_stay_in_the_log(exc, caplog):
    from nodvard_deck_ext_proxmox import GATEWAY_FALLBACK, bad_gateway

    with caplog.at_level(logging.WARNING, logger="test"):
        err = bad_gateway(exc, logging.getLogger("test"))
    assert err.status_code == 502
    assert err.detail == GATEWAY_FALLBACK
    assert any(r.exc_info and r.exc_info[1] is exc for r in caplog.records)


def test_no_route_hands_out_the_raw_exception_text():
    source = SOURCE.read_text(encoding="utf-8")
    # auch `detail=f"{exc}"`, `detail=repr(exc)` usw.: kein `detail=` darf die Ausnahme selbst enthalten
    assert not re.search(r"detail=[^\n]*\bexc\b", source)
    assert source.count("raise bad_gateway(exc, ctx.logger) from exc") >= 4
