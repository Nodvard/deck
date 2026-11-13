"""Weiterleitungen bei der Freigabe eines Releases."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.response import addinfourl

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN_URL = "https://ghcr.io/token?scope=repository%3Anodvard%2Fdeck%3Apull&service=ghcr.io"
MANIFEST_URL = "https://ghcr.io/v2/nodvard/deck/manifests/0.12.0"
MANIFEST = b'{"schemaVersion":2}'


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in [n for n in sys.modules if n == "nodvard_deck_updater" or n.startswith("nodvard_deck_updater.")]:
        monkeypatch.delitem(sys.modules, name)
    spec = importlib.util.spec_from_file_location("nodvard_release_grant", REPO_ROOT / "scripts" / "release_grant.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in [n for n in sys.modules if n == "nodvard_deck_updater" or n.startswith("nodvard_deck_updater.")]:
        del sys.modules[name]


def _registry(monkeypatch, tool, *, endpoint=None, location=None, code=302):
    asked = []

    def respond(handler, request):
        asked.append((request.full_url, dict(request.header_items())))
        headers = Message()
        status = 200
        if request.full_url == endpoint:
            status = code
            headers["Location"] = location
            body = b""
        elif request.full_url == TOKEN_URL or endpoint == TOKEN_URL:
            body = b'{"token":"anon"}'
        else:
            body = MANIFEST
        response = addinfourl(BytesIO(body), headers, request.full_url, status)
        response.msg = "Found" if status != 200 else "OK"
        return response

    monkeypatch.setattr(tool.urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(tool.urllib.request, "_opener", None)
    monkeypatch.setattr(tool.urllib.request.HTTPHandler, "http_open", respond)
    monkeypatch.setattr(tool.urllib.request.HTTPSHandler, "https_open", respond)
    return asked


def test_direct_registry_responses_still_produce_the_digest(tool, monkeypatch):
    asked = _registry(monkeypatch, tool)
    assert tool.registry_digest("0.12.0") == "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
    assert [url for url, _ in asked] == [TOKEN_URL, MANIFEST_URL]
    assert "Authorization" not in asked[0][1]
    assert asked[1][1]["Authorization"] == "Bearer anon"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", ["https://example.org/redirected", "http://ghcr.io/redirected", "/redirected"])
@pytest.mark.parametrize("endpoint", [TOKEN_URL, MANIFEST_URL])
def test_registry_redirects_are_rejected_before_a_second_request(tool, monkeypatch, code, location, endpoint):
    asked = _registry(monkeypatch, tool, endpoint=endpoint, location=location, code=code)
    with pytest.raises(tool.GrantError, match="Weiterleitungen"):
        tool.registry_digest("0.12.0")
    expected = [TOKEN_URL] if endpoint == TOKEN_URL else [TOKEN_URL, MANIFEST_URL]
    assert [url for url, _ in asked] == expected
