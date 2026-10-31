"""`on_secret_changed`: Wer ein Geheimnis lesen darf, erfährt nach dem Speichern von dessen Wechsel."""

from __future__ import annotations

import asyncio
import logging

import pytest
from nodvard_sdk import ExtensionManifest

from nodvard_deck.core import known_actors
from nodvard_deck.ext.runtime import LoadedExtension, get_extension_runtime
from nodvard_deck.services import extensions as extensions_service

LABEL = "pihole-passwort"


class _Session:
    def __init__(self, seen: list[str]) -> None:
        self._seen = seen

    async def commit(self) -> None:
        self._seen.append("commit")


class _Extension:
    def __init__(self, seen: list[str], name: str, *, fails: bool = False, hangs: bool = False) -> None:
        self._seen, self._name, self._fails, self._hangs = seen, name, fails, hangs

    async def on_secret_changed(self, ctx, label: str) -> None:
        if self._hangs:
            await asyncio.sleep(60)
        if self._fails:
            raise RuntimeError("kaputt")
        self._seen.append(f"{self._name}:{label}")
        if known_actors.is_known("user", "nutzer-1"):
            self._seen.append("mit Nutzer")


@pytest.fixture
def load(monkeypatch):
    runtime = get_extension_runtime()
    monkeypatch.setattr(runtime, "loaded", {})

    def add(ext_id: str, instance: object, permissions: list[str]) -> None:
        manifest = ExtensionManifest(
            id=ext_id, name=ext_id, version="0.1.0", api_version="0.1.0", entrypoint="x:Extension",
            permissions=permissions,
        )
        runtime.loaded[ext_id] = LoadedExtension(
            manifest=manifest, instance=instance, ctx=object(), granted_permissions=permissions,
        )

    return add


@pytest.mark.asyncio
async def test_only_extensions_that_may_read_the_secret_hear_of_it_after_saving(load):
    seen: list[str] = []
    load("pihole", _Extension(seen, "pihole"), [f"secrets.read:{LABEL}"])
    load("npm", _Extension(seen, "npm"), ["secrets.read:npm-passwort"])
    load("alt", object(), [f"secrets.read:{LABEL}"])

    known_actors.add_user("nutzer-1")
    await extensions_service.notify_secret_changed(_Session(seen), [LABEL, LABEL])
    assert seen == ["commit", f"pihole:{LABEL}"]


@pytest.mark.asyncio
async def test_a_broken_or_hanging_hook_stops_neither_the_others_nor_the_caller(load, monkeypatch, caplog):
    monkeypatch.setattr(extensions_service, "SECRET_HOOK_TIMEOUT_S", 0.05)
    seen: list[str] = []
    load("haengt", _Extension(seen, "haengt", hangs=True), [f"secrets.read:{LABEL}"])
    load("kaputt", _Extension(seen, "kaputt", fails=True), [f"secrets.read:{LABEL}"])
    load("pihole", _Extension(seen, "pihole"), [f"secrets.read:{LABEL}"])

    with caplog.at_level(logging.ERROR):
        await extensions_service.notify_secret_changed(_Session(seen), [LABEL])
    assert seen == ["commit", f"pihole:{LABEL}"]
    assert "on_secret_changed von 'haengt'" in caplog.text and "on_secret_changed von 'kaputt'" in caplog.text


@pytest.mark.asyncio
async def test_without_a_changed_secret_nothing_is_saved_early(load):
    seen: list[str] = []
    load("pihole", _Extension(seen, "pihole"), [f"secrets.read:{LABEL}"])
    await extensions_service.notify_secret_changed(_Session(seen), [])
    assert seen == []


@pytest.mark.asyncio
async def test_the_vault_api_tells_the_extension_after_create_replace_and_delete(client, load):
    seen: list[str] = []
    load("pihole", _Extension(seen, "pihole"), [f"secrets.read:{LABEL}"])
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    token = (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": PASSWORD})).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    body = {"label": LABEL, "kind": "password", "value": "alt"}
    created = await client.post("/api/v1/secrets", json=body, headers=headers)
    assert created.status_code == 201, created.text
    url = f"/api/v1/secrets/{created.json()['id']}"
    replaced = await client.put(f"{url}/value", json={"value": "neu", "current_password": PASSWORD}, headers=headers)
    assert replaced.status_code == 204, replaced.text
    deleted = await client.request("DELETE", url, json={"current_password": PASSWORD}, headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert seen == [f"pihole:{LABEL}"] * 3

    other = await client.post("/api/v1/secrets", json={**body, "label": "fremd"}, headers=headers)
    assert other.status_code == 201 and seen == [f"pihole:{LABEL}"] * 3


PASSWORD = "correct-horse-battery"
