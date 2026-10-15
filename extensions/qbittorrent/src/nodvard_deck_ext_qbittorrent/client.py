"""Schlanker, nur lesender Client für die Web-API von qBittorrent (er kennt `GET` und die Anmeldung)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .models import Torrent, Transfer
from .parse import (
    MainData,
    parse_api_version,
    parse_maindata,
    parse_torrents,
    parse_transfer,
    parse_version,
    session_cookie,
)
from .transport import (
    DEFAULT_TIMEOUT_S,
    MAX_LIST_BYTES,
    MAX_SMALL_BYTES,
    MAX_SYNC_BYTES,
    TOTAL_TIMEOUT_S,
    AuthFailed,
    HttpLike,
    LoginRequired,
    QbtError,
    Reply,
    send,
)

# Die einzigen Pfade, an die etwas gesendet (POST) wird.
LOGIN_PATH = "/api/v2/auth/login"
LOGOUT_PATH = "/api/v2/auth/logout"
POST_PATHS = frozenset({LOGIN_PATH, LOGOUT_PATH})

# qBittorrent sperrt die Adresse nach mehreren falschen Anmeldungen (Vorgabe: fünf, eine Stunde lang);
# nach einer abgelehnten Anmeldung fragen wir darum erst nach dieser Pause wieder.
AUTH_PAUSE_S = 1800.0

# Einer mehr als angezeigt wird, damit man weiß, ob es noch weitere gibt.
MAX_ACTIVE = 50
ACTIVE_QUERY = f"?filter=active&sort=dlspeed&reverse=true&limit={MAX_ACTIVE + 1}"

# Ein eigener Cookie statt keinem, sonst hängt httpx einen aus dem gemeinsamen Speicher an.
_NO_SESSION = "nodvard_deck=1"

_INVALID = object()


def _json(reply: Reply) -> Any:
    try:
        return json.loads(reply.body)
    except (ValueError, RecursionError):
        # Kaputtes, absichtlich tief verschachteltes JSON oder eine HTML-Seite.
        return _INVALID


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def request_headers(base_url: str, cookie: tuple[str, str] | None) -> dict[str, str]:
    """Referer und Origin gleich der Adresse, sonst weist der CSRF-Schutz der Weboberfläche die Anfrage ab."""
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": base_url,
        "Origin": _origin(base_url),
        "Cookie": f"{cookie[0]}={cookie[1]}" if cookie else _NO_SESSION,
    }


@dataclass(frozen=True)
class Target:
    """Wohin sich eine Sitzung wieder abmelden lässt."""

    service: str
    base_url: str
    insecure_tls: bool
    pin: str | None


@dataclass
class SessionBox:
    """Die Sitzung einer Verbindung im Speicher; sie überlebt die einzelnen Abfragen."""

    cookie: tuple[str, str] | None = None
    generation: int = 0  # zählt jeden Anmeldeversuch, damit parallele Abschnitte nicht alle neu anmelden
    target: Target | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class QbittorrentClient:
    def __init__(
        self,
        http: HttpLike,
        *,
        name: str,
        base_url: str,
        username: str,
        password: str | None,
        box: SessionBox,
        insecure_tls: bool = False,
        pin: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._name = name
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._box = box
        self._insecure_tls = insecure_tls
        self._pin = pin
        self._timeout_s = timeout_s
        self._total_timeout_s = total_timeout_s
        # Scheitert die Anmeldung, wird sie in dieser Abfrage nicht noch einmal versucht (jeder Fehlversuch zählt).
        self._login_failure: tuple[type[QbtError], str] | None = None

    @property
    def service(self) -> str:
        return f"qBittorrent „{self._name}“"

    async def _send(
        self, method: str, path: str, *, cookie: tuple[str, str] | None, max_bytes: int, form: dict[str, str] | None = None
    ) -> Reply:
        if method != "GET" and (method != "POST" or path not in POST_PATHS):
            raise ValueError(f"{method} {path}")
        # Passwort und Sitzung stehen nur im Formular bzw. im Cookie, nie in einer Adresse, Meldung oder einem Log.
        return await send(
            self._http, method, f"{self._base}{path}", service=self.service, insecure_tls=self._insecure_tls, pin=self._pin,
            headers=request_headers(self._base, cookie), form=form, timeout_s=self._timeout_s,
            total_timeout_s=self._total_timeout_s, max_bytes=max_bytes,
        )

    def _target(self) -> Target:
        return Target(self.service, self._base, self._insecure_tls, self._pin)

    def _bad_format(self, what: str) -> QbtError:
        return QbtError(
            f"Die Antwort von {self.service} auf {what} hat nicht das erwartete Format. "
            "Führt die Adresse wirklich zu qBittorrent?"
        )

    def _rejected(self, status: int | None) -> AuthFailed:
        service = self.service
        code = f" (HTTP {status})" if status else ""
        head = f"{service}: Benutzername oder Passwort wurden abgelehnt{code}."
        if status == 401:
            # Ab qBittorrent 5.2 antwortet die Anmeldung so; dieselbe Antwort gibt die Prüfung des Host-Headers.
            head += " Mit diesem Status weist die Weboberfläche auch Anfragen ab, deren Host-Header sie nicht kennt."
        return AuthFailed(
            f"{head} Prüfe beides in den Einstellungen und in qBittorrent unter Optionen → WebUI. Danach „Verbindung "
            f"testen“ drücken, sonst fragt Nodvard Deck erst in {int(AUTH_PAUSE_S // 60)} Minuten wieder: qBittorrent "
            "sperrt die Adresse nach mehreren Fehlversuchen (Vorgabe: fünf)."
        )

    def _banned(self) -> AuthFailed:
        return AuthFailed(
            f"{self.service}: qBittorrent hat die Adresse des Dashboards gesperrt, weil es zu viele falsche Anmeldungen "
            "gab. Die Sperre endet nach der Zeit, die in qBittorrent unter Optionen → WebUI eingestellt ist (Vorgabe: "
            f"eine Stunde). Prüfe bis dahin Benutzername und Passwort in den Einstellungen; Nodvard Deck fragt in "
            f"{int(AUTH_PAUSE_S // 60)} Minuten wieder."
        )

    def _status_problem(self, status: int) -> QbtError:
        """Fester Satz zu einem Statuscode, der kein Erfolg ist."""
        service = self.service
        if status == 403:
            if self._password is None:
                return LoginRequired(
                    f"Für {self._name} fehlt das Passwort, und qBittorrent verlangt eine Anmeldung. Trage es in den "
                    "Einstellungen ein, oder erlaube in qBittorrent unter Optionen → WebUI die Anmeldung zu überspringen "
                    "(„Authentifizierung für Clients im Subnetz überspringen“) für das Netz des Dashboards."
                )
            return QbtError(
                f"{service} nimmt die Sitzung nicht an (HTTP 403), obwohl die Anmeldung geklappt hat. "
                "Entfernt ein Proxy davor Cookies?"
            )
        if status == 401:
            return QbtError(
                f"{service} weist die Anfrage ab (HTTP 401). Prüfe in qBittorrent unter Optionen → WebUI die "
                "Host-Header-Prüfung („Server-Domänen“) und den Schutz vor Cross-Site-Anfragen (CSRF)."
            )
        if status == 404:
            return QbtError(
                f"{service}: Unter dieser Adresse läuft kein qBittorrent, oder der Pfad stimmt nicht. "
                "Prüfe die Adresse; liegt qBittorrent hinter einem Unterpfad, gehört er mit in die Adresse."
            )
        if status == 429:
            return QbtError(f"{service} bremst Anfragen gerade (HTTP 429). Versuche es in einigen Minuten noch einmal.")
        if status in (502, 503, 504):
            return QbtError(
                f"{service} ist gerade nicht bereit (HTTP {status}). Das ist beim Start oder Neustart von qBittorrent "
                "normal; sonst kann ein Proxy davor qBittorrent nicht erreichen. Versuche es gleich noch einmal."
            )
        if status >= 500:
            return QbtError(f"{service} meldet einen Fehler (HTTP {status}). Schau in das Protokoll von qBittorrent.")
        return QbtError(f"{service} hat die Anfrage abgelehnt (HTTP {status}).")

    async def _do_login(self) -> tuple[str, str]:
        """`POST /api/v2/auth/login`; die Sitzung kommt als Cookie zurück."""
        reply = await self._send(
            "POST", LOGIN_PATH, cookie=None, max_bytes=MAX_SMALL_BYTES,
            form={"username": self._username, "password": self._password or ""},
        )
        status = reply.status_code
        answer = reply.body.decode("utf-8", errors="replace").strip()
        if status in (200, 204):
            # Bis 5.1 antwortet eine falsche Anmeldung mit 200 und „Fails.“, ab 5.2 mit 401; Erfolg ist „Ok.“ bzw. leer.
            if answer == "Fails.":
                raise self._rejected(None)
            if answer not in ("", "Ok."):
                raise self._bad_format(LOGIN_PATH)
            cookie = session_cookie(reply.cookies)
            if cookie is None:
                raise QbtError(
                    f"{self.service} hat die Anmeldung angenommen, aber keine Sitzung gesetzt. "
                    "Entfernt ein Proxy davor Cookies?"
                )
            return cookie
        if status == 401:
            raise self._rejected(401)
        if status == 403:
            # Gebannt: 403 mit dem Satz „Your IP address has been banned ...“ (authcontroller.cpp).
            raise self._banned() if "banned" in answer.lower() else self._rejected(403)
        raise self._status_problem(status)

    async def _session(self, stale: int | None = None) -> tuple[str, str] | None:
        """Die Sitzung; meldet an, wenn es keine gibt oder `stale` (der Stand vor einer abgelehnten Anfrage) noch gilt."""
        if self._password is None:
            return None
        box = self._box
        if stale is None and box.cookie is not None:
            return box.cookie
        seen = box.generation if stale is None else stale
        async with box.lock:
            if self._login_failure is not None:
                raise self._login_failure[0](self._login_failure[1])
            if box.generation != seen and box.cookie is not None:  # ein anderer Abschnitt hat sich schon neu angemeldet
                return box.cookie
            try:
                cookie = await self._do_login()
            except QbtError as exc:
                self._login_failure = (type(exc), str(exc))
                box.cookie = None
                box.generation += 1
                raise
            box.cookie, box.target = cookie, self._target()
            box.generation += 1
            return cookie

    async def _get(self, path: str, *, max_bytes: int) -> Reply:
        """GET mit der Sitzung; bei 403 (abgelaufen) wird einmal neu angemeldet."""
        cookie = await self._session()
        seen = self._box.generation
        reply = await self._send("GET", path, cookie=cookie, max_bytes=max_bytes)
        if reply.status_code == 403 and self._password is not None:
            cookie = await self._session(stale=seen)
            reply = await self._send("GET", path, cookie=cookie, max_bytes=max_bytes)
        return reply

    async def _ok(self, path: str, *, max_bytes: int) -> Reply:
        reply = await self._get(path, max_bytes=max_bytes)
        if reply.status_code != 200:
            raise self._status_problem(reply.status_code)
        return reply

    async def version(self) -> str:
        """`GET /api/v2/app/version`: meldet bei Bedarf an; wirft, wenn qBittorrent nicht antwortet oder ablehnt."""
        path = "/api/v2/app/version"
        parsed = parse_version((await self._ok(path, max_bytes=MAX_SMALL_BYTES)).body)
        if parsed is None:
            raise self._bad_format(path)
        return parsed

    async def api_version(self) -> str:
        """`GET /api/v2/app/webapiVersion`."""
        path = "/api/v2/app/webapiVersion"
        parsed = parse_api_version((await self._ok(path, max_bytes=MAX_SMALL_BYTES)).body)
        if parsed is None:
            raise self._bad_format(path)
        return parsed

    async def transfer(self) -> Transfer:
        """`GET /api/v2/transfer/info`: Geschwindigkeit, Verbindungsstatus, Tempolimit."""
        path = "/api/v2/transfer/info"
        parsed = parse_transfer(_json(await self._ok(path, max_bytes=MAX_SMALL_BYTES)))
        if parsed is None:
            raise self._bad_format(path)
        return parsed

    async def active(self) -> tuple[list[Torrent], bool]:
        """`GET /api/v2/torrents/info`: die aktiven Torrents (höchstens `MAX_ACTIVE`) und ob es noch mehr gibt."""
        path = "/api/v2/torrents/info"
        parsed = parse_torrents(_json(await self._ok(f"{path}{ACTIVE_QUERY}", max_bytes=MAX_LIST_BYTES)))
        if parsed is None:
            raise self._bad_format(path)
        parsed.sort(key=lambda t: (-(t.download_speed + t.upload_speed), t.name.casefold()))
        return parsed[:MAX_ACTIVE], len(parsed) > MAX_ACTIVE

    async def maindata(self) -> MainData:
        """`GET /api/v2/sync/maindata?rid=0`: Anzahl je Zustand und freier Platz (die Antwort enthält alle Torrents)."""
        path = "/api/v2/sync/maindata"
        parsed = parse_maindata(_json(await self._ok(f"{path}?rid=0", max_bytes=MAX_SYNC_BYTES)))
        if parsed is None:
            raise self._bad_format(path)
        return parsed


async def logout(http: HttpLike, box: SessionBox, *, timeout_s: float = 5.0) -> None:
    """Meldet die Sitzung in qBittorrent ab und vergisst sie; Fehler dabei spielen keine Rolle."""
    cookie, target = box.cookie, box.target
    box.cookie = None
    if cookie is None or target is None:
        return
    try:
        await send(
            http, "POST", f"{target.base_url}{LOGOUT_PATH}", service=target.service, insecure_tls=target.insecure_tls,
            pin=target.pin, headers=request_headers(target.base_url, cookie), timeout_s=timeout_s, total_timeout_s=timeout_s,
        )
    except QbtError:
        return
