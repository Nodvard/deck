"""`_SpaStaticFiles` (main.py) -- live beim Testen der /settings-Route
gefunden: ein direkter Aufruf/Reload/Lesezeichen auf JEDER React-Router-Route ausser
`/` lieferte bisher ein rohes {"detail":"Not Found"} statt der App. Isolierter Test
gegen eine minimale, selbst gebaute `dist/`-Struktur statt der echten App-Fixtures --
das eigentliche Frontend-Build ist hier irrelevant, nur das Fallback-Verhalten
selbst."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nodvard_deck.main import SPA_CSP, _SpaStaticFiles


@pytest.fixture
def spa_client(tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<html>App-Shell</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('echt')", encoding="utf-8")

    app = FastAPI()
    app.mount("/", _SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    return TestClient(app)


def test_root_serves_index_html(spa_client: TestClient):
    res = spa_client.get("/")
    assert res.status_code == 200
    assert "App-Shell" in res.text


def test_an_existing_asset_is_served_as_itself_not_the_fallback(spa_client: TestClient):
    res = spa_client.get("/assets/app.js")
    assert res.status_code == 200
    assert "echt" in res.text


def test_a_client_side_route_falls_back_to_index_html_instead_of_404(spa_client: TestClient):
    """Der eigentliche Fund: /settings existiert nicht als Datei im Build --
    React Router entscheidet erst im Browser, was dort gerendert wird."""
    res = spa_client.get("/settings")
    assert res.status_code == 200
    assert "App-Shell" in res.text


def test_a_deep_nested_client_side_route_also_falls_back(spa_client: TestClient):
    res = spa_client.get("/ext/proxmox/nodes")
    assert res.status_code == 200
    assert "App-Shell" in res.text


def test_index_html_must_be_revalidated_but_hashed_assets_are_immutable(spa_client: TestClient):
    """Live gefunden: nach einem Redeploy sah der Nutzer weiter alte UI-Texte --
    ohne `Cache-Control` cachen Browser heuristisch. `index.html` (auch als
    Fallback fuer Client-Routen) muss revalidiert werden, `assets/` (Vite-Hash im
    Dateinamen) darf dauerhaft gecacht werden."""
    for path in ("/", "/settings", "/ext/proxmox/nodes"):
        res = spa_client.get(path)
        assert res.headers["cache-control"] == "no-cache", path

    asset = spa_client.get("/assets/app.js")
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_a_missing_asset_still_falls_back_not_a_raw_404(spa_client: TestClient):
    """Bewusst dokumentierte Nebenwirkung (main.py-Docstring): eine wirklich fehlende
    Asset-Datei (z.B. nach einem Rebuild veraltet referenziert) liefert jetzt
    ebenfalls die App-Shell statt eines rohen 404 -- Kompromiss des Fallback-Musters,
    kein Regressionsrisiko fuer diese Anwendung."""
    res = spa_client.get("/assets/does-not-exist.js")
    assert res.status_code == 200
    assert "App-Shell" in res.text
    # Der Fallback liefert HTML unter einer `assets/`-URL aus -- darf deshalb NICHT
    # die `immutable`-Policy der echten Assets bekommen, sonst saesse genau diese
    # Fehlantwort ein Jahr lang im Browser-Cache fest.
    assert res.headers["cache-control"] == "no-cache"


def test_an_unknown_api_path_is_an_honest_404_not_the_app(spa_client: TestClient):
    """Live gefunden: ein unbekannter `/api/...`-Pfad (Tippfehler, Route einer
    deaktivierten Extension) bekam `index.html` mit 200 -- API-Aufrufer scheiterten
    dann am HTML statt an einem 404."""
    for path in ("/api/v1/gibt-es-nicht", "/api/v1/ext/deaktiviert/widgets/x", "/api"):
        resp = spa_client.get(path)
        assert resp.status_code == 404, path
        assert "text/html" not in resp.headers.get("content-type", ""), path

    # Eine Client-Route, die nur mit "api" BEGINNT, bleibt eine Client-Route.
    assert spa_client.get("/apis-uebersicht").status_code == 200


def test_html_has_the_csp_assets_do_not(spa_client: TestClient):
    for path in ("/", "/settings/appearance"):
        headers = spa_client.get(path).headers
        # Ohne eingebettete Skripte bleibt für Skripte nur der eigene Server.
        assert headers["content-security-policy"] == f"{SPA_CSP}; script-src 'self'"
        directives = {part.strip() for part in headers["content-security-policy"].split(";")}
        assert directives == {
            "default-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
            "font-src 'self' data:", "frame-src blob:", "object-src 'none'", "base-uri 'self'", "form-action 'self'",
            "script-src 'self'",
        }
        assert "content-security-policy-report-only" not in headers
    asset = spa_client.get("/assets/app.js").headers
    assert "content-security-policy" not in asset and "content-security-policy-report-only" not in asset


def test_a_revalidated_page_keeps_its_csp(spa_client: TestClient):
    for path in ("/", "/settings"):
        first = spa_client.get(path)
        again = spa_client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304, path
        assert again.headers["content-security-policy"] == first.headers["content-security-policy"], path
