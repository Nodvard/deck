"""Ein fremdes Passwort genau einmal benutzen: eine SSH-Anmeldung, ein Befehl, danach vergessen.

Fuer jeden Weg, auf dem jemand Nodvard Deck einmalig das Passwort eines Kontos auf einem Server gibt,
damit Nodvard Deck dort selbst etwas einrichtet (heute: einen Linux-Server per SSH, siehe
`POST /hosts/{id}/credentials/{cid}/setup-with-password`). Die Regeln stehen hier an EINER Stelle, damit
spaetere Wege (Proxmox, Assistent) sie nicht neu erfinden:

1. **Einmal benutzen, nie behalten.** Das Passwort kommt als `SecretStr`, wird fuer genau eine Anmeldung
   benutzt und -- nur wenn `password_on_stdin` -- als Eingabe genau dieses einen Befehls (`sudo -S`). Es
   kommt in keine Datenbank, keinen Tresor, keine Datei, keinen Verbindungspool und keinen Zwischenspeicher;
   die Verbindung wird im `finally` geschlossen, die Variablen geloescht. In der Befehlszeile steht es nie
   (die ist auf dem Server fuer alle sichtbar und steht in Fehlern von asyncssh).
2. **Nie im Protokoll.** Fehler sind `PasswordUseError` mit festem `code` und festem deutschen Satz; Texte
   von asyncssh oder vom Server werden nie weitergegeben, und die Ursache wird nicht angehaengt (`from None`).
   Was der Server ausgibt, kommt nur mit unkenntlich gemachtem Passwort zurueck (`RemoteResult`); wer es
   weiterreicht, filtert zusaetzlich.
3. **Nur an einen Server mit bekanntem Server-Schluessel.** Dieselbe Pruefung wie jede Verbindung
   (`core.ssh.connect` mit den gemerkten Schluesseln), aber hier nie mit stillem Merken (`tofu=False`).
   „Bekannt“ heisst gemerkt -- ausdruecklich bestaetigt oder, auf bestehenden Installationen, beim ersten
   Kontakt still gemerkt (dort gilt das weiter wie fuer jede Verbindung). Ist fuer den Server noch kein
   Schluessel gemerkt, wird er nur gelesen (`ssh.inspect_host_key`, ohne Anmeldung) und zum Bestaetigen
   vorgemerkt (`host_check.remember_seen_key`, wie bei „Verbindung pruefen“): das Passwort kommt dann gar
   nicht erst in eine Verbindung.
4. **Nur das gespeicherte Ziel.** Adresse des Servers und Port des Zugangs, nie ein Ziel aus der Anfrage;
   keine Weiterleitung, kein Agent, keine Schluessel und keine ssh-Konfiguration des Rechners, auf dem
   Nodvard Deck laeuft (`core.ssh.connect` mit `client_keys=None`).
5. **Begrenzt.** Je Ziel (Adresse und Port) und je Nutzer nur wenige Versuche in 10 Minuten, je Server
   nur einer gleichzeitig -- damit Nodvard Deck kein Werkzeug zum Passwort-Raten wird. Als Versuch zaehlt
   jeder Lauf, bei dem das Passwort an den Server gehen koennte; ein noch nicht gemerkter Server-Schluessel
   kostet keinen Versuch (es wird ja nichts gesendet). Damit auch solche Laeufe nicht beliebig oft
   Verbindungen aufbauen, gilt davor eine grobe Drossel je Nutzer fuer jeden Aufruf. Eine Sperre steht
   je Nutzer einmal im Zeitfenster im Protokoll, nicht bei jedem weiteren Klopfen (`PasswordUseError.audit`).

Zustand (Zaehler, „laeuft gerade“) nur im Prozessspeicher, nach einem Neustart weg.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import asyncssh
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import rate_limit, ssh
from ..models import Host
from . import host_check

log = logging.getLogger("nodvard_deck.one_time_password")

ATTEMPTS_PER_TARGET = 5
"""Hoechstens so viele Versuche je Ziel (Adresse und Port) im Zeitfenster -- egal, wer es versucht."""
ATTEMPTS_PER_USER = 10
"""Hoechstens so viele Versuche je Nutzer im Zeitfenster -- egal, an welchem Server."""
ATTEMPT_WINDOW_S = 10 * 60
REQUESTS_PER_USER = 30
"""Grobe Drossel: hoechstens so viele Laeufe je Nutzer im Fenster `REQUEST_WINDOW_S`, ob sie als Versuch zaehlen
oder nicht (noch nicht gemerkter Schluessel, Server nicht erreichbar, „laeuft gerade“)."""
REQUEST_WINDOW_S = 5 * 60
MAX_PASSWORD_LENGTH = 512
CONNECT_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 90.0

MASK = "********"
"""Steht in `RemoteResult` an jeder Stelle, an der der Server das Passwort ausgegeben hat."""

_per_target = rate_limit.SlidingWindow(ATTEMPTS_PER_TARGET, ATTEMPT_WINDOW_S)
_per_user = rate_limit.SlidingWindow(ATTEMPTS_PER_USER, ATTEMPT_WINDOW_S)
_requests_per_user = rate_limit.SlidingWindow(REQUESTS_PER_USER, REQUEST_WINDOW_S)
_refusals_logged = rate_limit.SlidingWindow(1, ATTEMPT_WINDOW_S)
"""Je Nutzer kommt eine Sperre (`rate_limited`) einmal im Fenster ins Protokoll."""
_busy: set[str] = set()


# ---------------------------------------------------------------------------
# Fehler: feste Saetze, feste Codes
# ---------------------------------------------------------------------------

_ERRORS: dict[str, tuple[int, str]] = {
    "rate_limited": (429, "Zu viele Versuche mit Passwort. Bitte in {wait} erneut versuchen."),
    "invalid_password": (400, "Das Passwort enthält ein Zeichen, das sich nicht senden lässt."),
    "busy": (429, "Für diesen Server läuft gerade schon eine Einrichtung. Bitte warte, bis sie fertig ist."),
    "host_key_unconfirmed": (
        409,
        (
            "Nodvard Deck kennt den Schlüssel dieses Servers noch nicht. Vergleiche den Fingerabdruck und bestätige "
            "ihn – dein Passwort wurde nicht gesendet."
        ),
    ),
    "host_key_changed": (
        409,
        (
            "Achtung: Der Server meldet einen anderen Schlüssel als bisher. Dein Passwort wurde nicht gesendet. "
            "Sieh unter „Server-Schlüssel“ nach, was los ist."
        ),
    ),
    "wrong_password": (
        400,
        (
            "Der Server hat die Anmeldung abgelehnt: Benutzer oder Passwort stimmt nicht – oder der Server erlaubt "
            "keine Anmeldung mit Passwort."
        ),
    ),
    "unreachable": (502, "Der Server ist nicht erreichbar. Stimmen Adresse und Port, und ist er an?"),
    "connection_failed": (502, "Die Verbindung zum Server ist fehlgeschlagen. Bitte versuche es noch einmal."),
    "connection_lost": (
        502,
        (
            "Die Verbindung zum Server ist abgebrochen, während der Einrichtungsbefehl lief. Die Einrichtung kann "
            "teilweise gelaufen sein: Sieh mit „Verbindung prüfen“ nach, was schon eingerichtet ist. Du kannst sie "
            "danach gefahrlos noch einmal starten."
        ),
    ),
    "timeout": (
        504,
        (
            "Der Befehl auf dem Server hat nicht rechtzeitig geantwortet. Prüfe gleich die Verbindung – vielleicht "
            "ist er trotzdem durchgelaufen."
        ),
    ),
}
CODES = frozenset(_ERRORS)


class PasswordUseError(Exception):
    """Der Versuch ging nicht. `str(exc)` ist ein fester deutscher Satz, `code` ein fester Bezeichner,
    `status` der passende HTTP-Status. Nie mit Text vom Server oder von asyncssh."""

    def __init__(
        self, code: str, *, retry_after: int | None = None, host_key: dict[str, Any] | None = None,
        audit: bool = True,
    ) -> None:
        status, text = _ERRORS[code]
        if retry_after is not None:
            text = text.format(wait=rate_limit.wait_text(retry_after))
        super().__init__(text)
        self.code = code
        self.status = status
        self.retry_after = retry_after
        self.host_key = host_key
        """Bei `host_key_unconfirmed`/`host_key_changed`: `{status, key_type, fingerprint, expected}` wie
        `host_check.HostKeyInfo` -- zum Vergleichen und Bestaetigen."""
        self.audit = audit
        """`False`: dieselbe Sperre steht fuer diesen Nutzer schon im Protokoll (nicht noch einmal eintragen)."""


# ---------------------------------------------------------------------------
# Grenzen
# ---------------------------------------------------------------------------


def _target_key(address: str, port: int) -> str:
    # `pve1.example.` (vollstaendiger Name mit Punkt am Ende) ist dasselbe Ziel wie `pve1.example`.
    return f"{address.strip().lower().rstrip('.')}:{port}"


def _refused(wait: int, user_id: str) -> PasswordUseError:
    return PasswordUseError("rate_limited", retry_after=wait, audit=_refusals_logged.hit(user_id) == 0)


def claim_attempt(*, address: str, port: int, user_id: str) -> None:
    """Zaehlt einen Versuch fuer `user_id` und das Ziel `address:port`; `PasswordUseError("rate_limited")`,
    wenn eine der beiden Grenzen erreicht ist. Erst die Grenze des Nutzers: wer schon darueber ist, verbraucht
    nichts vom Budget des Ziels (das gehoert auch anderen). Ein abgelehnter Versuch zaehlt bei der Grenze,
    die ablehnt, nicht mit."""
    for window, key in ((_per_user, user_id), (_per_target, _target_key(address, port))):
        wait = window.hit(key)
        if wait:
            raise _refused(wait, user_id)


def _throttle(user_id: str) -> None:
    """Grobe Drossel je Nutzer fuer jeden Lauf, vor jeder Verbindung (auch vor dem blossen Lesen des Schluessels)."""
    wait = _requests_per_user.hit(user_id)
    if wait:
        raise _refused(wait, user_id)


@contextmanager
def _exclusive(host_id: str) -> Iterator[None]:
    if host_id in _busy:
        raise PasswordUseError("busy")
    _busy.add(host_id)  # kein await zwischen Abfrage und Eintrag
    try:
        yield
    finally:
        _busy.discard(host_id)


def reset_state() -> None:
    """Nur fuer Tests."""
    _busy.clear()
    _per_target.reset()
    _per_user.reset()
    _requests_per_user.reset()
    _refusals_logged.reset()


# ---------------------------------------------------------------------------
# Server-Schluessel
# ---------------------------------------------------------------------------


def _key_info(status: str, key_type: str | None, fingerprint: str | None, expected: str | None) -> dict[str, Any]:
    return {"status": status, "key_type": key_type or "?", "fingerprint": fingerprint or "?", "expected": expected}


async def _require_known_host_key(session: AsyncSession, host: Host, port: int, timeout_s: float) -> None:
    """Ohne gemerkten Server-Schluessel: ihn lesen, OHNE sich anzumelden, zum Bestaetigen vormerken und
    `host_key_unconfirmed` werfen. Ob ein gemerkter Schluessel auch PASST, prueft danach `ssh.connect`
    im Schluesseltausch, bevor das Passwort gesendet wird."""
    known = await ssh.load_known_fingerprints(session, host.id)
    if known:
        return
    try:
        report = await ssh.inspect_host_key(host.address, port, known, timeout_s=timeout_s)
    except ssh.SshError:
        raise PasswordUseError("unreachable") from None
    host_check.remember_seen_key(host, port, report.key_type, report.fingerprint)
    raise PasswordUseError("host_key_unconfirmed", host_key=_key_info("new", report.key_type, report.fingerprint, None))


# ---------------------------------------------------------------------------
# Der eine Lauf
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteResult:
    """Ergebnis des einen Befehls. In `stdout`/`stderr` ist das Passwort durch `MASK` ersetzt."""

    exit_status: int | None
    stdout: str
    stderr: str
    flags: frozenset[str] = frozenset()
    """Welche Pruefungen aus `flags` (siehe `run_once`) auf der UNVERAENDERTEN Ausgabe zutrafen -- nur deren
    Namen, also nie Text vom Server. So zerstoert das Unkenntlichmachen keine Marke, wenn das Passwort zufaellig
    darin vorkommt."""


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def _mask(text: str, secret: str) -> str:
    return text.replace(secret, MASK) if secret else text


async def _close(conn: asyncssh.SSHClientConnection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
        await asyncio.wait_for(conn.wait_closed(), timeout=2.0)
    except Exception:  # noqa: BLE001, S110 - Aufraeumen darf nie scheitern
        pass


async def run_once(
    session: AsyncSession,
    *,
    host: Host,
    port: int,
    user_id: str,
    login_user: str,
    password: SecretStr,
    command: str,
    password_on_stdin: bool = False,
    connect_timeout_s: float | None = None,
    command_timeout_s: float | None = None,
    flags: Mapping[str, Callable[[str, str], bool]] | None = None,
) -> RemoteResult:
    """Meldet sich EINMAL mit `login_user`/`password` an `host.address:port` an, fuehrt `command` aus und
    schliesst die Verbindung. Mit `password_on_stdin` bekommt der Befehl das Passwort (mit Zeilenende) als
    Standardeingabe, sonst ist die Standardeingabe sofort zu.

    `flags` ist eine feste Tabelle `{Name: Pruefung(stdout, stderr)}`; jede Pruefung bekommt die unveraenderte
    Ausgabe, zurueck kommen nur die Namen der zutreffenden (`RemoteResult.flags`).

    Reihenfolge: grobe Drossel je Nutzer (`rate_limited`) -> je Server nur ein Lauf gleichzeitig (`busy`) ->
    laesst sich das Passwort ueberhaupt senden? (`invalid_password`) -> Server-Schluessel gemerkt?
    (`host_key_unconfirmed`, kostet keinen Versuch) -> Versuch zaehlen (`rate_limited`) -> Anmeldung mit
    Pruefung des Server-Schluessels (`host_key_changed`, `wrong_password`, `unreachable`) -> Befehl
    (`timeout`). Jeder Fehler ist ein `PasswordUseError`, auch ein unerwarteter (im Log nur die Art): vor dem
    Start des Befehls `connection_failed` (nichts ist gelaufen, ein neuer Versuch ist harmlos), danach
    `connection_lost` (der Befehl kann schon etwas getan haben; auch wenn die Verbindung abreisst, ohne dass er
    einen Ausgang meldet). Ohne Angabe gelten `CONNECT_TIMEOUT_S` und `COMMAND_TIMEOUT_S`."""
    connect_timeout_s = CONNECT_TIMEOUT_S if connect_timeout_s is None else connect_timeout_s
    command_timeout_s = COMMAND_TIMEOUT_S if command_timeout_s is None else command_timeout_s
    _throttle(user_id)
    with _exclusive(host.id):
        secret = password.get_secret_value()
        try:
            secret.encode("utf-8")
        except UnicodeEncodeError:  # z. B. ein einzelnes Surrogat aus JSON („\ud800“)
            del secret
            raise PasswordUseError("invalid_password") from None
        await _require_known_host_key(session, host, port, connect_timeout_s)
        claim_attempt(address=host.address, port=port, user_id=user_id)
        target = ssh.ConnectionTarget(
            host_id=host.id, address=host.address, port=port, username=login_user, kind="ssh_password",
            secret_value=secret, allow_tofu=False,
        )
        stdin_data: bytes | None = None
        stdout = stderr = ""
        conn: asyncssh.SSHClientConnection | None = None
        try:
            try:
                conn = await ssh.connect(session, target, connect_timeout_s=connect_timeout_s, tofu=False)
            except ssh.HostKeyUnknown as exc:
                # Nur wenn der gemerkte Schluessel gerade erst vergessen wurde: wie oben, nichts gesendet.
                if exc.key_type and exc.fingerprint:
                    host_check.remember_seen_key(host, port, exc.key_type, exc.fingerprint)
                raise PasswordUseError(
                    "host_key_unconfirmed", host_key=_key_info("new", exc.key_type, exc.fingerprint, None)
                ) from None
            except ssh.HostKeyMismatch as exc:
                host_check.forget_seen_key(host.id)
                raise PasswordUseError(
                    "host_key_changed", host_key=_key_info("changed", exc.key_type, exc.actual, exc.expected)
                ) from None
            except ssh.SshAuthError:
                raise PasswordUseError("wrong_password") from None
            except (ssh.SshUnreachable, OSError, TimeoutError):
                raise PasswordUseError("unreachable") from None
            except (ssh.SshError, asyncssh.Error, ValueError):
                raise PasswordUseError("connection_failed") from None
            except Exception as exc:  # noqa: BLE001 - nie ein 500 mit Traceback (Ausnahme-Texte koennten Geheimes enthalten)
                log.warning("Einmal-Passwort fuer Server %s: unerwarteter Fehler (%s)", host.id, type(exc).__name__)
                raise PasswordUseError("connection_failed") from None
            try:
                stdin_data = (secret + "\n").encode("utf-8") if password_on_stdin else None
                if stdin_data is not None:
                    result = await conn.run(
                        command, input=stdin_data, check=False, encoding=None, timeout=command_timeout_s
                    )
                else:
                    result = await conn.run(
                        command, stdin=asyncssh.DEVNULL, check=False, encoding=None, timeout=command_timeout_s
                    )
            except TimeoutError:  # auch asyncssh.TimeoutError (ein Untertyp)
                raise PasswordUseError("timeout") from None
            except asyncssh.ChannelOpenError:
                # Der Server hat den Befehl gar nicht erst angenommen: es ist nichts gelaufen.
                raise PasswordUseError("connection_failed") from None
            except (asyncssh.Error, OSError):
                # Ab hier kann der Befehl schon etwas getan haben: nicht „bitte noch einmal“, sondern ehrlich.
                raise PasswordUseError("connection_lost") from None
            except Exception as exc:  # noqa: BLE001 - wie oben
                log.warning("Einmal-Passwort fuer Server %s: unerwarteter Fehler (%s)", host.id, type(exc).__name__)
                raise PasswordUseError("connection_lost") from None
            if result.exit_status is None:
                # Weder Ausgang noch Signal gemeldet: der Kanal ging zu, ohne dass der Befehl sich beendet hat
                # (die Verbindung riss ab). asyncssh wirft dann nicht, es liefert nur das bisher Gelesene.
                raise PasswordUseError("connection_lost") from None
            stdout, stderr = _decode(result.stdout), _decode(result.stderr)
            try:
                found = frozenset(name for name, test in (flags or {}).items() if test(stdout, stderr))
            except Exception as exc:  # noqa: BLE001 - wie oben
                log.warning("Einmal-Passwort fuer Server %s: Auswertung gescheitert (%s)", host.id, type(exc).__name__)
                found = frozenset()
            return RemoteResult(
                exit_status=result.exit_status, stdout=_mask(stdout, secret), stderr=_mask(stderr, secret), flags=found
            )
        except PasswordUseError as exc:
            log.info("Einmal-Passwort fuer Server %s: %s", host.id, exc.code)
            raise
        finally:
            await _close(conn)
            del secret, target, stdin_data, stdout, stderr, conn
