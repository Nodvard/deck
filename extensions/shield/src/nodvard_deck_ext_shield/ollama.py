"""Ollama als `AIProvider`: erst das Hauptmodell, dann ein optionales Failover-Modell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from nodvard_sdk.types import ConnectorHealth

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_TOKEN_LABEL = "nexus-soc-ollama-key"
"""Label des Schlüssels im Tresor; es bleibt, weil das Recht dazu im Manifest steht (`secrets.read:nexus-soc-*`)."""
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_FAILOVER_MODEL = "qwen2.5:0.5b"
_TIMEOUT_PRIMARY_S = 45.0
_TIMEOUT_FAILOVER_S = 30.0


NO_AI_TEXT = (
    "ℹ️ Nodvard KI ist nicht eingerichtet – das ist in Ordnung, sie ist optional. Hier steht nur, was passiert ist; "
    "eine Einschätzung und Vorschläge gibt es erst, wenn unter Einstellungen → Erweiterungen → Nodvard Shield "
    "ein KI-Server (Ollama) eingetragen ist."
)
"""Antwort ohne eingetragenen KI-Server. Bewusst keine Fehlermeldung: Ohne KI ist nichts kaputt."""


class OllamaUnavailable(Exception):
    pass


def _redirect_message(status_code: int) -> str:
    """Text für eine Weiterleitung (3xx); die Zieladresse kommt vom fremden Server und steht nie darin."""
    return (
        f"Der KI-Server hat die Anfrage auf eine andere Adresse umgeleitet (HTTP {status_code}). Solchen Umleitungen folgt "
        "Nodvard Deck aus Sicherheitsgründen nicht. Prüfe die eingetragene Adresse des KI-Servers."
    )


UNEXPECTED_ANSWER = "Unerwartete Antwort vom KI-Server."


def _unreachable_text(exc: BaseException) -> str:
    """Fester Satz für einen Netzwerkfehler.

    Der Ausnahmetext selbst bleibt draußen, httpx und h11 zitieren darin Teile der fremden Antwort."""
    text = f"{type(exc).__name__} {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "Keine Antwort vom KI-Server – Adresse und Port prüfen."
    if "name or service" in text or "getaddrinfo" in text or "name resolution" in text:
        return "Adresse des KI-Servers nicht gefunden."
    return "KI-Server nicht erreichbar – Adresse und Port prüfen."


async def _config(ctx: "ExtensionContext") -> dict[str, Any]:
    settings = await ctx.settings.get()
    return {
        "url": settings.get("ollama_url"),
        "model": settings.get("ollama_model") or DEFAULT_MODEL,
        "failover_url": settings.get("ollama_failover_url"),
        "failover_model": settings.get("ollama_failover_model") or DEFAULT_FAILOVER_MODEL,
    }


async def _auth_headers(ctx: "ExtensionContext") -> dict[str, str]:
    if not await ctx.secrets.exists(_TOKEN_LABEL):
        return {}
    handle = await ctx.secrets.get_handle(_TOKEN_LABEL)
    async with ctx.vault_use(handle) as token:
        return {"Authorization": f"Bearer {token}"}


class OllamaProvider:
    """Erfüllt `nodvard_sdk.capabilities.AIProvider`."""

    provider_id = "ollama"

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx
        self.model = DEFAULT_MODEL
        """Das Protokoll verlangt `model: str`; nach jedem Aufruf aus den Einstellungen aufgefrischt, davor der Standard."""

    async def complete(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
        cfg = await _config(self._ctx)
        self.model = cfg["model"]
        if not cfg["url"] and not cfg["failover_url"]:
            return NO_AI_TEXT
        headers = await _auth_headers(self._ctx)

        if cfg["url"]:
            try:
                return await self._generate(cfg["url"], cfg["model"], prompt, system, headers, _TIMEOUT_PRIMARY_S)
            except OllamaUnavailable:
                pass

        if cfg["failover_url"]:
            try:
                result = await self._generate(
                    cfg["failover_url"], cfg["failover_model"], prompt, system, headers, _TIMEOUT_FAILOVER_S
                )
                self.model = cfg["failover_model"]
                return f"[Failover-Modell] {result}"
            except OllamaUnavailable:
                pass

        return (
            "⚠️ Nodvard KI nicht erreichbar (weder Haupt- noch Backup-Modell hat "
            "geantwortet). Dies ist ein Platzhalter, KEIN geprüftes Ergebnis -- bitte "
            "manuell prüfen."
        )

    async def _generate(
        self, base_url: str, model: str, prompt: str, system: str | None, headers: dict[str, str], timeout_s: float
    ) -> str:
        try:
            response = await self._ctx.http.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={"model": model, "system": system or "", "prompt": prompt, "stream": False},
                headers=headers,
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - Netzwerkfehler sind hier der Normalfall (Failover)
            raise OllamaUnavailable(_unreachable_text(exc)) from exc
        if 300 <= response.status_code < 400:
            raise OllamaUnavailable(_redirect_message(response.status_code))
        if response.status_code != 200:
            raise OllamaUnavailable(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except (ValueError, RecursionError) as exc:  # kein JSON oder zu tief verschachtelt
            raise OllamaUnavailable(UNEXPECTED_ANSWER) from exc
        if not isinstance(body, dict):
            raise OllamaUnavailable(UNEXPECTED_ANSWER)
        text = body.get("response")
        if not text:
            raise OllamaUnavailable("Leere Antwort ohne 'response'-Feld.")
        if not isinstance(text, str):
            raise OllamaUnavailable(UNEXPECTED_ANSWER)
        return text

    async def stream(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> AsyncIterator[str]:
        """Kein echtes Streaming, sondern das ganze Ergebnis von `complete()` als ein Stück.

        `ctx.http` puffert den Body; der darunterliegende httpx-Client würde die Netzprüfung umgehen."""
        yield await self.complete(prompt, system=system, **kwargs)

    async def health(self) -> ConnectorHealth:
        cfg = await _config(self._ctx)
        if not cfg["url"]:
            return ConnectorHealth(ok=False, message="Kein Server für Nodvard KI eingetragen (optional).")
        try:
            response = await self._ctx.http.get(f"{cfg['url'].rstrip('/')}/api/tags")
        except Exception as exc:  # noqa: BLE001 - Health-Check darf nie werfen, nur melden
            return ConnectorHealth(ok=False, message=_unreachable_text(exc))
        if 300 <= response.status_code < 400:
            # Eine Weiterleitung ist keine Antwort des KI-Servers.
            return ConnectorHealth(ok=False, message=_redirect_message(response.status_code))
        return ConnectorHealth(ok=200 <= response.status_code < 300, message=f"HTTP {response.status_code}")


async def list_models(ctx: "ExtensionContext", which: str = "primary") -> dict[str, Any]:
    """Die Modelle des Ollama-Servers (`/api/tags`) für die Auswahlliste; bei Problemen bleibt `options` leer.

    `which` wählt nur eine gespeicherte Adresse, sonst ließen sich Anfragen samt Schlüssel ins Heimnetz lenken."""
    cfg = await _config(ctx)
    chosen = cfg["failover_url"] if which == "failover" else cfg["url"] if which == "primary" else None
    base = str(chosen or "").strip()
    if not base.startswith(("http://", "https://")):
        return {"options": [], "error": "Noch keine Adresse des KI-Servers gespeichert – erst Adresse eintragen und speichern, dann neu laden."}
    try:
        response = await ctx.http.get(f"{base.rstrip('/')}/api/tags", headers=await _auth_headers(ctx), timeout=8.0)
    except Exception as exc:  # noqa: BLE001 - jeder Netzwerkfehler wird zu einem Hinweistext
        return {"options": [], "error": _unreachable_text(exc)}
    if response.status_code in (401, 403):
        return {"options": [], "error": "Der KI-Server hat die Zugangsdaten abgelehnt."}
    if 300 <= response.status_code < 400:
        return {"options": [], "error": _redirect_message(response.status_code)}
    if response.status_code != 200:
        return {"options": [], "error": f"Der KI-Server antwortet mit HTTP {response.status_code}."}
    try:
        data = response.json()
    except (ValueError, RecursionError):  # kein JSON oder zu tief verschachtelt
        return {"options": [], "error": UNEXPECTED_ANSWER}
    models = (data.get("models") or []) if isinstance(data, dict) else None
    if not isinstance(models, list):  # Antwort kein Objekt oder "models" keine Liste
        return {"options": [], "error": UNEXPECTED_ANSWER}
    options: list[dict[str, str]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            continue
        size = model.get("size")
        label = f"{name} ({size / 1e9:.1f} GB)" if isinstance(size, (int, float)) and size > 0 else name
        options.append({"value": name, "label": label})
    options.sort(key=lambda o: o["value"])
    if not options:
        return {"options": [], "error": "Auf dem KI-Server ist noch kein Modell geladen."}
    return {"options": options, "error": None}


class OllamaConnectorType:
    """Erfüllt `nodvard_sdk.context.ConnectorType`; nur für die Sichtbarkeit registriert.

    `OllamaProvider` liest seine Einstellungen direkt, weil `ctx.connectors.get_client()` im Kern noch ein Stub ist."""

    id = "ollama"
    label = "Ollama"
    icon = "cpu"
    schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}, "model": {"type": "string"}},
        "required": ["url"],
    }
    secret_fields: list[str] = ["api_key"]

    async def test(self, config: dict[str, Any]) -> ConnectorHealth:
        return ConnectorHealth(ok=bool(config.get("url")), message="Ungetestet -- reine Registrierung.")

    async def build(self, config: dict[str, Any]) -> Any:
        return config
