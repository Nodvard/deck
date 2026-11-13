"""Wählt den gespeicherten KI-Anbieter für alle Shield-Anfragen."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import TYPE_CHECKING, Any, AsyncIterator

from nodvard_sdk.types import ConnectorHealth

from . import ollama, openai_compat

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


def provider_name(settings: dict[str, Any]) -> str:
    return "openai_compat" if settings.get("ai_provider") == "openai_compat" else "ollama"


def is_configured(settings: dict[str, Any]) -> bool:
    prefix = provider_name(settings)
    return bool(settings.get(f"{prefix}_url") or settings.get(f"{prefix}_failover_url"))


class ConfiguredAIProvider:
    """Wechselt den Anbieter ohne Neustart nach einer Einstellungsänderung."""

    # Die registrierte Kennung bleibt für bestehende Capability-Nutzer stabil.
    provider_id = "ollama"

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx
        self._providers = {"ollama": ollama.OllamaProvider(ctx), "openai_compat": openai_compat.OpenAICompatProvider(ctx)}
        self.model = ollama.DEFAULT_MODEL
        self._health_lock = asyncio.Lock()
        self._health_cache: tuple[dict[str, Any], float, ConnectorHealth] | None = None

    async def complete(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
        provider = self._providers[provider_name(await self._ctx.settings.get())]
        result = await provider.complete(prompt, system=system, **kwargs)
        self.model = provider.model
        return result

    async def stream(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> AsyncIterator[str]:
        yield await self.complete(prompt, system=system, **kwargs)

    async def health(self) -> ConnectorHealth:
        provider = self._providers[provider_name(await self._ctx.settings.get())]
        return await provider.health()

    async def cached_health(self) -> ConnectorHealth:
        async with self._health_lock:
            settings = dict(await self._ctx.settings.get())
            cached = self._health_cache
            if cached is not None and cached[0] == settings and monotonic() - cached[1] < 300:
                return cached[2]
            result = await self.health()
            self._health_cache = (settings, monotonic(), result)
            return result


async def list_models(ctx: "ExtensionContext", which: str, provider: str | None = None) -> dict[str, Any]:
    selected = provider or provider_name(await ctx.settings.get())
    module = openai_compat if selected == "openai_compat" else ollama
    return await module.list_models(ctx, which)


async def test_connection(ctx: "ExtensionContext", which: str) -> dict[str, Any]:
    if provider_name(await ctx.settings.get()) == "openai_compat":
        return await openai_compat.test_connection(ctx, which)
    start = monotonic()
    result: dict[str, Any] = {"ok": False, "latency_ms": 0, "model": "", "message": "", "models": [],
                              "models_error": None, "error_code": None}
    cfg = await ollama._config(ctx)
    model = cfg["failover_model"] if which == "failover" else cfg["model"]
    chosen = cfg["failover_url"] if which == "failover" else cfg["url"]
    try:
        if not chosen:
            raise openai_compat.ProviderUnavailable("Noch keine Adresse des KI-Servers gespeichert. Speichere zuerst die Adresse.", "configuration")
        # Dieselben Adressregeln gelten für beide Anbieter; Ollama behält seinen API-Pfad.
        openai_compat.normalize_base_url(chosen)
        headers = await ollama._auth_headers(ctx)
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        result["model"] = openai_compat._clean(model, token)
        if openai_compat._has_token(chosen, token):
            raise openai_compat.ProviderUnavailable("Entferne den API-Schlüssel aus der Serveradresse.", "configuration")
        with openai_compat._private_transport_logs(token):
            await ollama.OllamaProvider(ctx)._generate(chosen, model, "Antworte nur mit OK.", None, headers, 8.0)
            result.update(ok=True, message="Verbindung erfolgreich. Das gespeicherte Modell antwortet.")
            result["latency_ms"] = round((monotonic() - start) * 1000)
            models = await ollama.list_models(ctx, which)
            result.update(models=[entry for entry in models["options"] if not openai_compat._has_token(entry["value"], token)],
                          models_error=models["error"])
    except openai_compat.ProviderUnavailable as exc:
        result.update(ok=False, message=str(exc), error_code=exc.code)
    except ollama.OllamaUnavailable as exc:
        result["ok"] = False
        message = str(exc)
        if message in ("HTTP 401", "HTTP 403"):
            result.update(message="Der KI-Server hat die Zugangsdaten abgelehnt. Prüfe den API-Schlüssel im Tresor.", error_code="auth")
        else:
            result.update(message=message, error_code="timeout" if "Keine Antwort" in message else "unreachable")
    except Exception:
        result.update(ok=False, message="Verbindung nicht möglich. Prüfe den KI-Server und den Tresor.", error_code="unreachable")
    if not result["ok"]:
        result["latency_ms"] = round((monotonic() - start) * 1000)
    return result
