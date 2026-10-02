"""„Verbindung prüfen“: Schritt für Schritt testen, ob sich Nodvard Deck mit einem Server
verbinden kann (docs/04-API.md §3, `POST /hosts/{id}/check`).

**Sicherheit zuerst -- zwei Regeln, auf die alles andere aufbaut:**

1. *Ohne bestätigten Server-Schlüssel gehen keine Anmeldedaten an den Server.* Die Prüfung
   verbindet mit `ssh.connect(tofu=False)`: ist der Schlüssel noch nicht gemerkt, bricht schon der
   Schlüsseltausch ab (`HostKeyUnknown`), bevor Benutzername, Passwort oder Schlüssel gesendet
   werden, und es wird nichts gespeichert. Ohne Zugang liest `ssh.inspect_host_key` den Schlüssel,
   ohne sich anzumelden. Ein *geänderter* Schlüssel (`HostKeyMismatch`) ist immer ein Fehler und
   wird nie zum Bestätigen angeboten.
2. *Gemerkt wird ein Schlüssel nur auf ausdrücklichen Befehl*, und nur der, den diese Prüfung
   gerade selbst gesehen hat (`consume_seen_key`): ein angegebener Fingerabdruck allein genügt
   nicht, er muss frisch vom Server kommen.

Die Prüfung nutzt eine eigene, **nicht gepoolte** Verbindung, die danach geschlossen wird. Geprüft
wird nur `host.address` und der Port des Zugangs -- nie ein Ziel aus der Anfrage. Antworten
enthalten nur eingeordnete, feste deutsche Texte: nie die Ausnahme-Texte von asyncssh oder der
Vault, keine Fehlerausgabe des Servers, nie Passwort oder Schlüssel. Nur das SSH-Banner (auf
druckbares ASCII gekürzt), die OS-Angaben (gekürzt) und der Fingerabdruck des Server-Schlüssels
werden weitergereicht.

Zustand nur im Prozessspeicher: das Gemerkte zum Bestätigen (15 Minuten), das Ergebnis der
letzten erfolgreichen Anmeldung je Zugang (10 Minuten, für „alten Zugang löschen“) und die
Mengenbegrenzung. Nach einem Neustart ist alles weg und die Prüfung muss einfach neu laufen.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncssh
from nodvard_sdk import HostRequirementSpec
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import rate_limit, ssh
from ..core.vault import VaultError
from ..db import utcnow
from ..models import Host, HostCredential
from . import hosts as hosts_service

log = logging.getLogger("nodvard_deck.host_check")

OVERALL_TIMEOUT_S = 35.0
TCP_TIMEOUT_S = 6.0
"""TCP-Verbindung und Banner je höchstens so lange."""
COMMAND_TIMEOUT_S = 8.0
MAX_CONNECT_TIMEOUT_S = 10.0
MAX_PARALLEL_CHECKS = 2
"""Insgesamt höchstens so viele Prüfungen gleichzeitig (der Bastel-Pi ist klein)."""
SEMAPHORE_WAIT_S = 10.0
CHECKS_PER_USER = 12
CHECK_WINDOW_S = 5 * 60
SEEN_KEY_TTL_S = 15 * 60
LOGIN_FRESH_S = 10 * 60
"""So lange zählt eine erfolgreiche Anmeldung als „frisch“ (alten Zugang löschen)."""

DEFAULT_SSH_PORT = 22
"""Port, wenn es noch keinen Zugang gibt (der Port steht am Zugang)."""

_SSH_KINDS = ("ssh_key", "ssh_password")

OS_COMMAND = (
    "cat /etc/os-release 2>/dev/null; echo @@arch; uname -m; echo @@model; "
    "cat /proc/device-tree/model 2>/dev/null"
)
SUDO_COMMAND = "env LC_ALL=C sudo -n true"
"""`LC_ALL=C`: sudo meldet sonst in der Sprache des Servers („ein Passwort ist notwendig“) und
die Meldung liesse sich nicht einordnen. `env` statt `LC_ALL=C sudo ...`, damit es in jeder
Login-Shell geht."""


class CheckRefused(Exception):
    """Die Prüfung läuft gar nicht erst (zu viele, oder es läuft schon eine) -- für den Aufrufer HTTP 429."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------------


class CheckItem(BaseModel):
    id: str
    label: str
    status: Literal["ok", "warn", "fail", "skipped", "confirm"]
    """`confirm`: Der Server-Schlüssel ist neu und wartet auf die Bestätigung des Nutzers."""
    detail: str = ""
    hint: str = ""


class HostKeyInfo(BaseModel):
    status: Literal["known", "new", "changed"]
    key_type: str
    fingerprint: str
    """Der Schlüssel, den der Server jetzt zeigt (`SHA256:...`)."""
    expected: str | None = None
    """Der bisher gemerkte Fingerabdruck dieses Typs."""


class OsInfo(BaseModel):
    pretty_name: str | None = None
    arch: str | None = None
    model: str | None = None


class ConnectionCheckOut(BaseModel):
    ok: bool
    """Kein Punkt ist `fail` oder `confirm`."""
    checked_at: datetime
    items: list[CheckItem]
    host_key: HostKeyInfo | None = None
    os: OsInfo | None = None
    credential_id: str | None = Field(default=None)
    """Der Zugang, mit dem geprüft wurde (`null`: es gab keinen)."""


# ---------------------------------------------------------------------------
# Zustand im Speicher
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class _SeenKey:
    address: str
    port: int
    key_type: str
    fingerprint: str
    at: float


@dataclass(frozen=True)
class _LoginOk:
    address: str
    port: int
    at: float
    checked_at: datetime
    """Die Uhrzeit derselben Prüfung (für den dauerhaften Beleg, siehe `carry_over_login_ok`)."""


_seen_keys: dict[str, _SeenKey] = {}
_logins_ok: dict[str, _LoginOk] = {}
_busy: set[str] = set()
_per_user = rate_limit.SlidingWindow(CHECKS_PER_USER, CHECK_WINDOW_S)
_semaphore_state: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def reset_state() -> None:
    """Nur für Tests."""
    global _semaphore_state
    _seen_keys.clear()
    _logins_ok.clear()
    _busy.clear()
    _semaphore_state = None


def _semaphore() -> asyncio.Semaphore:
    """Je Event-Loop eine eigene (eine Semaphore bindet sich an den Loop, in dem sie warten musste)."""
    global _semaphore_state
    loop = asyncio.get_running_loop()
    if _semaphore_state is None or _semaphore_state[0] is not loop:
        _semaphore_state = (loop, asyncio.Semaphore(MAX_PARALLEL_CHECKS))
    return _semaphore_state[1]


def remember_seen_key(host: Host, port: int, key_type: str, fingerprint: str) -> None:
    """Merkt den Schlüssel, den die Prüfung gerade von `host.address:port` gesehen hat -- nur so
    kann er anschließend bestätigt werden."""
    _seen_keys[host.id] = _SeenKey(host.address, port, key_type, fingerprint, _now())


def forget_seen_key(host_id: str) -> None:
    _seen_keys.pop(host_id, None)


def consume_seen_key(host: Host, port: int, key_type: str, fingerprint: str) -> bool:
    """`True` (und der Merkzettel ist weg), wenn `key_type`/`fingerprint` genau der Schlüssel sind,
    den die letzte Prüfung dieses Servers gesehen hat, höchstens 15 Minuten her, und Adresse und
    `port` (der des Standard-Zugangs, sonst `DEFAULT_SSH_PORT`) seitdem unverändert sind. Ein
    Merkzettel gilt nur einmal. Bewusst ohne `await`."""
    seen = _seen_keys.get(host.id)
    if seen is None:
        return False
    if _now() - seen.at > SEEN_KEY_TTL_S or seen.address != host.address or seen.port != port:
        _seen_keys.pop(host.id, None)
        return False
    if seen.key_type != key_type or seen.fingerprint != fingerprint:
        return False  # ein falscher Versuch verbraucht den Merkzettel nicht
    del _seen_keys[host.id]
    return True


def mark_login_ok(host: Host, credential: HostCredential) -> None:
    """Hält fest: die Anmeldung mit `credential` hat gerade geklappt. Für „alten Zugang löschen“ gilt das
    je Zugang (`login_was_ok`); der dauerhafte Beleg am Server aber nur für den Standard-Zugang: wer
    einen neuen Zugang vor dem Umstellen prüft, soll den Beleg des bisherigen nicht überschreiben
    (`carry_over_login_ok` übernimmt ihn beim Umstellen)."""
    checked_at = utcnow()
    _logins_ok[credential.id] = _LoginOk(host.address, credential.port, _now(), checked_at)
    if credential.is_default:
        hosts_service.record_login_ok(host, credential, checked_at)


def carry_over_login_ok(host: Host, credential: HostCredential) -> bool:
    """Beim Umstellen des Standard-Zugangs: hat `credential` die Anmeldung vor Kurzem geschafft
    (`login_was_ok`), wird daraus der dauerhafte Beleg des Servers -- mit der Zeit dieser Prüfung --,
    sonst bleibt der neue Standard unbestätigt, bis eine Prüfung oder eine echte Verbindung es belegt."""
    if not login_was_ok(host, credential):
        return False
    hosts_service.record_login_ok(host, credential, _logins_ok[credential.id].checked_at)
    return True


def login_was_ok(host: Host, credential: HostCredential) -> bool:
    """Hat eine Prüfung mit GENAU diesem Zugang in den letzten 10 Minuten die Anmeldung geschafft
    (und Adresse und Port sind seitdem gleich)?"""
    record = _logins_ok.get(credential.id)
    return (
        record is not None
        and _now() - record.at <= LOGIN_FRESH_S
        and record.address == host.address
        and record.port == credential.port
    )


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _printable(text: str, limit: int) -> str:
    """Nur druckbares ASCII, gekürzt -- Text, der vom Server kommt, wird nie ungeprüft weitergereicht."""
    return "".join(c for c in text if " " <= c <= "~")[:limit].strip()


def _host_key_file(key_type: str) -> str:
    kind = "ed25519" if "ed25519" in key_type else "ecdsa" if "ecdsa" in key_type else "rsa" if "rsa" in key_type else None
    return f"/etc/ssh/ssh_host_{kind or '*'}_key.pub"


async def _close(conn: asyncssh.SSHClientConnection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
        await asyncio.wait_for(conn.wait_closed(), timeout=2.0)
    except Exception:  # noqa: BLE001, S110 - Aufräumen darf nie scheitern
        pass


class _State:
    """Sammelt das Ergebnis, während die Schritte laufen."""

    def __init__(self) -> None:
        self.items: list[CheckItem] = []
        self.host_key: HostKeyInfo | None = None
        self.os: OsInfo | None = None
        self.conn: asyncssh.SSHClientConnection | None = None
        self.login_ok = False
        self.unreachable = False
        self.key_problem = False

    def add(self, id_: str, label: str, status: str, detail: str = "", hint: str = "") -> None:
        self.items.append(CheckItem(id=id_, label=label, status=status, detail=detail, hint=hint))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Die Prüfung
# ---------------------------------------------------------------------------


async def run_check(
    session: AsyncSession,
    settings: Settings,
    host: Host,
    credential: HostCredential | None,
    requirements: Sequence[tuple[str, HostRequirementSpec]] = (),
    *,
    user_id: str,
) -> ConnectionCheckOut:
    """Prüft `host` mit `credential` (`None`: ohne Zugang, nur Erreichbarkeit und Server-Schlüssel).
    `requirements` sind die zum Server passenden Meldungen der Extensions (`HostRequirementSpec`).
    Wirft `CheckRefused`, wenn gerade schon für diesen Server geprüft wird, der Nutzer zu oft
    geprüft hat oder der Pi schon mit Prüfungen ausgelastet ist."""
    if host.id in _busy:
        raise CheckRefused("Für diesen Server läuft gerade schon eine Prüfung.")
    _busy.add(host.id)  # kein await zwischen Abfrage und Eintrag
    try:
        wait = _per_user.hit(user_id)
        if wait:
            raise CheckRefused(f"Zu viele Prüfungen. Bitte in {rate_limit.wait_text(wait)} erneut versuchen.", wait)
        semaphore = _semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), SEMAPHORE_WAIT_S)
        except TimeoutError:
            raise CheckRefused(
                "Es laufen gerade schon mehrere Prüfungen. Bitte in einem Moment erneut versuchen.", 10
            ) from None
        try:
            return await _run(session, settings, host, credential, requirements)
        finally:
            semaphore.release()
    finally:
        _busy.discard(host.id)


async def _run(
    session: AsyncSession,
    settings: Settings,
    host: Host,
    credential: HostCredential | None,
    requirements: Sequence[tuple[str, HostRequirementSpec]],
) -> ConnectionCheckOut:
    state = _State()
    try:
        async with asyncio.timeout(OVERALL_TIMEOUT_S):
            await _probe(state, session, settings, host, credential, requirements)
    except TimeoutError:
        state.add(
            "timeout", "Prüfung", "fail", f"Die Prüfung hat zu lange gedauert (über {int(OVERALL_TIMEOUT_S)} Sekunden).",
            "Ist der Server sehr langsam oder überlastet? Später noch einmal versuchen.",
        )
    except Exception as exc:  # noqa: BLE001 - nie ein 500 mit Ausnahme-Text, der Geheimes enthalten könnte
        log.warning("Verbindung prüfen: unerwarteter Fehler (%s)", type(exc).__name__)
        state.add("internal", "Prüfung", "fail", "Bei der Prüfung ist ein unerwarteter Fehler aufgetreten.", "Bitte später noch einmal versuchen.")
    finally:
        await _close(state.conn)
        state.conn = None

    if state.login_ok and credential is not None:
        host.status = "up"
        host.last_seen_at = utcnow()
        mark_login_ok(host, credential)
        # Neue Rechte (z. B. Gruppe docker) sollen für alle folgenden Verbindungen gelten, ohne ein
        # laufendes `docker logs -f` abzuschneiden.
        ssh.get_ssh_pool().retire_host(host.id)
    else:
        if credential is not None:
            _logins_ok.pop(credential.id, None)
            # Nur wenn die Anmeldung selbst scheiterte: ein Server, der gerade nicht antwortet, hat
            # damit nicht widerrufen, dass sie früher klappte.
            # Und nur der Beleg dieses Zugangs: scheitert ein anderer als der Standard (etwa ein neuer, dessen
            # Einrichtungsbefehl noch nicht gelaufen ist), bleibt der Beleg des funktionierenden stehen.
            if not state.unreachable and not state.key_problem and any(i.id == "login" and i.status == "fail" for i in state.items):
                hosts_service.clear_login_ok(host, credential.id)
        if state.host_key is not None and state.host_key.status == "changed":
            # Ein anderer Server-Schlüssel: die frühere Anmeldung galt einem anderen Gegenüber, mit jedem Zugang.
            hosts_service.clear_login_ok(host)
        if state.unreachable:
            host.status = "down"
        elif state.key_problem:
            host.status = "unknown"
    await session.flush()
    return ConnectionCheckOut(
        ok=not any(item.status in ("fail", "confirm") for item in state.items),
        checked_at=utcnow(), items=state.items, host_key=state.host_key, os=state.os,
        credential_id=credential.id if credential is not None else None,
    )


async def _probe(
    state: _State,
    session: AsyncSession,
    settings: Settings,
    host: Host,
    credential: HostCredential | None,
    requirements: Sequence[tuple[str, HostRequirementSpec]],
) -> None:
    port = credential.port if credential is not None else DEFAULT_SSH_PORT
    if not await _step_reachable(state, host.address, port):
        return
    connect_timeout = min(settings.ssh_connect_timeout_s, MAX_CONNECT_TIMEOUT_S)
    known = await ssh.load_known_fingerprints(session, host.id)

    if credential is None:
        if not await _step_key_without_login(state, host, port, known, connect_timeout):
            return
        state.add("login", "Anmeldung", "skipped", "Noch kein SSH-Zugang hinterlegt.")
        return
    if not await _step_key_and_login(state, session, settings, host, credential, connect_timeout):
        return
    conn = state.conn
    assert conn is not None
    user = credential.username
    if host.os_family == "windows":
        for id_, label in (("root", "Root-Rechte"), ("os", "Betriebssystem")):
            state.add(id_, label, "skipped", "Wird bei Windows-Servern nicht geprüft.")
        for ext_id, spec in requirements:
            if spec.check_command:
                state.add(f"req:{ext_id}:{spec.id}", spec.label, "skipped", "Wird bei Windows-Servern nicht geprüft.")
        return
    await _step_root(state, conn, user, requirements)
    await _step_requirements(state, conn, user, requirements)
    await _step_os(state, conn)


# 1 ---------------------------------------------------------------------------


async def _step_reachable(state: _State, address: str, port: int) -> bool:
    label = "Server erreichbar"
    not_reached_hint = f"Stimmt die Adresse? Ist der Server an? Blockiert eine Firewall Port {port}?"
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), TCP_TIMEOUT_S)
    except TimeoutError:
        state.add("reachable", label, "fail", f"Keine Antwort von {address}:{port}.", not_reached_hint)
    except ConnectionRefusedError:
        state.add(
            "reachable", label, "fail", f"Der Server lehnt Verbindungen auf Port {port} ab.",
            "Läuft dort SSH? Debian/Raspberry Pi OS: „sudo apt install openssh-server“ und "
            "„sudo systemctl enable --now ssh“ (Raspberry Pi OS: auch über raspi-config).",
        )
    except socket.gaierror:
        state.add("reachable", label, "fail", f"Den Namen {address} kennt das Netz nicht.", "Besser die IP-Adresse eintragen.")
    except OSError:
        state.add("reachable", label, "fail", f"Keine Verbindung zu {address}:{port} möglich.", not_reached_hint)
    else:
        try:
            data = await asyncio.wait_for(reader.read(255), TCP_TIMEOUT_S)
        except (TimeoutError, OSError):
            data = b""
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
            except Exception:  # noqa: BLE001, S110 - Aufräumen darf nie scheitern
                pass
        first_line = data.split(b"\n", 1)[0].decode("ascii", "ignore")
        if not first_line.startswith("SSH-"):
            state.add(
                "reachable", label, "fail", f"Auf Port {port} antwortet kein SSH-Dienst.",
                "Ist der Port richtig? Der SSH-Standard ist 22.",
            )
        else:
            banner = _printable(first_line, 80)
            state.add("reachable", label, "ok", f"SSH-Dienst antwortet ({banner}).")
            return True
    state.unreachable = True
    return False


# 2 (ohne Zugang) -------------------------------------------------------------


def _apply_key_report(state: _State, report: ssh.HostKeyReport, host: Host, port: int) -> bool:
    """Trägt `host_key` ein und gibt an, ob es weitergehen darf (nur bei `known`)."""
    label = "Server-Schlüssel"
    state.host_key = HostKeyInfo(
        status=report.status, key_type=report.key_type, fingerprint=report.fingerprint, expected=report.expected
    )
    if report.status == "known":
        forget_seen_key(host.id)
        state.add("host_key", label, "ok", f"Bekannt ({report.key_type}).")
        return True
    state.key_problem = True
    if report.status == "new":
        remember_seen_key(host, port, report.key_type, report.fingerprint)
        state.add(
            "host_key", label, "confirm",
            f"Nodvard Deck kennt diesen Server noch nicht. Fingerabdruck: {report.fingerprint}",
            f"Zum Vergleichen auf dem Server: ssh-keygen -lf {_host_key_file(report.key_type)}",
        )
        return False
    _changed_key_item(state, host, report.expected, report.fingerprint, report.key_type)
    return False


def _changed_key_item(
    state: _State, host: Host, expected: str | None, actual: str | None, key_type: str | None = None
) -> None:
    """Ein geänderter Schlüssel: Fehler, nie zum Bestätigen angeboten, und ein älterer Merkzettel
    (Bestätigen) ist damit hinfällig."""
    forget_seen_key(host.id)
    state.key_problem = True
    state.add(
        "host_key", "Server-Schlüssel", "fail",
        f"Achtung: Der Server meldet einen anderen Schlüssel als bisher. Bisher {expected}, jetzt {actual}"
        + (f" ({key_type})." if key_type else "."),
        "Das passiert nach einer Neuinstallation – oder wenn sich jemand dazwischenschaltet. Nur wenn du den "
        "Server neu aufgesetzt hast: gemerkten Schlüssel vergessen, erneut prüfen und den neuen Fingerabdruck bestätigen.",
    )


async def _step_key_without_login(
    state: _State, host: Host, port: int, known: dict[str, str], connect_timeout: float
) -> bool:
    try:
        report = await ssh.inspect_host_key(host.address, port, known, timeout_s=connect_timeout)
    except ssh.SshError:
        state.add(
            "host_key", "Server-Schlüssel", "fail", "Der Server hat keinen SSH-Schlüssel gezeigt.",
            f"Läuft dort wirklich ein SSH-Dienst? Ist Port {port} der richtige?",
        )
        return False
    return _apply_key_report(state, report, host, port)


# 2 + 3 (mit Zugang) ----------------------------------------------------------


async def _step_key_and_login(
    state: _State, session: AsyncSession, settings: Settings, host: Host, credential: HostCredential,
    connect_timeout: float,
) -> bool:
    """Schlüsseltausch und Anmeldung in einer Verbindung. Ohne bestätigten Server-Schlüssel bricht
    der Schlüsseltausch ab, bevor etwas von den Zugangsdaten gesendet wird (`tofu=False`)."""
    login_label = f"Anmeldung als {credential.username}"
    if credential.kind not in _SSH_KINDS:
        state.add("login", login_label, "fail", "Dieser Zugang ist keine SSH-Anmeldung.")
        return False
    try:
        target = await hosts_service.resolve_connection_target(session, settings, host, credential)
    except VaultError:
        state.add("login", login_label, "fail", "Das gespeicherte Geheimnis lässt sich nicht lesen.")
        return False
    try:
        conn = await ssh.connect(session, target, connect_timeout_s=connect_timeout, tofu=False)
    except ssh.HostKeyUnknown as exc:
        _apply_key_report(
            state, ssh.HostKeyReport(exc.key_type or "?", exc.fingerprint or "?", "new", None), host, credential.port
        )
        return False
    except ssh.HostKeyMismatch as exc:
        state.host_key = HostKeyInfo(
            status="changed", key_type=exc.key_type or "?", fingerprint=exc.actual or "?", expected=exc.expected
        )
        _changed_key_item(state, host, exc.expected, exc.actual, exc.key_type)
        return False
    except (ssh.SshError, asyncssh.Error, OSError, ValueError) as exc:
        _login_failed(state, credential, login_label, exc)
        return False
    key = conn.get_server_host_key()
    state.conn = conn
    if key is not None:
        key_type, fingerprint = key.get_algorithm(), key.get_fingerprint()
        state.host_key = HostKeyInfo(status="known", key_type=key_type, fingerprint=fingerprint, expected=fingerprint)
        state.add("host_key", "Server-Schlüssel", "ok", f"Bekannt ({key_type}).")
    else:
        state.add("host_key", "Server-Schlüssel", "ok", "Bekannt.")
    state.login_ok = True
    how = "Mit Schlüssel angemeldet." if credential.kind == "ssh_key" else "Mit Passwort angemeldet."
    state.add("login", login_label, "ok", how)
    return True


def _login_failed(state: _State, credential: HostCredential, label: str, exc: BaseException) -> None:
    """Ordnet einen Anmeldefehler ein. Der Text der Ausnahme wird bewusst NICHT weitergereicht."""
    offered = getattr(exc, "key_type", None)
    if offered:  # der Schlüsseltausch war durch, der Server-Schlüssel war bekannt
        state.add("host_key", "Server-Schlüssel", "ok", f"Bekannt ({offered}).")
    if isinstance(exc, ssh.SshAuthError):
        if credential.kind == "ssh_key":
            state.add(
                "login", label, "fail", "Der Server hat den Schlüssel nicht akzeptiert.",
                "Ist der Einrichtungsbefehl auf dem Server gelaufen? Stimmt der Benutzer?",
            )
        else:
            state.add(
                "login", label, "fail", "Der Server hat die Anmeldung abgelehnt.",
                "Passwort falsch – oder der Server erlaubt keine Anmeldung mit Passwort.",
            )
        return
    # `ssh.connect` verpackt asyncssh-Fehler in `SshError`; eingeordnet wird die eigentliche Ursache.
    cause = exc.__cause__ if isinstance(exc, ssh.SshError) and exc.__cause__ is not None else exc
    if isinstance(cause, ValueError):
        state.add("login", label, "fail", "Der gespeicherte Schlüssel lässt sich nicht lesen.", "Zugang löschen und neu anlegen.")
    elif isinstance(cause, TimeoutError):
        state.add("login", label, "fail", "Der Server hat nicht rechtzeitig geantwortet.")
        state.unreachable = True
    elif isinstance(cause, asyncssh.KeyExchangeFailed):
        state.add(
            "login", label, "fail",
            "Der Server zeigt den gemerkten Schlüssel nicht, oder es gibt keine gemeinsame Verschlüsselung.",
            "Wurde der Server neu aufgesetzt? Dann den gemerkten Schlüssel vergessen und erneut prüfen.",
        )
    elif isinstance(cause, asyncssh.DisconnectError):
        state.add("login", label, "fail", "Der Server hat die Verbindung abgebrochen.")
    elif isinstance(cause, OSError):
        state.add("login", label, "fail", "Die Verbindung zum Server ist abgebrochen.")
        state.unreachable = True
    else:
        state.add("login", label, "fail", "Die Anmeldung ist fehlgeschlagen.")
    log.info("Verbindung prüfen: Anmeldung fehlgeschlagen (%s)", type(exc).__name__)


# 4 ---------------------------------------------------------------------------


async def _run_command(
    conn: asyncssh.SSHClientConnection, command: str
) -> tuple[int, str, str] | None:
    """Ein lesender Befehl mit Zeitgrenze; `None`, wenn er nicht ausgeführt werden konnte."""
    try:
        return await ssh.run(conn, command, timeout_s=COMMAND_TIMEOUT_S)
    except (TimeoutError, asyncssh.Error, OSError):
        return None


def _root_label(requirements: Sequence[tuple[str, HostRequirementSpec]]) -> str:
    reasons: list[str] = []
    for _ext_id, spec in requirements:
        reason = (spec.root_reason or "").strip()
        if spec.needs_root and reason and reason not in reasons:
            reasons.append(reason)
    return f"Root-Rechte (für {', '.join(reasons)})" if reasons else "Root-Rechte"


async def _step_root(
    state: _State, conn: asyncssh.SSHClientConnection, user: str,
    requirements: Sequence[tuple[str, HostRequirementSpec]],
) -> None:
    label = _root_label(requirements)
    if user == "root":
        state.add("root", label, "ok", "Angemeldet als root – sudo nicht nötig.")
        return
    result = await _run_command(conn, SUDO_COMMAND)
    if result is None:
        state.add("root", label, "warn", "Keine Antwort auf die sudo-Prüfung.")
        return
    code, out, err = result
    text = f"{out}\n{err}".lower()
    if code == 0:
        state.add("root", label, "ok", "sudo ohne Passwort klappt.")
    elif "password is required" in text or "a terminal is required" in text:
        state.add(
            "root", label, "warn", "sudo verlangt ein Passwort.",
            "Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen.",
        )
    elif code == 127 or "not found" in text or "no such file" in text:
        state.add(
            "root", label, "warn", "sudo ist nicht installiert.",
            "Als root „apt install sudo“ – oder den Zugang mit dem Benutzer root einrichten.",
        )
    elif "not in the sudoers" in text or "not allowed" in text or "may not run sudo" in text:
        state.add("root", label, "warn", "Dieser Benutzer darf kein sudo.")
    else:
        state.add(
            "root", label, "warn", "sudo ohne Passwort klappt nicht.",
            "Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen.",
        )


# 5 ---------------------------------------------------------------------------


async def _step_requirements(
    state: _State, conn: asyncssh.SSHClientConnection, user: str,
    requirements: Sequence[tuple[str, HostRequirementSpec]],
) -> None:
    for ext_id, spec in requirements:
        if not spec.check_command:
            continue
        item_id = f"req:{ext_id}:{spec.id}"
        result = await _run_command(conn, spec.check_command)
        if result is None:
            state.add(item_id, spec.label, "warn", "Keine Antwort auf die Prüfung.")
            continue
        code = result[0]
        if code == 0:
            state.add(item_id, spec.label, "ok", spec.ok_text or "In Ordnung.")
        elif code == 127:
            state.add(item_id, spec.label, "skipped", "Nicht installiert.")
        else:
            # `replace` statt `format`: der Text kommt von einer Erweiterung, Platzhalter wie
            # `{0.__class__}` sollen nie ausgewertet werden.
            state.add(item_id, spec.label, "warn", "Nicht erfüllt.", spec.fail_hint.replace("{user}", user))


# 6 ---------------------------------------------------------------------------


def parse_os(output: str) -> OsInfo | None:
    """`cat /etc/os-release; echo @@arch; uname -m; echo @@model; cat .../model` -> `OsInfo`."""
    release, _, rest = output.partition("@@arch")
    arch_part, _, model_part = rest.partition("@@model")
    pretty = None
    for line in release.splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = _printable(line.partition("=")[2].strip().strip('"').strip("'"), 80) or None
            break
    arch = _printable(arch_part.strip().splitlines()[0], 30) if arch_part.strip() else None
    model = _printable(model_part.replace("\x00", "").strip(), 80) or None
    if not (pretty or arch or model):
        return None
    return OsInfo(pretty_name=pretty, arch=arch or None, model=model)


async def _step_os(state: _State, conn: asyncssh.SSHClientConnection) -> None:
    result = await _run_command(conn, OS_COMMAND)
    info = parse_os(result[1]) if result is not None and result[0] == 0 else None
    if info is None:
        state.add("os", "Betriebssystem", "skipped", "Nicht erkannt.")
        return
    state.os = info
    state.add("os", "Betriebssystem", "ok", " · ".join(p for p in (info.pretty_name, info.arch, info.model) if p))
