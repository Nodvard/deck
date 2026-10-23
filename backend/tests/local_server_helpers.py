"""Ein echter uvicorn-Server auf Loopback für Tests, die einen echten HTTP-Roundtrip brauchen."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from typing import Any

import uvicorn

START_WAIT_S = 10.0
STOP_WAIT_S = 5.0


@contextlib.asynccontextmanager
async def serve_app(app: Any) -> AsyncIterator[str]:
    """`http://127.0.0.1:<port>` der laufenden App; der Socket ist vor dem Start gebunden, den Port belegt niemand dazwischen."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    task = asyncio.ensure_future(server.serve(sockets=[sock]))
    try:
        await _until_started(server, task)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        _, pending = await asyncio.wait({task}, timeout=STOP_WAIT_S)
        for hanging in pending:
            hanging.cancel()
            await asyncio.wait({hanging})
        sock.close()


async def _until_started(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + START_WAIT_S
    while not server.started:
        if task.done():
            raise RuntimeError(f"Der Test-Server ist beim Start ausgefallen: {task.exception()!r}")
        if loop.time() > deadline:
            raise RuntimeError(f"Der Test-Server war nach {START_WAIT_S:g} s noch nicht gestartet.")
        await asyncio.sleep(0.01)
