"""Holt die Kennzahlen der Nextcloud und hält sie kurz im Speicher."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .config import (
    PASSWORD_LABEL,
    SETTINGS_HINT,
    TOKEN_LABEL,
    ServerInfoConfig,
    read_serverinfo_config,
)
from .models import ActiveUsers, LoadInfo, ServerInfo, StorageInfo, UpdateInfo
from .serverinfo import ServerInfoError, fetch, parse_info

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

# Seite und Kachel (fragt jede Minute) sollen die Nextcloud nicht doppelt belasten.
CACHE_TTL_S = 20.0
# Nextcloud zählt jede Ablehnung als Fehlversuch und bremst danach (bis HTTP 429), darum nicht jede Minute neu fragen.
AUTH_FAILED_TTL_S = 1800.0

NOT_CONFIGURED = (
    "Nextcloud ist noch nicht eingerichtet. Trage Adresse, Benutzername und App-Passwort ein (oder statt des "
    f"Passworts ein Token für „Server-Info“). {SETTINGS_HINT}"
)


def empty(state: str, message: str | None, auth: str | None = None) -> ServerInfo:
    """Eine Antwort ohne Zahlen (Problem oder noch nicht eingerichtet)."""
    return ServerInfo(
        state=state, message=message, auth=auth, version=None, updates=UpdateInfo(), storage=StorageInfo(),
        active_users=ActiveUsers(), load=LoadInfo(),
    )


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    result: ServerInfo


class ServerInfoService:
    def __init__(self, ctx: ExtensionContext, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._ctx = ctx
        self._clock = clock
        self._entry: _Entry | None = None
        self._lock = asyncio.Lock()
        # Steht in der Kennung; nach einem neuen Geheimnis gilt so auch nichts, was eine laufende Abfrage noch einträgt.
        self._generation = 0

    def clear(self) -> None:
        """Verwirft das Zwischenergebnis."""
        self._entry = None

    def forget(self) -> None:
        """Verwirft Zwischenergebnis und Pause, etwa nach einem neuen Token oder App-Passwort im Tresor."""
        self._generation += 1
        self._entry = None

    async def _read_secret(self, label: str, what: str) -> str:
        try:
            handle = await self._ctx.secrets.get_handle(label)
            async with self._ctx.vault_use(handle) as value:
                return str(value or "").strip()
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("Nextcloud: %s nicht lesbar (%s)", what, type(exc).__name__)
            raise ServerInfoError(
                f"{what[0].upper()}{what[1:]} ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → "
                "Erweiterungen → Nextcloud neu eintragen."
            ) from None

    async def _load(self, cfg: ServerInfoConfig, has_token: bool, has_password: bool) -> ServerInfo:
        if cfg.problem:
            return empty("error", cfg.problem)
        if cfg.base_url is None or cfg.username is None or not (has_token or has_password):
            return empty("not_configured", NOT_CONFIGURED)
        auth: str | None = None
        secret = ""
        try:
            # Das Token hat Vorrang und geht ohne Basic-Anmeldung raus.
            token = await self._read_secret(TOKEN_LABEL, "das Token für „Server-Info“") if has_token else ""
            password = ""
            if token:
                auth = "token"
            elif has_password:
                password = await self._read_secret(PASSWORD_LABEL, "das App-Passwort")
                auth = "password"
            else:
                return empty("not_configured", NOT_CONFIGURED)
            secret = token or password
            data = await fetch(
                self._ctx.http,
                cfg.base_url,
                token=token or None,
                auth=None if token else (cfg.username, password),
                insecure_tls=cfg.insecure_tls,
            )
            parsed = parse_info(data)
        except ServerInfoError as exc:
            return empty(exc.state, _scrub(str(exc), secret), auth)
        return ServerInfo(state="ok", message=None, auth=auth, **parsed)

    def _ttl(self, result: ServerInfo) -> float:
        return AUTH_FAILED_TTL_S if result.state == "auth_failed" else CACHE_TTL_S

    async def info(self, *, refresh: bool = False) -> ServerInfo:
        """Liefert die Kennzahlen; Probleme stehen in `state`, es wird nie eine Ausnahme geworfen."""
        try:
            cfg = read_serverinfo_config(await self._ctx.settings.get())
            # Nur `exists`: die Geheimnisse liest erst `_load`, nie für eine Antwort aus dem Zwischenspeicher.
            has_token = await self._ctx.secrets.exists(TOKEN_LABEL)
            has_password = await self._ctx.secrets.exists(PASSWORD_LABEL)
            async with self._lock:
                # Ändert sich eines davon, wird neu gefragt.
                fingerprint = (cfg, has_token, has_password, self._generation)
                entry = self._entry
                if not refresh and entry is not None and entry.fingerprint == fingerprint and entry.expires > self._clock():
                    return entry.result
                result = await self._load(cfg, has_token, has_password)
                self._entry = _Entry(fingerprint, self._clock() + self._ttl(result), result)
                return result
        except Exception:  # eine Abfrage darf nie zur 500 werden
            logger.exception("Nextcloud: unerwarteter Fehler bei den Kennzahlen")
            return empty("error", "Bei der Abfrage der Nextcloud ist ein unerwarteter Fehler aufgetreten.")


def _scrub(text: str, secret: str) -> str:
    """Ersetzt Token oder Passwort im Text, falls eine Ausnahme es zitiert."""
    return text.replace(secret, "…") if secret else text
