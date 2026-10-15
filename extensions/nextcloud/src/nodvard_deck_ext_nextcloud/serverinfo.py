"""Fragt die Kennzahlen der Nextcloud-App „Server-Info“ ab (nur lesend) und liest sie defensiv."""

from __future__ import annotations

import asyncio
import json
import math
import re
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from nodvard_sdk.errors import NodvardError

from .connector import _status_problem, _transport_problem, redirect_text

INFO_PATH = "/ocs/v2.php/apps/serverinfo/api/v1/info"
# skipApps und skipUpdate stehen seit Nextcloud 28 standardmäßig auf true, dann fehlen `apps` und `update`.
INFO_QUERY = "format=json&skipApps=false&skipUpdate=false"

CONNECT_TIMEOUT_S = 10.0
# Die App rechnet alles vor dem ersten Byte (und fragt den App-Store); nach Fehlversuchen bremst Nextcloud bis 25 s.
READ_TIMEOUT_S = 30.0
# Gesamtfrist: ein Server, der Byte für Byte tropft, hielte sonst ewig.
TOTAL_TIMEOUT_S = 35.0
# Die echte Antwort ist wenige KB groß.
MAX_BYTES = 2 * 1024 * 1024

_MAX_NUMBER = 2**63


class HttpLike(Protocol):
    def stream(
        self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any
    ) -> AbstractAsyncContextManager[Any]: ...


class ServerInfoError(Exception):
    """`state` ist der Maschinenwert für Seite und Kachel, `str(exc)` ein deutscher Satz."""

    state = "error"


class Unreachable(ServerInfoError):
    state = "unreachable"


class AuthFailed(ServerInfoError):
    state = "auth_failed"


class AppInactive(ServerInfoError):
    state = "app_inactive"


AUTH_TEXT = (
    "Kennzahlen gibt es nur für Admin-Konten. Trage entweder das App-Passwort eines Admin-Kontos ein oder lege in "
    "Nextcloud ein Token für „Server-Info“ an und trage es hier ein. Danach „Verbindung testen“ drücken, sonst "
    "fragt die Kachel erst in einer halben Stunde wieder."
)
APP_INACTIVE_TEXT = (
    "Die App „Server-Info“ (in Nextcloud „Monitoring“ genannt) ist in der Nextcloud nicht aktiv. Schalte sie unter "
    "Apps ein oder prüfe die Adresse in den Einstellungen."
)
THROTTLED_TEXT = (
    "Nextcloud bremst Anfragen von diesem Rechner gerade (HTTP 429), meist nach mehreren fehlgeschlagenen "
    "Anmeldungen. Prüfe die Zugangsdaten in den Einstellungen; nach einigen Minuten klappt es wieder."
)
UNEXPECTED_PREFIX = "Unerwartete Antwort der Nextcloud: "
CHECK_ADDRESS = "Prüfe, ob die Adresse wirklich zur Nextcloud führt (die Adresse der Nextcloud, ohne /ocs oder /remote.php)."


class _TooLarge(Exception):
    pass


async def _read_limited(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise _TooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _unreachable_problem(exc: BaseException) -> ServerInfoError:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text.lower():
        return ServerInfoError(
            "Das Zertifikat der Nextcloud wurde nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den "
            "Einstellungen „Selbstsigniertes Zertifikat erlauben“ einschalten."
        )
    return Unreachable("Nextcloud ist nicht erreichbar. Prüfe die Adresse in den Einstellungen und ob der Server läuft.")


async def fetch(
    http: HttpLike,
    base_url: str,
    *,
    token: str | None = None,
    auth: tuple[str, str] | None = None,
    insecure_tls: bool = False,
    total_timeout_s: float = TOTAL_TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Holt `ocs.data` der App; wirft nur `ServerInfoError`."""
    url = f"{base_url.rstrip('/')}{INFO_PATH}?{INFO_QUERY}"
    headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
    kwargs: dict[str, Any] = {}
    # Mit Token nur der Header, ohne Basic-Anmeldung. Basic geht bei der App nur für Admin-Konten, sonst 401.
    if token:
        headers["NC-Token"] = token
    elif auth is not None:
        kwargs["auth"] = auth
    problem: ServerInfoError
    try:
        async with asyncio.timeout(total_timeout_s):
            async with http.stream(
                "GET",
                url,
                insecure_tls=insecure_tls,
                timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                headers=headers,
                **kwargs,
            ) as response:
                status = response.status_code
                if 300 <= status < 400:
                    problem = ServerInfoError(redirect_text(status))
                else:
                    body = await _read_limited(response, max_bytes)
                    return _interpret(status, body)
    except _TooLarge:
        problem = ServerInfoError(
            f"{UNEXPECTED_PREFIX}Sie ist ungewöhnlich groß (mehr als {max_bytes // (1024 * 1024)} MB) und wird nicht "
            f"gelesen. {CHECK_ADDRESS}"
        )
    except ServerInfoError:
        raise
    except NodvardError:
        # z. B. PermissionDenied von ctx.http (Ziel oder TLS-Ausnahme nicht erlaubt).
        problem = ServerInfoError("Die Verbindung zur Nextcloud ist für diese Erweiterung nicht erlaubt.")
    except (TimeoutError, httpx.TimeoutException):
        # Die Gesamtfrist (`asyncio.timeout`) oder ein Limit von httpx.
        problem = Unreachable(_transport_problem("GET", INFO_PATH, url, TimeoutError()))
    except (httpx.ProtocolError, httpx.InvalidURL, ValueError) as exc:
        # Kaputte Antwort, unbrauchbare Weiterleitung oder unbrauchbare eigene Adresse: fester Satz, nie ihr Text.
        problem = ServerInfoError(_transport_problem("GET", INFO_PATH, url, exc))
    except (httpx.HTTPError, OSError) as exc:
        problem = _unreachable_problem(exc)
    # Außerhalb der `except`-Blöcke, sonst hinge die httpx-Ausnahme (mit Text der fremden Antwort) als `__context__` dran.
    raise problem


_INVALID = object()


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        # Kaputtes oder absichtlich tief verschachteltes JSON, oder eine HTML-Seite; der Ausnahmetext könnte die
        # fremde Antwort zitieren und bleibt darum draußen.
        return _INVALID


def _interpret(status: int, body: bytes) -> dict[str, Any]:
    """Macht aus Statuscode und Körper der Antwort `ocs.data` oder wirft ein `ServerInfoError`."""
    if status in (401, 403):
        raise AuthFailed(AUTH_TEXT)
    # Eine abgeschaltete App antwortet mit 404 (OCS-Status 998).
    if status == 404:
        raise AppInactive(APP_INACTIVE_TEXT)
    if status == 429:
        raise ServerInfoError(THROTTLED_TEXT)
    if status >= 500:
        raise ServerInfoError(_status_problem("GET", INFO_PATH, status) or f"Die Nextcloud meldet einen Fehler (HTTP {status}).")
    if status != 200:
        raise ServerInfoError(f"Nextcloud hat die Abfrage der Kennzahlen abgelehnt (HTTP {status}).")
    document = _json(body)
    if document is _INVALID:
        raise ServerInfoError(f"{UNEXPECTED_PREFIX}Sie ist kein gültiges JSON. {CHECK_ADDRESS}")
    ocs = document.get("ocs") if isinstance(document, dict) else None
    if not isinstance(ocs, dict):
        raise ServerInfoError(f"{UNEXPECTED_PREFIX}Sie hat nicht das erwartete Format. {CHECK_ADDRESS}")
    meta = ocs.get("meta")
    code = meta.get("statuscode") if isinstance(meta, dict) else None
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        raise ServerInfoError(f"{UNEXPECTED_PREFIX}Sie hat nicht das erwartete Format. {CHECK_ADDRESS}")
    if code is not None and code not in (100, 200):
        # OCS-Status der Nextcloud: 997 = nicht angemeldet, 998 = es gibt keine solche Adresse (App aus).
        if code in (997, 401, 403):
            raise AuthFailed(AUTH_TEXT)
        if code in (998, 404):
            raise AppInactive(APP_INACTIVE_TEXT)
        if code in (996, 999):
            raise ServerInfoError("Die Nextcloud meldet einen Fehler bei der Abfrage der Kennzahlen. Schau in ihr Protokoll.")
        raise ServerInfoError(f"{UNEXPECTED_PREFIX}Sie hat nicht das erwartete Format. {CHECK_ADDRESS}")
    data = ocs.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("nextcloud"), dict):
        raise ServerInfoError(f"{UNEXPECTED_PREFIX}Sie hat nicht das erwartete Format. {CHECK_ADDRESS}")
    return data


def _dig(data: Any, *path: str) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _count(value: Any) -> int | None:
    """Ganze Zahl ab 0, sonst `None` (auch -1 für „unbekannt“)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_NUMBER else None
    if isinstance(value, float) and math.isfinite(value) and 0 <= value <= _MAX_NUMBER and value == int(value):
        return int(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


_VERSION = re.compile(r"[0-9][0-9A-Za-z .+_-]{0,38}")


def _version(value: Any) -> str | None:
    """Versionsnummer wie "30.0.4.1"; sie kommt vom fremden Server, darum nur ein kurzer Text aus harmlosen Zeichen."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _VERSION.fullmatch(text) else None


def _time(value: Any) -> datetime | None:
    seconds = _count(value)
    if not seconds:  # 0 heißt: noch nie
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _load(value: Any) -> tuple[float, float, float] | None:
    # Ist die Last nicht lesbar, steht dort "N/A" (bis stable31) oder false (ab stable32).
    if not isinstance(value, list) or len(value) < 3:
        return None
    first = [_number(v) for v in value[:3]]
    if any(v is None for v in first):
        return None
    return first[0], first[1], first[2]  # type: ignore[return-value]


def _kib_to_bytes(value: Any) -> int | None:
    # Die App liefert mem_total und mem_free in KiB, nicht in Bytes.
    count = _count(value)
    return count * 1024 if count is not None else None


def parse_info(data: dict[str, Any]) -> dict[str, Any]:
    """Macht aus `ocs.data` die Felder für `models.ServerInfo` (ohne `state`, `message`, `auth`); wirft nie."""
    # Feldnamen und Einheiten: github.com/nextcloud/serverinfo, `lib/SystemStatistics.php` (system),
    # `StorageStatistics.php` (storage), `SessionStatistics.php` (activeUsers).
    system = _dig(data, "nextcloud", "system")
    storage = _dig(data, "nextcloud", "storage")
    active = data.get("activeUsers")

    update = _dig(system, "update")
    available = _dig(update, "available")
    core_available = available if isinstance(available, bool) else None
    core_version = _version(_dig(update, "available_version")) if core_available else None

    apps = _dig(system, "apps")
    app_updates = _count(_dig(apps, "num_updates_available"))
    if app_updates is None and isinstance(_dig(apps, "app_updates"), dict):
        app_updates = len(apps["app_updates"])

    files = _count(_dig(storage, "num_files"))
    load = _load(_dig(system, "cpuload"))
    cores = _count(_dig(system, "cpunum"))  # erst ab stable30, -1 wenn unbekannt
    mem_total = _kib_to_bytes(_dig(system, "mem_total"))
    mem_available = _kib_to_bytes(_dig(system, "mem_free"))  # MemAvailable, nicht MemFree
    if not mem_total:
        mem_total = mem_available = None
    elif mem_available is not None and mem_available > mem_total:
        mem_available = None

    return {
        "version": _version(_dig(system, "version")),
        "updates": {
            "core_available": core_available,
            "core_version": core_version,
            "core_checked_at": _time(_dig(update, "lastupdatedat")),
            "app_updates": app_updates,
        },
        "storage": {
            "free_bytes": _count(_dig(system, "freespace")),
            # Ein Hintergrundjob füllt den Zähler, davor steht dort 0.
            "files": files or None,
            "users": _count(_dig(storage, "num_users")),
        },
        "active_users": {
            "last_5_minutes": _count(_dig(active, "last5minutes")),
            "last_hour": _count(_dig(active, "last1hour")),
            "last_24_hours": _count(_dig(active, "last24hours")),
        },
        "load": {
            "load_1": load[0] if load else None,
            "load_5": load[1] if load else None,
            "load_15": load[2] if load else None,
            "cpu_cores": cores or None,
            "memory_total_bytes": mem_total,
            "memory_available_bytes": mem_available,
        },
    }
