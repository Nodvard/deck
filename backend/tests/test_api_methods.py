import importlib

import pytest
from fastapi import APIRouter, FastAPI, routing
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from nodvard_deck.api import methods


@pytest.mark.parametrize("frontend", [False, True])
@pytest.mark.parametrize("method", ["TRACE", "OPTIONS", "BREW", "HEAD"])
async def test_methodenpruefung_beachtet_verschachtelte_router(tmp_path, frontend, method):
    app = FastAPI()
    app.add_middleware(methods.ApiMethodsMiddleware, router=app.router)
    items = APIRouter(prefix="/items")

    @items.get("/{item_id}")
    async def read(item_id: int):
        return {"id": item_id}

    @items.post("/{item_id}")
    async def write(item_id: int):
        return {"id": item_id}

    router = APIRouter()
    router.include_router(items, prefix="/nested")
    for prefix in ("/api/v1/ext/probe", "/api/v1/ext/second"):
        app.include_router(router, prefix=prefix)
    if frontend:
        (tmp_path / "index.html").write_text("App-Shell", encoding="utf-8")
        app.mount("/", StaticFiles(directory=tmp_path, html=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for prefix in ("/api/v1/ext/probe", "/api/v1/ext/second"):
            path = f"{prefix}/nested/items/42"
            response = await client.request(method, path)
            assert response.status_code == 405
            assert set(response.headers["allow"].split(", ")) == {"GET", "POST"}
            assert response.headers["content-type"] == "application/json"
            for allowed in ("GET", "POST"):
                response = await client.request(allowed, path)
                assert response.status_code == 200
                assert response.json() == {"id": 42}
        assert (await client.get("/api/v1/ext/probe/items/42")).status_code == 404


async def test_methodenpruefung_unterstuetzt_flache_routen(tmp_path, monkeypatch):
    monkeypatch.delattr(routing, "iter_route_contexts", raising=False)
    importlib.reload(methods)
    app = FastAPI()
    app.add_middleware(methods.ApiMethodsMiddleware, router=app.router)

    @app.get("/api/v1/items/{item_id}")
    async def read(item_id: int):
        return {"id": item_id}

    @app.post("/api/v1/items/{item_id}")
    async def write(item_id: int):
        return {"id": item_id}

    (tmp_path / "index.html").write_text("App-Shell", encoding="utf-8")
    app.mount("/", StaticFiles(directory=tmp_path, html=True))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request("OPTIONS", "/api/v1/items/42")
        assert response.status_code == 405
        assert set(response.headers["allow"].split(", ")) == {"GET", "POST"}
        assert response.headers["content-type"] == "application/json"
        for method in ("GET", "POST"):
            response = await client.request(method, "/api/v1/items/42")
            assert response.status_code == 200
            assert response.json() == {"id": 42}
