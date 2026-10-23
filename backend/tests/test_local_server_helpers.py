import asyncio

import httpx
import local_server_helpers
import pytest
import uvicorn
from fastapi import FastAPI
from local_server_helpers import serve_app


@pytest.mark.asyncio
async def test_serves_the_app_on_loopback():
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"ok": True}

    async with serve_app(app) as url, httpx.AsyncClient() as client:
        assert url.startswith("http://127.0.0.1:")
        assert (await client.get(url)).json() == {"ok": True}


@pytest.mark.asyncio
async def test_a_server_that_fails_to_start_is_reported(monkeypatch):
    async def fails(self, sockets=None):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(uvicorn.Server, "serve", fails)
    with pytest.raises(RuntimeError, match="beim Start ausgefallen.*Address already in use"):
        async with serve_app(FastAPI()):
            pytest.fail("der Rumpf darf nicht laufen")


@pytest.mark.asyncio
async def test_a_server_that_does_not_come_up_is_reported_after_the_wait(monkeypatch):
    async def hangs(self, sockets=None):
        await asyncio.sleep(60)

    monkeypatch.setattr(uvicorn.Server, "serve", hangs)
    monkeypatch.setattr(local_server_helpers, "START_WAIT_S", 0.2)
    monkeypatch.setattr(local_server_helpers, "STOP_WAIT_S", 0.2)
    with pytest.raises(RuntimeError, match="nach 0.2 s noch nicht gestartet"):
        async with serve_app(FastAPI()):
            pytest.fail("der Rumpf darf nicht laufen")
