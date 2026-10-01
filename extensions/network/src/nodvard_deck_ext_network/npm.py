"""Duenner Client fuer die API des Nginx Proxy Managers (`/api/...`).

- `POST /api/tokens {"identity", "secret"}` -> `{"token", "expires"}` (JWT, ISO-Zeit).
  Mit Zwei-Faktor-Anmeldung kommt stattdessen `{"requires_2fa", "challenge_token"}` --
  das unterstuetzen wir (noch) nicht und sagen es deutlich.
- Danach `Authorization: Bearer <token>`. Das Token wird bis kurz vor Ablauf
  wiederverwendet; ein 401 zwischendurch -> einmal neu anmelden.
- `GET /api/nginx/proxy-hosts`, `GET /api/nginx/certificates`,
  `POST /api/nginx/proxy-hosts/{id}/enable|disable` (-> `true`, bzw. 400 "Host is
  already enabled/disabled").

Aeltere NPM-Versionen liefern `enabled`/`ssl_forced` als 0/1 und `expires_on` als
"YYYY-MM-DD HH:MM:SS" statt ISO -- beides wird hier toleriert.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .transport import (
    DEFAULT_TIMEOUT_S,
    HttpLike,
    ServiceError,
    as_bool,
    as_int,
    error_message,
    json_body,
    redirect_message,
    send,
)

SERVICE = "Nginx Proxy Manager"

WARN_DAYS = 14
LOGIN_RETRY_AFTER_S = 30.0
TOKEN_MARGIN_S = 120.0
"""Token so lange vor dem Ablauf schon erneuern -- sonst laeuft es mitten in einem
Aufruf ab."""


class NpmError(ServiceError):
    state = "unreachable"


class NpmAuthError(NpmError):
    state = "auth_failed"


PasswordGetter = Callable[[], Awaitable[str | None]]


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if len(text) >= 19 and text[10] == " ":
        text = text[:10] + "T" + text[11:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


CERT_STATUS_LABEL = {"ok": "gültig", "warn": "läuft bald ab", "expired": "abgelaufen", "unknown": "unbekannt"}
CERT_STATUS_TONE = {"ok": "good", "warn": "warn", "expired": "danger", "unknown": "neutral"}


def certificate_state(expires_at: datetime | None, now: datetime, *, warn_days: int = WARN_DAYS) -> tuple[str, int | None]:
    """(`ok`|`warn`|`expired`|`unknown`, volle Tage bis zum Ablauf -- negativ, wenn
    schon abgelaufen)."""
    if expires_at is None:
        return "unknown", None
    seconds = (expires_at - now).total_seconds()
    days = math.floor(seconds / 86400)
    if seconds <= 0:
        return "expired", days
    if seconds <= warn_days * 86400:
        return "warn", days
    return "ok", days


def days_text(status: str, days: int | None) -> str:
    if days is None:
        return "Ablaufdatum unbekannt"
    if status == "expired":
        ago = -days - 1 if days < 0 else 0
        if ago <= 0:
            return "heute abgelaufen"
        return f"seit {ago} Tag{'en' if ago != 1 else ''} abgelaufen"
    if days <= 0:
        return "läuft heute ab"
    return f"noch {days} Tag{'e' if days != 1 else ''}"


def _domains(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(d) for d in raw if isinstance(d, (str, int)) and str(d).strip()]
    if isinstance(raw, str) and raw.strip():
        return [d.strip() for d in raw.split(",") if d.strip()]
    return []


def parse_certificate(raw: Any, now: datetime) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    cert_id = as_int(raw.get("id"))
    if cert_id is None:
        return None
    domains = _domains(raw.get("domain_names"))
    expires_at = parse_datetime(raw.get("expires_on"))
    status, days = certificate_state(expires_at, now)
    provider = str(raw.get("provider") or "")
    return {
        "id": cert_id,
        "name": str(raw.get("nice_name") or "").strip() or (domains[0] if domains else f"Zertifikat {cert_id}"),
        "domains": domains,
        "provider": provider,
        "provider_label": "Let's Encrypt" if provider == "letsencrypt" else "eigenes Zertifikat" if provider == "other" else provider,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "days_left": days,
        "status": status,
        "status_label": CERT_STATUS_LABEL[status],
        "days_text": days_text(status, days),
    }


def parse_proxy_host(raw: Any, certificates: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    host_id = as_int(raw.get("id"))
    if host_id is None:
        return None
    scheme = str(raw.get("forward_scheme") or "http")
    forward_host = str(raw.get("forward_host") or "").strip()
    port = as_int(raw.get("forward_port"))
    target = f"{scheme}://{forward_host}{':' + str(port) if port else ''}" if forward_host else None
    cert_id = as_int(raw.get("certificate_id"))
    cert_id = cert_id if cert_id and cert_id > 0 else None
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    online = meta.get("nginx_online")
    return {
        "id": host_id,
        "domains": _domains(raw.get("domain_names")),
        "target": target,
        "enabled": as_bool(raw.get("enabled")),
        "ssl_forced": as_bool(raw.get("ssl_forced")),
        "certificate_id": cert_id,
        "certificate": certificates.get(cert_id) if cert_id else None,
        "nginx_online": online if isinstance(online, bool) else None,
        "nginx_error": str(meta.get("nginx_err") or "") or None,
    }


class NpmClient:
    def __init__(
        self,
        http: HttpLike,
        *,
        base_url: str,
        identity: str,
        password: PasswordGetter,
        insecure_tls: bool = False,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._identity = identity
        self._password = password
        self._insecure_tls = insecure_tls
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._login_error: NpmError | None = None
        self._login_failed_at: float | None = None

    @property
    def base_url(self) -> str:
        return self._base

    async def _send(self, method: str, path: str, *, token: str | None = None, json: Any = None) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        kwargs: dict[str, Any] = {"headers": headers}
        if json is not None:
            kwargs["json"] = json
        return await send(
            self._http, method, f"{self._base}{path}", service=SERVICE, error_cls=NpmError,
            insecure_tls=self._insecure_tls, timeout_s=self._timeout_s, **kwargs,
        )

    async def _login(self) -> None:
        """Muss unter `self._lock` laufen."""
        now = self._clock()
        if self._login_error is not None and self._login_failed_at is not None and now - self._login_failed_at < LOGIN_RETRY_AFTER_S:
            raise self._login_error
        try:
            await self._do_login()
        except NpmAuthError as exc:
            self._login_error, self._login_failed_at = exc, now
            raise
        self._login_error = self._login_failed_at = None

    async def _do_login(self) -> None:
        password = await self._password()
        if not password:
            raise NpmAuthError("Für den Nginx Proxy Manager fehlt noch das Passwort (Einstellungen → Erweiterungen → Netzwerk).")
        response = await self._send("POST", "/api/tokens", json={"identity": self._identity, "secret": password})
        body = json_body(response)
        redirect = redirect_message(response, SERVICE)
        if redirect:
            raise NpmError(redirect)
        if response.status_code == 404 or (response.status_code < 400 and not isinstance(body, dict)):
            raise NpmError(
                "Unter dieser Adresse antwortet keine Nginx-Proxy-Manager-Schnittstelle. Gemeint ist die "
                "Verwaltungsoberfläche, meist Port 81."
            )
        if response.status_code in (400, 401, 403):
            raise NpmAuthError("Nginx Proxy Manager hat die Anmeldung abgelehnt – bitte E-Mail-Adresse und Passwort prüfen.")
        if response.status_code >= 400:
            detail = error_message(response)
            raise NpmError(f"Anmeldung beim Nginx Proxy Manager fehlgeschlagen (HTTP {response.status_code}{': ' + detail if detail else ''}).")
        if body.get("requires_2fa"):
            raise NpmAuthError(
                "Für dieses Konto ist die Zwei-Faktor-Anmeldung aktiv. Das wird noch nicht unterstützt – bitte im "
                "Nginx Proxy Manager ein eigenes Konto ohne Zwei-Faktor-Anmeldung für das Dashboard anlegen."
            )
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise NpmError("Nginx Proxy Manager hat kein Anmelde-Token geliefert.")
        expires = parse_datetime(body.get("expires"))
        self._token = token
        self._expires_at = expires.timestamp() if expires else self._clock() + 3600

    async def _current_token(self) -> str:
        async with self._lock:
            if self._token is None or self._expires_at - TOKEN_MARGIN_S <= self._clock():
                self._token = None
                await self._login()
            assert self._token is not None
            return self._token

    async def _api(self, method: str, path: str) -> Any:
        token = await self._current_token()
        response = await self._send(method, path, token=token)
        if response.status_code == 401:
            async with self._lock:
                if self._token == token:
                    self._token = None
                if self._token is None:
                    await self._login()
                token = self._token
            response = await self._send(method, path, token=token)
            if response.status_code == 401:
                async with self._lock:
                    self._token = None
                raise NpmAuthError("Nginx Proxy Manager lehnt die Anmeldung ab – bitte Zugangsdaten prüfen.")
        return response

    async def _api_json(self, method: str, path: str) -> Any:
        response = await self._api(method, path)
        if response.status_code >= 400:
            detail = error_message(response)
            raise NpmError(f"Nginx Proxy Manager meldet einen Fehler (HTTP {response.status_code}{': ' + detail if detail else ''}).")
        body = json_body(response)
        if body is None:
            raise NpmError("Nginx Proxy Manager hat keine gültige Antwort geschickt.")
        return body

    async def certificates(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        body = await self._api_json("GET", "/api/nginx/certificates")
        if not isinstance(body, list):
            raise NpmError("Nginx Proxy Manager hat keine Zertifikatsliste geliefert.")
        moment = now or datetime.now(timezone.utc)
        return [c for c in (parse_certificate(raw, moment) for raw in body) if c is not None]

    async def proxy_hosts(self, certificates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        body = await self._api_json("GET", "/api/nginx/proxy-hosts")
        if not isinstance(body, list):
            raise NpmError("Nginx Proxy Manager hat keine Liste der Proxy-Hosts geliefert.")
        by_id = {c["id"]: c for c in certificates or []}
        return [h for h in (parse_proxy_host(raw, by_id) for raw in body) if h is not None]

    async def set_host_enabled(self, host_id: int, enabled: bool) -> bool:
        """`True`, wenn sich etwas geaendert hat; `False`, wenn der Host schon so war."""
        verb = "enable" if enabled else "disable"
        response = await self._api("POST", f"/api/nginx/proxy-hosts/{int(host_id)}/{verb}")
        if response.status_code == 400 and "already" in (error_message(response) or "").lower():
            return False
        if response.status_code == 404:
            raise NpmError(f"Proxy-Host {host_id} gibt es im Nginx Proxy Manager nicht (mehr).")
        if response.status_code >= 400:
            detail = error_message(response)
            raise NpmError(f"Nginx Proxy Manager meldet einen Fehler (HTTP {response.status_code}{': ' + detail if detail else ''}).")
        return True

    def forget(self) -> None:
        """NPM kennt kein Abmelden -- das Token einfach verwerfen."""
        self._token = None
        self._expires_at = 0.0
