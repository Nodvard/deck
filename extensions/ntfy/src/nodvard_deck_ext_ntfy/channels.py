"""Weitere Kanäle für Push-Nachrichten: Telegram, Discord, Gotify, Pushover, Matrix, Slack, Mattermost und Apprise-API.

Schlank selbst gebaut, jede Anfrage über `ctx.http`; die Adressen schreibt man wie bei Apprise (`tgram://…`).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
from nodvard_sdk import Notification, Severity

# Keine Apprise-Bibliothek: Sie umginge Zielprüfung, Weiterleitungsverbot und Proxy-Regel von `ctx.http` und brächte neue
# Abhängigkeiten. Die Adressen folgen trotzdem Apprise, damit Anleitungen passen; der Rest geht über eine eigene Apprise-API.
#
# Eine Adresse trägt Zugangsdaten und liegt deshalb im Tresor (`push-kanal:<name>`). Kein Fehlertext nennt sie: Die Sätze
# sind fest, der Text einer HTTP-Ausnahme bleibt draußen (httpx und h11 zitieren die Anfrage, bei Telegram mit Token).

SECRET_PREFIX = "push-kanal"
# Wie `channels[].name` in settings.schema.json; „ntfy“ ist vergeben (der ntfy-Server selbst).
NAME_PATTERN = re.compile(r"^(?![Nn][Tt][Ff][Yy]$)[A-Za-z0-9_.-]{1,40}$")
MAX_URL = 2000
MAX_CHANNELS = 10
# Grenze je Phase einer Anfrage (Verbindungsaufbau, Lesen ...), wie httpx sie zählt.
TIMEOUT_S = 10.0
# Grenze für eine ganze Anfrage. Die Empfänger eines Kanals laufen nebeneinander, Matrix braucht höchstens zwei Runden
# (Raum-Namen auflösen, dann senden): So endet ein Kanal immer vor `CHANNEL_TIMEOUT_S`, und ein Teilerfolg geht nicht als
# Totalausfall verloren (sonst schickte ein Wiederholversuch den erreichten Empfängern doppelt).
REQUEST_TIMEOUT_S = 7.0
# Sicherheitsgrenze für einen ganzen Kanal. Die Kanäle laufen nebeneinander, so bleibt auch die Testnachricht mit acht
# toten Kanälen unter der Frist des Kerns (25 s).
CHANNEL_TIMEOUT_S = 18.0

KIND_LABELS = {
    "telegram": "Telegram",
    "discord": "Discord",
    "gotify": "Gotify",
    "pushover": "Pushover",
    "matrix": "Matrix",
    "slack": "Slack",
    "mattermost": "Mattermost",
    "apprise": "Apprise-API",
}

# Wie `default` von `channels[].min_severity` in settings.schema.json.
DEFAULT_MIN_SEVERITY = Severity.WARNING
_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
_SEVERITY_MARK = {Severity.INFO: "ℹ️", Severity.WARNING: "⚠️", Severity.CRITICAL: "🚨"}
_GOTIFY_PRIORITY = {Severity.INFO: 4, Severity.WARNING: 6, Severity.CRITICAL: 8}
_PUSHOVER_PRIORITY = {Severity.INFO: 0, Severity.WARNING: 0, Severity.CRITICAL: 1}
_APPRISE_TYPE = {Severity.INFO: "info", Severity.WARNING: "warning", Severity.CRITICAL: "failure"}

_TELEGRAM_TOKEN = re.compile(r"^\d{4,16}:[A-Za-z0-9_-]{20,100}$")
_TELEGRAM_CHAT = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{3,40})$")
_DISCORD_ID = re.compile(r"^\d{10,25}$")
_TOKENISH = re.compile(r"^[A-Za-z0-9_.~-]{4,200}$")
_PUSHOVER_KEY = re.compile(r"^[A-Za-z0-9]{20,40}$")
_PUSHOVER_DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,25}$")
_SLACK_PART = re.compile(r"^[A-Za-z0-9]{6,40}$")
_MATRIX_ROOM = re.compile(r"^[!#][^\s:/]{1,200}(:[A-Za-z0-9.-]{1,253}(:\d{1,5})?)?$")
# Mit Unterstrich: Namen von Docker-Containern im selben Netz enthalten oft einen.
_HOSTNAME = re.compile(r"^[A-Za-z0-9_]([A-Za-z0-9._-]{0,251}[A-Za-z0-9_])?$")

FORMAT_HELP = (
    "So sieht die Adresse aus: tgram://BOT-TOKEN/CHAT-ID, discord://WEBHOOK-ID/WEBHOOK-TOKEN, "
    "gotifys://SERVER/APP-TOKEN, pover://USER-KEY@APP-TOKEN, matrixs://ZUGRIFFSTOKEN@SERVER/!RAUM:SERVER, "
    "slack://T…/B…/X…, mmosts://SERVER/WEBHOOK-ID oder apprises://SERVER/SCHLÜSSEL."
)


class ChannelError(RuntimeError):
    """Fertiger deutscher Satz ohne Adresse, Token oder Antworttext (`readable` wie `NtfyError`)."""

    readable = True


@dataclass(frozen=True)
class ChannelRequest:
    """Eine Anfrage an einen Dienst. `repr=False` bei allem, was Zugangsdaten tragen kann."""

    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    json: Any = field(default=None, repr=False)
    data: Mapping[str, str] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Target:
    """Ein gelesener Kanal. `parts` hält die Angaben aus der Adresse (auch Zugangsdaten) und erscheint nie in `repr`."""

    kind: str
    parts: Mapping[str, Any] = field(repr=False)

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]


def _bad(kind: str | None = None) -> ChannelError:
    what = f"für {KIND_LABELS[kind]} " if kind else ""
    return ChannelError(f"Die Adresse des Kanals {what}ist ungültig. {FORMAT_HELP}")


def _clean(value: str) -> str:
    return "".join(ch for ch in value if not unicodedata.category(ch).startswith(("C", "Z")))


def _host_port(netloc: str, kind: str) -> tuple[str, int | None]:
    """`rechner[:port]` (auch `[IPv6]:port`) ohne Zugangsdaten; `ChannelError`, wenn es kein Rechnername ist."""
    try:
        parts = urlsplit(f"//{netloc}")
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise _bad(kind) from None
    if not host or parts.username is not None:
        raise _bad(kind)
    if ":" not in host and not _HOSTNAME.fullmatch(host):
        raise _bad(kind)
    return host, port


def _base(secure: bool, host: str, port: int | None, path: str = "") -> str:
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    return f"{'https' if secure else 'http'}://{authority}{path}"


def _segments(path: str) -> list[str]:
    return [unquote(s) for s in path.split("/") if s]


def parse_channel_url(raw: Any) -> Target:
    """Liest eine Adresse in Apprise-Schreibweise (dazu die Webhook-Adressen von Discord und Slack, wie die Dienste sie
    anzeigen). `ChannelError` mit festem Satz, wenn sie nicht passt; die Adresse selbst steht nie im Text."""
    if not isinstance(raw, str):
        raise _bad()
    text = raw.strip()
    if not text or len(text) > MAX_URL or _clean(text) != text:
        raise _bad()
    scheme, sep, rest = text.partition("://")
    if not sep:
        raise _bad()
    scheme = scheme.lower()
    rest, _, fragment = rest.partition("#")
    rest, _, _query = rest.partition("?")

    if scheme == "tgram":
        token, _, path = rest.partition("/")
        if token.startswith("bot") and _TELEGRAM_TOKEN.fullmatch(token[3:]):
            token = token[3:]  # so steht es in der Adresse der Bot-API (…/bot<TOKEN>/…)
        chats = _segments(path)
        if not _TELEGRAM_TOKEN.fullmatch(token) or not chats or len(chats) > 10:
            raise _bad("telegram")
        if not all(_TELEGRAM_CHAT.fullmatch(c) for c in chats):
            raise _bad("telegram")
        return Target("telegram", {"token": token, "chats": tuple(chats)})

    if scheme == "discord":
        segs = _segments(rest)
        if len(segs) != 2 or not _DISCORD_ID.fullmatch(segs[0]) or not _TOKENISH.fullmatch(segs[1]):
            raise _bad("discord")
        return Target("discord", {"id": segs[0], "token": segs[1]})

    if scheme in ("gotify", "gotifys", "mmost", "mmosts", "apprise", "apprises"):
        kind = {"gotify": "gotify", "mmost": "mattermost", "apprise": "apprise"}[scheme.rstrip("s")]
        netloc, _, path = rest.partition("/")
        host, port = _host_port(netloc, kind)
        segs = _segments(path)
        if not segs or not _TOKENISH.fullmatch(segs[-1]) or any(not _TOKENISH.fullmatch(s) for s in segs[:-1]):
            raise _bad(kind)
        prefix = "".join(f"/{quote(s, safe='')}" for s in segs[:-1])
        return Target(kind, {
            "secure": scheme.endswith("s"), "host": host, "port": port, "prefix": prefix, "token": segs[-1],
        })

    if scheme == "pover":
        user, at, path = rest.partition("@")
        app_token, _, devices = path.partition("/")
        device_list = _segments(devices)
        if not at or not _PUSHOVER_KEY.fullmatch(user) or not _PUSHOVER_KEY.fullmatch(app_token):
            raise _bad("pushover")
        if len(device_list) > 10 or not all(_PUSHOVER_DEVICE.fullmatch(d) for d in device_list):
            raise _bad("pushover")
        return Target("pushover", {"user": user, "token": app_token, "devices": tuple(device_list)})

    if scheme in ("matrix", "matrixs"):
        credentials, at, location = rest.rpartition("@")
        if not at:
            raise ChannelError(
                "Für Matrix braucht die Adresse ein Zugriffstoken: matrixs://ZUGRIFFSTOKEN@SERVER/!RAUM:SERVER. "
                "Benutzername und Passwort nimmt Nodvard Deck dafür nicht."
            )
        if ":" in credentials or not _TOKENISH.fullmatch(credentials):
            raise ChannelError(
                "Für Matrix nimmt Nodvard Deck nur ein Zugriffstoken (matrixs://ZUGRIFFSTOKEN@SERVER/!RAUM:SERVER), "
                "nicht Benutzername und Passwort."
            )
        netloc, _, path = location.partition("/")
        host, port = _host_port(netloc, "matrix")
        rooms = _segments(path) + ([f"#{unquote(fragment)}"] if fragment else [])
        rooms = [r if r[:1] in "!#" else f"#{r}" for r in rooms]
        if not rooms or len(rooms) > 10 or not all(_MATRIX_ROOM.fullmatch(r) for r in rooms):
            raise _bad("matrix")
        return Target("matrix", {
            "secure": scheme == "matrixs", "host": host, "port": port, "token": credentials, "rooms": tuple(rooms),
        })

    if scheme == "slack":
        segs = _segments(rest)
        if len(segs) < 3 or not all(_SLACK_PART.fullmatch(s) for s in segs[:3]):
            raise _bad("slack")
        return Target("slack", {"path": "/".join(segs[:3])})

    if scheme == "https":
        host, _, path = rest.partition("/")
        segs = _segments(path)
        if host.lower() in ("discord.com", "discordapp.com") and len(segs) == 4 and segs[:2] == ["api", "webhooks"]:
            if _DISCORD_ID.fullmatch(segs[2]) and _TOKENISH.fullmatch(segs[3]):
                return Target("discord", {"id": segs[2], "token": segs[3]})
            raise _bad("discord")
        if host.lower() == "hooks.slack.com" and len(segs) == 4 and segs[0] == "services":
            if all(_SLACK_PART.fullmatch(s) for s in segs[1:]):
                return Target("slack", {"path": "/".join(segs[1:])})
            raise _bad("slack")
    raise _bad()


def _text(notification: Notification, dashboard_url: str | None) -> str:
    mark = _SEVERITY_MARK.get(notification.severity, "")
    lines = [f"{mark} {notification.title}".strip()]
    if notification.body:
        lines.append(notification.body)
    link = open_link(notification, dashboard_url)
    if link:
        lines.append(f"Öffnen: {link}")
    return "\n\n".join(lines)


def _body(notification: Notification, dashboard_url: str | None) -> str:
    """Text ohne Titel (für Dienste mit eigenem Titelfeld), mit dem Link ins Dashboard."""
    parts = [notification.body] if notification.body else []
    link = open_link(notification, dashboard_url)
    if link:
        parts.append(f"Öffnen: {link}")
    return "\n\n".join(parts) or notification.title


def open_link(notification: Notification, dashboard_url: str | None) -> str | None:
    """Link ins Dashboard (`payload.path`, sonst die Meldungen-Seite), wenn die Adresse des Dashboards eingetragen ist."""
    if not dashboard_url:
        return None
    path = str((notification.payload or {}).get("path") or "/notifications")
    return dashboard_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")


def build_requests(target: Target, notification: Notification, dashboard_url: str | None) -> list[ChannelRequest]:
    """Die Anfragen für eine Meldung (bei Telegram je Chat eine); Matrix läuft über `matrix_requests`."""
    p = target.parts
    severity = notification.severity
    text = _text(notification, dashboard_url)
    if target.kind == "telegram":
        return [
            ChannelRequest("POST", f"https://api.telegram.org/bot{p['token']}/sendMessage",
                           json={"chat_id": chat, "text": text[:4096], "disable_web_page_preview": True})
            for chat in p["chats"]
        ]
    if target.kind == "discord":
        return [ChannelRequest(
            "POST", f"https://discord.com/api/webhooks/{p['id']}/{quote(p['token'], safe='')}?wait=true",
            # Keine Erwähnungen: Ein Text mit „@everyone“ aus einer Meldung pingt sonst den ganzen Server an.
            json={"content": text[:2000], "allowed_mentions": {"parse": []}},
        )]
    if target.kind == "gotify":
        return [ChannelRequest(
            "POST", _base(p["secure"], p["host"], p["port"], f"{p['prefix']}/message"),
            headers={"X-Gotify-Key": p["token"]},
            json={"title": notification.title[:250], "message": _body(notification, dashboard_url)[:10000],
                  "priority": _GOTIFY_PRIORITY.get(severity, 5)},
        )]
    if target.kind == "pushover":
        base = {
            "token": p["token"], "user": p["user"], "title": notification.title[:250],
            "message": (notification.body or notification.title)[:1024],  # der Link geht als `url` mit
            "priority": str(_PUSHOVER_PRIORITY.get(severity, 0)),
        }
        link = open_link(notification, dashboard_url)
        if link:
            base["url"] = link[:512]
        if p["devices"]:
            base["device"] = ",".join(p["devices"])  # eine Anfrage für alle Geräte: nie doppelt bei einem Fehler
        return [ChannelRequest("POST", "https://api.pushover.net/1/messages.json", data=base)]
    if target.kind == "matrix":
        raise ValueError("Matrix braucht die aufgeloesten Raeume (matrix_requests).")
    if target.kind == "slack":
        return [ChannelRequest("POST", f"https://hooks.slack.com/services/{p['path']}", json={"text": slack_escape(text)[:3000]})]
    if target.kind == "mattermost":
        return [ChannelRequest(
            "POST", _base(p["secure"], p["host"], p["port"], f"{p['prefix']}/hooks/{quote(p['token'], safe='')}"),
            json={"text": mattermost_escape(text)[:4000]},
        )]
    if target.kind == "apprise":
        return [ChannelRequest(
            "POST", _base(p["secure"], p["host"], p["port"], f"{p['prefix']}/notify/{quote(p['token'], safe='')}"),
            json={"title": notification.title[:250], "body": _body(notification, dashboard_url)[:4000],
                  "type": _APPRISE_TYPE.get(severity, "info")},
        )]
    raise ValueError(target.kind)


def slack_escape(text: str) -> str:
    """Slacks eigene Maskierung: Aus `<!channel>` in einer Meldung (Benutzername, Dateiname; Text, den ein Angreifer
    mitbestimmen kann) wird so keine Erwähnung, die den ganzen Kanal anpingt."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mattermost_escape(text: str) -> str:
    """Ein unsichtbares Leerzeichen nach jedem `@`: `@all`, `@channel` oder `@name` in einer Meldung pingt sonst."""
    return text.replace("@", "@\u200b")


def matrix_requests(target: Target, room_ids: list[str], notification: Notification, dashboard_url: str | None) -> list[ChannelRequest]:
    p = target.parts
    text = _text(notification, dashboard_url)
    base = _base(p["secure"], p["host"], p["port"])
    return [
        ChannelRequest(
            "PUT",
            f"{base}/_matrix/client/v3/rooms/{quote(room, safe='')}/send/m.room.message/{uuid.uuid4().hex}",
            headers={"Authorization": f"Bearer {p['token']}"},
            # `m.text`, nicht `m.notice`: Für Hinweise schickt Matrix nach seiner Standardregel keine Push-Nachricht.
            # Leeres `m.mentions`: Ein „@room“ im Text pingt so nicht den ganzen Raum an.
            json={"msgtype": "m.text", "body": text[:20000], "m.mentions": {}},
        )
        for room in room_ids
    ]


def matrix_alias_request(target: Target, alias: str) -> ChannelRequest:
    p = target.parts
    full = alias if ":" in alias else f"{alias}:{p['host']}"
    base = _base(p["secure"], p["host"], p["port"])
    return ChannelRequest(
        "GET", f"{base}/_matrix/client/v3/directory/room/{quote(full, safe='')}",
        headers={"Authorization": f"Bearer {p['token']}"},
    )


def status_text(kind: str, status_code: int) -> str | None:
    """Fester Satz zu einer Antwort, die kein Erfolg ist; `None` bei 2xx."""
    label = KIND_LABELS[kind]
    if 200 <= status_code < 300:
        return None
    if 300 <= status_code < 400:
        return (
            f"{label} leitet auf eine andere Adresse um (HTTP {status_code}). Solchen Umleitungen folgt Nodvard Deck "
            "aus Sicherheitsgründen nicht. Prüfe die Adresse des Kanals, etwa die Schreibweise mit oder ohne s am Ende "
            "(z. B. gotify:// oder gotifys://)."
        )
    if status_code == 403 and kind == "telegram":
        return (
            "Telegram lässt den Bot nicht in diesen Chat schreiben (HTTP 403). Ist der Bot Mitglied im Chat bzw. "
            "hat ihn dort jemand gestartet?"
        )
    if status_code == 403 and kind == "matrix":
        return "Matrix lässt das Konto nicht in diesen Raum schreiben (HTTP 403). Ist es Mitglied im Raum?"
    if status_code in (401, 403):
        return f"{label} hat den Zugang abgelehnt (HTTP {status_code}). Prüfe Token bzw. Schlüssel in der Adresse des Kanals."
    if status_code == 404:
        return f"{label} kennt das Ziel nicht (HTTP 404). Prüfe die Adresse des Kanals (Chat, Raum, Webhook oder Schlüssel)."
    if status_code == 429:
        return f"{label} nimmt gerade keine weiteren Nachrichten an (HTTP 429). Versuche es später erneut."
    if 400 <= status_code < 500:
        return f"{label} hat die Nachricht nicht angenommen (HTTP {status_code}). Prüfe die Adresse des Kanals."
    return f"{label} meldet einen Fehler (HTTP {status_code}). Versuche es später erneut."


def transport_text(kind: str, exc: BaseException) -> str:
    """Fester Satz zu einer Ausnahme beim Senden, nie ihr Text (er kann die Adresse samt Token tragen)."""
    label = KIND_LABELS[kind]
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return f"{label} antwortet nicht (Zeitüberschreitung). Versuche es später erneut."
    if isinstance(exc, httpx.ProtocolError):
        return f"{label} hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. Prüfe die Adresse des Kanals."
    name = type(exc).__name__
    if name == "PermissionDenied":
        return str(exc)  # eigener Satz des Kerns (welche Berechtigung fehlt), nichts aus der Anfrage
    if isinstance(exc, (httpx.ConnectError, OSError)) or "SSL" in name:
        return f"{label} ist nicht erreichbar. Prüfe die Adresse des Kanals und das Netz."
    return f"Die Nachricht an {label} ließ sich nicht senden."


def below_minimum(notification: Notification, minimum: Any) -> bool:
    """Ob die Meldung unter der Mindest-Stufe des Kanals liegt; die Testnachricht geht immer raus."""
    if (notification.payload or {}).get("test") is True:
        return False
    # Fehlt die Stufe, gilt die Vorgabe des Formulars („Ab Warnung“): Es zeigt sie an, speichert sie aber erst, wenn man
    # das Feld anfasst. Eine unbekannte Stufe gilt als „alles“ (lieber eine Meldung zu viel).
    if minimum is None or (isinstance(minimum, str) and not minimum.strip()):
        level = DEFAULT_MIN_SEVERITY
    else:
        try:
            level = Severity(minimum.strip().lower()) if isinstance(minimum, str) else Severity.INFO
        except ValueError:
            level = Severity.INFO
    return _SEVERITY_RANK.get(notification.severity, 0) < _SEVERITY_RANK[level]


def secret_label(name: str) -> str:
    return f"{SECRET_PREFIX}:{name}"


_MATRIX_ROOM_ID = re.compile(r"^![^\s/]{1,250}$")


def _merge(problems: list[str]) -> str:
    """Die Sätze ohne Wiederholung, in der Reihenfolge ihres Auftretens."""
    return " ".join(dict.fromkeys(problems))


async def _resolve_room(http: Any, target: Target, room: str) -> str:
    """Die Raum-ID (`!…`) zu einem Raum aus der Adresse; Aliase (`#name:server`) fragt sie beim Server nach."""
    if room.startswith("!"):
        return room
    answer = await _exchange(http, target.kind, matrix_alias_request(target, room))
    try:
        body = answer.json()
        room_id = body.get("room_id") if isinstance(body, dict) else None
    except (ValueError, RecursionError):
        room_id = None
    if not isinstance(room_id, str) or not _MATRIX_ROOM_ID.fullmatch(room_id):
        raise ChannelError("Matrix kennt den Raum nicht. Prüfe den Raum in der Adresse des Kanals (!ID:SERVER oder #NAME:SERVER).")
    return room_id


async def _each(coros: list[Any]) -> tuple[list[Any], list[str]]:
    """Führt die Aufrufe nebeneinander aus: (Ergebnisse der gelungenen, Sätze der gescheiterten)."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    done = [r for r in results if not isinstance(r, BaseException)]
    problems: list[str] = []
    for r in results:
        if isinstance(r, ChannelError):
            problems.append(str(r))
        elif isinstance(r, BaseException):
            problems.append("Die Nachricht ließ sich nicht senden.")  # nie der Text (er kann die Adresse tragen)
    return done, problems


async def send_to(http: Any, target: Target, notification: Notification, dashboard_url: str | None) -> str | None:
    """Schickt die Meldung nebeneinander an alle Empfänger (Chats, Räume); ein Fehler hält die anderen nicht auf.

    `None`, wenn alle sie bekamen; ein Satz, wenn nur ein Teil sie bekam; `ChannelError`, wenn keiner sie bekam."""
    problems: list[str] = []
    if target.kind == "matrix":
        room_ids, problems = await _each([_resolve_room(http, target, room) for room in target.parts["rooms"]])
        requests = matrix_requests(target, room_ids, notification, dashboard_url)
    else:
        requests = build_requests(target, notification, dashboard_url)
    sent, failed = await _each([_exchange(http, target.kind, request) for request in requests])
    problems += failed
    if not sent:
        raise ChannelError(_merge(problems) or f"Die Nachricht an {target.label} ließ sich nicht senden.")
    if problems:
        # Teilerfolg: Der Kanal zählt als zugestellt, und ein Wiederholversuch schickt die Meldung den anderen nicht doppelt.
        return f"{len(problems)} von {len(problems) + len(sent)} Empfängern nicht erreicht: {_merge(problems)}"
    return None


_TELEGRAM_CAN_POST = frozenset({"creator", "administrator", "member"})


async def _telegram_chat_problem(http: Any, token: str, bot_id: int, chat: str) -> None:
    """`ChannelError`, wenn der Bot in diese Gruppe bzw. diesen Kanal nicht schreiben kann (`getChatMember`)."""
    answer = await _exchange(http, "telegram", ChannelRequest(
        "POST", f"https://api.telegram.org/bot{token}/getChatMember", json={"chat_id": chat, "user_id": bot_id},
    ))
    try:
        member = answer.json().get("result") or {}
        status = member.get("status")
        allowed = status in _TELEGRAM_CAN_POST or (status == "restricted" and member.get("can_send_messages") is True)
    except (ValueError, AttributeError, RecursionError):
        allowed = False
    if not allowed:
        raise ChannelError("Telegram: Der Bot darf in diesen Chat nicht schreiben. Füge ihn dort hinzu (bei Kanälen als Admin).")


async def probe(http: Any, target: Target) -> str | None:
    """Prüft einen Kanal, ohne zu senden (für „Verbindung testen“): `None`, wenn der Dienst Token bzw. Schlüssel
    und Empfänger bestätigt hat, sonst ein Satz zu dem, was sich so nicht prüfen ließ; `ChannelError`, wenn etwas
    nicht stimmt."""
    p = target.parts
    if target.kind == "telegram":
        me = await _exchange(http, "telegram", ChannelRequest("GET", f"https://api.telegram.org/bot{p['token']}/getMe"))
        bot_id: int | None = None
        try:
            bot_id = int(me.json()["result"]["id"])
        except (ValueError, KeyError, TypeError, RecursionError):
            pass
        if bot_id is None:  # außerhalb von `except`: kein `__context__` mit Teilen der Antwort
            raise ChannelError("Telegram hat eine unerwartete Antwort geschickt. Prüfe das Bot-Token.")
        groups = [c for c in p["chats"] if c.startswith(("-", "@"))]
        _, problems = await _each([_telegram_chat_problem(http, p["token"], bot_id, chat) for chat in groups])
        if problems:
            raise ChannelError(f"Bot-Token stimmt, aber {len(problems)} von {len(groups)} Gruppen bzw. Kanälen gehen nicht: {_merge(problems)}")
        if len(groups) < len(p["chats"]):
            return "Bot-Token stimmt. Ob der Bot einer Person schreiben darf (sie muss ihn einmal starten), zeigt erst die Testnachricht."
        return None
    if target.kind == "discord":
        await _exchange(http, "discord", ChannelRequest("GET", f"https://discord.com/api/webhooks/{p['id']}/{quote(p['token'], safe='')}"))
        return None
    if target.kind == "pushover":
        devices = list(p["devices"]) or [None]
        _, problems = await _each([
            _exchange(http, "pushover", ChannelRequest(
                "POST", "https://api.pushover.net/1/users/validate.json",
                data={"token": p["token"], "user": p["user"], **({"device": d} if d else {})},
            ))
            for d in devices
        ])
        if problems:
            raise ChannelError(_merge(problems))
        return None
    if target.kind == "matrix":
        headers = {"Authorization": f"Bearer {p['token']}"}
        base = _base(p["secure"], p["host"], p["port"])
        await _exchange(http, "matrix", ChannelRequest("GET", f"{base}/_matrix/client/v3/account/whoami", headers=headers))
        room_ids, problems = await _each([_resolve_room(http, target, room) for room in p["rooms"]])
        joined = await _exchange(http, "matrix", ChannelRequest("GET", f"{base}/_matrix/client/v3/joined_rooms", headers=headers))
        try:
            member_of = set(joined.json().get("joined_rooms") or [])
        except (ValueError, AttributeError, TypeError, RecursionError):
            member_of = set()
        missing = [r for r in room_ids if r not in member_of]
        if missing:
            problems.append(f"Das Konto ist in {len(missing)} Raum/Räumen nicht Mitglied. Lade es dort ein und nimm die Einladung an.")
        if problems:
            raise ChannelError(_merge(problems))
        return "Zugriffstoken stimmt, und das Konto ist in allen Räumen. Ob es dort schreiben darf, zeigt die Testnachricht."
    if target.kind == "apprise":
        # `privacy=1`: Die Apprise-API nennt ihre Ziele ohne deren Zugangsdaten (hier zählt nur, ob es welche gibt).
        answer = await _exchange(http, "apprise", ChannelRequest(
            "GET", _base(p["secure"], p["host"], p["port"], f"{p['prefix']}/json/urls/{quote(p['token'], safe='')}?privacy=1"),
        ))
        if answer.status_code == 204:
            raise ChannelError("Die Apprise-API kennt für diesen Schlüssel keine Ziele. Prüfe den Schlüssel in der Adresse des Kanals.")
        return None
    if target.kind in ("gotify", "mattermost"):
        path = "/version" if target.kind == "gotify" else "/api/v4/system/ping"
        await _exchange(http, target.kind, ChannelRequest("GET", _base(p["secure"], p["host"], p["port"], f"{p['prefix']}{path}")))
        return f"{target.label} ist erreichbar. Ob der Schlüssel in der Adresse stimmt, zeigt erst die Testnachricht."
    return f"Die Adresse ist gültig. Ob sie stimmt, zeigt bei {target.label} erst die Testnachricht."


def fingerprint(url: str) -> str:
    """Kurzer, nicht umkehrbarer Abdruck der Adresse: Ein gemerkter Fehler gilt nur, solange sie gleich bleibt."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


async def _exchange(http: Any, kind: str, request: ChannelRequest) -> Any:
    problem: str | None = None
    response: Any = None
    kwargs: dict[str, Any] = {"headers": dict(request.headers), "timeout": TIMEOUT_S}
    if request.json is not None:
        kwargs["json"] = request.json
    if request.data is not None:
        kwargs["data"] = dict(request.data)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_S):
            response = await http.request(request.method, request.url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - jeder Fehler wird ein fester Satz (auch TimeoutError)
        problem = transport_text(kind, exc)
    # Außerhalb des `except`-Blocks: Sonst hinge die Ausnahme der HTTP-Schicht (mit der Adresse samt Token) als
    # `__context__` am Fehler.
    if problem is not None:
        raise ChannelError(problem)
    problem = status_text(kind, response.status_code)
    if problem is not None:
        raise ChannelError(problem)
    return response
