"""Ollama als `AIProvider` (`nodvard_sdk.capabilities.AIProvider`) -- ersetzt
`ask_ai()` aus dem Vorgaengersystem: dieselbe Zwei-Stufen-Anfrage
(primaeres Modell, dann ein optionales, typischerweise schwaecheres Failover-Modell),
dieselbe Ollama-`/api/generate`-Anfrageform (`{model, system, prompt, stream}`,
Antwortfeld `response`).

**Ehrlich abgegrenzt:** `ctx.connectors.register_type()` (docs/02-EXTENSION-API.md §6
zeigt Ollama als das kanonische `ConnectorType`-Beispiel) wird unten registriert, aber
`OllamaProvider` selbst liest seine Konfiguration DIREKT ueber `ctx.settings.get()` +
optional `ctx.secrets`, genau wie ntfy/proxmox es tun -- NICHT ueber
`ctx.connectors.get_client()`. Der Grund: `get_client()` ist im Kern noch ein
`NotImplementedError`-Stub (`ext/context.py::ConnectorsHandle.get_client()`, Docstring
wörtlich: "erster Bedarf voraussichtlich mit einer spaeteren KI-/Connector-Extension"
-- das ist genau diese Extension). Der registrierte `ConnectorType` ist damit fuer
Sichtbarkeit/Dokumentation da, nicht fuer den tatsaechlichen Laufzeitpfad.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from nodvard_sdk.types import ConnectorHealth

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_TOKEN_LABEL = "nexus-soc-ollama-key"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_FAILOVER_MODEL = "qwen2.5:0.5b"
_TIMEOUT_PRIMARY_S = 45.0
_TIMEOUT_FAILOVER_S = 30.0


NO_AI_TEXT = (
    "ℹ️ Nodvard KI ist nicht eingerichtet – das ist in Ordnung, sie ist optional. Hier steht nur, was passiert ist; "
    "eine Einschätzung und Vorschläge gibt es erst, wenn unter Einstellungen → Erweiterungen → Nodvard Shield "
    "ein KI-Server (Ollama) eingetragen ist."
)
"""Antwort, wenn gar kein KI-Server eingetragen ist. Bewusst kein „Nodvard KI nicht erreichbar“: nichts ist kaputt,
und jemand ohne KI soll bei jedem Container-Absturz nicht lesen, es sei etwas schiefgelaufen."""


class OllamaUnavailable(Exception):
    pass


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
    """Erfuellt `nodvard_sdk.capabilities.AIProvider`."""

    provider_id = "ollama"

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx
        self.model = DEFAULT_MODEL
        """Bewusst ein einfaches, synchron lesbares Attribut (das Protokoll verlangt
        `model: str`, keine async-Property) -- wird nach jedem echten Aufruf aus den
        aktuellen Einstellungen aufgefrischt. Fuer Anzeige-/Attributionszwecke
        (`Actor.ai(model=...)`) reicht das; ein Aufrufer, der den Wert VOR dem ersten
        Aufruf braucht, sieht den Default aus `settings.schema.json`."""

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
        except Exception as exc:  # noqa: BLE001 - Netzwerkfehler sind hier ein normaler, erwarteter Fall (Failover)
            raise OllamaUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise OllamaUnavailable(f"HTTP {response.status_code}")
        body = response.json()
        text = body.get("response")
        if not text:
            raise OllamaUnavailable("Leere Antwort ohne 'response'-Feld.")
        return str(text)

    async def stream(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> AsyncIterator[str]:
        """**Ehrlich abgegrenzt:** kein echtes Token-fuer-Token-Streaming -- `ctx.http`
        (der geteilte, `net.outbound`-gepruefte Client, siehe `HttpHandle`) hat keine
        Streaming-Methode, nur `get`/`post`/`request` mit vollstaendig gepuffertem
        Body. Ein direkter Zugriff auf den darunterliegenden `httpx`-Client wuerde die
        CIDR-/Permission-Pruefung umgehen (dasselbe Muster wie D-13) -- das ist hier
        bewusst NICHT gemacht. Liefert stattdessen das vollstaendige Ergebnis von
        `complete()` als einzelnen Chunk; erfuellt das Protokoll, ohne echtes
        Streaming vorzutaeuschen. Die Chat-Seite dieser Extension nutzt in dieser
        Runde ohnehin `complete()` direkt, kein WS-Streaming."""
        yield await self.complete(prompt, system=system, **kwargs)

    async def health(self) -> ConnectorHealth:
        cfg = await _config(self._ctx)
        if not cfg["url"]:
            return ConnectorHealth(ok=False, message="Kein Server für Nodvard KI eingetragen (optional).")
        try:
            response = await self._ctx.http.get(f"{cfg['url'].rstrip('/')}/api/tags")
            return ConnectorHealth(ok=response.status_code < 400, message=f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - Health-Check darf nie werfen, nur melden
            return ConnectorHealth(ok=False, message=str(exc))


async def list_models(ctx: "ExtensionContext", which: str = "primary") -> dict[str, Any]:
    """Die Modelle, die der Ollama-Server kennt (`/api/tags`) -- fuer die Auswahlliste in den
    Einstellungen. Antwortet nie mit einem Fehlerstatus: bei Problemen bleibt `options` leer
    und `error` erklaert es auf Deutsch (die Oberflaeche faellt dann auf ein Textfeld zurueck).

    **Nur gespeicherte Adressen:** `which` waehlt `ollama_url` ("primary") oder
    `ollama_failover_url` ("failover"); eine Adresse vom Aufrufer gibt es bewusst nicht. Sonst
    waere die Route ein Weg, beliebige Anfragen -- samt Bearer-Schluessel -- ins Heimnetz zu
    schicken (SSRF, Sicherheits-Review). Wer eine neue Adresse eingetragen hat, speichert sie
    erst und laedt die Liste dann neu."""
    cfg = await _config(ctx)
    chosen = cfg["failover_url"] if which == "failover" else cfg["url"] if which == "primary" else None
    base = str(chosen or "").strip()
    if not base.startswith(("http://", "https://")):
        return {"options": [], "error": "Noch keine Adresse des KI-Servers gespeichert – erst Adresse eintragen und speichern, dann neu laden."}
    try:
        response = await ctx.http.get(f"{base.rstrip('/')}/api/tags", headers=await _auth_headers(ctx), timeout=8.0)
    except Exception as exc:  # noqa: BLE001 - jeder Netzwerkfehler wird zu einem Hinweistext
        text = f"{type(exc).__name__} {exc}".lower()
        if "timeout" in text or "timed out" in text:
            return {"options": [], "error": "Keine Antwort vom KI-Server – Adresse und Port prüfen."}
        if "name or service" in text or "getaddrinfo" in text or "name resolution" in text:
            return {"options": [], "error": "Adresse des KI-Servers nicht gefunden."}
        return {"options": [], "error": "KI-Server nicht erreichbar – Adresse und Port prüfen."}
    if response.status_code in (401, 403):
        return {"options": [], "error": "Der KI-Server hat die Zugangsdaten abgelehnt."}
    if response.status_code != 200:
        return {"options": [], "error": f"Der KI-Server antwortet mit HTTP {response.status_code}."}
    try:
        models = response.json().get("models") or []
    except ValueError:
        return {"options": [], "error": "Unerwartete Antwort vom KI-Server."}
    options: list[dict[str, str]] = []
    for model in models:
        name = str((model or {}).get("name") or "").strip()
        if not name:
            continue
        size = (model or {}).get("size")
        label = f"{name} ({size / 1e9:.1f} GB)" if isinstance(size, (int, float)) and size > 0 else name
        options.append({"value": name, "label": label})
    options.sort(key=lambda o: o["value"])
    if not options:
        return {"options": [], "error": "Auf dem KI-Server ist noch kein Modell geladen."}
    return {"options": options, "error": None}


class OllamaConnectorType:
    """Erfuellt `nodvard_sdk.context.ConnectorType` -- registriert fuer Sichtbarkeit
    (docs/02 §6), siehe Modul-Docstring fuer den Grund, warum `OllamaProvider` selbst
    NICHT ueber `ctx.connectors.get_client()` geht."""

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
