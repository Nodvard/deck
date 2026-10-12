"""Proxmox-Routen geben nur Sätze der Erweiterung an die Oberfläche, nie den Text einer fremden Ausnahme."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from nodvard_deck_ext_proxmox import GATEWAY_FALLBACK, bad_gateway
from nodvard_deck_ext_proxmox.config import ConnectionMissing
from nodvard_deck_ext_proxmox.connector import ProxmoxApiError

SOURCE = Path(__file__).resolve().parents[2] / "extensions" / "proxmox" / "src" / "nodvard_deck_ext_proxmox" / "__init__.py"


@pytest.mark.parametrize("exc", [
    ProxmoxApiError("GET /nodes/pve/qemu/100/snapshot -> Proxmox VE antwortet nicht (Verbindung abgelehnt)."),
    ConnectionMissing("Proxmox-Verbindung 'pve2' ist nicht (mehr) konfiguriert."),
])
def test_sentences_of_the_extension_reach_the_page(exc):
    err = bad_gateway(exc, logging.getLogger("test"))
    assert err.status_code == 502
    assert err.detail == str(exc)


@pytest.mark.parametrize("exc", [
    RuntimeError("Event loop is closed"),
    RuntimeError("Fehler in /app/data/ext/proxmox/geheim.json, Zeile 3"),
    RuntimeError(""),
])
def test_foreign_exception_texts_stay_in_the_log(exc, caplog):
    """Sicherheitsfund: Vorher ging `str(exc)` jeder `RuntimeError` an die Oberfläche, auch aus Bibliotheken."""
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
