"""`limit` und `offset` an Endpunkten: überall mit Grenzen, damit niemand mit einer Anfrage die ganze Tabelle holt."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "backend" / "src" / "nodvard_deck" / "api", *sorted((ROOT / "extensions").glob("*/src"))]
_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
MAX_LIMIT = 1000


def _is_route(func: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in _ROUTE_METHODS
        for d in func.decorator_list
    )


def _query_bounds(default: ast.expr | None) -> dict[str, int]:
    if not (isinstance(default, ast.Call) and getattr(default.func, "id", None) == "Query"):
        return {}
    return {
        kw.arg: kw.value.value for kw in default.keywords
        if kw.arg in ("ge", "le") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int)
    }


def _route_params(name: str) -> list[tuple[str, dict[str, int]]]:
    found = []
    for base in SOURCES:
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.AsyncFunctionDef, ast.FunctionDef)) or not _is_route(func):
                    continue
                args = func.args.args + func.args.kwonlyargs
                defaults = [None] * (len(func.args.args) - len(func.args.defaults)) + list(func.args.defaults)
                defaults += list(func.args.kw_defaults)
                for arg, default in zip(args, defaults):
                    if arg.arg == name:
                        found.append((f"{path.relative_to(ROOT)}:{func.lineno} {func.name}", _query_bounds(default)))
    return found


def test_every_limit_of_a_route_is_bounded():
    found = _route_params("limit")
    assert len(found) >= 8, "die Suche findet die Endpunkte"
    for where, bounds in found:
        assert bounds.get("ge") == 1, f"{where}: limit braucht Query(ge=1, ...)"
        assert 1 <= bounds.get("le", MAX_LIMIT + 1) <= MAX_LIMIT, f"{where}: limit braucht Query(le<={MAX_LIMIT})"


def test_every_offset_of_a_route_is_not_negative():
    for where, bounds in _route_params("offset"):
        assert bounds.get("ge") == 0, f"{where}: offset braucht Query(ge=0)"


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "expected"), [("limit=0", 422), ("limit=1001", 422), ("offset=-1", 422), ("limit=1000", 200)])
async def test_notifications_refuse_limits_outside_the_bounds(client, query, expected):
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    res = await client.get(f"/api/v1/notifications?{query}", headers=headers)
    assert res.status_code == expected, res.text
