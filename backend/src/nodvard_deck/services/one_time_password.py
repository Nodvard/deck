"""Ein fremdes Passwort genau einmal am gespeicherten Ziel mit bekanntem Server-Schlüssel benutzen, nie gespeichert
oder protokolliert."""

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

# Damit das Dashboard kein Werkzeug zum Passwort-Raten wird. Als Versuch zählt jeder Lauf, bei dem das Passwort an den
# Server gehen könnte; ein noch nicht gemerkter Server-Schlüssel kostet keinen Versuch, es wird ja nichts gesendet.
ATTEMPTS_PER_TARGET = 5
"""Höchstens so viele Versuche je Ziel (Adresse und Port) im Zeitfenster, egal, wer es versucht."""
ATTEMPTS_PER_USER = 10
"""Höchstens so viele Versuche je Nutzer im Zeitfenster, egal, an welchem Server."""
ATTEMPT_WINDOW_S = 10 * 60
REQUESTS_PER_USER = 30
"""Grobe Drossel: Höchstens so viele Läufe je Nutzer im Fenster, damit auch Läufe ohne Versuch nicht beliebig oft
verbinden."""
REQUEST_WINDOW_S = 5 * 60
MAX_PASSWORD_LENGTH = 512
CONNECT_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 90.0

MASK = "********"
"""Steht in `RemoteResult` an jeder Stelle, an der der Server das Passwort ausgegeben hat."""

# Zähler und „läuft gerade“ liegen nur im Prozessspeicher und sind nach einem Neustart weg.
_per_target = rate_limit.SlidingWindow(ATTEMPTS_PER_TARGET, ATTEMPT_WINDOW_S)
_per_user = rate_limit.SlidingWindow(ATTEMPTS_PER_USER, ATTEMPT_WINDOW_S)
_requests_per_user = rate_limit.SlidingWindow(REQUESTS_PER_USER, REQUEST_WINDOW_S)
_refusals_logged = rate_limit.SlidingWindow(1, ATTEMPT_WINDOW_S)
"""Je Nutzer kommt eine Sperre (`rate_limited`) einmal im Fenster ins Protokoll."""
_busy: set[str] = set()


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
    """Der Versuch ging nicht: `str(exc)` ist ein fester deutscher Satz, nie mit Text vom Server, damit kein
    Passwort ins Protokoll gelangt."""

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
        """Bei `host_key_unconfirmed` und `host_key_changed`: `{status, key_type, fingerprint, expected}` zum
        Vergleichen und Bestätigen."""
        self.audit = audit
        """`False`: dieselbe Sperre steht für diesen Nutzer schon im Protokoll."""


def _target_key(address: str, port: int) -> str:
    # `pve1.example.` (vollständiger Name mit Punkt am Ende) ist dasselbe Ziel wie `pve1.example`.
    return f"{address.strip().lower().rstrip('.')}:{port}"


def _refused(wait: int, user_id: str) -> PasswordUseError:
    return PasswordUseError("rate_limited", retry_after=wait, audit=_refusals_logged.hit(user_id) == 0)


def claim_attempt(*, address: str, port: int, user_id: str) -> None:
    """Zählt einen Versuch für `user_id` und das Ziel `address:port`; `rate_limited`, wenn eine Grenze erreicht
    ist."""
    # Erst die Grenze des Nutzers: Wer darüber ist, verbraucht nichts vom Budget des Ziels, das auch anderen gehört.
    # Ein abgelehnter Versuch zählt bei der ablehnenden Grenze nicht mit.
    for window, key in ((_per_user, user_id), (_per_target, _target_key(address, port))):
        wait = window.hit(key)
        if wait:
            raise _refused(wait, user_id)


def _throttle(user_id: str) -> None:
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
    """Nur für Tests."""
    _busy.clear()
    _per_target.reset()
    _per_user.reset()
    _requests_per_user.reset()
    _refusals_logged.reset()


def _key_info(status: str, key_type: str | None, fingerprint: str | None, expected: str | None) -> dict[str, Any]:
    return {"status": status, "key_type": key_type or "?", "fingerprint": fingerprint or "?", "expected": expected}


async def _require_known_host_key(session: AsyncSession, host: Host, port: int, timeout_s: float) -> None:
    # Ob ein gemerkter Schlüssel auch passt, prüft danach `ssh.connect` im Schlüsseltausch, bevor das Passwort geht.
    known = await ssh.load_known_fingerprints(session, host.id)
    if known:
        return
    try:
        report = await ssh.inspect_host_key(host.address, port, known, timeout_s=timeout_s)
    except ssh.SshError:
        raise PasswordUseError("unreachable") from None
    host_check.remember_seen_key(host, port, report.key_type, report.fingerprint)
    raise PasswordUseError("host_key_unconfirmed", host_key=_key_info("new", report.key_type, report.fingerprint, None))


@dataclass(frozen=True)
class RemoteResult:
    """Ergebnis des einen Befehls, in `stdout`/`stderr` ist das Passwort durch `MASK` ersetzt."""

    exit_status: int | None
    stdout: str
    stderr: str
    flags: frozenset[str] = frozenset()
    """Namen der Prüfungen aus `flags`, die auf der unveränderten Ausgabe zutrafen, nie Servertext."""
    extracted: Any = None
    """Ergebnis von `extract` (siehe `run_once`) auf der unveränderten Standardausgabe, sonst `None`."""


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
    except Exception:  # noqa: BLE001, S110 - Aufräumen darf nie scheitern
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
    stdin_payload: bytes = b"",
    password_on_stdin: bool = False,
    connect_timeout_s: float | None = None,
    command_timeout_s: float | None = None,
    flags: Mapping[str, Callable[[str, str], bool]] | None = None,
    extract: Callable[[str], Any] | None = None,
) -> RemoteResult:
    """Meldet sich einmal mit `login_user`/`password` an `host.address:port` an, führt `command` mit `stdin_payload`
    (bei `password_on_stdin` gefolgt von der Passwortzeile) auf der Standardeingabe aus und schließt wieder; ein zu
    langer `command` ergibt `ssh.CommandTooLong`, bevor etwas zählt oder gesendet wird."""
    ssh.check_command_line(command)
    connect_timeout_s = CONNECT_TIMEOUT_S if connect_timeout_s is None else connect_timeout_s
    command_timeout_s = COMMAND_TIMEOUT_S if command_timeout_s is None else command_timeout_s
    # Reihenfolge: grobe Drossel, ein Lauf je Server, Passwort sendbar, Server-Schlüssel bekannt (kostet keinen
    # Versuch), erst dann Versuch zählen und anmelden.
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
        # Adresse und Port stammen vom gespeicherten Server, nie aus der Anfrage.
        target = ssh.ConnectionTarget(
            host_id=host.id, address=host.address, port=port, username=login_user, kind="ssh_password",
            secret_value=secret, allow_tofu=False,
        )
        stdin_data = b""
        stdout = stderr = ""
        conn: asyncssh.SSHClientConnection | None = None
        try:
            # Bis hier ist kein Befehl gelaufen, ein neuer Versuch ist harmlos (`connection_failed`).
            try:
                conn = await ssh.connect(session, target, connect_timeout_s=connect_timeout_s, tofu=False)
            except ssh.HostKeyUnknown as exc:
                # Nur wenn der gemerkte Schlüssel gerade erst vergessen wurde: wie oben, nichts gesendet.
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
            except Exception as exc:  # noqa: BLE001 - nie ein 500 mit Traceback (Ausnahmetexte könnten Geheimes tragen)
                log.warning("Einmal-Passwort fuer Server %s: unerwarteter Fehler (%s)", host.id, type(exc).__name__)
                raise PasswordUseError("connection_failed") from None
            try:
                # Das Passwort geht nur auf die Standardeingabe, nie in die Befehlszeile: Die ist auf dem Server
                # für alle sichtbar und steht in Fehlern von asyncssh.
                stdin_data = stdin_payload + ((secret + "\n").encode("utf-8") if password_on_stdin else b"")
                if stdin_data:
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
            extracted: Any = None
            if extract is not None:
                try:
                    extracted = extract(stdout)
                except Exception as exc:  # noqa: BLE001 - wie oben
                    log.warning("Einmal-Passwort fuer Server %s: Auswertung gescheitert (%s)", host.id, type(exc).__name__)
            return RemoteResult(
                exit_status=result.exit_status, stdout=_mask(stdout, secret), stderr=_mask(stderr, secret), flags=found,
                extracted=extracted,
            )
        except PasswordUseError as exc:
            log.info("Einmal-Passwort fuer Server %s: %s", host.id, exc.code)
            raise
        finally:
            # Das Passwort bleibt nicht länger referenziert als nötig.
            await _close(conn)
            del secret, target, stdin_data, stdout, stderr, conn
