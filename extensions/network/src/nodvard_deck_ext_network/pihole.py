"""Duenner Client fuer die REST-API von Pi-hole v6 (`/api/...`).

Ablauf (Pi-hole-Doku "Authentication"):
- `POST /api/auth {"password": ...}` -> `{"session": {"valid", "sid", "validity", ...}}`.
  Ein App-Passwort funktioniert an derselben Stelle wie das normale Passwort.
- Jede weitere Anfrage traegt die Sitzung im Header `X-FTL-SID`. Jede Anfrage
  verlaengert die Sitzung; laeuft sie trotzdem ab, kommt 401 -> einmal neu anmelden.
- `DELETE /api/auth` meldet ab. Pi-hole hat nur wenige API-Plaetze
  (`webserver.api.max_sessions`) -- deshalb EINE zwischengespeicherte Sitzung je
  Pi-hole statt einer Anmeldung pro Aufruf, und Abmelden beim Stoppen.

Das Passwort wird NUR im Moment der Anmeldung aus dem Vault geholt (`password()`),
nicht bei jedem Aufruf -- jeder Vault-Zugriff schreibt eine Audit-Zeile, und die
Kacheln fragen jede Minute.

Pi-hole v5 (`/admin/api.php`) wird bewusst nicht nachgebaut, nur erkannt.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .transport import (
    DEFAULT_TIMEOUT_S,
    HttpLike,
    ServiceError,
    as_int,
    error_message,
    json_body,
    redirect_message,
    send,
)

SERVICE = "Pi-hole"

LOGIN_RETRY_AFTER_S = 30.0
"""Nach einer abgelehnten Anmeldung so lange nicht erneut versuchen -- sonst meldet
jede Kachel-Aktualisierung einen Fehlversuch, und Pi-hole sperrt irgendwann (429)."""


class PiholeError(ServiceError):
    state = "unreachable"


class PiholeAuthError(PiholeError):
    state = "auth_failed"


class PiholeUnsupported(PiholeError):
    state = "unsupported"


PasswordGetter = Callable[[], Awaitable[str | None]]


def parse_summary(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    queries = body.get("queries") if isinstance(body.get("queries"), dict) else {}
    gravity = body.get("gravity") if isinstance(body.get("gravity"), dict) else {}
    clients = body.get("clients") if isinstance(body.get("clients"), dict) else {}
    total = as_int(queries.get("total"))
    blocked = as_int(queries.get("blocked"))
    percent = queries.get("percent_blocked")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        percent = (blocked / total * 100) if total and blocked is not None else None
    domains = as_int(gravity.get("domains_being_blocked"))
    updated = as_int(gravity.get("last_update"))
    return {
        "queries_total": total,
        "queries_blocked": blocked,
        "percent_blocked": round(float(percent), 1) if percent is not None else None,
        "domains_blocked": domains if domains is not None and domains >= 0 else None,
        "gravity_updated_at": updated if updated and updated > 0 else None,
        "clients_active": as_int(clients.get("active")),
    }


_BLOCKING_STATES = ("enabled", "disabled", "failed", "unknown")


def parse_blocking(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    status = str(body.get("blocking") or "unknown").lower()
    if status not in _BLOCKING_STATES:
        status = "unknown"
    timer = body.get("timer")
    timer_s = round(timer) if isinstance(timer, (int, float)) and not isinstance(timer, bool) and timer > 0 else None
    return {
        "status": status,
        "enabled": True if status == "enabled" else False if status == "disabled" else None,
        "timer_s": timer_s,
    }


class PiholeClient:
    def __init__(
        self,
        http: HttpLike,
        *,
        base_url: str,
        password: PasswordGetter,
        insecure_tls: bool = False,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._password = password
        self._insecure_tls = insecure_tls
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._authed = False
        self._sid: str | None = None
        self._login_error: PiholeError | None = None
        self._login_failed_at: float | None = None

    @property
    def base_url(self) -> str:
        return self._base

    async def _send(self, method: str, path: str, *, sid: str | None = None, json: Any = None, timeout_s: float | None = None) -> Any:
        headers = {"Accept": "application/json"}
        if sid:
            headers["X-FTL-SID"] = sid
        kwargs: dict[str, Any] = {"headers": headers}
        if json is not None:
            kwargs["json"] = json
        return await send(
            self._http, method, f"{self._base}{path}", service=SERVICE, error_cls=PiholeError,
            insecure_tls=self._insecure_tls, timeout_s=timeout_s or self._timeout_s, **kwargs,
        )

    async def _looks_like_v5(self) -> bool:
        try:
            response = await self._send("GET", "/admin/api.php?version")
        except PiholeError:
            return False
        return response.status_code == 200 and json_body(response) is not None

    async def _login(self) -> None:
        """Muss unter `self._lock` laufen."""
        now = self._clock()
        if self._login_error is not None and self._login_failed_at is not None and now - self._login_failed_at < LOGIN_RETRY_AFTER_S:
            raise self._login_error
        try:
            await self._do_login()
        except PiholeAuthError as exc:
            self._login_error, self._login_failed_at = exc, now
            raise
        self._login_error = self._login_failed_at = None

    async def _do_login(self) -> None:
        password = await self._password()
        response = await self._send("POST", "/api/auth", json={"password": password or ""})
        body = json_body(response)
        not_found = PiholeError(
            "Unter dieser Adresse antwortet keine Pi-hole-Schnittstelle (v6). Bitte die Adresse prüfen – ohne /admin."
        )
        redirect = redirect_message(response, SERVICE)
        if redirect:
            raise PiholeError(redirect)
        if response.status_code in (404, 405):
            if await self._looks_like_v5():
                raise PiholeUnsupported("Pi-hole v5 wird nicht unterstützt – bitte auf v6 aktualisieren.")
            raise not_found
        if response.status_code == 401:
            if not password:
                raise PiholeAuthError("Pi-hole verlangt ein Passwort – bitte in den Einstellungen hinterlegen.")
            raise PiholeAuthError(
                "Pi-hole hat das Passwort abgelehnt. Bei Zwei-Faktor-Anmeldung bitte ein App-Passwort verwenden."
            )
        if response.status_code == 429:
            raise PiholeError(
                "Pi-hole lässt gerade keine Anmeldung zu (zu viele Versuche oder alle API-Plätze belegt). Bitte später erneut versuchen."
            )
        if response.status_code >= 400:
            detail = error_message(response)
            raise PiholeError(f"Anmeldung bei Pi-hole fehlgeschlagen (HTTP {response.status_code}{': ' + detail if detail else ''}).")
        if not isinstance(body, dict):
            raise not_found
        session = body.get("session")
        if not isinstance(session, dict) or not session.get("valid"):
            raise PiholeAuthError("Pi-hole hat die Anmeldung nicht bestätigt. Bei Zwei-Faktor-Anmeldung bitte ein App-Passwort verwenden.")
        sid = session.get("sid")
        self._sid = str(sid) if sid else None  # ohne Passwort am Pi-hole: keine Sitzung noetig
        self._authed = True

    async def _session(self) -> str | None:
        async with self._lock:
            if not self._authed:
                await self._login()
            return self._sid

    async def _api(self, method: str, path: str, *, json: Any = None) -> Any:
        sid = await self._session()
        response = await self._send(method, path, sid=sid, json=json)
        if response.status_code == 401:
            async with self._lock:
                if self._authed and self._sid == sid:
                    # Sitzung abgelaufen -- niemand hat inzwischen neu angemeldet.
                    self._authed, self._sid = False, None
                if not self._authed:
                    await self._login()
                sid = self._sid
            response = await self._send(method, path, sid=sid, json=json)
            if response.status_code == 401:
                async with self._lock:
                    self._authed, self._sid = False, None
                raise PiholeAuthError("Pi-hole lehnt die Anmeldung ab – bitte Passwort in den Einstellungen prüfen.")
        if response.status_code >= 400:
            detail = error_message(response)
            raise PiholeError(f"Pi-hole meldet einen Fehler (HTTP {response.status_code}{': ' + detail if detail else ''}).")
        body = json_body(response)
        if not isinstance(body, dict):
            raise PiholeError("Pi-hole hat keine gültige Antwort geschickt.")
        return body

    async def summary(self) -> dict[str, Any]:
        return parse_summary(await self._api("GET", "/api/stats/summary"))

    async def blocking(self) -> dict[str, Any]:
        return parse_blocking(await self._api("GET", "/api/dns/blocking"))

    async def set_blocking(self, enabled: bool, *, timer_s: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"blocking": bool(enabled), "timer": timer_s}
        return parse_blocking(await self._api("POST", "/api/dns/blocking", json=body))

    async def logout(self) -> None:
        """Best effort: ein nicht erreichbares Pi-hole darf das Stoppen nicht aufhalten."""
        async with self._lock:
            sid, self._sid, self._authed = self._sid, None, False
        if not sid:
            return
        try:
            await self._send("DELETE", "/api/auth", sid=sid, timeout_s=3.0)
        except PiholeError:
            pass
