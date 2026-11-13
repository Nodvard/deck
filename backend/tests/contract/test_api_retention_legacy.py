"""Vertragsrouten ohne lokale Aufrufer brauchen eine dokumentierte Entscheidung."""

from __future__ import annotations

import ast
import fnmatch
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
API = "backend/src/nodvard_deck/api/v1"
HELLO = "extensions/hello-world/src/nodvard_deck_ext_hello_world/__init__.py"
ROUTES = [
    ("GET /api/v1/files/{source_id}/info", f"{API}/files.py", "source_info"),
    ("GET /api/v1/files/{source_id}/stat", f"{API}/files.py", "stat_entry"),
    ("GET /api/v1/hosts/{host_id}/status", f"{API}/hosts.py", "get_host_status"),
    ("GET /api/v1/runs/{run_id}", f"{API}/jobs.py", "get_run"),
    ("GET /api/v1/runs/{run_id}/output", f"{API}/jobs.py", "get_run_output"),
    ("POST /api/v1/runs/{run_id}/cancel", f"{API}/jobs.py", "cancel_run"),
    ("GET /api/v1/system/openapi.json", f"{API}/system.py", "openapi_document"),
    ("GET /api/v1/system/backups/download/{ticket}/status", f"{API}/system.py", "download_status"),
    ("GET /api/v1/ext/hello-world/protected-widget", HELLO, "protected_widget_data"),
    ("POST /api/v1/ext/hello-world/notify-test", HELLO, "notify_test"),
    ("POST /api/v1/ext/hello-world/vault-use-and-fail", HELLO, "vault_use_and_fail"),
]
ALIASES = [
    "* /api/v1/ext/service-matrix/**",
    "* /api/v1/ext/nexus-soc/**",
    "* /api/v1/ext/network/**",
]


def _contract_reason(line: str) -> None:
    assert line.strip().startswith("# API-Vertrag:"), "Vertragskommentar fehlt"
    assert line.partition("API-Vertrag:")[2].strip(), "Vertragsgrund fehlt"


@pytest.mark.parametrize("endpoint,source,handler", ROUTES, ids=[row[0] for row in ROUTES])
def test_retained_route_has_contract_reason(endpoint, source, handler):
    text = (ROOT / source).read_text(encoding="utf-8")
    node = next(
        node for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == handler
    )
    decorator = node.decorator_list[0]
    method, path = endpoint.split(" ", 1)
    assert decorator.func.attr.upper() == method
    assert path.endswith(decorator.args[0].value)
    _contract_reason(text.splitlines()[decorator.lineno - 2])


@pytest.mark.parametrize("extension,field", [
    ("docker", "legacy_ids"), ("shield", "legacy_ids"), ("pihole", "routes"), ("npm", "routes"),
])
def test_retained_aliases_have_contract_reason(extension, field):
    lines = (ROOT / "extensions" / extension / "extension.toml").read_text(encoding="utf-8").splitlines()
    position = next(i for i, line in enumerate(lines) if line.startswith(field + " "))
    _contract_reason(lines[position - 1])


def test_retention_table_covers_contract_routes():
    doc = (ROOT / "docs/04-API.md").read_text(encoding="utf-8")
    table = doc.split("### Routen ohne lokale Aufrufer\n", 1)[1]
    rows = [line.strip("| ").split(" | ") for line in table.splitlines() if line.startswith("| `")]
    decisions = {endpoint.strip("`"): (decision, reason) for endpoint, decision, reason in rows}
    assert len(rows) == len(decisions)
    assert set(decisions) == {row[0] for row in ROUTES} | set(ALIASES)
    saved = json.loads((Path(__file__).parent / "api_v1.json").read_text(encoding="utf-8"))["endpoints"]
    for pattern, (decision, reason) in decisions.items():
        assert decision in {"Behalten", "Behalten, bereits veraltet"}
        assert reason.strip(), pattern
        assert any(fnmatch.fnmatchcase(endpoint, pattern) for endpoint in saved), pattern


def test_retention_counts_match_snapshot():
    doc = (ROOT / "docs/04-API.md").read_text(encoding="utf-8")
    saved = json.loads((Path(__file__).parent / "api_v1.json").read_text(encoding="utf-8"))["endpoints"]
    counts = [sum(fnmatch.fnmatchcase(endpoint, pattern) for endpoint in saved) for pattern in ALIASES]
    match = re.search(
        r"enthält dort (\d+) Docker-, (\d+) Shield- und\s+(\d+) Netzwerk-Routen\. "
        r"Zusammen mit den elf Einzelrouten sind das (\d+) Vertragsrouten\.",
        doc,
    )
    assert match is not None
    assert tuple(map(int, match.groups())) == (*counts, sum(counts) + len(ROUTES))


def test_api_docs_keep_retention_and_telemetry_sections():
    doc = (ROOT / "docs/04-API.md").read_text(encoding="utf-8")
    assert not re.search(r"^([<=>])\1{6}(?:[ \t].*)?$", doc, re.MULTILINE)
    retention, telemetry = doc.split("\n## Nutzungsdaten\n", 1)
    assert "### Routen ohne lokale Aufrufer\n" in retention
    assert "### Bericht an den Empfänger, Datenformat 1\n" in telemetry
    for method, path in (
        ("GET", "/api/v1/telemetry"),
        ("POST", "/api/v1/telemetry/preview"),
        ("PUT", "/api/v1/telemetry"),
        ("POST", "/api/v1/telemetry/send"),
        ("DELETE", "/api/v1/telemetry"),
    ):
        assert f"| {method} | `{path}` |" in telemetry
