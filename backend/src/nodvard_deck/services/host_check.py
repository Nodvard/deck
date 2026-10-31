"""„Verbindung prüfen“ (`POST /hosts/{id}/check`): testet Schritt für Schritt die Verbindung zu einem Server.

Der Zustand liegt nur im Prozessspeicher; nach einem Neustart muss die Prüfung einfach neu laufen."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
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
from ..ext.root_helpers import RootHelperFile
from ..models import Host, HostCredential
from . import helper_hints
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
    "cat /proc/device-tree/model 2>/dev/null; true"
)
"""Alle drei Angaben sind freiwillig (einen Gerätebaum hat nur ein Raspberry Pi). Das abschließende `true` hält den
Exit-Code bei 0, denn `_step_os` wertet nur Exit 0 aus und `cat` allein endet auf x86 mit 1."""
SUDO_COMMAND = "env LC_ALL=C sudo -n true"
"""`LC_ALL=C`: sudo meldet sonst in der Sprache des Servers („ein Passwort ist notwendig“) und
die Meldung ließe sich nicht einordnen. `env` statt `LC_ALL=C sudo ...`, damit es in jeder
Login-Shell geht."""


DROPBEAR_KEY_HINT = (
    "Auf dem Server läuft Dropbear (etwa bei DietPi). Dropbear verwirft eine Schlüsselzeile ganz, wenn sie eine Option "
    "enthält, die er nicht kennt, etwa „pty“ aus einer älteren Einrichtung durch Nodvard Deck. Abhilfe: den Server "
    "noch einmal einrichten, mit Passwort oder mit dem Befehl; dabei wird die alte Zeile angepasst."
)
"""Zusatz zum Hinweis, wenn Dropbear den Schlüssel ablehnt."""


class CheckRefused(Exception):
    """Die Prüfung läuft gar nicht erst (zu viele, oder es läuft schon eine); für den Aufrufer HTTP 429."""

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
    full_rule_removable: bool = False
    """Die volle sudo-Regel ist da, aber nicht nötig: Jedes Modul, das hier root braucht, hat ein brauchbares
    Hilfsskript mit eigener Regel. Gilt auch, wenn sie beim Server gewollt ist (`sudo_full_wanted`)."""


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
    """Merkt den Schlüssel, den die Prüfung gerade von `host.address:port` gesehen hat; nur so lässt er sich bestätigen."""
    _seen_keys[host.id] = _SeenKey(host.address, port, key_type, fingerprint, _now())


def forget_seen_key(host_id: str) -> None:
    _seen_keys.pop(host_id, None)


def consume_seen_key(host: Host, port: int, key_type: str, fingerprint: str) -> bool:
    """`True` (und der Merkzettel ist weg), wenn `key_type`/`fingerprint` der Schlüssel der letzten Prüfung dieses Servers
    sind, höchstens 15 Minuten her, bei unveränderter Adresse und unverändertem `port` (Standard-Zugang, sonst
    `DEFAULT_SSH_PORT`)."""
    # Ein Merkzettel gilt nur einmal; kein `await` darin, damit Prüfen und Verbrauchen nicht unterbrochen werden.
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
    """Hält fest: Die Anmeldung mit `credential` hat gerade geklappt (je Zugang, siehe `login_was_ok`).
    Der dauerhafte Beleg am Server gilt nur für den Standard-Zugang: Wer einen neuen Zugang vor dem Umstellen prüft,
    soll den Beleg des bisherigen nicht überschreiben (`carry_over_login_ok` übernimmt ihn beim Umstellen)."""
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
    """Hat eine Prüfung mit genau diesem Zugang in den letzten 10 Minuten die Anmeldung geschafft
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
    """Nur druckbares ASCII, gekürzt: Text, der vom Server kommt, wird nie ungeprüft weitergereicht."""
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
        self.banner = ""
        """Erste Zeile des SSH-Dienstes (etwa `SSH-2.0-dropbear_2025.89`), auf druckbares ASCII gekürzt."""
        self.unreachable = False
        self.key_problem = False
        self.sudo_full: bool | None = None
        """Volle Regel: `sudo -n true` klappte, oder `sudo -n -l` zeigt sie, wenn die Skripte schon reichen; `None`: nicht
        geprüft oder keine Antwort."""
        self.helper_rules = False
        """Mindestens ein Hilfsskript hat seine eigene sudo-Regel (eingerichtet mit nur den Regeln der Skripte)."""
        self.helper_stale: dict[str, int] | None = None
        """Für den Hinweis in der Übersicht (`helper_hints`): fehlende oder veraltete Hilfsskripte, wenn sudo dort
        Skripte erlaubt; `{}` für „nichts zu melden“; `None`: nicht festgestellt, der bisherige Stand bleibt."""
        self.helper_without_root = False
        """Für denselben Hinweis: Ohne volle Regel haben diese Module dort gerade gar kein root."""
        self.helper_still_used: list[str] = []
        """Für denselben Hinweis: Module, die ihr veraltetes Skript trotzdem aufrufen (`calls_any_version`)."""
        self.full_rule_removable = False

    def add(self, id_: str, label: str, status: str, detail: str = "", hint: str = "") -> None:
        self.items.append(CheckItem(id=id_, label=label, status=status, detail=detail, hint=hint))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Die Prüfung
# ---------------------------------------------------------------------------

# Ohne bestätigten Server-Schlüssel gehen keine Anmeldedaten an den Server: Die Prüfung verbindet mit
# `ssh.connect(tofu=False)`, ist der Schlüssel noch nicht gemerkt, bricht schon der Schlüsseltausch ab (`HostKeyUnknown`),
# bevor Benutzername, Passwort oder Schlüssel gesendet werden, und es wird nichts gespeichert. Ohne Zugang liest
# `ssh.inspect_host_key` den Schlüssel, ohne sich anzumelden. Ein geänderter Schlüssel (`HostKeyMismatch`) ist immer ein
# Fehler und wird nie zum Bestätigen angeboten. Gemerkt wird ein Schlüssel nur auf ausdrücklichen Befehl und nur der, den
# diese Prüfung gerade selbst gesehen hat (`consume_seen_key`); ein angegebener Fingerabdruck allein genügt nicht.
# Die Prüfung nutzt eine eigene, nicht gepoolte Verbindung und prüft nur `host.address` und den Port des Zugangs, nie ein
# Ziel aus der Anfrage. Antworten enthalten nur eingeordnete, feste deutsche Texte (nie Ausnahme-Texte von asyncssh oder
# Vault, keine Fehlerausgabe des Servers, nie Passwort oder Schlüssel); weitergereicht werden nur das auf druckbares ASCII
# gekürzte SSH-Banner, die gekürzten OS-Angaben und der Fingerabdruck des Server-Schlüssels.


async def run_check(
    session: AsyncSession,
    settings: Settings,
    host: Host,
    credential: HostCredential | None,
    requirements: Sequence[tuple[str, HostRequirementSpec]] = (),
    *,
    user_id: str,
    helpers: Sequence[RootHelperFile] = (),
) -> ConnectionCheckOut:
    """Prüft `host` mit `credential` (`None`: nur Erreichbarkeit und Server-Schlüssel); `requirements` und `helpers`
    (ihre Hilfsskripte für root-Befehle) kommen von den Erweiterungen. `CheckRefused`, wenn für diesen Server schon
    geprüft wird, der Nutzer zu oft geprüft hat oder der Pi mit Prüfungen ausgelastet ist."""
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
            return await _run(session, settings, host, credential, requirements, helpers)
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
    helpers: Sequence[RootHelperFile] = (),
) -> ConnectionCheckOut:
    state = _State()
    try:
        async with asyncio.timeout(OVERALL_TIMEOUT_S):
            await _probe(state, session, settings, host, credential, requirements, helpers)
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
            # Nur wenn die Anmeldung selbst scheiterte (ein Server, der nicht antwortet, widerruft nicht, dass sie
            # bisher klappte), und nur der Beleg dieses Zugangs: Scheitert ein neuer Zugang, dessen Einrichtungsbefehl
            # noch nicht lief, bleibt der Beleg des funktionierenden stehen.
            if not state.unreachable and not state.key_problem and any(i.id == "login" and i.status == "fail" for i in state.items):
                hosts_service.clear_login_ok(host, credential.id)
        if state.host_key is not None and state.host_key.status == "changed":
            # Ein anderer Server-Schlüssel: Die bisherige Anmeldung galt einem anderen Gegenüber, mit jedem Zugang.
            hosts_service.clear_login_ok(host)
        if state.unreachable:
            host.status = "down"
        elif state.key_problem:
            host.status = "unknown"
    if state.helper_stale is not None and credential is not None and credential.is_default:
        await helper_hints.record(
            session, host.id, state.helper_stale, without_root=state.helper_without_root,
            still_used=state.helper_still_used, removable=state.full_rule_removable,
        )
    await session.flush()
    return ConnectionCheckOut(
        ok=not any(item.status in ("fail", "confirm") for item in state.items),
        checked_at=utcnow(), items=state.items, host_key=state.host_key, os=state.os,
        credential_id=credential.id if credential is not None else None,
        full_rule_removable=state.full_rule_removable,
    )


async def _probe(
    state: _State,
    session: AsyncSession,
    settings: Settings,
    host: Host,
    credential: HostCredential | None,
    requirements: Sequence[tuple[str, HostRequirementSpec]],
    helpers: Sequence[RootHelperFile] = (),
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
        for helper in helpers:
            state.add(_helper_id(helper), _helper_label(helper), "skipped", "Wird bei Windows-Servern nicht geprüft.")
        state.helper_stale = {}
        return
    # Die Hilfsskripte zuerst abfragen: Ohne volle Regel entscheiden ihre eigenen Regeln über „Root-Rechte“.
    helper_answer = await _query_helpers(conn, user, helpers)
    await _step_root(
        state, conn, user, requirements, helpers, helper_answer, full_wanted=hosts_service.full_sudo_wanted(host),
    )
    _step_helpers(state, user, helpers, helper_answer)
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
            state.banner = _printable(first_line, 80)
            state.add("reachable", label, "ok", f"SSH-Dienst antwortet ({state.banner}).")
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
    """Ordnet einen Anmeldefehler ein. Der Text der Ausnahme wird bewusst nicht weitergereicht."""
    offered = getattr(exc, "key_type", None)
    if offered:  # der Schlüsseltausch war durch, der Server-Schlüssel war bekannt
        state.add("host_key", "Server-Schlüssel", "ok", f"Bekannt ({offered}).")
    if isinstance(exc, ssh.SshAuthError):
        if credential.kind == "ssh_key":
            hint = "Ist der Einrichtungsbefehl auf dem Server gelaufen? Stimmt der Benutzer?"
            if "dropbear" in state.banner.lower():
                hint = f"{hint} {DROPBEAR_KEY_HINT}"
            state.add("login", label, "fail", "Der Server hat den Schlüssel nicht akzeptiert.", hint)
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


HELPER_RULES_ENOUGH = "sudo erlaubt nur die Hilfsskripte der Module, keine volle Regel. Das reicht."
NO_ROOT_NEEDED = "Kein eingeschaltetes Modul braucht hier root, sudo ohne Passwort ist nicht nötig."
FULL_RULE_NOT_NEEDED = "sudo ohne Passwort für alles ist hier nicht nötig. Die Hilfsskripte reichen."
FULL_RULE_WANTED = "sudo ohne Passwort klappt. Die volle Regel ist bei diesem Server gewollt."


FULL_RULE_HINT = (
    "Stammt sie aus einer früheren Einrichtung, nimmt der Einrichtungsbefehl sie mit „Volle sudo-Regel entfernen“ "
    "(unter „Erweitert“) zurück. Brauchst du sie für eigene Befehle mit sudo, setz beim Server „Volle sudo-Regel "
    "gewollt“."
)


async def _step_root(
    state: _State, conn: asyncssh.SSHClientConnection, user: str,
    requirements: Sequence[tuple[str, HostRequirementSpec]],
    helpers: Sequence[RootHelperFile] = (), helper_answer: _HelperAnswer | None = None, *, full_wanted: bool = False,
) -> None:
    label = _root_label(requirements)
    if user == "root":
        state.add("root", label, "ok", "Angemeldet als root – sudo nicht nötig.")
        return
    seen = helper_answer.seen if helper_answer is not None else {}
    state.helper_rules = any(item.tight_rule for item in seen.values())
    without_root = [h.ext_name for h in helpers if not _helper_ready(h, seen.get(h.install_path))]
    if {ext_id for ext_id, spec in requirements if spec.needs_root} - {h.ext_id for h in helpers}:
        without_root.append("Module ohne Hilfsskript")
    if helper_answer is not None and helpers and not without_root:
        # Die Skripte reichen. `sudo -n true` wäre auf einem Server nur mit ihren Regeln ein abgelehnter Aufruf im Protokoll.
        state.sudo_full = helper_hints.has_full_rule(helper_answer.sudo_lines)
        state.full_rule_removable = state.sudo_full
        if not state.sudo_full:
            state.add("root", label, "ok", HELPER_RULES_ENOUGH)
        elif full_wanted:
            state.add("root", label, "ok", FULL_RULE_WANTED)
        else:
            state.add("root", label, "warn", FULL_RULE_NOT_NEEDED, FULL_RULE_HINT)
        return
    result = await _run_command(conn, SUDO_COMMAND)
    if result is None:
        state.add("root", label, "warn", "Keine Antwort auf die sudo-Prüfung.")
        return
    code, out, err = result
    text = f"{out}\n{err}".lower()
    state.sudo_full = code == 0
    if code == 0:
        stuck = [
            h.ext_name for h in helpers if not h.sudo_fallback and not helper_hints.usable(h, seen.get(h.install_path))
        ] if helper_answer is not None else []
        if stuck:
            # Die volle Regel hilft einem Modul, das nur über sein Skript arbeitet, nicht.
            who = (f"{stuck[0]} hat" if len(stuck) == 1
                   else f"{', '.join(stuck[:-1])} und {stuck[-1]} haben")
            state.add(
                "root", label, "warn",
                f"sudo ohne Passwort klappt, aber {who} hier kein brauchbares Hilfsskript und damit kein root.", _RERUN_HINT,
            )
        else:
            state.add("root", label, "ok", "sudo ohne Passwort klappt.")
    elif not any(spec.needs_root for _ext_id, spec in requirements):
        state.add("root", label, "ok", NO_ROOT_NEEDED)
    elif state.helper_rules:
        state.add(
            "root", label, "warn", f"sudo erlaubt nur einzelne Hilfsskripte. Hier ohne root: {', '.join(without_root)}.",
            _RERUN_HINT,
        )
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


# 4b --------------------------------------------------------------------------
# Hilfsskripte der Module. Ein Modul ruft sein Skript nur auf, wenn es Byte für Byte das mitgelieferte ist (sha256), sonst
# nimmt es die volle sudo-Regel (ohne `sudo_fallback` hat es dann kein root); ein Modul mit `calls_any_version` ruft jede
# Fassung auf, als root zählt aber nur eine lesbar ältere (`helper_hints.usable`). `ok` heißt: genau dieses Skript, und
# nur root kann es ändern. Fehlt es, steht `skipped` da. Ein veraltetes oder neueres Skript und eines, das andere ändern
# können, ist eine Warnung, ein verändertes Skript (gleiche Version, anderer Inhalt) ein Fehler. Die Abfrage führt das
# Skript nie aus, auch nicht für die Version.

_HELPER_TAG = "@@nd-"
_VERSION_RE = re.compile(r"[0-9]{1,4}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


def _helper_id(helper: RootHelperFile) -> str:
    return f"helper:{helper.ext_id}"


def _helper_label(helper: RootHelperFile) -> str:
    return f"Hilfsskript für {helper.ext_name}"


# Wie `W` im Einrichtungsbefehl: Schreiben darf nur root, keine Gruppe und kein anderer. Mit ACL sind die Gruppenbits
# die Maske, ohne w darin schreibt auch kein benannter Eintrag.
_FOLDER_WRITERS = 'case "$(stat -c %a "$q" 2>/dev/null)" in ""|*[2367]|*[2367]?) s=bad;; esac; '


def helper_check_command(helpers: Sequence[RootHelperFile]) -> str:
    """Lesender Befehl als der angemeldete Benutzer: je Hilfsskript Version (ohne es auszuführen), sha256 und ob nur root
    es und jeden Ordner bis `/` ändern kann, am Ende die eigenen sudo-Regeln (`sudo -n -l`)."""
    paths = " ".join(shlex.quote(h.install_path) for h in helpers)
    return "sh -c " + shlex.quote(
        f'for p in {paths}; do echo "{_HELPER_TAG}path $p"; if [ -f "$p" ] && [ ! -L "$p" ]; then '
        f'echo "{_HELPER_TAG}version $(sed -n "s/^HELPER_VERSION=//p" "$p" 2>/dev/null | head -n 1)"; '
        f'echo "{_HELPER_TAG}sha $(sha256sum "$p" 2>/dev/null | cut -d" " -f1)"; '
        'q="$p"; s=ok; while :; do if [ -L "$q" ] || [ -w "$q" ] || [ "$(stat -c %u "$q" 2>/dev/null)" != 0 ]; then s=bad; fi; '
        f'{_FOLDER_WRITERS}[ "$q" = / ] && break; q=$(dirname "$q"); done; '
        f'echo "{_HELPER_TAG}owner $s"; else echo "{_HELPER_TAG}missing"; fi; done; '
        f'echo "{_HELPER_TAG}sudo"; env LC_ALL=C sudo -n -l 2>/dev/null'
    )


@dataclass
class _HelperSeen:
    present: bool = False
    version: int | None = None
    sha256: str | None = None
    owner_ok: bool = False
    tight_rule: bool = False


def parse_helper_check(out: str, helpers: Sequence[RootHelperFile]) -> dict[str, _HelperSeen]:
    """Die Antwort auf `helper_check_command`, je Ort auf dem Server. Was nicht genau ins Muster passt, zählt nicht."""
    seen: dict[str, _HelperSeen] = {}
    expected = {h.install_path for h in helpers}
    current: _HelperSeen | None = None
    sudo_lines: list[str] | None = None
    for raw in out.splitlines():
        line = raw.strip()
        if sudo_lines is not None:
            sudo_lines.append(line)
            continue
        if not line.startswith(_HELPER_TAG):
            continue
        tag, _, value = line[len(_HELPER_TAG):].partition(" ")
        if tag == "path":
            current = seen.setdefault(value, _HelperSeen()) if value in expected else None
        elif tag == "sudo":
            sudo_lines = []
        elif current is None:
            continue
        elif tag == "version":
            current.present = True
            current.version = int(value) if _VERSION_RE.fullmatch(value) else None
        elif tag == "sha":
            current.sha256 = value if _SHA_RE.fullmatch(value) else None
        elif tag == "owner":
            current.owner_ok = value == "ok"
    for path, item in seen.items():
        item.tight_rule = helper_hints.allows_helper(sudo_lines or [], path)
    return seen


def split_sudo_lines(out: str) -> list[str]:
    """Die Zeilen von `sudo -n -l` am Ende der Antwort auf `helper_check_command` (leer ohne die Marke)."""
    lines = out.splitlines()
    for index, raw in enumerate(lines):
        if raw.strip() == f"{_HELPER_TAG}sudo":
            return [line.strip() for line in lines[index + 1:]]
    return []


@dataclass(frozen=True)
class _HelperAnswer:
    seen: dict[str, _HelperSeen]
    sudo_lines: list[str]


async def _query_helpers(
    conn: asyncssh.SSHClientConnection, user: str, helpers: Sequence[RootHelperFile]
) -> _HelperAnswer | None:
    """Die Antwort auf `helper_check_command`; `None` ohne Skripte, für root oder ohne Antwort."""
    if not helpers or user == "root":
        return None
    result = await _run_command(conn, helper_check_command(helpers))
    if result is None:
        return None
    return _HelperAnswer(parse_helper_check(result[1], helpers), split_sudo_lines(result[1]))


def _helper_ready(helper: RootHelperFile, seen: _HelperSeen | None) -> bool:
    """Ob das Modul ohne volle Regel über sein Skript root bekommt: eigene sudo-Regel und `helper_hints.usable`."""
    return seen is not None and seen.tight_rule and helper_hints.usable(helper, seen)


def _step_helpers(
    state: _State, user: str, helpers: Sequence[RootHelperFile], answer: _HelperAnswer | None
) -> None:
    if not helpers:
        state.helper_stale = {}
        return
    if user == "root":
        for helper in helpers:
            state.add(_helper_id(helper), _helper_label(helper), "skipped", "Angemeldet als root – kein Hilfsskript nötig.")
        state.helper_stale = {}
        return
    seen = answer.seen if answer is not None else {}
    for helper in helpers:
        _add_helper_item(state, helper, seen.get(helper.install_path))
    if answer is not None:
        # Wie der Job `helper-hints`: die Regeln aus `sudo -n -l` (nicht aus `sudo -n true`), damit beide gleich urteilen.
        state.helper_stale, state.helper_without_root = helper_hints.judge(helpers, answer.sudo_lines, seen)
        state.helper_still_used = helper_hints.still_used(helpers, answer.sudo_lines, seen, state.helper_stale)


_RERUN_HINT = "Server neu einrichten: Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen."


def _add_helper_item(state: _State, helper: RootHelperFile, seen: _HelperSeen | None) -> None:
    id_, label = _helper_id(helper), _helper_label(helper)
    if seen is None:
        state.add(id_, label, "skipped", "Keine verwertbare Antwort auf die Abfrage.")
        return
    if not seen.present:
        if state.helper_rules and not state.sudo_full:
            state.add(
                id_, label, "warn", "Fehlt auf dem Server. Ohne das Skript hat dieses Modul hier kein root.", _RERUN_HINT,
            )
            return
        if state.sudo_full and not helper.sudo_fallback:
            state.add(
                id_, label, "warn",
                "Fehlt auf dem Server. Ohne das Skript hat dieses Modul hier kein root, auch nicht über die volle sudo-Regel.",
                _RERUN_HINT,
            )
            return
        if state.sudo_full:
            # Volle Regel gesetzt, Skript fehlt: Das Modul läuft, aber mit mehr Rechten als nötig. Ein Hinweis (gelb), kein
            # Fehler, wie in der Übersicht.
            state.add(
                id_, label, "warn",
                "Fehlt auf dem Server. Bis es dort liegt, nutzt Nodvard Deck für dieses Modul die volle sudo-Regel statt enger Rechte.",
                _RERUN_HINT,
            )
            return
        detail = "Noch nicht auf dem Server." if state.sudo_full is False or not helper.sudo_fallback else (
            "Noch nicht auf dem Server. Bis dahin nutzt Nodvard Deck für dieses Modul die volle sudo-Regel."
        )
        state.add(
            id_, label, "skipped", detail, "Kommt mit dem Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ auf den Server.",
        )
        return
    if not seen.owner_ok:
        state.add(
            id_, label, "warn",
            "Liegt auf dem Server, aber nicht nur root kann es ändern (Datei oder ein Ordner darüber, etwa weil eine "
            "Gruppe dort schreiben darf). Wer es ändern kann, kommt über seine sudo-Regel an root. Der Einrichtungsbefehl "
            "nennt den Ordner und die Abhilfe.",
            _RERUN_HINT,
        )
        return
    if seen.sha256 != helper.sha256:
        _add_foreign_helper_item(state, helper, seen)
        return
    rule = (
        " Eigene sudo-Regel vorhanden." if seen.tight_rule
        else " Noch ohne eigene sudo-Regel, die volle Regel deckt es ab." if state.sudo_full
        else " sudo erlaubt es noch nicht." if state.sudo_full is False
        else ""
    )
    state.add(id_, label, "ok", f"Version {helper.version}, nur root kann es ändern.{rule}")


def _add_foreign_helper_item(state: _State, helper: RootHelperFile, seen: _HelperSeen) -> None:
    """Ein Skript, das nicht das mitgelieferte ist: Bis der Server neu eingerichtet ist, ruft das Modul es nicht auf,
    außer mit `calls_any_version`. Gleiche Version mit anderem Inhalt hat niemand von Nodvard Deck so ausgeliefert."""
    id_, label = _helper_id(helper), _helper_label(helper)
    if not helper.calls_any_version:
        unused = " Nodvard Deck ruft es nicht auf."
    elif helper_hints.usable(helper, seen) and (seen.tight_rule or state.sudo_full):
        # Nur dann zählt das Modul hier als „mit root“, wie beim Punkt Root-Rechte und in der Übersicht.
        unused = f" {helper.ext_name} ruft es trotzdem auf, bis der Server neu eingerichtet ist."
    else:
        unused = ""
    if seen.sha256 is None:
        state.add(id_, label, "warn", f"Die Prüfsumme lässt sich auf dem Server nicht bilden (sha256sum).{unused}")
    elif seen.version is None:
        state.add(id_, label, "fail", f"Die Version lässt sich nicht lesen, der Inhalt ist unbekannt.{unused}", _RERUN_HINT)
    elif seen.version == helper.version:
        state.add(id_, label, "fail", f"Version {seen.version}, aber mit anderem Inhalt als hier.{unused}", _RERUN_HINT)
    elif seen.version < helper.version:
        state.add(
            id_, label, "warn",
            f"Veraltet: Version {seen.version} auf dem Server, dieses Dashboard bringt Version {helper.version} mit.{unused}",
            _RERUN_HINT,
        )
    else:
        state.add(
            id_, label, "warn",
            f"Version {seen.version} auf dem Server ist neuer als die dieses Dashboards ({helper.version}).{unused}",
            _RERUN_HINT,
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
