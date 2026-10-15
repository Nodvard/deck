"""Grundabnahme: /health und /branding antworten."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["uptime_s"] >= 0


@pytest.mark.asyncio
async def test_branding_default(client: AsyncClient) -> None:
    """Ohne Eintraege in `settings` liefert /branding den Seed-Default aus
    branding.py -- der einzige Ort im Kern, an dem ein Produktname stehen darf."""
    r = await client.get("/api/v1/branding")
    assert r.status_code == 200
    body = r.json()
    assert body["product_name"] == "Nodvard Deck"
    assert body["short_name"] == "Nodvard Deck"
    assert body["colors"]["accent"]


@pytest.mark.asyncio
async def test_app_manifest_default_is_nodvard_deck(client: AsyncClient) -> None:
    """Ohne eigenen Namen heisst die installierbare App "Nodvard Deck" (Manifest wie Symbol)."""
    r = await client.get("/api/v1/app/manifest.webmanifest")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["name"] == "Nodvard Deck"
    assert manifest["short_name"] == "Nodvard Deck"
    assert (await client.get("/api/v1/app/icon-192.png")).status_code == 200


@pytest.mark.asyncio
async def test_app_manifest_colors_come_from_the_default_theme(client: AsyncClient) -> None:
    """Startbild und Statusleiste der App im Hintergrund des Standard-Themes, nicht im alten Dunkelblau aus dem
    Branding; der Wert muss zu `:root` in `index.css` passen."""
    css = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "styles" / "index.css").read_text(encoding="utf-8")
    root = css[css.index(":root {") :]
    match = re.search(r"--color-background:\s*(#[0-9a-fA-F]{6});", root[: root.index("}")])
    assert match is not None
    manifest = (await client.get("/api/v1/app/manifest.webmanifest")).json()
    assert manifest["background_color"] == match.group(1)
    assert manifest["theme_color"] == match.group(1)


@pytest.mark.asyncio
async def test_branding_override_from_settings(client: AsyncClient, db_session) -> None:
    """Ein Wert in `settings` (scope=global, key=branding.<feld>) gewinnt gegen den
    Default -- docs/01-ARCHITECTURE.md §6: Branding ist Laufzeit-Konfiguration."""
    from nodvard_deck.db import utcnow
    from nodvard_deck.models import Setting

    db_session.add(
        Setting(
            key="branding.product_name",
            scope="global",
            user_id="",
            value="Kaeufer GmbH Dashboard",
            updated_at=utcnow(),
        )
    )
    await db_session.commit()

    r = await client.get("/api/v1/branding")
    assert r.json()["product_name"] == "Kaeufer GmbH Dashboard"
