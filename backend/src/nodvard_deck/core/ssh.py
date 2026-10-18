"""SSH-Schicht des Kerns: gepinnte, gepoolte asyncssh-Verbindungen für Terminal, Skripte und Remediation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncssh
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models import KnownHostKey, Setting
from .error_text import describe_connection_error

KEEPALIVE_INTERVAL_S = 15.0
KEEPALIVE_COUNT_MAX = 3
"""Ohne Keepalive bemerkt asyncssh einen hart weggefallenen Host erst nach dem TCP-Timeout (etwa 15 min); so ist die
Verbindung nach 45-60 s zu und der Pool verbindet neu."""


class SshError(Exception):
    readable = True
    """Der Text ist ein fertiger deutscher Satz für die Oberfläche (siehe `core.error_text`)."""
    key_type: str | None = None
    fingerprint: str | None = None
    """Typ und Fingerabdruck des akzeptierten Server-Schlüssels, wenn der Fehler erst danach kam (z. B. abgelehnte
    Anmeldung)."""


class SshUnreachable(SshError, OSError):
    """Server nicht erreichbar oder Verbindung abgerissen (Netzwerkfehler, Zeitlimit, Name unbekannt).

    Auch ein `OSError`, damit ein bestehendes `except OSError` greift; `__cause__` ist die ursprüngliche Ausnahme."""


class SshTimeout(SshUnreachable, TimeoutError):
    """Wie `SshUnreachable`, aber der Server hat gar nicht geantwortet (auch ein `TimeoutError`)."""


class SshAuthError(SshError):
    """Der Server hat die Anmeldung abgelehnt (falsches Passwort, Schlüssel nicht eingetragen, Benutzer unbekannt);
    sein Schlüssel war schon geprüft."""


class HostKeyUnknown(SshError):
    """Server-Schlüssel noch nicht bestätigt und TOFU aus: Es wurden weder Anmeldedaten gesendet noch etwas gespeichert.
    `fingerprint` ist der angebotene Schlüssel, zum Vergleichen und Bestätigen."""

    def __init__(self, host_id: str, key_type: str | None, fingerprint: str | None) -> None:
        super().__init__(
            "Der Server-Schlüssel von diesem Server ist noch nicht bestätigt. "
            "Unter Einstellungen → Server & Zugänge „Verbindung prüfen“ drücken."
        )
        self.host_id = host_id
        self.key_type = key_type
        self.fingerprint = fingerprint


class HostKeyMismatch(SshError):
    """Der Server zeigt einen anderen Schlüssel als den gemerkten (möglicher Angriff oder Neuinstallation)."""

    def __init__(self, host_id: str, key_type: str | None, expected: str | None, actual: str | None) -> None:
        super().__init__(
            f"Host-Schlüssel ({key_type}) für Host '{host_id}' hat sich geändert: "
            f"erwartet {expected!r}, jetzt {actual!r}. Verbindung abgelehnt -- "
            f"möglicher Angriff oder Neuinstallation. Erst nach Prüfung in "
            f"`known_hosts` erneut zulassen."
        )
        self.host_id = host_id
        self.key_type = key_type
        self.expected = expected
        self.actual = actual


class SshTargetChanged(SshError):
    """Die Verbindungsdaten des Servers haben sich während des Aufbaus geändert: kein Ausfall, nur "bitte noch mal"."""


@dataclass(frozen=True)
class ConnectionTarget:
    """Alles, was `connect()` braucht. Der Klartext lebt nur kurz (aus dem Tresor) und wird nie gespeichert."""

    host_id: str
    address: str
    port: int
    username: str
    kind: str  # "ssh_key" | "ssh_password"
    secret_value: str = field(repr=False)
    """Passwort oder privater Schlüssel im Klartext; nicht im `repr`, damit er in keinem Traceback landet."""
    generation: int | None = None
    """`SshPool.generation(host_id)` vor dem Lesen der Verbindungsdaten aus der Datenbank. Hat `drop_host()` seitdem
    zugeschlagen, lehnt `get()` das Ziel ab. `None`: es gilt der Stand beim Start von `get()`."""
    allow_tofu: bool = True
    """`False`: ein unbekannter Server-Schlüssel wird nicht still gemerkt, die Verbindung scheitert mit
    `HostKeyUnknown`."""


class _PinningClient(asyncssh.SSHClient):
    """Entscheidet synchron im Schlüsseltausch über den Host-Schlüssel (Pinning)."""

    def __init__(self, known: dict[str, str], allow_tofu: bool = True) -> None:
        self._known = known
        self._allow_tofu = allow_tofu
        self.offered_type: str | None = None
        self.offered_fingerprint: str | None = None
        self.mismatch = False
        self.unknown = False
        """Ein unbekannter Schlüssel wurde abgelehnt, weil TOFU aus ist."""

    def validate_host_public_key(self, host: str, addr: str, port: int, key: Any) -> bool:
        key_type = key.get_algorithm()
        fingerprint = key.get_fingerprint()
        self.offered_type = key_type
        self.offered_fingerprint = fingerprint

        expected = self._known.get(key_type)
        if expected is None and self._known:
            # Anderer Typ als der gemerkte ist eine Abweichung, nicht "neu": sonst könnte ein Mittelsmann das
            # Pinning umgehen, indem er nur einen anderen Typ anbietet.
            self.mismatch = True
            return False
        if expected is None:
            if not self._allow_tofu:
                # Letzter Moment vor der Anmeldung: nach `False` sieht der Server nie Benutzername, Passwort oder
                # Schlüssel.
                self.unknown = True
                return False
            return True  # TOFU für diesen Schlüsseltyp an diesem Host
        if expected != fingerprint:
            self.mismatch = True
            return False
        return True


class _InspectClient(asyncssh.SSHClient):
    """Merkt sich den angebotenen Server-Schlüssel und lehnt immer ab, damit nichts gesendet wird."""

    def __init__(self) -> None:
        self.key_type: str | None = None
        self.fingerprint: str | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key: Any) -> bool:
        self.key_type = key.get_algorithm()
        self.fingerprint = key.get_fingerprint()
        return False


_RSA_SIGNATURE_ALGS = ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")


def _preferred_host_key_algs(known: dict[str, str]) -> list[str] | None:
    """Host-Key-Verfahren für asyncssh: gemerkte Typen zuerst, danach die übrigen; `None`, wenn noch nichts gemerkt ist."""
    # Der Server nimmt das erste Verfahren der Clientliste, das er kann, und zeigt so bei mehreren Schlüsseln den
    # gemerkten. Die übrigen bleiben erlaubt: zeigt ein Server nur einen anderen Typ, soll das als geänderter
    # Schlüssel gemeldet werden (`_PinningClient`) und nicht als unverständlicher Verschlüsselungsfehler.
    if not known:
        return None
    from asyncssh.public_key import get_default_public_key_algs, get_public_key_algs

    available = {alg.decode("ascii") for alg in get_public_key_algs()}
    preferred: list[str] = []
    for key_type in sorted(known):
        for alg in _RSA_SIGNATURE_ALGS if key_type == "ssh-rsa" else (key_type,):
            if alg in available and alg not in preferred:
                preferred.append(alg)
    rest = [alg.decode("ascii") for alg in get_default_public_key_algs()]
    return preferred + [alg for alg in rest if alg not in preferred]


def _known_summary(known: dict[str, str]) -> str | None:
    """Das Gemerkte, lesbar (für Meldungen bei einem anderen Schlüsseltyp)."""
    return "; ".join(f"{fp} ({key_type})" for key_type, fp in sorted(known.items())) or None


# Der Client bleibt vom Rechner abgeschottet (asyncssh 2.24): `client_keys=None` schaltet auch die Standard-Schlüssel
# aus ~/.ssh und den ssh-agent ab (`[]` lädt die Standard-Schlüssel); `config=[]` liest keine ssh_config;
# `agent_path=None` ignoriert SSH_AUTH_SOCK auch bei eigenem Schlüssel; `gss_host=None` schaltet Kerberos ab.
_ISOLATED_CLIENT: dict[str, Any] = {"config": [], "agent_path": None, "gss_host": None}


async def _known_fingerprints(session: AsyncSession, host_id: str) -> dict[str, str]:
    result = await session.execute(select(KnownHostKey).where(KnownHostKey.host_id == host_id))
    return {row.key_type: row.fingerprint for row in result.scalars().all()}


HOST_KEY_CONFIRM_REQUIRED = "ssh.host_key_confirm_required"
"""Merker je Server in `settings` (`scope=HOST_KEY_CONFIRM_SCOPE`, `user_id=<host_id>`): Nach "Schlüssel vergessen" wird
kein Schlüssel still gemerkt, bis er ausdrücklich bestätigt ist. Gesetzt und entfernt in `services/hosts.py`."""
HOST_KEY_CONFIRM_SCOPE = "host"


async def host_key_confirm_pending(session: AsyncSession, host_id: str) -> bool:
    """Liegt für `host_id` der Merker `HOST_KEY_CONFIRM_REQUIRED`? Immer frisch aus der Datenbank."""
    result = await session.execute(
        select(Setting.key).where(
            Setting.key == HOST_KEY_CONFIRM_REQUIRED, Setting.scope == HOST_KEY_CONFIRM_SCOPE,
            Setting.user_id == host_id,
        )
    )
    return result.first() is not None


async def _remember_fingerprint(
    session: AsyncSession, host_id: str, key_type: str, fingerprint: str
) -> None:
    session.add(KnownHostKey(host_id=host_id, key_type=key_type, fingerprint=fingerprint))
    await session.flush()


async def load_known_fingerprints(session: AsyncSession, host_id: str) -> dict[str, str]:
    """Die gemerkten Server-Schlüssel von `host_id` als `{Schlüsseltyp: Fingerabdruck}`."""
    return await _known_fingerprints(session, host_id)


async def connect(
    session: AsyncSession, target: ConnectionTarget, *, connect_timeout_s: float = 10.0, tofu: bool = True
) -> asyncssh.SSHClientConnection:
    """Baut eine einzelne, gepinnte Verbindung auf (gepoolt wird in `SshPool.get()`). Ist `tofu` oder
    `target.allow_tofu` falsch, scheitert ein ungemerkter Schlüssel mit `HostKeyUnknown`, bevor Anmeldedaten gesendet
    werden."""
    allow_tofu = tofu and target.allow_tofu
    # Vor dem Connect laden: asyncssh fragt den Schlüssel synchron im Handshake ab, ein `await` ist dort nicht möglich.
    # Ein neu gesehener Typ wird deshalb erst nach dem Connect gespeichert.
    known = await _known_fingerprints(session, target.host_id)
    if allow_tofu and not known and await host_key_confirm_pending(session, target.host_id):
        # `target` kann von kurz vor "Schlüssel vergessen" stammen. Den Merker erst nach den gemerkten Schlüsseln lesen:
        # ist der Schlüssel weg, ist der Merker (selber Commit) auch schon zu sehen.
        allow_tofu = False
    holder: list[_PinningClient] = []

    def _client_factory() -> asyncssh.SSHClient:
        client = _PinningClient(known, allow_tofu)
        holder.append(client)
        return client

    connect_kwargs: dict[str, Any] = dict(
        host=target.address,
        port=target.port,
        username=target.username,
        # Nie `known_hosts=None`: das schaltet die Host-Key-Prüfung ganz ab, asyncssh ruft `validate_host_public_key()`
        # dann nicht auf und akzeptiert jeden Schlüssel. Das leere Tupel (server_keys, ca_keys, revoked_keys) ist
        # nicht-None, also wird für jeden Schlüssel nachgefragt und `_PinningClient` entscheidet.
        known_hosts=([], [], []),
        client_factory=_client_factory,
        connect_timeout=connect_timeout_s,
        keepalive_interval=KEEPALIVE_INTERVAL_S,
        keepalive_count_max=KEEPALIVE_COUNT_MAX,
        **_ISOLATED_CLIENT,
    )
    host_key_algs = _preferred_host_key_algs(known)
    if host_key_algs is not None:
        connect_kwargs["server_host_key_algs"] = host_key_algs
    if target.kind == "ssh_password":
        connect_kwargs["password"] = target.secret_value
        connect_kwargs["client_keys"] = None
    elif target.kind == "ssh_key":
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(target.secret_value)]
    else:
        raise SshError(f"Unbekannte Credential-Art {target.kind!r} für SSH.")

    try:
        conn = await asyncssh.connect(**connect_kwargs)
    except (OSError, asyncio.TimeoutError) as exc:
        # Netzfehler: nicht erreichbar, abgelehnt, Name unbekannt, Zeitlimit. Ein `TimeoutError()` hat keinen Text,
        # ohne diese Umwandlung stünde nirgends, warum es nicht geht.
        error_type = SshTimeout if isinstance(exc, TimeoutError) else SshUnreachable
        raise error_type(describe_connection_error(exc, address=target.address, port=target.port, network=True)) from exc
    except asyncssh.Error as exc:
        if holder and holder[0].mismatch:
            client = holder[0]
            raise HostKeyMismatch(
                target.host_id, client.offered_type,
                known.get(client.offered_type or "") or _known_summary(known), client.offered_fingerprint,
            ) from exc
        if holder and holder[0].unknown:
            client = holder[0]
            raise HostKeyUnknown(target.host_id, client.offered_type, client.offered_fingerprint) from exc
        error_class = SshAuthError if isinstance(exc, asyncssh.PermissionDenied) else SshError
        error = error_class(f"Verbindung zu {target.address}:{target.port} fehlgeschlagen: {exc}")
        if holder:
            # Schlüsseltausch war durch, sonst wäre `offered_*` leer oder der Schlüssel abgelehnt.
            error.key_type = holder[0].offered_type
            error.fingerprint = holder[0].offered_fingerprint
        raise error from exc

    client = holder[0]
    if client.offered_type is not None and client.offered_type not in known:
        await _remember_fingerprint(session, target.host_id, client.offered_type, client.offered_fingerprint or "")

    return conn


@dataclass(frozen=True)
class HostKeyReport:
    """Was ein Server im Schlüsseltausch gezeigt hat, verglichen mit dem Gemerkten."""

    key_type: str
    fingerprint: str
    """Wie `key.get_fingerprint()`: `SHA256:...`."""
    status: Literal["known", "new", "changed"]
    """`known`: stimmt mit dem Gemerkten dieses Typs überein; `new`: für den Host ist noch nichts gemerkt;
    `changed`: es ist etwas gemerkt und der Server zeigt anderes (auch einen anderen Typ)."""
    expected: str | None = None
    """Gemerkter Fingerabdruck dieses Typs (`known`, `changed`); bei einem anderen Typ alles Gemerkte als
    `SHA256:... (typ)`; bei `new` `None`."""


async def inspect_host_key(
    address: str, port: int, known: dict[str, str], *, timeout_s: float = 10.0
) -> HostKeyReport:
    """Liest den Server-Schlüssel, ohne sich anzumelden: der Client lehnt ihn im Schlüsseltausch immer ab. Keine
    Zugangsdaten, nichts wird gespeichert; ein anderer Typ als der gemerkte gilt als `changed`, nicht als `new`."""
    holder: list[_InspectClient] = []

    def _client_factory() -> asyncssh.SSHClient:
        client = _InspectClient()
        holder.append(client)
        return client

    try:
        conn = await asyncssh.connect(
            host=address, port=port,
            client_factory=_client_factory,
            known_hosts=([], [], []),  # siehe connect(): nie `None`, sonst wird nichts geprüft
            client_keys=None, password=None,
            connect_timeout=timeout_s,
            login_timeout=timeout_s,
            **_ISOLATED_CLIENT,
            **({"server_host_key_algs": algs} if (algs := _preferred_host_key_algs(known)) else {}),
        )
    except asyncssh.HostKeyNotVerifiable:
        conn = None  # der Normalfall: abgelehnt, nachdem der Schlüssel gesehen wurde
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        if not holder or holder[0].key_type is None:
            raise SshError(f"Verbindung zu {address}:{port} fehlgeschlagen: {exc}") from exc
        conn = None
    if conn is not None:  # kann nicht passieren, der Client lehnt immer ab; sicherheitshalber zumachen
        await _close_quietly(conn)
    if not holder or holder[0].key_type is None or holder[0].fingerprint is None:
        raise SshError(f"Der Server {address}:{port} hat keinen Schlüssel gezeigt.")
    key_type, fingerprint = holder[0].key_type, holder[0].fingerprint
    expected = known.get(key_type)
    if expected is None:
        if known:  # anderer Typ als der gemerkte: Abweichung, nicht "neu" (siehe _PinningClient)
            return HostKeyReport(key_type, fingerprint, "changed", _known_summary(known))
        return HostKeyReport(key_type, fingerprint, "new", None)
    return HostKeyReport(key_type, fingerprint, "known" if expected == fingerprint else "changed", expected)


@dataclass
class _Retired:
    host_id: str
    conn: asyncssh.SSHClientConnection
    handle: asyncio.TimerHandle | None = None


async def _close_quietly(conn: asyncssh.SSHClientConnection) -> None:
    """Verbindung schließen und kurz auf das Ende warten; Aufräumen darf weder scheitern noch hängen."""
    try:
        conn.close()
        await asyncio.wait_for(conn.wait_closed(), timeout=2.0)
    except Exception:  # noqa: BLE001, S110 - Aufräumen darf nicht scheitern
        pass


_TARGET_CHANGED_TEXT = (
    "Die Verbindungsdaten dieses Servers haben sich geändert, während die Verbindung "
    "aufgebaut wurde. Bitte erneut versuchen."
)


class SshPool:
    """Ein Verbindungspool je (Host, Zugangsdaten); tote Verbindungen werden bei der nächsten Anfrage ersetzt."""

    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], asyncssh.SSHClientConnection] = {}
        # Eine Sperre je (Host, Zugangsdaten): ein nicht erreichbarer Host hält seine bis zum Connect-Timeout,
        # das darf Verbindungen zu anderen Hosts nicht aufhalten.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Steigt bei jedem drop_host(): ein Aufbau mit den alten Daten (alte Adresse, gelöschter Zugang) darf nicht
        # mehr im Pool landen.
        self._generation: dict[str, int] = {}
        self._retired: list[_Retired] = []

    async def get(
        self,
        session: AsyncSession,
        target: ConnectionTarget,
        *,
        credential_id: str,
        connect_timeout_s: float = 10.0,
    ) -> asyncssh.SSHClientConnection:
        key = (target.host_id, credential_id)
        conn = self._connections.get(key)
        if conn is not None and not conn.is_closed():
            return conn  # Schnellweg: lebende Verbindung, keine Sperre nötig
        # Vor dem Warten auf die Sperre festhalten: wer mit einem alten `target` wartet, darf nach einem
        # drop_host() nicht mit diesem in den Pool.
        generation = target.generation if target.generation is not None else self._generation.get(target.host_id, 0)
        # `setdefault` ohne `await` ist im Event-Loop atomar und braucht keine eigene Sperre.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Erneut prüfen: ein paralleler Aufrufer kann die Verbindung inzwischen aufgebaut haben.
            conn = self._connections.get(key)
            if conn is not None and not conn.is_closed():
                return conn
            if self._generation.get(target.host_id, 0) != generation:
                raise SshTargetChanged(_TARGET_CHANGED_TEXT)  # schon vor dem Aufbau veraltet
            conn = await connect(session, target, connect_timeout_s=connect_timeout_s)
            if self._generation.get(target.host_id, 0) != generation:
                # drop_host() lief während des Aufbaus: `target` kann veraltet sein, nicht poolen und nicht herausgeben.
                await _close_quietly(conn)
                raise SshTargetChanged(_TARGET_CHANGED_TEXT)
            self._connections[key] = conn
            return conn

    def generation(self, host_id: str) -> int:
        """Stand des Zählers, der bei jedem `drop_host()` steigt (siehe `ConnectionTarget.generation`)."""
        return self._generation.get(host_id, 0)

    async def drop_host(self, host_id: str) -> None:
        """Schließt alle Verbindungen (auch ausgemusterte) zu `host_id` sofort, für alles, was Vertrauen oder Ziel
        ändert; der nächste Zugriff verbindet mit dann aktuellen Daten."""
        self._generation[host_id] = self._generation.get(host_id, 0) + 1
        conns = [self._connections.pop(k) for k in [k for k in self._connections if k[0] == host_id]]
        for retired in [r for r in self._retired if r.host_id == host_id]:
            self._retire_done(retired)
            conns.append(retired.conn)
        # Die Sperren bleiben bewusst: eine gelöschte ließe einen Wartenden und einen Neuen gleichzeitig verbinden.
        await asyncio.gather(*(_close_quietly(conn) for conn in conns))

    def retire_host(self, host_id: str, grace_s: float = 900.0) -> None:
        """Nimmt die Verbindungen zu `host_id` aus dem Pool und schließt die alten erst nach `grace_s` Sekunden,
        damit bei neuen Gruppenrechten kein laufendes `tail -f` abreißt."""
        loop = asyncio.get_running_loop()
        for key in [k for k in self._connections if k[0] == host_id]:
            retired = _Retired(host_id, self._connections.pop(key))
            retired.handle = loop.call_later(grace_s, self._expire, retired)
            self._retired.append(retired)

    def _retire_done(self, retired: _Retired) -> None:
        if retired.handle is not None:
            retired.handle.cancel()
        if retired in self._retired:
            self._retired.remove(retired)

    def _expire(self, retired: _Retired) -> None:
        self._retire_done(retired)
        retired.conn.close()

    async def close_all(self) -> None:
        conns = list(self._connections.values())
        self._connections.clear()
        for retired in list(self._retired):
            self._retire_done(retired)
            conns.append(retired.conn)
        for conn in conns:
            conn.close()
        for conn in conns:
            try:
                await conn.wait_closed()
            except Exception:  # noqa: BLE001 - Aufräumen darf nicht scheitern
                pass


_pool: SshPool | None = None


def get_ssh_pool() -> SshPool:
    global _pool
    if _pool is None:
        _pool = SshPool()
    return _pool


async def reset_ssh_pool() -> None:
    """Nur für Tests und Shutdown: schließt alle Pool-Verbindungen; der nächste `get_ssh_pool()` baut einen neuen Pool."""
    global _pool
    if _pool is not None:
        await _pool.close_all()
    _pool = None


async def run(
    conn: asyncssh.SSHClientConnection, command: str, *, timeout_s: float = 60.0, stdin: bytes | None = None,
) -> tuple[int, str, str]:
    """Führt ein *lesendes* Kommando ohne Nebenwirkung aus und liefert `(exit_code, stdout, stderr)`.

    `stdin`: Daten für die Standardeingabe, danach EOF. Ohne (None) bleibt die Standardeingabe wie bisher offen."""
    # `encoding=None`: bei abweichenden Standard-Kodierungen von Client und Server wirft der Server einen unbehandelten
    # `TypeError`, und die ganze Verbindung fällt mit "Connection lost", nicht nur der Kanal. Rohe Bytes und
    # explizites Dekodieren sind robuster als auf übereinstimmende Kodierungen fremder Server zu bauen.
    # asyncssh schreibt `input` samt EOF nur, wenn es nicht leer ist; leere Daten bekommen deshalb `DEVNULL` (sofort
    # EOF), sonst wartet ein lesender Prozess bis zur Zeitgrenze.
    redirect: dict[str, Any] = {}
    if stdin is not None:
        redirect = {"input": stdin} if stdin else {"stdin": asyncssh.DEVNULL}
    result = await asyncio.wait_for(
        conn.run(command, check=False, encoding=None, **redirect), timeout=timeout_s
    )
    return (
        result.exit_status if result.exit_status is not None else -1,
        (result.stdout or b"").decode("utf-8", "replace") if isinstance(result.stdout, bytes) else (result.stdout or ""),
        (result.stderr or b"").decode("utf-8", "replace") if isinstance(result.stderr, bytes) else (result.stderr or ""),
    )


@asynccontextmanager
async def open_shell(
    conn: asyncssh.SSHClientConnection, *, cols: int = 80, rows: int = 24, term_type: str = "xterm",
    command: str | None = None,
) -> AsyncIterator[asyncssh.SSHClientProcess]:
    """Interaktive PTY-Sitzung für das Web-Terminal; mit `command` läuft statt der Login-Shell genau dieses Kommando."""
    # Ohne Positionsargument startet asyncssh die Login-Shell.
    args = (command,) if command is not None else ()
    process = await conn.create_process(*args, term_type=term_type, term_size=(cols, rows), encoding=None)
    try:
        yield process
    finally:
        process.close()


@asynccontextmanager
async def open_command_stream(
    conn: asyncssh.SSHClientConnection, command: str
) -> AsyncIterator[asyncssh.SSHClientProcess]:
    """Ein Kommando mit laufend gelesener Ausgabe (z. B. `tail -f`); `run()` kehrt bei einem endlosen Strom nie zurück."""
    # Bewusst mit PTY: ohne bekommt ein entferntes `... -f` beim Schließen des Kanals kein Signal und läuft weiter, bis
    # es das nächste Mal schreibt. Mit PTY hängt sshd die Sitzung auf und der Prozess bekommt SIGHUP. Nebenwirkung:
    # stdout und stderr kommen gemischt, Zeilenenden `\r\n`; der Aufrufer normalisiert sie.
    process = await conn.create_process(command, term_type="dumb", term_size=(250, 50), encoding=None)
    try:
        yield process
    finally:
        process.close()


async def start_sftp(conn: asyncssh.SSHClientConnection) -> asyncssh.SFTPClient:
    """Grundlage für `FileSource` (SFTP)."""
    return await conn.start_sftp_client()


_DROP_INFO_KEY = "nodvard_deck_drop_hosts"
_drop_tasks: set[asyncio.Task[None]] = set()


def drop_host_after_commit(session: AsyncSession, host_id: str) -> None:
    """Nimmt `host_id` nach dem Commit dieser Session nochmals aus dem Pool.

    Ein paralleler Leser mit eigener Session sieht bis dahin noch alte Daten und würde sofort neu poolen."""
    session.sync_session.info.setdefault(_DROP_INFO_KEY, set()).add(host_id)


def _after_commit(session: Session) -> None:
    host_ids = session.info.pop(_DROP_INFO_KEY, None)
    if not host_ids:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # kein Event-Loop (z. B. synchroner Aufrufer): nichts zu tun
        return
    pool = get_ssh_pool()
    for host_id in host_ids:
        task = loop.create_task(pool.drop_host(host_id))
        _drop_tasks.add(task)
        task.add_done_callback(_drop_tasks.discard)


def _after_rollback(session: Session) -> None:
    session.info.pop(_DROP_INFO_KEY, None)


event.listen(Session, "after_commit", _after_commit)
event.listen(Session, "after_rollback", _after_rollback)
