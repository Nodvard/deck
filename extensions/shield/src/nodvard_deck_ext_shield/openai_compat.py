"""Chat Completions für einen selbst eingerichteten KI-Server."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import TYPE_CHECKING, Any, AsyncIterator
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

import httpx
from nodvard_sdk.types import ConnectorHealth

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

TOKEN_LABEL = "shield-openai-compat-key"
UNAVAILABLE_TEXT = "Nodvard KI ist nicht erreichbar. Bitte prüfe den KI-Server und die gespeicherten Einstellungen."
NO_AI_TEXT = "Nodvard KI ist optional. Trage unter Einstellungen → Erweiterungen → Nodvard Shield einen KI-Server ein."

_MAX_RESPONSE_BYTES = 1024 * 1024

_AUTHENTICATED_HTTP: ContextVar[bool] = ContextVar("shield_authenticated_http", default=False)


class _PrivateTransportFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _AUTHENTICATED_HTTP.get()


@contextmanager
def _private_transport_logs(token: str):
    if not token:
        yield
        return
    # Fremde Statuszeilen und HTTP-Diagnosen können Zugangsdaten spiegeln.
    names = {"httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"}
    names.update(name for name in list(logging.Logger.manager.loggerDict) if name.startswith(("httpx.", "httpcore.")))
    loggers = [logging.getLogger(name) for name in names]
    guard = _PrivateTransportFilter()
    for logger in loggers:
        logger.addFilter(guard)
    marker = _AUTHENTICATED_HTTP.set(True)
    try:
        yield
    finally:
        _AUTHENTICATED_HTTP.reset(marker)
        for logger in loggers:
            logger.removeFilter(guard)


class ProviderUnavailable(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_base_url(value: Any) -> str:
    """Ergänzt genau ein abschließendes /v1 und lehnt Zugangsdaten in der Adresse ab."""
    try:
        if not isinstance(value, str) or not value or any(c.isspace() for c in value):
            raise ValueError
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
            raise ValueError
        if parts.query or parts.fragment or parts.port == 0:
            raise ValueError
        path = parts.path.rstrip("/")
        while path.endswith("/v1"):
            path = path[:-3].rstrip("/")
        return urlunsplit((parts.scheme, parts.netloc, f"{path}/v1", "", ""))
    except ValueError:
        raise ProviderUnavailable(
            "Ungültige Serveradresse. Verwende http:// oder https:// ohne Zugangsdaten, Abfrage oder Fragment.",
            "configuration",
        ) from None


def _token_forms(token: str) -> set[str]:
    return {token, quote(token, safe=""), quote_plus(token)} - {""}


def _clean(text: str, token: str) -> str:
    for form in sorted(_token_forms(token), key=len, reverse=True):
        text = re.sub(re.escape(form), "[ausgeblendet]", text, flags=re.IGNORECASE)
    return text


def _has_token(text: str, token: str) -> bool:
    return _clean(text, token) != text


def _network_error(exc: Exception) -> ProviderUnavailable:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ProviderUnavailable("Der KI-Server antwortet nicht rechtzeitig. Prüfe den Server und versuche es erneut.", "timeout")
    return ProviderUnavailable("KI-Server nicht erreichbar. Prüfe Adresse, Port und Verbindung.", "unreachable")


def _check_status(status: int) -> None:
    if status in (401, 403):
        raise ProviderUnavailable("Der KI-Server hat die Zugangsdaten abgelehnt. Prüfe den API-Schlüssel im Tresor.", "auth")
    if 300 <= status < 400:
        raise ProviderUnavailable("Der KI-Server leitet die Anfrage um. Speichere die endgültige Serveradresse.", "redirect")
    if status != 200:
        raise ProviderUnavailable(f"Der KI-Server antwortet mit HTTP {status}. Prüfe Adresse und Modell.", "http")


def _json(content: bytes) -> dict[str, Any]:
    try:
        body = json.loads(content)
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, dict):
        raise ProviderUnavailable("Unerwartete Antwort vom KI-Server. Prüfe die Unterstützung für Chat Completions.", "invalid_response")
    return body


@asynccontextmanager
async def _credentials(ctx: "ExtensionContext") -> AsyncIterator[str]:
    try:
        exists = await ctx.secrets.exists(TOKEN_LABEL)
        handle = await ctx.secrets.get_handle(TOKEN_LABEL) if exists else None
    except Exception:
        raise ProviderUnavailable("Der API-Schlüssel ist nicht verfügbar. Prüfe den Tresor und die Berechtigungen.", "configuration") from None
    if handle is None:
        yield ""
        return
    try:
        async with ctx.vault_use(handle) as token:
            value = token
    except Exception:
        raise ProviderUnavailable("Der API-Schlüssel ist nicht verfügbar. Prüfe den Tresor und die Berechtigungen.", "configuration") from None
    with _private_transport_logs(value):
        yield value


@asynccontextmanager
async def _request_config(ctx: "ExtensionContext"):
    settings = dict(await ctx.settings.get())
    async with _credentials(ctx) as token:
        if settings != await ctx.settings.get():
            raise ProviderUnavailable("Die Einstellungen wurden geändert. Bitte versuche es erneut.", "configuration")
        yield settings, token


def _config(settings: dict[str, Any], which: str = "primary") -> tuple[str, str]:
    prefix = "openai_compat_failover" if which == "failover" else "openai_compat"
    url = settings.get(f"{prefix}_url")
    if not url:
        raise ProviderUnavailable("Noch keine Adresse des KI-Servers gespeichert. Trage die Adresse ein und speichere zuerst.", "configuration")
    return normalize_base_url(url), str(settings.get(f"{prefix}_model") or "").strip()


async def _request(ctx: ExtensionContext, method: str, url: str, token: str,
                   timeout: float, **kwargs: Any) -> tuple[int, bytes]:
    headers = {"Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with asyncio.timeout(timeout):
            async with ctx.http.stream(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                if response.status_code != 200:
                    return response.status_code, b""
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ProviderUnavailable("Der Server liefert eine unerwartet komprimierte Antwort.", "invalid_response")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise ProviderUnavailable("Die Antwort des Servers ist zu groß. Prüfe den Server und versuche es erneut.", "invalid_response")
                    body.extend(chunk)
                return response.status_code, bytes(body)
    except ProviderUnavailable:
        raise
    except Exception as exc:
        raise _network_error(exc) from None


async def _completion(ctx: "ExtensionContext", base: str, model: str, prompt: str, system: str | None,
                      token: str, timeout: float, *, probe: bool = False) -> str:
    if not model:
        raise ProviderUnavailable("Noch kein Modell gespeichert. Trage den Modellnamen ein und speichere zuerst.", "configuration")
    # Geheimnisse gehören auch dann nicht in die URL, wenn sie versehentlich dort gespeichert wurden.
    if _has_token(base, token):
        raise ProviderUnavailable("Die Serveradresse enthält den API-Schlüssel. Entferne ihn aus der Adresse.", "configuration")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if probe:
        payload["max_tokens"] = 8
    status, content = await _request(ctx, "POST", f"{base}/chat/completions", token, timeout, json=payload)
    _check_status(status)
    body = _json(content)
    choices = body.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderUnavailable("Der KI-Server liefert keinen Antworttext. Prüfe das gespeicherte Modell.", "invalid_response")
    return _clean(content, token)


async def _models(ctx: "ExtensionContext", base: str, token: str) -> dict[str, Any]:
    if _has_token(base, token):
        raise ProviderUnavailable("Entferne den API-Schlüssel aus der Serveradresse.", "configuration")
    status, content = await _request(ctx, "GET", f"{base}/models", token, 8.0)
    if status in (404, 405, 501):
        return {"options": [], "error": "Dieser Server bietet keine Modellliste an. Trage den Modellnamen von Hand ein."}
    _check_status(status)
    entries = _json(content).get("data")
    if not isinstance(entries, list):
        raise ProviderUnavailable("Unerwartete Modellliste vom KI-Server.", "invalid_response")
    names = sorted({entry["id"].strip() for entry in entries
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip()
                    and not _has_token(entry["id"], token)})
    return {"options": [{"value": name, "label": name} for name in names],
            "error": None if names else "Der KI-Server meldet keine Modelle. Trage den Modellnamen von Hand ein."}


async def list_models(ctx: "ExtensionContext", which: str = "primary") -> dict[str, Any]:
    try:
        async with _request_config(ctx) as (settings, token):
            base, _model = _config(settings, which)
            return await _models(ctx, base, token)
    except ProviderUnavailable as exc:
        return {"options": [], "error": str(exc)}


async def test_connection(ctx: "ExtensionContext", which: str = "primary") -> dict[str, Any]:
    start = monotonic()
    result: dict[str, Any] = {"ok": False, "latency_ms": 0, "model": "", "message": "", "models": [],
                              "models_error": None, "error_code": None}
    try:
        async with _request_config(ctx) as (settings, token):
            base, model = _config(settings, which)
            result["model"] = _clean(model, token)
            await _completion(ctx, base, model, "Antworte nur mit OK.", None, token, 8.0, probe=True)
            result.update(ok=True, message="Verbindung erfolgreich. Das gespeicherte Modell antwortet.")
            result["latency_ms"] = round((monotonic() - start) * 1000)
            try:
                models = await _models(ctx, base, token)
                result.update(models=models["options"], models_error=models["error"])
            except ProviderUnavailable as exc:
                result["models_error"] = str(exc)
    except ProviderUnavailable as exc:
        result.update(message=str(exc), error_code=exc.code, latency_ms=round((monotonic() - start) * 1000))
    return result


class OpenAICompatProvider:
    """Erfüllt AIProvider ohne Streaming oder zusätzliche Dienstabhängigkeiten."""

    provider_id = "openai_compat"

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx
        self.model = ""

    async def complete(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
        self.model = ""
        settings = await self._ctx.settings.get()
        if not settings.get("openai_compat_url") and not settings.get("openai_compat_failover_url"):
            return NO_AI_TEXT
        try:
            async with _request_config(self._ctx) as (settings, token):
                for which, timeout in (("primary", 45.0), ("failover", 30.0)):
                    try:
                        base, model = _config(settings, which)
                        self.model = _clean(model, token)
                        answer = await _completion(self._ctx, base, model, prompt, system, token, timeout)
                        return f"[Failover-Modell] {answer}" if which == "failover" else answer
                    except ProviderUnavailable:
                        continue
        except ProviderUnavailable:
            pass
        return UNAVAILABLE_TEXT

    async def stream(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> AsyncIterator[str]:
        yield await self.complete(prompt, system=system, **kwargs)

    async def health(self) -> ConnectorHealth:
        message = "Kein Server für Nodvard KI eingetragen (optional)."
        settings = await self._ctx.settings.get()
        if not settings.get("openai_compat_url") and not settings.get("openai_compat_failover_url"):
            return ConnectorHealth(ok=False, message=message)
        try:
            async with _request_config(self._ctx) as (settings, token):
                for which, prefix in (("primary", "openai_compat"), ("failover", "openai_compat_failover")):
                    if not settings.get(f"{prefix}_url"):
                        continue
                    try:
                        base, model = _config(settings, which)
                        await _completion(self._ctx, base, model, "Antworte nur mit OK.", None, token, 8.0, probe=True)
                        message = "Ersatzserver antwortet." if which == "failover" else "KI-Server erreichbar."
                        return ConnectorHealth(ok=True, message=message)
                    except ProviderUnavailable as exc:
                        message = str(exc)
            return ConnectorHealth(ok=False, message=message)
        except ProviderUnavailable as exc:
            return ConnectorHealth(ok=False, message=str(exc))
