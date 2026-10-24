"""`script-src` der App-Seite: eigene Dateien plus die Prüfsummen der eingebetteten Skripte (die Import-Map)."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nodvard_deck.main import SPA_CSP, SPA_CSP_REPORT_ONLY, _SpaStaticFiles, spa_script_policy

REPO = Path(__file__).resolve().parents[2]
IMPORT_MAP = '\n      {\n        "imports": { "react": "/lattice-shim/react.js" }\n      }\n    '


def _sha(text: str) -> str:
    return f"'sha256-{base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()}'"


def test_the_inline_import_map_is_allowed_by_its_hash(tmp_path):
    index = tmp_path / "index.html"
    index.write_text(
        "<html><body>\n"
        "<!-- MUSS vor jedem <script type=\"module\"> stehen -->\n"
        f'<script type="importmap">{IMPORT_MAP}</script>\n'
        '<script type="module" src="/assets/index.js"></script>\n'
        "</body></html>",
        encoding="utf-8",
    )
    # Der Kommentar mit `<script …>` zählt nicht, das Skript mit `src` braucht keine Prüfsumme.
    assert spa_script_policy(index) == f"script-src 'self' {_sha(IMPORT_MAP)}"


@pytest.mark.parametrize(
    ("html", "scripts"),
    [
        # Ein Kommentar endet auch mit `--!>`, das Skript dahinter läuft.
        ("<!-- alt --!><script>a()</script><!-- neu -->", ["a()"]),
        ("<script>b()</script >", ["b()"]),
        ('<SCRIPT TYPE="importmap">{}</SCRIPT>', ["{}"]),
        ('<script data-src="x">c()</script>', ["c()"]),
        ("<script src></script><script src=/x.js></script>", []),
        ('<script>if (a < b) "</scr" + "ipt>"</script>', ['if (a < b) "</scr" + "ipt>"']),
    ],
)
def test_inline_scripts_are_read_like_a_browser_reads_them(tmp_path, html, scripts):
    index = tmp_path / "index.html"
    index.write_text(html, encoding="utf-8")
    assert spa_script_policy(index) == " ".join(["script-src", "'self'", *sorted(_sha(s) for s in scripts)])


def test_a_missing_index_allows_only_own_files(tmp_path):
    assert spa_script_policy(tmp_path / "fehlt.html") == "script-src 'self'"


def test_the_app_page_blocks_inline_scripts_and_javascript_links(tmp_path):
    (tmp_path / "index.html").write_text(f'<html><script type="importmap">{IMPORT_MAP}</script></html>', encoding="utf-8")
    app = FastAPI()
    app.mount("/", _SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    headers = TestClient(app).get("/settings").headers
    script = f"script-src 'self' {_sha(IMPORT_MAP)}"
    assert headers["content-security-policy"] == f"{SPA_CSP}; {script}"
    assert headers["content-security-policy-report-only"] == SPA_CSP_REPORT_ONLY
    assert "script-src" not in SPA_CSP and "script-src" not in SPA_CSP_REPORT_ONLY
    assert not {"'unsafe-inline'", "'unsafe-eval'", "'strict-dynamic'"} & set(script.split())


def test_the_real_index_html_has_only_the_import_map_inline():
    """Eingebettet ist nur die Import-Map; ein weiteres Skript bekäme seine Prüfsumme von selbst, darum fällt es hier
    auf."""
    policy = spa_script_policy(REPO / "frontend" / "index.html")
    assert policy.startswith("script-src 'self' 'sha256-") and policy.count("'sha256-") == 1


def test_a_changed_index_html_gets_its_new_hash_without_a_restart(tmp_path: Path):
    index = tmp_path / "index.html"
    index.write_text('<html><script type="importmap">{"imports": {}}</script></html>', encoding="utf-8")
    app = FastAPI()
    app.mount("/", _SpaStaticFiles(directory=tmp_path, html=True), name="frontend")
    client = TestClient(app)
    before = client.get("/").headers["content-security-policy"]
    index.write_text(f'<html><script type="importmap">{IMPORT_MAP}</script></html>', encoding="utf-8")
    after = client.get("/").headers["content-security-policy"]
    assert before != after and after == f"{SPA_CSP}; script-src 'self' {_sha(IMPORT_MAP)}"
