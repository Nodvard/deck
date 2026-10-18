"""Schlanker, nur lesender Client für die Web-API von DSM: Schnittstellen finden, anmelden, fünf Teile lesen."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from .models import Load, SystemInfo, Update
from .parse import (
    ApiInfo,
    BackupStatus,
    Storage,
    TaskEntry,
    parse_backup_status,
    parse_backup_tasks,
    parse_discovery,
    parse_sid,
    parse_storage,
    parse_system_info,
    parse_update,
    parse_utilization,
)
from .transport import (
    DEFAULT_TIMEOUT_S,
    MAX_SMALL_BYTES,
    MAX_STORAGE_BYTES,
    SERVICE,
    TOTAL_TIMEOUT_S,
    AuthFailed,
    DsmError,
    HttpLike,
    NoRights,
    Reply,
    Unavailable,
    send,
)

API_INFO = "SYNO.API.Info"
API_AUTH = "SYNO.API.Auth"
API_DSM_INFO = "SYNO.DSM.Info"
API_UTILIZATION = "SYNO.Core.System.Utilization"
API_STORAGE = "SYNO.Storage.CGI.Storage"
API_UPGRADE = "SYNO.Core.Upgrade.Server"
API_BACKUP = "SYNO.Backup.Task"

QUERY_PATH = "query.cgi"
WEBAPI = "/webapi/"
SESSION_NAME = "NodvardDeck"

# Versionen, die wir sprechen (von, bis). DSM 7.1 meldet etwa Auth 1–7, DSM.Info 2–2, Upgrade.Server 1–4.
VERSIONS = {
    API_AUTH: (2, 6),
    API_DSM_INFO: (1, 2),
    API_UTILIZATION: (1, 1),
    API_STORAGE: (1, 1),
    API_UPGRADE: (1, 4),
    API_BACKUP: (1, 1),
}
QUERIED_APIS = (API_AUTH, API_DSM_INFO, API_UTILIZATION, API_STORAGE, API_UPGRADE, API_BACKUP)

# Zeiten und Ergebnis des letzten Laufs liefert Hyper Backup nur auf Nachfrage.
BACKUP_FIELDS = '["last_bkp_time","next_bkp_time","last_bkp_result","is_modified","last_bkp_progress"]'
# Frist für die Stände aller Aufgaben zusammen; sie bleibt deutlich unter der Frist der ganzen Abfrage, damit Speicher,
# System und Update auch bei vielen Aufgaben ankommen.
BACKUP_TIMEOUT_S = 25.0

# Die Sitzung ist abgelaufen (106), durch eine andere Anmeldung beendet (107) oder DSM kennt sie nicht (119).
SESSION_ERRORS = frozenset({106, 107, 119})
UNKNOWN_API_ERRORS = frozenset({102, 103, 104})

# DSM sperrt die Adresse nach mehreren falschen Anmeldungen automatisch; nach einer abgelehnten Anmeldung fragen wir
# darum erst nach dieser Pause wieder.
AUTH_PAUSE_S = 1800.0

_INVALID = object()


def _json(reply: Reply) -> Any:
    try:
        return json.loads(reply.body)
    except (ValueError, RecursionError):
        # Kaputtes, absichtlich tief verschachteltes JSON oder eine HTML-Seite.
        return _INVALID


def _error_code(body: dict[str, Any]) -> int:
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) and 0 <= code <= 99999 else 0


def _pause_hint() -> str:
    return (
        f"Danach „Verbindung testen“ drücken, sonst fragt Nodvard Deck erst in {int(AUTH_PAUSE_S // 60)} Minuten wieder: "
        "DSM sperrt Adressen nach mehreren Fehlversuchen automatisch."
    )


def _two_factor() -> str:
    return (
        "Dieses Konto verlangt Zwei-Faktor; bitte ein eigenes Konto ohne Zwei-Faktor für Nodvard Deck anlegen "
        "(in der Systemsteuerung von DSM) und es in den Einstellungen eintragen."
    )


_EXPIRED = "Das Passwort ist abgelaufen oder muss geändert werden. Ändere es in DSM und trage das neue in den Einstellungen ein."

# Fehlercode der Anmeldung -> fester Satz (`SYNO.API.Auth`).
LOGIN_ERRORS: dict[int, str] = {
    400: f"Benutzername oder Passwort stimmt nicht. Prüfe beides in den Einstellungen. {_pause_hint()}",
    401: "Das Konto ist deaktiviert. Aktiviere es in DSM oder trage ein anderes Konto in den Einstellungen ein.",
    402: "Das Konto darf sich nicht anmelden. Prüfe in DSM die Rechte des Kontos oder trage ein anderes ein.",
    403: _two_factor(),
    404: f"Zwei-Faktor-Code abgelehnt. {_two_factor()}",
    406: _two_factor(),
    407: (
        "Die Adresse von Nodvard Deck ist in DSM gesperrt (automatische Sperre). Hebe die Sperre in DSM auf "
        "(Systemsteuerung → Sicherheit) und erlaube die Adresse des Dashboards dort. Bis dahin fragt Nodvard Deck "
        f"nur alle {int(AUTH_PAUSE_S // 60)} Minuten."
    ),
    408: _EXPIRED,
    409: _EXPIRED,
    410: _EXPIRED,
}

# Wofür ein Teil Rechte braucht, im Satz „Für <…> braucht das Konto Administratorrechte“.
NEEDS_ADMIN = {
    API_STORAGE: "Speicher",
    API_UPGRADE: "Updates",
    API_UTILIZATION: "die Auslastung",
    API_DSM_INFO: "diese Angabe",
    API_BACKUP: "Hyper Backup",
}


def pick_version(api: str, info: ApiInfo) -> int | None:
    """Die höchste Version, die wir und DSM sprechen; `None`, wenn DSM nur ältere kennt.

    Kennt DSM nur neuere, nehmen wir seine älteste: Die Antworten dieser Lese-Aufrufe sind bisher gleich geblieben."""
    low, high = VERSIONS[api]
    version = min(info.max_version, high)
    version = max(version, info.min_version)
    return version if low <= version <= info.max_version else None


@dataclass(frozen=True)
class Target:
    """Wohin sich eine Sitzung wieder abmelden lässt."""

    base_url: str
    insecure_tls: bool
    auth: ApiInfo | None
    pin: str | None = None


@dataclass
class SessionBox:
    """Sitzung und Schnittstellenliste im Speicher; sie überleben die einzelnen Abfragen."""

    sid: str | None = field(default=None, repr=False)
    apis: dict[str, ApiInfo] | None = None
    generation: int = 0  # zählt jeden Anmeldeversuch, damit parallele Teile nicht alle neu anmelden
    target: Target | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SynologyClient:
    def __init__(
        self,
        http: HttpLike,
        *,
        base_url: str,
        username: str,
        password: str,
        box: SessionBox,
        insecure_tls: bool = False,
        pin: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._box = box
        self._insecure_tls = insecure_tls
        self._pin = pin
        self._timeout_s = timeout_s
        self._total_timeout_s = total_timeout_s
        # Scheitert die Anmeldung, wird sie in dieser Abfrage nicht noch einmal versucht (jeder Fehlversuch zählt).
        self._login_failure: DsmError | None = None

    # --- Senden

    async def _send(
        self, method: str, path: str, params: dict[str, str] | None = None, *, form: dict[str, str] | None = None,
        max_bytes: int = MAX_SMALL_BYTES,
    ) -> Reply:
        if method not in ("GET", "POST") or not path.startswith(WEBAPI) or (method == "POST" and form is None):
            raise ValueError(f"{method} {path}")
        # Das Passwort steht nur im Formular einer Anmeldung (POST), nie in einer Adresse, Meldung oder einem Log; die
        # Sitzung steht bei DSM in der Adresse und darum nur hier und im (gefilterten) Log von httpx.
        query = f"?{urlencode(params, safe=',')}" if params else ""
        return await send(
            self._http, method, f"{self._base}{path}{query}", insecure_tls=self._insecure_tls, pin=self._pin, form=form,
            timeout_s=self._timeout_s, total_timeout_s=self._total_timeout_s, max_bytes=max_bytes,
        )

    @staticmethod
    def _bad_format(what: str) -> DsmError:
        return DsmError(
            f"Die Antwort von {SERVICE} auf {what} hat nicht das erwartete Format. "
            "Führt die Adresse wirklich zu DSM (Port 5000 für http, 5001 für https)?"
        )

    @staticmethod
    def _status_problem(status: int) -> DsmError:
        """Fester Satz zu einem Statuscode, der kein Erfolg ist."""
        if status == 404:
            return DsmError(
                f"Unter dieser Adresse läuft kein {SERVICE}, oder der Pfad stimmt nicht. Prüfe die Adresse; liegt DSM "
                "hinter einem Unterpfad, gehört er mit in die Adresse."
            )
        if status == 429:
            return DsmError(f"{SERVICE} bremst Anfragen gerade (HTTP 429). Versuche es in einigen Minuten noch einmal.")
        if status in (502, 503, 504):
            return DsmError(
                f"{SERVICE} ist gerade nicht bereit (HTTP {status}). Das ist beim Start oder Neustart normal; sonst kann "
                "ein Proxy davor DSM nicht erreichen. Versuche es gleich noch einmal."
            )
        if status >= 500:
            return DsmError(f"{SERVICE} meldet einen Fehler (HTTP {status}). Schau in das Protokoll von DSM.")
        return DsmError(f"{SERVICE} hat die Anfrage abgelehnt (HTTP {status}).")

    async def _answer(self, reply: Reply, what: str) -> tuple[Any, int | None]:
        """(`data`, None) bei Erfolg, (None, Fehlercode) bei einem Fehler von DSM; wirft bei einer Antwort, die keine ist."""
        if reply.status_code != 200:
            raise self._status_problem(reply.status_code)
        body = _json(reply)
        if not isinstance(body, dict) or not isinstance(body.get("success"), bool):
            raise self._bad_format(what)
        if body["success"]:
            return body.get("data"), None
        return None, _error_code(body)

    # --- Schnittstellen finden

    async def discover(self) -> dict[str, ApiInfo]:
        """`SYNO.API.Info` `query`: Pfad und Versionen der Schnittstellen (einmal je Sitzung)."""
        if self._box.apis is not None:
            return self._box.apis
        params = {"api": API_INFO, "version": "1", "method": "query", "query": ",".join(QUERIED_APIS)}
        reply = await self._send("GET", f"{WEBAPI}{QUERY_PATH}", params)
        data, code = await self._answer(reply, "die Frage nach den Schnittstellen")
        apis = parse_discovery(data) if code is None else None
        if apis is None:
            if code is not None:
                raise DsmError(f"{SERVICE} hat die Frage nach den Schnittstellen abgelehnt (Fehler {code}).")
            raise self._bad_format("die Frage nach den Schnittstellen")
        if API_AUTH not in apis:
            raise DsmError(
                f"Unter dieser Adresse antwortet etwas, aber es ist kein {SERVICE}: Die Anmelde-Schnittstelle fehlt. "
                "Prüfe die Adresse in den Einstellungen."
            )
        self._box.apis = apis
        return apis

    def _version_of(self, api: str) -> tuple[ApiInfo, int]:
        """Pfad und Version für `api`; `Unavailable`, wenn dieses DSM die Schnittstelle oder eine passende Version nicht hat."""
        info = (self._box.apis or {}).get(api)
        version = None if info is None else pick_version(api, info)
        if info is None or version is None:
            raise Unavailable("Auf diesem DSM nicht verfügbar.")
        return info, version

    # --- Anmelden

    def _login_problem(self, code: int) -> DsmError:
        known = LOGIN_ERRORS.get(code)
        if known is not None:
            return AuthFailed(known)
        if code in UNKNOWN_API_ERRORS:
            return DsmError(f"{SERVICE} kennt die Anmeldung in dieser Form nicht (Fehler {code}). Ist die Version von DSM sehr alt?")
        # Auch eine unbekannte Ablehnung kann bei DSM als Fehlversuch zählen; darum gilt dieselbe Pause.
        return AuthFailed(f"{SERVICE} hat die Anmeldung mit Fehler {code} abgelehnt. {_pause_hint()}")

    async def _do_login(self) -> str:
        """`SYNO.API.Auth` `login` als POST; die Sitzungs-ID kommt als `data.sid` zurück."""
        try:
            info, version = self._version_of(API_AUTH)
        except Unavailable:
            raise DsmError(f"Die Anmeldung wird von diesem {SERVICE} nicht unterstützt.") from None
        form = {
            "api": API_AUTH, "version": str(version), "method": "login", "account": self._username,
            "passwd": self._password, "session": SESSION_NAME, "format": "sid",
        }
        reply = await self._send("POST", f"{WEBAPI}{info.path}", form=form)
        data, code = await self._answer(reply, "die Anmeldung")
        if code is not None:
            raise self._login_problem(code)
        sid = parse_sid(data)
        if sid is None:
            raise self._bad_format("die Anmeldung")
        return sid

    async def connect(self) -> None:
        """Schnittstellen finden und anmelden, falls es noch keine Sitzung gibt; wirft `DsmError`."""
        await self.discover()
        await self._session()

    async def _session(self, stale: int | None = None) -> str:
        """Die Sitzung; meldet an, wenn es keine gibt oder `stale` (der Stand vor einer abgelehnten Anfrage) noch gilt."""
        box = self._box
        if stale is None and box.sid is not None:
            return box.sid
        seen = box.generation if stale is None else stale
        async with box.lock:
            if self._login_failure is not None:
                raise type(self._login_failure)(str(self._login_failure))
            if box.generation != seen and box.sid is not None:  # ein anderer Teil hat sich schon neu angemeldet
                return box.sid
            try:
                sid = await self._do_login()
            except DsmError as exc:
                self._login_failure = exc
                box.sid = None
                box.generation += 1
                raise
            box.sid, box.generation = sid, box.generation + 1
            box.target = Target(self._base, self._insecure_tls, (box.apis or {}).get(API_AUTH), self._pin)
            return sid

    # --- Lesen

    async def _call(
        self, api: str, method: str, extra: dict[str, str] | None = None, *, max_bytes: int = MAX_SMALL_BYTES,
    ) -> Any:
        """Ein Lese-Aufruf mit der Sitzung; bei 106/107/119 wird einmal neu angemeldet und einmal wiederholt."""
        info, version = self._version_of(api)
        params = {"api": api, "version": str(version), "method": method, **(extra or {})}
        sid = await self._session()
        seen = self._box.generation
        reply = await self._send("GET", f"{WEBAPI}{info.path}", {**params, "_sid": sid}, max_bytes=max_bytes)
        data, code = await self._answer(reply, method)
        if code in SESSION_ERRORS:
            sid = await self._session(stale=seen)
            reply = await self._send("GET", f"{WEBAPI}{info.path}", {**params, "_sid": sid}, max_bytes=max_bytes)
            data, code = await self._answer(reply, method)
            if code in SESSION_ERRORS:
                raise DsmError(f"{SERVICE} nimmt die Sitzung nicht an (Fehler {code}), obwohl die Anmeldung geklappt hat.")
        if code is None:
            return data
        if code == 105:
            raise NoRights(
                f"Diesem Konto fehlen Rechte. Für {NEEDS_ADMIN[api]} braucht das Konto Administratorrechte "
                "(Mitglied der Gruppe „administrators“)."
            )
        if code in UNKNOWN_API_ERRORS:
            raise Unavailable("Auf diesem DSM nicht verfügbar.")
        raise DsmError(f"{SERVICE} hat die Anfrage abgelehnt (Fehler {code}).")

    async def system_info(self) -> SystemInfo:
        """`SYNO.DSM.Info` `getinfo`: Modell, DSM-Version, Laufzeit, Temperatur, RAM."""
        parsed = parse_system_info(await self._call(API_DSM_INFO, "getinfo"))
        if parsed is None:
            raise self._bad_format("die Frage nach dem Gerät")
        return parsed

    async def load(self) -> Load:
        """`SYNO.Core.System.Utilization` `get`: CPU und RAM in Prozent."""
        parsed = parse_utilization(await self._call(API_UTILIZATION, "get"))
        if parsed is None:
            raise self._bad_format("die Frage nach der Auslastung")
        return parsed

    async def storage(self) -> Storage:
        """`SYNO.Storage.CGI.Storage` `load_info`: Speicherpools, Volumes und Festplatten."""
        parsed = parse_storage(await self._call(API_STORAGE, "load_info", max_bytes=MAX_STORAGE_BYTES))
        if parsed is None:
            raise self._bad_format("die Frage nach dem Speicher")
        return parsed

    async def update(self) -> Update:
        """`SYNO.Core.Upgrade.Server` `check`: ob ein DSM-Update bereitsteht."""
        parsed = parse_update(await self._call(API_UPGRADE, "check"))
        if parsed is None:
            raise self._bad_format("die Frage nach Updates")
        return parsed

    async def backup_tasks(self) -> tuple[list[tuple[TaskEntry, BackupStatus | None]], int]:
        """`SYNO.Backup.Task` `list` und je Aufgabe `status`: die Aufgaben von Hyper Backup mit dem letzten Lauf, dazu wie
        viele es insgesamt gibt. Lässt sich der Stand einer Aufgabe nicht lesen oder kommt er nicht innerhalb von
        `BACKUP_TIMEOUT_S`, steht sie mit `None` in der Liste."""
        listed = parse_backup_tasks(await self._call(API_BACKUP, "list"))
        if listed is None:
            raise self._bad_format("die Frage nach Hyper Backup")
        tasks, total = listed
        # Kleine Geräte (etwa mit Atom-Prozessor) sollen nicht alle Anfragen auf einmal bekommen.
        gate = asyncio.Semaphore(4)
        found: list[BackupStatus | None] = [None] * len(tasks)

        async def status(index: int, task: TaskEntry) -> None:
            try:
                async with gate:
                    data = await self._call(
                        API_BACKUP, "status", {"task_id": task.id, "blOnline": "false", "additional": BACKUP_FIELDS},
                    )
            except DsmError:
                return
            found[index] = parse_backup_status(data)

        try:
            async with asyncio.timeout(BACKUP_TIMEOUT_S):
                await asyncio.gather(*(status(index, task) for index, task in enumerate(tasks)))
        except TimeoutError:
            pass  # was bis dahin angekommen ist, bleibt; die übrigen Aufgaben haben keinen Stand
        return list(zip(tasks, found, strict=True)), total


async def logout(http: HttpLike, box: SessionBox, *, timeout_s: float = 5.0) -> None:
    """Meldet die Sitzung in DSM ab und vergisst sie; Fehler dabei spielen keine Rolle."""
    sid, target = box.sid, box.target
    box.sid = None
    if sid is None or target is None or target.auth is None:
        return
    version = pick_version(API_AUTH, target.auth)
    if version is None:
        return
    params = {"api": API_AUTH, "version": str(version), "method": "logout", "session": SESSION_NAME, "_sid": sid}
    try:
        await send(
            http, "GET", f"{target.base_url}{WEBAPI}{target.auth.path}?{urlencode(params)}", insecure_tls=target.insecure_tls,
            pin=target.pin, timeout_s=timeout_s, total_timeout_s=timeout_s,
        )
    except DsmError:
        return
