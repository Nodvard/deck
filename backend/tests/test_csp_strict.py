"""Erzwungene CSP mit frischen Nonces und begrenzten Verbindungen."""

from __future__ import annotations

import ast
import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nodvard_deck import rescue
from nodvard_deck.api.security_headers import SecurityHeadersMiddleware
from nodvard_deck.config import Settings
from nodvard_deck.main import _SpaStaticFiles


def _client(tmp_path, *, legacy=False, origin="https://deck.example:8443"):
    (tmp_path / "index.html").write_text(
        '<html><head><meta name="csp-nonce" content="__DECK_CSP_NONCE__"></head>'
        '<body><script type="module" src="/assets/main.js"></script></body></html>', encoding="utf-8",
    )
    (tmp_path / "preview.html").write_text((tmp_path / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/", _SpaStaticFiles(directory=tmp_path, html=True, csp_legacy=legacy))
    return TestClient(app, base_url=origin)


@pytest.mark.parametrize("path", ["/", "/settings", "/ext/docker", "/ext/shield", "/preview.html"])
def test_exact_enforced_header_and_nonce_in_html(tmp_path, path):
    response = _client(tmp_path).get(path)
    nonce = re.search(r'name="csp-nonce" content="([A-Za-z0-9_-]+)"', response.text)[1]
    assert len(nonce) >= 43
    assert response.headers["content-security-policy"] == (
        f"default-src 'self'; style-src 'self' 'nonce-{nonce}'; style-src-attr 'none'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self' wss://deck.example:8443; "
        "frame-src blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    )
    assert "content-security-policy-report-only" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert "__DECK_CSP_" not in response.text
    assert "etag" not in response.headers


def test_nonce_is_unique_even_for_conditional_requests_and_head(tmp_path):
    client = _client(tmp_path)
    answers = [client.get("/"), client.get("/", headers={"If-None-Match": "*"}), client.head("/")]
    assert all(answer.status_code == 200 for answer in answers)
    nonces = [re.search(r"'nonce-([^']+)'", answer.headers["content-security-policy"])[1] for answer in answers]
    assert len(set(nonces)) == 3
    assert answers[-1].content == b""
    assert int(answers[-1].headers["content-length"]) == len(answers[0].content)


@pytest.mark.parametrize(("origin", "socket"), [
    ("http://192.0.2.1:8080", "ws://192.0.2.1:8080"),
    ("https://[2001:db8::1]:8443", "wss://[2001:db8::1]:8443"),
])
def test_websockets_use_only_the_current_origin(tmp_path, origin, socket):
    policy = _client(tmp_path, origin=origin).get("/").headers["content-security-policy"]
    assert f"connect-src 'self' {socket};" in policy
    assert "*" not in policy and "'unsafe-inline'" not in policy


def test_legacy_switch_is_explicit_and_warns(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("NODVARD_DECK_CSP_LEGACY", "1")
    settings = Settings(_env_file=None)
    assert settings.csp_legacy is True
    response = _client(tmp_path, legacy=settings.csp_legacy).get("/")
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
        "frame-src blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "script-src 'self'; frame-ancestors 'none'"
    )
    assert "NODVARD_DECK_CSP_LEGACY" in caplog.text
    monkeypatch.delenv("NODVARD_DECK_CSP_LEGACY")
    assert Settings(_env_file=None).csp_legacy is False


@pytest.mark.parametrize("host", ["deck.example; script-src *", "[invalid", "deck.example,evil.example"])
def test_host_header_cannot_add_sources(tmp_path, host):
    response = _client(tmp_path).get("/", headers={"Host": host})
    assert response.status_code == 200
    assert "*" not in response.headers["content-security-policy"]


def test_rescue_styles_are_allowed_only_by_their_hash():
    digest = base64.b64encode(hashlib.sha256(rescue._CSS.encode()).digest()).decode()
    assert rescue._SECURITY_HEADERS["Content-Security-Policy"] == (
        f"default-src 'none'; style-src 'sha256-{digest}'; style-src-attr 'none'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    source = Path(rescue.__file__).read_text(encoding="utf-8")
    assert 'style="word-break' not in source


@pytest.mark.parametrize("file, name", [
    ("backend/src/nodvard_deck/branding.py", "UNTRUSTED_FILE_HEADERS"),
    ("extensions/documents/src/nodvard_deck_ext_documents/__init__.py", "_UNTRUSTED_FILE_HEADERS"),
    ("extensions/inventory/src/nodvard_deck_ext_inventory/__init__.py", "_UNTRUSTED_FILE_HEADERS"),
])
def test_uploaded_files_cannot_enable_inline_styles(file, name):
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / file).read_text(encoding="utf-8"))
    headers = next(
        ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    assert headers["Content-Security-Policy"] == "default-src 'none'; style-src 'none'; sandbox"
