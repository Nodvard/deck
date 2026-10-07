"""Reiner Dev-/Demo-Helfer (kein Teil des Kerns/der Tests, siehe
scripts/dev_mock_proxmox.py fuer dasselbe Muster) -- ein winziger lokaler Ollama-
API-Mock fuer die manuelle Live-Verifikation der Erweiterung Nodvard Shield (WP-9).

    python scripts/dev_mock_ollama.py --port 11500
"""

from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Ollama-API-Mock (Dev)")

_REPLIES = {
    "restart": (
        "Lagebericht: nginx-proxy antwortet nicht mehr und haeuft Speicherfehler an.\n"
        "NODVARD-Entscheidung:\n"
        "BEGRUENDUNG: Wiederholter Speicherfehler, ein Neustart behebt das kurzfristig\n"
        "AKTION: EXEC docker docker restart nginx-proxy"
    ),
    "default": (
        "Lagebericht: Alle bekannten Hosts melden sich normal, keine Auffaelligkeiten in der Telemetrie.\n"
        "NODVARD-Entscheidung:\n"
        "BEGRUENDUNG: Kein Handlungsbedarf erkennbar\n"
        "AKTION: KEINE"
    ),
}


@app.post("/api/generate")
async def generate(request: Request) -> dict:
    body = await request.json()
    prompt = str(body.get("prompt", "")).lower()
    print(f"[mock-ollama] model={body.get('model')} prompt[:80]={prompt[:80]!r}")
    key = "restart" if ("nginx" in prompt or "restart" in prompt or "abgestuerzt" in prompt or "crash" in prompt) else "default"
    return {"response": _REPLIES[key]}


@app.get("/api/tags")
async def tags() -> dict:
    return {"models": [{"name": "qwen2.5:7b"}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11500)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
