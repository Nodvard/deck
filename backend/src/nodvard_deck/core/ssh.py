"""Gemeinsamer SSH-Ausfuehrungs-Layer -- Terminal, Skript-Ausfuehrung und
KI-Remediation benutzen DENSELBEN Layer (docs/00-DECISIONS.md D-05,
docs/01-ARCHITECTURE.md §2 "ein asyncssh-Verbindungspool, geteilt von Terminal,
Scripts und KI"; docs/02-EXTENSION-API.md §3 CommandRunner: "kein zweiter, separater
Ausfuehrungsweg mit eigenen Edge-Cases").

Dieses Modul ist bewusst generische Kern-Infrastruktur -- es kennt "Terminal" oder
"Extension" nicht, nur Hosts, Zugangsdaten und Befehle (dieselbe Ebene wie
`core/vault.py`, das `cryptography` kapselt, ohne von Secrets-Verwendern zu wissen).

**TOFU + Pinning statt `StrictHostKeyChecking=no`** (D-05 Sicherheitskorrektur): beim
ERSTEN Kontakt zu einem Host/Schluesseltyp wird der Server-Fingerprint in
`known_hosts` gespeichert; jede spaetere Verbindung mit demselben Schluesseltyp muss
exakt dazu passen, sonst scheitert sie sichtbar mit `HostKeyMismatch` statt still zu
akzeptieren (docs/03-DATA-MODEL.md §2).

**Warum das Pinning synchron entscheidet, aber die DB asynchron ist:** asyncssh ruft
`SSHClient.validate_host_public_key()` SYNCHRON aus dem Handshake heraus auf -- ein
`await` darin ist nicht moeglich. Deshalb werden die bekannten Fingerprints VOR
`asyncssh.connect()` async geladen und der `_PinningClient`-Instanz mitgegeben; die
Pruefung selbst ist reiner In-Memory-Vergleich. Ein neu gesehener Schluesseltyp wird
NACH erfolgreichem Connect asynchron gespeichert (TOFU), nicht waehrend des Handshakes.

**Ohne TOFU (`tofu=False`, Sicherheitskern der "Verbindung pruefen"-Funktion):** ist der
Schluessel eines Servers noch nicht bestaetigt, verweigert der `_PinningClient` ihn im
Schluesseltausch. Die Anmeldung beginnt erst NACH dem Schluesseltausch -- an einen
unbestaetigten Server gehen deshalb weder Passwort noch Schluessel, und es wird nichts
gespeichert. Stattdessen `HostKeyUnknown` mit dem Fingerabdruck zum Bestaetigen.
"""

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

from ..models import KnownHostKey

KEEPALIVE_INTERVAL_S = 15.0
KEEPALIVE_COUNT_MAX = 3
"""Keepalive fuer jede Verbindung: verschwindet ein Host ohne FIN/RST
(VM hart gestoppt, Netz weg), merkt asyncssh das sonst erst, wenn TCP nach etwa
15 min aufgibt -- bis dahin gaebe der Pool die tote Verbindung weiter heraus und
jeder Befehl liefe in sein eigenes Timeout. So ist sie nach etwa 45-60 s
geschlossen, `is_closed()` greift und der Pool verbindet neu."""


class SshError(Exception):
    key_type: str | None = None
    fingerprint: str | None = None
    """Hat der Server im Schluesseltausch einen Schluessel gezeigt und der wurde akzeptiert, stehen
    hier Typ und Fingerabdruck (`connect()` setzt sie, bevor es den Fehler wirft). So weiss der
    Aufrufer bei einer abgelehnten Anmeldung, dass der Server-Schluessel selbst in Ordnung war."""


class SshAuthError(SshError):
    """Der Server hat die Anmeldung abgelehnt (falsches Passwort, Schluessel nicht eingetragen,
    Benutzer unbekannt). Der Server-Schluessel war dabei schon geprueft und bekannt."""


class HostKeyUnknown(SshError):
    """Der Server-Schluessel ist noch nicht bestaetigt und TOFU ist aus: es wurde NICHTS
    gesendet (keine Anmeldedaten) und NICHTS gespeichert. `fingerprint` ist der angebotene
    Schluessel, zum Vergleichen und Bestaetigen."""

    def __init__(self, host_id: str, key_type: str | None, fingerprint: str | None) -> None:
        super().__init__(
            "Der Server-Schlüssel von diesem Server ist noch nicht bestätigt. "
            "Unter Einstellungen → Server & Zugänge „Verbindung prüfen“ drücken."
        )
        self.host_id = host_id
        self.key_type = key_type
        self.fingerprint = fingerprint


class HostKeyMismatch(SshError):
    """Der sichtbare Fehler, den D-05 anstelle von `StrictHostKeyChecking=no` verlangt."""

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
    """Waehrend des Verbindungsaufbaus wurden die Verbindungsdaten des Servers geaendert
    (Adresse, Zugang, Schluessel) -- kein "Server nicht erreichbar", nur "bitte nochmal"."""


@dataclass(frozen=True)
class ConnectionTarget:
    """Alles, was `connect()` braucht -- der Klartext lebt hier nur kurzlebig
    (aus `ctx.vault_use()`), nie ausserhalb dieses Bausteins persistiert."""

    host_id: str
    address: str
    port: int
    username: str
    kind: str  # "ssh_key" | "ssh_password"
    secret_value: str
    generation: int | None = None
    """`SshPool.generation(host_id)` VOR dem Lesen der Verbindungsdaten aus der DB. Hat
    `drop_host()` seitdem zugeschlagen, sind die Daten evtl. veraltet und `get()` lehnt
    das Ziel ab. `None` = unbekannt (dann gilt der Stand beim Start von `get()`)."""
    allow_tofu: bool = True
    """`False`: unbekannte Server-Schluessel werden nicht still gemerkt, die Verbindung scheitert
    mit `HostKeyUnknown` (siehe `connect(tofu=...)`). Standard `True` wie bisher."""


class _PinningClient(asyncssh.SSHClient):
    """Synchroner Host-Key-Torwaechter, siehe Modul-Docstring."""

    def __init__(self, known: dict[str, str], allow_tofu: bool = True) -> None:
        self._known = known
        self._allow_tofu = allow_tofu
        self.offered_type: str | None = None
        self.offered_fingerprint: str | None = None
        self.mismatch = False
        self.unknown = False
        """Ein unbekannter Schluessel wurde abgelehnt, weil TOFU aus ist."""

    def validate_host_public_key(self, host: str, addr: str, port: int, key: Any) -> bool:
        key_type = key.get_algorithm()
        fingerprint = key.get_fingerprint()
        self.offered_type = key_type
        self.offered_fingerprint = fingerprint

        expected = self._known.get(key_type)
        if expected is None and self._known:
            # Fuer diesen Host ist schon ein Schluessel gemerkt, der Server zeigt aber einen ANDEREN
            # Typ: das ist kein "neuer" Schluessel, sondern eine Abweichung (sonst koennte ein
            # Mittelsmann das Pinning umgehen, indem er nur einen anderen Typ anbietet).
            self.mismatch = True
            return False
        if expected is None:
            if not self._allow_tofu:
                # Hier, im Schluesseltausch, ist der letzte Moment vor der Anmeldung: wer
                # `False` zurueckgibt, bekommt nie Benutzername, Passwort oder Schluessel zu sehen.
                self.unknown = True
                return False
            return True  # TOFU fuer DIESEN Schluesseltyp an diesem Host
        if expected != fingerprint:
            self.mismatch = True
            return False
        return True


class _InspectClient(asyncssh.SSHClient):
    """Merkt sich den angebotenen Server-Schluessel und lehnt IMMER ab -- die Verbindung
    kommt nie ueber den Schluesseltausch hinaus, es wird nichts gesendet."""

    def __init__(self) -> None:
        self.key_type: str | None = None
        self.fingerprint: str | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key: Any) -> bool:
        self.key_type = key.get_algorithm()
        self.fingerprint = key.get_fingerprint()
        return False


_RSA_SIGNATURE_ALGS = ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")


def _preferred_host_key_algs(known: dict[str, str]) -> list[str] | None:
    """Reihenfolge der Host-Key-Verfahren fuer asyncssh: die gemerkten Typen zuerst, danach die
    uebrigen. `None` (asyncssh-Standard), wenn noch nichts gemerkt ist.

    Der Server waehlt das erste Verfahren der Liste des Clients, das er kann -- ein Server mit
    mehreren Schluesseln zeigt so den gemerkten statt eines anderen. Die uebrigen bleiben
    absichtlich erlaubt: zeigt ein Server NUR einen anderen Typ, soll das als geaenderter Schluessel
    gemeldet werden (`_PinningClient`), nicht als unverstaendlicher Verschluesselungsfehler."""
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
    """Was bisher gemerkt ist, lesbar (fuer Meldungen bei einem anderen Schluesseltyp)."""
    return "; ".join(f"{fp} ({key_type})" for key_type, fp in sorted(known.items())) or None


# Keine Schluessel, kein Agent, keine Konfiguration des Rechners, auf dem Nodvard Deck laeuft
# (Werte aus asyncssh 2.24, SSHClientConnectionOptions): `client_keys=None` schaltet auch die
# Standard-Schluessel aus ~/.ssh UND den ssh-agent ab (`[]` taete das nicht: es laedt die Standard-
# Schluessel); `config=[]` liest weder ~/.ssh/config noch /etc/ssh/ssh_config; `agent_path=None`
# ignoriert SSH_AUTH_SOCK auch dann, wenn ein eigener Schluessel mitgegeben wird; `gss_host=None`
# schaltet Kerberos ab.
_ISOLATED_CLIENT: dict[str, Any] = {"config": [], "agent_path": None, "gss_host": None}


async def _known_fingerprints(session: AsyncSession, host_id: str) -> dict[str, str]:
    result = await session.execute(select(KnownHostKey).where(KnownHostKey.host_id == host_id))
    return {row.key_type: row.fingerprint for row in result.scalars().all()}


async def _remember_fingerprint(
    session: AsyncSession, host_id: str, key_type: str, fingerprint: str
) -> None:
    session.add(KnownHostKey(host_id=host_id, key_type=key_type, fingerprint=fingerprint))
    await session.flush()


async def load_known_fingerprints(session: AsyncSession, host_id: str) -> dict[str, str]:
    """Die gemerkten Server-Schluessel von `host_id` als `{Schluesseltyp: Fingerabdruck}`."""
    return await _known_fingerprints(session, host_id)


async def connect(
    session: AsyncSession, target: ConnectionTarget, *, connect_timeout_s: float = 10.0, tofu: bool = True
) -> asyncssh.SSHClientConnection:
    """Baut eine EINZELNE, gepinnte Verbindung auf. Fuer Wiederverwendung siehe
    `SshPool.get()` -- diese Funktion selbst poolt nicht.

    `tofu` (UND `target.allow_tofu`, beide muessen wahr sein): ist der Schluessel dieses Servers
    noch nicht gemerkt, wird er nur bei wahr still gemerkt. Bei falsch scheitert die Verbindung
    mit `HostKeyUnknown`, bevor Anmeldedaten gesendet werden, und es wird nichts gespeichert."""
    allow_tofu = tofu and target.allow_tofu
    known = await _known_fingerprints(session, target.host_id)
    holder: list[_PinningClient] = []

    def _client_factory() -> asyncssh.SSHClient:
        client = _PinningClient(known, allow_tofu)
        holder.append(client)
        return client

    connect_kwargs: dict[str, Any] = dict(
        host=target.address,
        port=target.port,
        username=target.username,
        # **Live gefunden, nicht in der Doku so offensichtlich:** `known_hosts=None`
        # deaktiviert die Host-Key-Pruefung KOMPLETT -- asyncssh ruft
        # `validate_host_public_key()` dann NIE auf und akzeptiert jeden Schluessel
        # (siehe SSHClientConnectionOptions._validate_host_key: der Aufruf steht in
        # einem `if self._trusted_host_keys is not None:`-Block, der bei `None` nie
        # betreten wird). Das waere exakt das `StrictHostKeyChecking=no`-Verhalten,
        # das D-05 ersetzen soll. Ein leeres 3-Tupel (server_keys, ca_keys,
        # revoked_keys) haelt `_trusted_host_keys` auf einer LEEREN, aber nicht-None
        # Collection -- dadurch wird `validate_host_public_key()` fuer JEDEN
        # Schluessel aufgerufen (er ist nie schon "bekannt"), womit `_PinningClient`
        # tatsaechlich entscheidet.
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
            # Schluesseltausch war durch (sonst waere `offered_*` leer oder der Schluessel abgelehnt).
            error.key_type = holder[0].offered_type
            error.fingerprint = holder[0].offered_fingerprint
        raise error from exc

    client = holder[0]
    if client.offered_type is not None and client.offered_type not in known:
        await _remember_fingerprint(session, target.host_id, client.offered_type, client.offered_fingerprint or "")

    return conn


@dataclass(frozen=True)
class HostKeyReport:
    """Was ein Server im Schluesseltausch gezeigt hat, verglichen mit dem Gemerkten."""

    key_type: str
    fingerprint: str
    """Wie `key.get_fingerprint()`: `SHA256:...`."""
    status: Literal["known", "new", "changed"]
    """`known`: stimmt mit dem gemerkten Schluessel dieses Typs ueberein; `new`: fuer diesen Host
    ist noch GAR KEIN Schluessel gemerkt; `changed`: es ist einer gemerkt, und der Server zeigt einen
    anderen (auch: einen anderen Schluesseltyp)."""
    expected: str | None = None
    """Der gemerkte Fingerabdruck dieses Typs (bei `known` und `changed`); bei einem anderen Typ
    alles Gemerkte als `SHA256:... (typ)`; bei `new` `None`."""


async def inspect_host_key(
    address: str, port: int, known: dict[str, str], *, timeout_s: float = 10.0
) -> HostKeyReport:
    """Liest den Server-Schluessel, OHNE sich anzumelden: der Client lehnt den Schluessel im
    Schluesseltausch immer ab, die Verbindung kommt nie zur Anmeldung. Es werden keine
    Zugangsdaten gebraucht und keine gesendet, und es wird nichts gespeichert. Fuer Server, die
    noch keinen Zugang haben. Auch hier bleiben Standard-Schluessel aus ~/.ssh, der ssh-agent und
    die ssh-Konfiguration des Rechners aussen vor (`_ISOLATED_CLIENT`, `client_keys=None`): selbst
    wenn die Verbindung je weiterkaeme, gaebe es nichts, womit sie sich anmelden koennte.

    Ist fuer den Host schon ein Schluessel gemerkt, zeigt ein Server mit mehreren Schluesseln den
    gemerkten (Verfahren-Reihenfolge, `_preferred_host_key_algs`); ein anderer Typ gilt als
    `changed`, nicht als `new`."""
    holder: list[_InspectClient] = []

    def _client_factory() -> asyncssh.SSHClient:
        client = _InspectClient()
        holder.append(client)
        return client

    try:
        conn = await asyncssh.connect(
            host=address, port=port,
            client_factory=_client_factory,
            known_hosts=([], [], []),  # siehe connect(): nie `None`, sonst wird nichts geprueft
            client_keys=None, password=None,
            connect_timeout=timeout_s,
            login_timeout=timeout_s,
            **_ISOLATED_CLIENT,
            **({"server_host_key_algs": algs} if (algs := _preferred_host_key_algs(known)) else {}),
        )
    except asyncssh.HostKeyNotVerifiable:
        conn = None  # der Normalfall: abgelehnt, nachdem der Schluessel gesehen wurde
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        if not holder or holder[0].key_type is None:
            raise SshError(f"Verbindung zu {address}:{port} fehlgeschlagen: {exc}") from exc
        conn = None
    if conn is not None:  # kann nicht passieren (der Client lehnt immer ab) -- sicherheitshalber zumachen
        await _close_quietly(conn)
    if not holder or holder[0].key_type is None or holder[0].fingerprint is None:
        raise SshError(f"Der Server {address}:{port} hat keinen Schlüssel gezeigt.")
    key_type, fingerprint = holder[0].key_type, holder[0].fingerprint
    expected = known.get(key_type)
    if expected is None:
        if known:  # ein anderer Typ als der gemerkte: Abweichung, nicht "neu" (siehe _PinningClient)
            return HostKeyReport(key_type, fingerprint, "changed", _known_summary(known))
        return HostKeyReport(key_type, fingerprint, "new", None)
    return HostKeyReport(key_type, fingerprint, "known" if expected == fingerprint else "changed", expected)


@dataclass
class _Retired:
    host_id: str
    conn: asyncssh.SSHClientConnection
    handle: asyncio.TimerHandle | None = None


async def _close_quietly(conn: asyncssh.SSHClientConnection) -> None:
    """Verbindung schliessen und kurz auf das Ende warten -- Aufraeumen darf nie scheitern
    und nie haengen."""
    try:
        conn.close()
        await asyncio.wait_for(conn.wait_closed(), timeout=2.0)
    except Exception:  # noqa: BLE001, S110 - Aufraeumen darf nicht scheitern
        pass


_TARGET_CHANGED_TEXT = (
    "Die Verbindungsdaten dieses Servers haben sich geändert, während die Verbindung "
    "aufgebaut wurde. Bitte erneut versuchen."
)


class SshPool:
    """Ein Verbindungspool je (Host, Zugangsdaten) -- D-05: "kann Verbindungen und
    Kanaele poolen". Kein Idle-Timeout in dieser Runde (siehe Abnahmebericht) --
    tote Verbindungen werden bei der naechsten Anfrage erkannt und ersetzt.

    Zwei Wege, einen Host aus dem Pool zu nehmen:
    - `drop_host()`: sofort schliessen. Fuer alles, was Vertrauen oder Ziel aendert
      (Zugang geloescht, Server-Schluessel vergessen, Server geloescht, Adresse
      geaendert) -- eine noch offene Verbindung darf danach nicht weiterarbeiten.
    - `retire_host()`: nur aus dem Pool nehmen, die alte Verbindung laeuft noch eine
      Weile aus (z. B. ein `docker logs -f`). Fuer "neue Rechte sollen gelten"."""

    def __init__(self) -> None:
        self._connections: dict[tuple[str, str], asyncssh.SSHClientConnection] = {}
        # Eine Sperre je (Host, Zugangsdaten) statt einer globalen: ein
        # nicht erreichbarer Host haelt seine Sperre bis zum Connect-Timeout (10 s),
        # das darf Verbindungen zu anderen Hosts nicht aufhalten.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Zaehler je Host, der bei jedem drop_host() steigt: ein Verbindungsaufbau, der
        # waehrenddessen mit den ALTEN Daten (alte Adresse, geloeschter Zugang)
        # fertig wird, darf nicht mehr im Pool landen.
        self._generation: dict[str, int] = {}
        # Ausgemusterte Verbindungen samt ihrem Schliess-Timer.
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
            return conn  # lebende Verbindung: ohne auf irgendeine Sperre zu warten
        # VOR dem Warten auf die Sperre festhalten (nicht erst danach): wer hier mit einem
        # alten `target` wartet, darf nach einem drop_host() nicht mit diesem ins Pool.
        generation = target.generation if target.generation is not None else self._generation.get(target.host_id, 0)
        # `setdefault` ohne `await` dazwischen -- im Event-Loop atomar, braucht
        # darum selbst keine Sperre.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Erneut pruefen: ein paralleler Aufrufer fuer denselben Schluessel kann
            # die Verbindung inzwischen aufgebaut haben.
            conn = self._connections.get(key)
            if conn is not None and not conn.is_closed():
                return conn
            if self._generation.get(target.host_id, 0) != generation:
                raise SshTargetChanged(_TARGET_CHANGED_TEXT)  # schon vor dem Aufbau veraltet
            conn = await connect(session, target, connect_timeout_s=connect_timeout_s)
            if self._generation.get(target.host_id, 0) != generation:
                # drop_host() lief waehrend des Aufbaus: `target` stammt noch aus der
                # Zeit davor und ist evtl. veraltet. Nicht poolen, nicht herausgeben.
                await _close_quietly(conn)
                raise SshTargetChanged(_TARGET_CHANGED_TEXT)
            self._connections[key] = conn
            return conn

    def generation(self, host_id: str) -> int:
        """Stand des Zaehlers, der bei jedem `drop_host()` steigt (siehe `ConnectionTarget.generation`)."""
        return self._generation.get(host_id, 0)

    async def drop_host(self, host_id: str) -> None:
        """Alle Verbindungen (auch ausgemusterte) zu `host_id` sofort schliessen; der
        naechste Zugriff verbindet neu, mit dann aktuellen Daten."""
        self._generation[host_id] = self._generation.get(host_id, 0) + 1
        conns = [self._connections.pop(k) for k in [k for k in self._connections if k[0] == host_id]]
        for retired in [r for r in self._retired if r.host_id == host_id]:
            self._retire_done(retired)
            conns.append(retired.conn)
        # Die Sperren bleiben bewusst bestehen: eine geloeschte Sperre liesse einen
        # Wartenden und einen Neuen gleichzeitig verbinden.
        await asyncio.gather(*(_close_quietly(conn) for conn in conns))

    def retire_host(self, host_id: str, grace_s: float = 900.0) -> None:
        """Die gepoolten Verbindungen zu `host_id` aus dem Pool nehmen (neue Aufrufer
        verbinden neu), die alten nach `grace_s` Sekunden schliessen. So gelten z. B.
        neue Gruppenrechte, ohne ein laufendes `docker logs -f` abzuschneiden."""
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
            except Exception:  # noqa: BLE001 - Aufraeumen darf nicht scheitern
                pass


_pool: SshPool | None = None


def get_ssh_pool() -> SshPool:
    global _pool
    if _pool is None:
        _pool = SshPool()
    return _pool


async def reset_ssh_pool() -> None:
    """Nur fuer Tests/Shutdown: schliesst alle gepoolten Verbindungen und erzwingt
    beim naechsten `get_ssh_pool()` einen frischen Pool."""
    global _pool
    if _pool is not None:
        await _pool.close_all()
    _pool = None


async def run(
    conn: asyncssh.SSHClientConnection, command: str, *, timeout_s: float = 60.0
) -> tuple[int, str, str]:
    """Nur fuer *lesende* Kommandos ohne Nebenwirkung (docs/02 §2 `ctx.exec.run()`).
    Gibt `(exit_code, stdout, stderr)` zurueck.

    `encoding=None` -- live gefunden: laesst man Client und Server bei
    unterschiedlichen Default-Encodings (Server schreibt Bytes, Kanal erwartet Str
    o.ae.), wirft der Server eine uncaught `TypeError` und die GESAMTE Verbindung
    faellt mit "Connection lost", nicht nur der eine Kanal. Rohe Bytes ueberall und
    hier explizit dekodieren ist robuster als sich auf eine Encoding-Uebereinstimmung
    zwischen Kern und (potenziell fremden) SSH-Servern zu verlassen."""
    result = await asyncio.wait_for(
        conn.run(command, check=False, encoding=None), timeout=timeout_s
    )
    return (
        result.exit_status if result.exit_status is not None else -1,
        (result.stdout or b"").decode("utf-8", "replace") if isinstance(result.stdout, bytes) else (result.stdout or ""),
        (result.stderr or b"").decode("utf-8", "replace") if isinstance(result.stderr, bytes) else (result.stderr or ""),
    )


@asynccontextmanager
async def open_shell(
    conn: asyncssh.SSHClientConnection, *, cols: int = 80, rows: int = 24, term_type: str = "xterm"
) -> AsyncIterator[asyncssh.SSHClientProcess]:
    """Interaktive PTY-Sitzung -- die Grundlage fuer das Web-Terminal
    (docs/00-DECISIONS.md D-05)."""
    process = await conn.create_process(term_type=term_type, term_size=(cols, rows), encoding=None)
    try:
        yield process
    finally:
        process.close()


@asynccontextmanager
async def open_command_stream(
    conn: asyncssh.SSHClientConnection, command: str
) -> AsyncIterator[asyncssh.SSHClientProcess]:
    """Ein Kommando, dessen Ausgabe LAUFEND gelesen wird (z. B. `docker logs -f`) --
    `run()` kehrt erst nach dem Ende zurueck, fuer einen endlosen Strom nie.

    Mit PTY, bewusst: ohne PTY bekommt ein entfernter `... -f`-Prozess beim Schliessen
    des Kanals kein Signal und laeuft weiter, bis er das naechste Mal schreibt (bei
    einem ruhigen Container: beliebig lange). Mit PTY haengt sshd beim Schliessen die
    Sitzung auf, der Prozess bekommt SIGHUP und endet sofort. Nebenwirkung: stdout und
    stderr kommen gemischt, Zeilenenden als `\\r\\n` -- fuer eine Log-Ansicht genau
    richtig, der Aufrufer normalisiert die Zeilenenden."""
    process = await conn.create_process(command, term_type="dumb", term_size=(250, 50), encoding=None)
    try:
        yield process
    finally:
        process.close()


async def start_sftp(conn: asyncssh.SSHClientConnection) -> asyncssh.SFTPClient:
    """Grundlage fuer `FileSource` (SFTP) -- docs/02-EXTENSION-API.md §3."""
    return await conn.start_sftp_client()


_DROP_INFO_KEY = "nodvard_deck_drop_hosts"
_drop_tasks: set[asyncio.Task[None]] = set()


def drop_host_after_commit(session: AsyncSession, host_id: str) -> None:
    """Merkt `host_id` vor: sobald die Transaktion dieser Session COMMITTET ist, wird der Host
    (nochmals) aus dem Pool genommen. Noetig zusaetzlich zum Droppen vor dem Commit -- ein
    paralleler Leser mit eigener Session (z. B. `ExecHandle`) kann zwischen Drop und Commit
    noch die ALTEN Daten lesen und damit sofort wieder eine Verbindung poolen."""
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
