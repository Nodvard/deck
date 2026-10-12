"""Messwerte eines Proxmox-Servers, der nicht antwortet: ein 502 mit dem Satz der Erweiterung, kein 500.

Vorher wurde `ProxmoxApiError` (Proxmox aus, Verbindung abgelehnt, Zeitüberschreitung) im Kern nicht als erwartbarer
Verbindungsfehler erkannt: `/hosts/{id}/metrics` antwortete mit 500, und die Oberfläche zeigte „Internal Server Error“.
"""

from __future__ import annotations

import httpx
import pytest
from nodvard_deck.core.error_text import (
    describe_connection_error,
    is_expected_connection_error,
)
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck_ext_proxmox.connector import ProxmoxApiError
from nodvard_deck_ext_proxmox.transport import transport_text
from nodvard_sdk.capabilities import MetricsProvider
from test_verbindungsfehler import (  # noqa: F401 - _cleanup setzt die Laufzeit zurück
    _cleanup,
    _host_with_metrics_provider,
    _UnreachableMetrics,
)

TEXT = "GET /nodes/pve/status -> Proxmox VE antwortet nicht (Verbindung abgelehnt)."


def test_der_satz_der_erweiterung_kommt_unveraendert_durch():
    err = ProxmoxApiError(TEXT)
    assert is_expected_connection_error(err)
    assert describe_connection_error(err) == TEXT


@pytest.mark.asyncio
async def test_messwerte_eines_proxmox_servers_ohne_antwort_sind_502_mit_grund(client, db_session):
    token, host_id = await _host_with_metrics_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(ProxmoxApiError(TEXT)))
    res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    assert res.json()["detail"] == TEXT


@pytest.mark.asyncio
async def test_mit_dem_echten_satz_bei_abgelehnter_verbindung(client, db_session):
    """Derselbe Satz, den der Connector bei einer abgelehnten Verbindung baut (`transport_text`), kommt so an."""
    problem = transport_text(httpx.ConnectError("All connection attempts failed"), "https://pve.example.org:8006/api2/json/nodes/pve/status")
    text = f"GET /nodes/pve/status -> {problem}"
    token, host_id = await _host_with_metrics_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-metrics", MetricsProvider, _UnreachableMetrics(ProxmoxApiError(text)))
    res = await client.get(f"/api/v1/hosts/{host_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 502
    assert res.json()["detail"] == text
    assert "All connection attempts failed" not in text  # nie der englische Text der Ausnahme
