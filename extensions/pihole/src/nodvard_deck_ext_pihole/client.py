"""Dünner Client für die REST-API von Pi-hole v6 (`/api/...`), angemeldet mit einer Sitzung (SID)."""

from __future__ import annotations

import asyncio
import re
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
    raise_if_redirect,
    send,
)

SERVICE = "Pi-hole"

# Nach einer abgelehnten Anmeldung so lange nicht erneut versuchen: sonst wäre jede Kachel-Aktualisierung ein
# Fehlversuch, und Pi-hole sperrt irgendwann (429).
LOGIN_RETRY_AFTER_S = 30.0
TOP_CLIENTS = 5
# Pi-hole lädt dabei jede Sperrliste neu und baut seine Datenbank neu auf; auf einem Raspberry Pi dauert das Minuten.
GRAVITY_TIMEOUT_S = 600.0
# So viel vom Ende der Ausgabe (Klartext) bleibt im Ergebnis der Aktion.
GRAVITY_TAIL_CHARS = 2000
DOMAIN_LISTS = ("allow", "deny")


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


def _text(value: Any, limit: int = 100) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_top_clients(body: Any, limit: int = TOP_CLIENTS) -> list[dict[str, Any]]:
    """`GET /api/stats/top_clients`: `[{name, ip, count}]`, der Name fällt auf die IP-Adresse zurück."""
    clients = body.get("clients") if isinstance(body, dict) else None
    out: list[dict[str, Any]] = []
    for client in clients if isinstance(clients, list) else []:
        if not isinstance(client, dict):
            continue
        ip = _text(client.get("ip"), 64)
        name = _text(client.get("name")) or ip
        count = as_int(client.get("count"))
        if name is None or count is None or count < 0:
            continue
        out.append({"name": name, "ip": ip, "count": count})
        if len(out) >= limit:
            break
    return out


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def gravity_tail(text: str, limit: int = GRAVITY_TAIL_CHARS) -> str:
    """Das Ende der Ausgabe von `pihole -g` als lesbarer Text: ohne Farbcodes, und je Zeile nur der letzte Stand, wenn
    Pi-hole eine Zeile mit `\\r` überschreibt (Fortschrittsanzeige)."""
    lines = []
    for raw in _ANSI.sub("", text).split("\n"):
        line = raw.rstrip("\r").split("\r")[-1]
        line = "".join(ch for ch in line if ch == "\t" or ch.isprintable()).rstrip()
        if line.strip():
            lines.append(line)
    out = "\n".join(lines)
    return out[-limit:].lstrip("\n") if len(out) > limit else out


def domain_errors(body: Any) -> list[str]:
    """Fehler aus `processed.errors` einer Antwort von `POST /api/domains/...` (Text gekürzt)."""
    processed = body.get("processed") if isinstance(body, dict) else None
    errors = processed.get("errors") if isinstance(processed, dict) else None
    out: list[str] = []
    for item in errors if isinstance(errors, list) else []:
        message = _text(item.get("error"), 200) if isinstance(item, dict) else None
        out.append(message or "unbekannter Fehler")
    return out


class PiholeClient:
    """Pi-hole hat nur wenige API-Plätze (`webserver.api.max_sessions`): darum eine zwischengespeicherte Sitzung je
    Pi-hole statt einer Anmeldung pro Aufruf, und Abmelden beim Stoppen."""

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
        # Pi-hole v5 (`/admin/api.php`) wird nicht unterstützt, nur erkannt.
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
        # Das Passwort kommt nur bei der Anmeldung aus dem Tresor: jeder Zugriff schreibt eine Audit-Zeile, und die
        # Kacheln fragen jede Minute. Ein App-Passwort geht an derselben Stelle wie das normale.
        password = await self._password()
        response = await self._send("POST", "/api/auth", json={"password": password or ""})
        body = json_body(response)
        not_found = PiholeError(
            "Unter dieser Adresse antwortet keine Pi-hole-Schnittstelle (v6). Bitte die Adresse prüfen – ohne /admin."
        )
        raise_if_redirect(response, SERVICE, PiholeError)
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
        self._sid = str(sid) if sid else None  # ohne Passwort am Pi-hole ist keine Sitzung nötig
        self._authed = True

    async def _session(self) -> str | None:
        async with self._lock:
            if not self._authed:
                await self._login()
            return self._sid

    async def _request(self, method: str, path: str, *, json: Any = None, timeout_s: float | None = None) -> Any:
        """Die Antwort einer angemeldeten Anfrage (läuft die Sitzung ab, einmal neu angemeldet); Fehler als `PiholeError`."""
        sid = await self._session()
        response = await self._send(method, path, sid=sid, json=json, timeout_s=timeout_s)
        # Jede Anfrage trägt die Sitzung im Header `X-FTL-SID` und verlängert sie; läuft sie trotzdem ab, kommt 401.
        if response.status_code == 401:
            async with self._lock:
                if self._authed and self._sid == sid:
                    # Sitzung abgelaufen, und niemand hat inzwischen neu angemeldet.
                    self._authed, self._sid = False, None
                if not self._authed:
                    await self._login()
                sid = self._sid
            response = await self._send(method, path, sid=sid, json=json, timeout_s=timeout_s)
            if response.status_code == 401:
                async with self._lock:
                    self._authed, self._sid = False, None
                raise PiholeAuthError("Pi-hole lehnt die Anmeldung ab – bitte Passwort in den Einstellungen prüfen.")
        raise_if_redirect(response, SERVICE, PiholeError)
        if response.status_code >= 400:
            detail = error_message(response)
            raise PiholeError(f"Pi-hole meldet einen Fehler (HTTP {response.status_code}{': ' + detail if detail else ''}).")
        return response

    async def _api(self, method: str, path: str, *, json: Any = None) -> Any:
        body = json_body(await self._request(method, path, json=json))
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

    async def top_clients(self, limit: int = TOP_CLIENTS) -> list[dict[str, Any]]:
        return parse_top_clients(await self._api("GET", f"/api/stats/top_clients?count={int(limit)}"), limit)

    async def update_gravity(self) -> str:
        """Lädt die Sperrlisten neu (`POST /api/action/gravity`) und gibt das Ende der Ausgabe von Pi-hole zurück."""
        response = await self._request("POST", "/api/action/gravity", timeout_s=GRAVITY_TIMEOUT_S)
        return gravity_tail(getattr(response, "text", "") or "")

    async def add_domain(self, list_type: str, domain: str, *, comment: str) -> list[str]:
        """Trägt `domain` genau so (nicht als Muster) in die Liste `allow` oder `deny` ein; zurück: Pi-holes Fehler."""
        if list_type not in DOMAIN_LISTS:
            raise ValueError(list_type)
        body = await self._api(
            "POST", f"/api/domains/{list_type}/exact", json={"domain": domain, "comment": comment, "enabled": True},
        )
        return domain_errors(body)

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
