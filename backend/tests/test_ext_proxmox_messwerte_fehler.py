"""Messwerte eines Proxmox-Servers, der nicht antwortet: ein 502 mit dem Satz der Erweiterung, kein 500."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from nodvard_deck.core.error_text import (
    describe_connection_error,
    is_expected_connection_error,
)
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_sdk.capabilities import MetricsProvider
from test_verbindungsfehler import (  # noqa: F401 - _cleanup setzt die Laufzeit zurück
    _cleanup,
    _host_with_metrics_provider,
    _UnreachableMetrics,
)

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "proxmox" / "src"
TEXT = "GET /nodes/pve/status -> Proxmox VE antwortet nicht (Verbindung abgelehnt)."


@pytest.fixture(autouse=True)
def _ext_proxmox_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_proxmox"):
            del sys.modules[name]


def _api_error(text: str) -> Exception:
    from nodvard_deck_ext_proxmox.connector import ProxmoxApiError

    return ProxmoxApiError(text)


def test_der_satz_der_erweiterung_kommt_unveraendert_durch():
    err = _api_error(TEXT)
    assert is_expected_connection_error(err)
    assert describe_connection_error(err) == TEXT


@pytest.mark.asyncio
async def test_messwerte_eines_proxmox_servers_ohne_antwort_sind_502_mit_grund(client, db_session):
    token, host_id = await _host_with_metrics_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(_api_error(TEXT)))
    res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    assert res.json()["detail"] == TEXT


@pytest.mark.asyncio
async def test_mit_dem_echten_satz_bei_abgelehnter_verbindung(client, db_session):
    """Derselbe Satz, den der Connector bei einer abgelehnten Verbindung baut (`transport_text`), kommt so an."""
    from nodvard_deck_ext_proxmox.transport import transport_text

    problem = transport_text(httpx.ConnectError("All connection attempts failed"), "https://pve.example.org:8006/api2/json/nodes/pve/status")
    text = f"GET /nodes/pve/status -> {problem}"
    token, host_id = await _host_with_metrics_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(_api_error(text)))
    res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    assert res.json()["detail"] == text
    assert "All connection attempts failed" not in text  # nie der englische Text der Ausnahme
