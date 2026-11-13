"""Fehlgeschlagene Update-Prüfungen in Sammelvorschlägen und der Sicherheitskachel."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def shield_api(monkeypatch):
    loaded = set(sys.modules)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))
    from nodvard_deck_ext_shield import defender_api

    yield defender_api
    for name in set(sys.modules) - loaded:
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest.mark.parametrize(("kind", "count", "message"), [
    ("dpkg", 0, "Die Paketverwaltung ist beschädigt. Bitte auf dem Server dpkg prüfen und danach erneut prüfen."),
    ("locked", 3, "Die Paketverwaltung ist gesperrt. Bitte den laufenden Vorgang abwarten und danach erneut prüfen."),
    ("permission", 0, "Die nötigen Rechte fehlen. Bitte den Serverzugang prüfen und danach erneut prüfen."),
    ("timeout", 3, "Die Update-Prüfung hat zu lange gedauert. Bitte später erneut prüfen."),
])
@pytest.mark.parametrize("mode", ["all", "security"])
def test_failed_check_skips_bulk_proposals(shield_api, kind, count, message, mode):
    class Updates:
        async def overview(self):
            return {"hosts": [{
                "host_id": "h1", "host_name": "Server", "busy": None,
                "status": {"manager": "apt", "count": count, "security_count": count,
                           "error_kind": kind, "error": message},
            }]}

        async def plan(self, host, mode):
            pytest.fail("Ein fehlgeschlagener Prüfstand darf keinen Vorschlag auslösen.")

    async def list_actions(**kwargs):
        return []

    async def audit_log(**kwargs):
        pass

    async def get_host(host_id):
        return SimpleNamespace(id=host_id, name="Server", display_name=None)

    ctx = SimpleNamespace(
        api=SimpleNamespace(current_actor=lambda: None, require_permission=lambda permission: lambda: None),
        actions=SimpleNamespace(list=list_actions), audit=SimpleNamespace(log=audit_log),
        hosts=SimpleNamespace(get=get_host),
    )
    _, manage = shield_api.build_routers(ctx, object(), Updates(), None)
    app = FastAPI()
    app.include_router(manage)
    response = TestClient(app).post("/defender/updates/propose-all", json={"mode": mode, "host_ids": ["h1"]})
    assert response.status_code == 200
    assert response.json()["results"] == [{"host_id": "h1", "name": "Server", "result": "skipped", "reason": message}]
    assert response.json()["counts"] == {"proposed": 0, "skipped": 1, "failed": 0, "total": 1}


@pytest.mark.parametrize("checked", [0, 1])
def test_security_widget_shows_failed_update_checks(shield_api, checked):
    class Updates:
        async def overview(self):
            return {"summary": {
                "checked": checked, "hosts": checked + 1, "up_to_date": checked,
                "packages": 0, "security": 0, "reboot": 0, "errors": 1,
            }}

    ctx = SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None, require_permission=lambda permission: lambda: None))
    read, _ = shield_api.build_routers(ctx, object(), Updates(), None)
    app = FastAPI()
    app.include_router(read)
    response = TestClient(app).get("/defender/widgets/security")
    assert response.status_code == 200
    assert response.json()["data"] == [{
        "title": "Updates", "label": "Prüfung fehlgeschlagen", "tone": "warn",
        "subtitle": f"{checked}/{checked + 1} Server aktuell · 1 Prüfung fehlgeschlagen. Unter Updates erneut prüfen.",
    }]
