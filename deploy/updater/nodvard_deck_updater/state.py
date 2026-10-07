"""Eigener Zustand des Helfers in `/state`.

`/state` ist ein eigenes Volume, das das Dashboard nie sieht. Der Helfer trifft Entscheidungen **nur** auf
Grundlage dieses Ordners und der Engine -- nie aus `status.json`, dem Kanal (ausser den Anforderungen) oder
`/app/data`.

* Ordner: root, Modus 0700 (wird beim Oeffnen gesetzt); Dateien 0600.
* `lock` -- `flock`: hoechstens ein Helfer arbeitet. Ein zweiter bekommt `second_instance` und tut nichts.
  Die Sperre gilt je geoeffneter Datei und endet mit dem Prozess, auch bei einem Absturz.
* `state.json` -- `slot` (Rueckweg, `policy.Slot`), `actions` (Zeitpunkte angenommener Aktionen),
  `blocked` (`{version: bis}`, 24 h nach einem Rueckweg), `seen` (`{id: zeit}`, 1 h), `hold_until` und `results` (die
  letzten Ergebnisse fuer `status.results`: der Ablauf haelt ein Ergebnis hier fest, bevor er das Journal loescht, so
  geht es auch bei einem Absturz dazwischen nicht verloren).
* `journal.json` -- der laufende Vorgang. **Write-ahead:** der Ablauf schreibt den naechsten Schritt,
  *bevor* er ihn ausfuehrt; jedes Schreiben endet mit fsync auf Datei **und** Ordner. Ebenso kuendigt er einen
  Rueckbau an (`undo`, mit dem Grund in `code`), bevor er den ersten Teil davon ausfuehrt: So weiss die
  Wiederaufnahme nach einem Absturz, ob ein halb zurueckgebauter Stand von ihm selbst stammt oder von aussen.

Geschrieben wird immer atomar: Zufallsname mit `O_CREAT|O_EXCL|O_NOFOLLOW`, fsync, `os.replace`, fsync des
Ordners. Gelesen wird ueber den Ordner-Deskriptor mit `O_NOFOLLOW|O_NONBLOCK`, danach `fstat`: regulaere Datei,
ein Link, richtiger Besitzer, Groessengrenze.

**Zwei Arten von Fehlern beim Lesen -- nie verwechseln:**

* *Die Datei ist ungueltig* (`UnsafeFile`, kein JSON, falsches Schema, Symlink, falscher Besitzer, zu gross, ...):
  der Inhalt ist kaputt, ein zweiter Versuch aendert nichts. Dann gilt der sichere Zustand (unten).
* *Die Datei ist gerade nicht lesbar* (`ReadFailed`: `EMFILE`, `ENFILE`, `ENOMEM`, `EIO`, `ESTALE`, ... beim
  Oeffnen, `fstat` oder `read`): ein voruebergehender Fehler sagt nichts ueber den Inhalt. Der Helfer fasst dann
  **nichts** an -- nichts umbenannt, nichts ueberschrieben, keine Sperre gesetzt --, wirft `StateUnsafe`
  (`state_read` bzw. `journal_read`), tut diese Runde nichts und liest spaeter erneut. So wird aus einem
  einzelnen Lesefehler nie ein verlorener Rueckweg oder ein liegen gelassener halber Vorgang.

**Kaputter Zustand -> definierter sicherer Zustand (keine Aktionen):**

* `state.json` ungueltig (kein JSON, falsches Schema, Symlink, falscher Besitzer, zu gross, ...): Der Helfer
  ersetzt sie durch einen leeren Zustand **ohne Rueckweg-Slot** mit `hold_until = jetzt + 24 h` und schreibt
  ihn sofort (ein Neustart setzt die Sperre also nicht zurueck). Bis dahin lehnt er jede Anforderung mit
  `state_unsafe` ab (`policy.check_limits`). 24 h decken alle zeitlichen Grenzen ab (10 min Aktionen, 24 h
  Sperre nach Rueckweg, 1 h IDs); der verlorene Slot heisst nur: kein Rueckweg auf Knopfdruck (fail-closed).
  Lesbarer Inhalt bleibt zur Ansicht als `state.json.broken` liegen. **Im Zweifel bleibt es gesperrt:** scheitert
  das Schreiben des sicheren Zustands, bleibt die ungueltige Datei liegen (sie wird nur beiseitegeschoben, wenn
  `state.json` ein Ordner ist), und beim naechsten Start gilt wieder der sichere Zustand. Fehlt `state.json`, aber
  `state.json.broken` liegt da, war der Zustand schon einmal kaputt -- auch das gilt als sicherer Zustand.
* `journal.json` ungueltig: Der Helfer setzt dieselbe 24-h-Sperre im Zustand, legt das Journal als
  `journal.json.broken` beiseite und meldet `JournalCorrupt` (mit dem gesperrten Zustand in `.state`). Der Ablauf
  handelt dann **nicht** automatisch, denn er weiss nicht, was schon geschehen ist (nie gegen den Nutzer).
  **Die Sperre gehoert dem Speicher:** der Store merkt sich die gesetzte Sperre (`_hold_floor`) und
  `save_state` schreibt nie weniger -- ein vorher geladener `State` kann sie nicht mehr ueberschreiben, in welcher
  Reihenfolge der Ablauf auch laedt und speichert. **Geprueft wird deshalb ueber den Store:** `StateStore.check`
  wendet die Sperre vor den Grenzen auf den uebergebenen `State` an (ein `State`, der vor dem Erkennen des kaputten
  Journals geladen wurde, kennt sie sonst nicht). Es gibt bewusst kein `State.check`.
* **Zeitstempel in der Zukunft** (die Uhr war vorgesprungen und wurde zurueckgestellt) sperren weiter, aber nur
  bis zur Hoechstfrist: `State.prune()` (das auch `StateStore.check()` zuerst aufruft) kappt Eintraege, die weiter als
  `CLOCK_SLACK_S` in der Zukunft liegen, auf "jetzt" bzw. "jetzt + Hoechstfrist" (Aktionen und IDs: gerade eben,
  Sperre nach Rueckweg und `hold_until`: 24 h). Ein einzelner Uhrsprung sperrt so nie laenger als die Frist selbst.
  Das gilt auch fuer die Sperre des Speichers (`_hold_floor`): sie wird mitgekappt, sonst hobe sie die Kappung von
  `hold_until` bei jedem Laden wieder auf. Gespeichert wird beim Laden nur, wenn sich am Zustand wirklich etwas
  geaendert hat.
* Der Ordner selbst unsicher (Symlink, kein Ordner, falscher Besitzer) -> `state_unsafe`, der Helfer arbeitet
  nicht. Die Automatik zur Wiederherstellung (Journal) wird von den Grenzen nie gedrosselt; das regelt der
  Ablauf.

Fuer Tests ohne root nimmt der Konstruktor die erwartete uid (`expected_uid`, Vorgabe 0). Nie aus der Umgebung.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import fcntl
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from typing import Any, Self

from . import policy
from .policy import Refusal

STATE_NAME = "state.json"
JOURNAL_NAME = "journal.json"
LOCK_NAME = "lock"
BROKEN_SUFFIX = ".broken"
DIR_MODE = 0o700
FILE_MODE = 0o600
STATE_FORMAT = 1
JOURNAL_FORMAT = 1
MAX_STATE_BYTES = 256 * 1024
MAX_JOURNAL_BYTES = 64 * 1024
MAX_ACTIONS = 64
MAX_BLOCKED = 64
MAX_SEEN = 1024
"""Obergrenze der gemerkten IDs. Darueber fallen die aeltesten weg -- das oeffnet keinen Replay, der nicht auch mit
einer frischen ID ginge (die Grenzen gelten fuer jede Anforderung)."""
HOLD_AFTER_CORRUPT_S = 24 * 3600

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY
_TMP_RE = re.compile(r"\.(?:state|journal|broken)-[0-9a-f]{16}\.tmp", re.ASCII)


class UnsafeFile(Exception):
    """Eine Datei ist nicht das, was sie sein soll (keine regulaere Datei, mehrere Links, falscher Besitzer,
    zu gross, ausgetauscht). Der Text ist ein fester Bezeichner."""


class ReadFailed(Exception):
    """Die Datei liess sich gerade nicht lesen (voruebergehender Fehler beim Oeffnen, `fstat` oder `read`:
    `EMFILE`, `ENFILE`, `ENOMEM`, `EIO`, `ESTALE`, ...). Das sagt nichts ueber ihren Inhalt: der Aufrufer fasst
    nichts an und versucht es spaeter erneut -- anders als bei `UnsafeFile`, wo der Inhalt ungueltig ist."""


# Fehler beim Oeffnen, die etwas ueber die *Datei* sagen (Symlink, keine Rechte, kein Datei-Typ, ...) -- nicht
# ueber den Zustand des Rechners. Jeder andere Fehler gilt als voruebergehend (`ReadFailed`).
_CONTENT_ERRNOS = frozenset({
    errno.ELOOP, errno.EACCES, errno.EPERM, errno.ENXIO, errno.ENODEV, errno.EISDIR, errno.ENOTDIR, errno.EINVAL,
    errno.ENAMETOOLONG, errno.EOPNOTSUPP, errno.EOVERFLOW, errno.EFBIG,
})


class StateUnsafe(Refusal):
    def __init__(self, detail: str | None = None) -> None:
        super().__init__(policy.STATE_UNSAFE, detail)


class SecondInstance(Refusal):
    def __init__(self) -> None:
        super().__init__(policy.SECOND_INSTANCE)


class JournalCorrupt(Refusal):
    """Das Journal war ungueltig. Es liegt jetzt als `journal.json.broken` daneben, die 24-h-Sperre ist gesetzt.

    `state` ist der gesperrte Zustand, wie er jetzt auf der Platte steht. **Ein vorher geladener `State` ist
    damit veraltet:** `state` uebernehmen oder neu laden (`save_state` faellt ohnehin nie hinter die Sperre
    zurueck, siehe `StateStore.hold`, und `StateStore.check` wendet sie auch auf einen alten `State` an)."""

    def __init__(self, state: State | None = None) -> None:
        super().__init__(policy.STATE_UNSAFE, "journal_corrupt")
        self.state = state


# ---------------------------------------------------------------------------
# Sichere Datei-Helfer (auch fuer den Kanal)
# ---------------------------------------------------------------------------


def read_regular(
    dir_fd: int,
    name: str,
    *,
    max_bytes: int,
    owner: int | None = None,
    identity: tuple[int, int] | None = None,
) -> bytes:
    """Liest eine kleine regulaere Datei `name` relativ zu `dir_fd`.

    Oeffnet mit `O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC|O_NOCTTY` (ein Symlink scheitert, ein FIFO blockiert
    nicht, ein Terminal wird nie Kontrollterminal) und prueft dann am **Deskriptor**: regulaere Datei, genau ein
    Link, hoechstens `max_bytes`, ggf. Besitzer `owner` und `(st_dev, st_ino) == identity`. Gelesen werden
    hoechstens `max_bytes + 1` Byte; ist es mehr, war die Datei gewachsen -> `UnsafeFile`.

    `FileNotFoundError` bleibt `FileNotFoundError`. Eine ungueltige Datei (Symlink, falscher Typ, Besitzer,
    Groesse, ... und Oeffnen-Fehler aus `_CONTENT_ERRNOS`) ist `UnsafeFile`. Ein voruebergehender Fehler (anderer
    Fehler beim Oeffnen, jeder Fehler bei `fstat`/`read`) ist `ReadFailed` -- der Inhalt ist damit nicht bewertet.
    """
    try:
        fd = os.open(name, READ_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeFile("symlink") from None
        if exc.errno in _CONTENT_ERRNOS:
            raise UnsafeFile("open_failed") from None
        raise ReadFailed("open_failed") from None
    try:
        try:
            st = os.fstat(fd)
        except OSError:
            raise ReadFailed("fstat_failed") from None
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeFile("not_regular")
        if st.st_nlink != 1:
            raise UnsafeFile("links")
        if owner is not None and st.st_uid != owner:
            raise UnsafeFile("owner")
        if identity is not None and (st.st_dev, st.st_ino) != identity:
            raise UnsafeFile("replaced")
        if st.st_size > max_bytes:
            raise UnsafeFile("too_large")
        chunks: list[bytes] = []
        size = 0
        try:
            while size <= max_bytes:
                chunk = os.read(fd, max_bytes + 1 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
        except OSError:
            raise ReadFailed("read_failed") from None
    finally:
        os.close(fd)
    if size > max_bytes:
        raise UnsafeFile("too_large")
    return b"".join(chunks)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def write_atomic(dir_fd: int, name: str, data: bytes, *, mode: int, tmp_prefix: str, durable: bool = True) -> None:
    """Schreibt `name` relativ zu `dir_fd` atomar: neue Datei unter einem Zufallsnamen (`O_CREAT|O_EXCL|
    O_NOFOLLOW`, ein vorab hingelegter Symlink wird also nie verfolgt), `fchmod(mode)`, fsync, `os.replace`
    (ersetzt auch einen Symlink an `name` selbst, nie sein Ziel), fsync des Ordners.

    `durable=False` spart die beiden fsync (nur fuer den Heartbeat des Status gedacht).
    Bei einem Fehler bleibt die alte Datei unveraendert und der Zufallsname wird entfernt.
    """
    fd = -1
    tmp = ""
    for _ in range(3):
        tmp = f"{tmp_prefix}{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(tmp, _CREATE_FLAGS, mode, dir_fd=dir_fd)
            break
        except FileExistsError:
            continue
    if fd < 0:
        raise FileExistsError(errno.EEXIST, "kein freier Name fuer die Temp-Datei")
    try:
        try:
            os.fchmod(fd, mode)
            _write_all(fd, data)
            if durable:
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise
    if durable:
        os.fsync(dir_fd)


def remove_stale_tmp(dir_fd: int, pattern: re.Pattern[str]) -> int:
    """Entfernt Reste abgebrochener Schreibvorgaenge (Namen nach `pattern`, nur regulaere Dateien)."""
    removed = 0
    with os.scandir(dir_fd) as entries:
        names = [entry.name for entry in entries if pattern.fullmatch(entry.name)]
    for name in names:
        try:
            if stat.S_ISREG(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
                os.unlink(name, dir_fd=dir_fd)
                removed += 1
        except OSError:
            continue
    return removed


def _require_int(value: object, where: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(where)
    return value


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------

STATE_KEYS = ("format", "slot", "actions", "blocked", "seen", "hold_until", "results")


@dataclass
class State:
    """Inhalt von `state.json`. Alle Zeiten sind ganze Sekunden (Unix-Zeit)."""

    slot: policy.Slot | None = None
    actions: list[int] = field(default_factory=list)
    blocked: dict[str, int] = field(default_factory=dict)
    seen: dict[str, int] = field(default_factory=dict)
    hold_until: int | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    """Die letzten Ergebnisse (`policy.is_result`), hoechstens `policy.RESULTS_MAX`, eins je Anforderungs-ID."""

    # --- Aenderungen --------------------------------------------------------

    def mark_seen(self, request_id: str, now: float) -> None:
        """Merkt eine verarbeitete ID (angenommen **oder** abgelehnt)."""
        if not policy.is_request_id(request_id):
            raise ValueError("keine Anforderungs-ID")
        self.seen[request_id] = int(now)
        if len(self.seen) > MAX_SEEN:
            newest = sorted(self.seen.items(), key=lambda item: (item[1], item[0]), reverse=True)[:MAX_SEEN]
            self.seen = dict(newest)

    def record_action(self, now: float) -> None:
        """Zaehlt eine **angenommene** Aktion. Gedacht fuer den Moment der Annahme, nicht erst den Commit:
        auch ein gescheitertes Update kostet einen Neustart."""
        self.actions.append(int(now))
        self.actions = sorted(self.actions)[-MAX_ACTIONS:]

    def block_version(self, version: str, now: float) -> None:
        """24 h kein Update auf `version` (nach einem Rueckweg von dort)."""
        if not policy.is_version(version):
            raise ValueError("keine Version")
        self.blocked[version] = max(self.blocked.get(version, 0), int(now) + policy.BLOCK_AFTER_ROLLBACK_S)
        if len(self.blocked) > MAX_BLOCKED:
            latest = sorted(self.blocked.items(), key=lambda item: (item[1], item[0]), reverse=True)[:MAX_BLOCKED]
            self.blocked = dict(latest)

    def record_result(self, entry: dict[str, Any]) -> bool:
        """Merkt das Ergebnis eines Vorgangs (ein Eintrag je ID, die letzten `policy.RESULTS_MAX`). `False`, wenn fuer
        diese ID schon dasselbe Ergebnis mit demselben Code dasteht (eine Wiederaufnahme schreibt es nicht noch einmal,
        der erste Zeitpunkt bleibt)."""
        if not policy.is_result(entry):
            raise ValueError("kein Ergebnis")
        for known in self.results:
            if known["id"] == entry["id"] and (known["outcome"], known["code"]) == (entry["outcome"], entry["code"]):
                return False
        self.results = [known for known in self.results if known["id"] != entry["id"]] + [dict(entry)]
        self.results = self.results[-policy.RESULTS_MAX:]
        return True

    def commit_update(self, slot: policy.Slot) -> None:
        """Commit eines Updates: der neue Slot ersetzt einen alten."""
        self.slot = slot

    def commit_rollback(self, reverted_from: str, now: float) -> None:
        """Commit eines Rueckwegs: der Slot ist verbraucht (einmal benutzbar) und es entsteht **kein** neuer
        (nie verkettet); `reverted_from` ist 24 h gesperrt."""
        self.slot = None
        self.block_version(reverted_from, now)

    def cap_future(self, now: float) -> bool:
        """Kappt Zeitstempel in der Zukunft (siehe `prune`); `True`, wenn etwas geaendert wurde."""
        now_s = int(now)
        late = now_s + policy.CLOCK_SLACK_S
        actions = [min(at, now_s) if at > late else at for at in self.actions]
        seen = {rid: (min(at, now_s) if at > late else at) for rid, at in self.seen.items()}
        block_max = now_s + policy.BLOCK_AFTER_ROLLBACK_S
        blocked = {v: (block_max if until > block_max + policy.CLOCK_SLACK_S else until)
                   for v, until in self.blocked.items()}
        hold_until = self.hold_until
        hold_max = now_s + HOLD_AFTER_CORRUPT_S
        if hold_until is not None and hold_until > hold_max + policy.CLOCK_SLACK_S:
            hold_until = hold_max
        changed = (actions, seen, blocked, hold_until) != (self.actions, self.seen, self.blocked, self.hold_until)
        self.actions, self.seen, self.blocked, self.hold_until = actions, seen, blocked, hold_until
        return changed

    def prune(self, now: float) -> None:
        """Kappt Zeitstempel in der Zukunft und wirft Abgelaufenes weg. Der Slot bleibt stehen, auch abgelaufen:
        das Aufraeumen des Schutz-Tags gehoert dem Ablauf.

        **Zukunft:** ein Zeitstempel, der weiter als `CLOCK_SLACK_S` nach `now` liegt (die Uhr war vorgesprungen und
        wurde zurueckgestellt, oder die Datei ist falsch), sperrt weiter (fail-closed), aber nur bis zur Hoechstfrist:
        Aktionen und IDs zaehlen dann als "gerade eben" (`now`), `blocked` und `hold_until` laufen hoechstens
        `BLOCK_AFTER_ROLLBACK_S` bzw. `HOLD_AFTER_CORRUPT_S` ab jetzt. Ohne diese Kappung sperrte ein einziger
        Uhrsprung fuer Jahre (nur `/state` von Hand loeschen half). `StateStore.load_state` ruft das beim Laden auf
        und schreibt den gekappten Zustand zurueck."""
        self.cap_future(now)
        now_s = int(now)
        self.actions = sorted(at for at in self.actions if now_s - at < policy.ACTION_INTERVAL_S)[-MAX_ACTIONS:]
        self.blocked = {v: until for v, until in self.blocked.items() if until > now_s}
        self.seen = {rid: at for rid, at in self.seen.items() if now_s - at < policy.SEEN_TTL_S}
        if self.hold_until is not None and self.hold_until <= now_s:
            self.hold_until = None

    # --- JSON ---------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "format": STATE_FORMAT,
            "slot": self.slot.to_json() if self.slot is not None else None,
            "actions": list(self.actions),
            "blocked": dict(self.blocked),
            "seen": dict(self.seen),
            "hold_until": self.hold_until,
            "results": [dict(entry) for entry in self.results],
        }

    @classmethod
    def from_json(cls, obj: object, *, repository: str = policy.REPOSITORY) -> State:
        """Streng: jede Abweichung ist `ValueError` (der Aufrufer behandelt die Datei dann als kaputt)."""
        if not isinstance(obj, dict) or set(obj) != set(STATE_KEYS):
            raise ValueError("state: Schluessel")
        if type(obj["format"]) is not int or obj["format"] != STATE_FORMAT:
            raise ValueError("state: format")
        slot = None if obj["slot"] is None else policy.Slot.from_json(obj["slot"], repository=repository)
        actions = obj["actions"]
        if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
            raise ValueError("state: actions")
        for at in actions:
            _require_int(at, "state: actions")
        blocked = obj["blocked"]
        if not isinstance(blocked, dict) or len(blocked) > MAX_BLOCKED:
            raise ValueError("state: blocked")
        for version, until in blocked.items():
            if not policy.is_version(version):
                raise ValueError("state: blocked")
            _require_int(until, "state: blocked")
        seen = obj["seen"]
        if not isinstance(seen, dict) or len(seen) > MAX_SEEN:
            raise ValueError("state: seen")
        for rid, at in seen.items():
            if not policy.is_request_id(rid):
                raise ValueError("state: seen")
            _require_int(at, "state: seen")
        hold_until = obj["hold_until"]
        if hold_until is not None:
            _require_int(hold_until, "state: hold_until")
        results = obj["results"]
        if not isinstance(results, list) or len(results) > policy.RESULTS_MAX \
                or not all(policy.is_result(entry) for entry in results) \
                or len({entry["id"] for entry in results}) != len(results):
            raise ValueError("state: results")
        return cls(slot=slot, actions=list(actions), blocked=dict(blocked), seen=dict(seen), hold_until=hold_until,
                   results=[dict(entry) for entry in results])


# ---------------------------------------------------------------------------
# journal.json
# ---------------------------------------------------------------------------

RESTART_POLICIES = ("", "no", "always", "unless-stopped", "on-failure")
CONTAINER_NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", re.ASCII)
"""Containername ohne den fuehrenden `/` aus dem Inspect."""
PREVIOUS_SUFFIX = "-previous"
"""Endung, unter der der alte Container waehrend eines Vorgangs weiterlaeuft (`<name>-previous`)."""

JOURNAL_KEYS = ("format", "request_id", "action", "step", "started_at", "deadline", "old", "new", "undo", "code")
OLD_KEYS = ("id", "name", "image_id", "version", "restart_policy", "tag_text", "repo_digest")
NEW_KEYS = ("id", "image_id", "digest", "version")
RESTART_KEYS = ("Name", "MaximumRetryCount")


@dataclass(frozen=True)
class OldContainer:
    """Das Ziel vor dem Vorgang. `tag_text` ist `Config.Image` unveraendert.

    `repo_digest` ist `<repository>@sha256:...` des alten Images aus der Vorpruefung, fuer den Rueckweg-Slot nach dem
    Update. Er steht im Journal, bevor sich etwas aendert: Mit dem containerd-Image-Store (Standard einer neuen
    Installation von Docker 29) leitet der Docker-Dienst `RepoDigests` aus den Namen eines Images ab -- zeigt das
    bewegliche Tag schon auf das neue Image, nennt er den Digest des alten oft nicht mehr, und ein Commit, den erst die
    Wiederaufnahme macht, fragte vergeblich."""

    id: str
    name: str
    image_id: str
    version: str
    restart_policy: tuple[str, int]
    tag_text: str
    repo_digest: str


@dataclass(frozen=True)
class NewImage:
    """Das Image, auf das gewechselt wird, und ab `created` der neue Container (`id`).
    Beim Rueckweg ist es der Vorgaenger aus dem Slot; `digest` darf dann fehlen."""

    image_id: str
    digest: str | None
    version: str
    id: str | None = None


@dataclass(frozen=True)
class Journal:
    """Der laufende Vorgang. Unveraenderlich; der naechste Stand entsteht mit `advance()`.

    `undo`: der Rueckbau ist angekuendigt (vor dem Commit, nie danach). Ab dann geht es nicht mehr vorwaerts
    (`advance` erlaubt nur noch das Eintragen des eigenen, schon angelegten Containers von `creating` nach
    `created`), und `Engine.bind` laesst nur noch Aufrufe des Rueckbaus zu. `code` ist der Grund dafuer (ein Code aus
    `policy.CODES`, oder `None`, wenn die Wiederaufnahme nach einem Absturz zurueckbaut); ohne `undo` immer `None`.

    `repository` ist das Repository, gegen das `old.tag_text` geprueft wird (Vorgabe: die Konstante
    `policy.REPOSITORY`; nur Tests geben ein anderes mit, nie die Umgebung). Es steht nicht in der Datei
    (`to_json`) und zaehlt nicht beim Vergleich; `from_json` und `StateStore` setzen es, damit `validate`,
    `advance` und `floating_tag` immer dasselbe pruefen."""

    request_id: str
    action: str
    step: str
    started_at: int
    deadline: int
    old: OldContainer
    new: NewImage | None = None
    undo: bool = False
    code: str | None = None
    repository: str = field(default=policy.REPOSITORY, compare=False, repr=False)

    @property
    def floating_tag(self) -> str:
        """Das bewegliche Tag des Ziels (`latest` oder `X.Y`) gegen `self.repository`."""
        return policy.floating_tag(self.old.tag_text, repository=self.repository)

    def advance(self, step: str, **changes: Any) -> Journal:
        """Naechster Stand (geprueft). Schritte gehen nur vorwaerts, im Rueckbau gar nicht mehr (ausser `creating` ->
        `created`: der Rueckbau traegt den eigenen, schon angelegten Container ein, um ihn entfernen zu duerfen)."""
        if step not in policy.STEPS or policy.STEPS.index(step) < policy.STEPS.index(self.step):
            raise ValueError("journal: Schritt rueckwaerts")
        if self.undo and (self.step, step) != ("creating", "created"):
            raise ValueError("journal: im Rueckbau geht es nicht mehr vorwaerts")
        if {"undo", "code"} & set(changes):
            raise ValueError("journal: den Rueckbau kuendigt nur start_undo an")
        journal = dataclasses.replace(self, step=step, **changes)
        journal.validate()
        return journal

    def start_undo(self, code: str | None) -> Journal:
        """Der Stand mit angekuendigtem Rueckbau (`undo`) und seinem Grund (`code`). Ist er schon angekuendigt, bleibt
        es beim ersten Grund. Nach dem Commit gibt es keinen Rueckbau (`ValueError`)."""
        if self.undo:
            return self
        journal = dataclasses.replace(self, undo=True, code=code)
        journal.validate()
        return journal

    def validate(self, *, repository: str | None = None) -> None:
        """Wirft `ValueError`, wenn das Journal nicht in sich stimmig ist. Ohne `repository` gilt `self.repository`."""
        repository = self.repository if repository is None else repository
        if not policy.is_request_id(self.request_id):
            raise ValueError("journal: request_id")
        if self.action not in policy.ACTIONS:
            raise ValueError("journal: action")
        if self.step not in policy.STEPS:
            raise ValueError("journal: step")
        _require_int(self.started_at, "journal: started_at")
        _require_int(self.deadline, "journal: deadline")
        old = self.old
        if not isinstance(old, OldContainer):
            raise TypeError("journal: old")
        if not policy.is_container_id(old.id) or not policy.is_image_id(old.image_id):
            raise ValueError("journal: old.id")
        if not isinstance(old.name, str) or not CONTAINER_NAME_RE.fullmatch(old.name):
            raise ValueError("journal: old.name")
        if not policy.is_version(old.version):
            raise ValueError("journal: old.version")
        restart = old.restart_policy
        if (not isinstance(restart, tuple) or len(restart) != 2 or restart[0] not in RESTART_POLICIES
                or type(restart[1]) is not int or not 0 <= restart[1] <= 1_000_000):
            raise ValueError("journal: old.restart_policy")
        try:
            policy.floating_tag(old.tag_text, repository=repository)
        except Refusal:
            raise ValueError("journal: old.tag_text") from None
        if policy.registry_digests([old.repo_digest], repository=repository) != [old.repo_digest]:
            raise ValueError("journal: old.repo_digest")
        if type(self.undo) is not bool:
            raise ValueError("journal: undo")
        if self.code is not None and (not self.undo or not policy.is_code(self.code)):
            raise ValueError("journal: code")
        if self.undo and self.step == "committed":
            raise ValueError("journal: Rueckbau nach dem Commit")
        index = policy.STEPS.index(self.step)
        new = self.new
        if index < policy.STEPS.index("pulled"):
            if new is not None:
                raise ValueError("journal: new zu frueh")
            return
        if not isinstance(new, NewImage):
            raise TypeError("journal: new fehlt")
        if not policy.is_image_id(new.image_id) or not policy.is_version(new.version):
            raise ValueError("journal: new")
        if new.digest is not None and not policy.is_digest(new.digest):
            raise ValueError("journal: new.digest")
        if self.action == "update" and new.digest is None:
            raise ValueError("journal: new.digest fehlt")
        if index < policy.STEPS.index("created"):
            if new.id is not None:
                raise ValueError("journal: new.id zu frueh")
        elif not policy.is_container_id(new.id):
            raise ValueError("journal: new.id fehlt")

    def to_json(self) -> dict[str, Any]:
        old = self.old
        new = self.new
        return {
            "format": JOURNAL_FORMAT,
            "request_id": self.request_id,
            "action": self.action,
            "step": self.step,
            "started_at": self.started_at,
            "deadline": self.deadline,
            "old": {
                "id": old.id,
                "name": old.name,
                "image_id": old.image_id,
                "version": old.version,
                "restart_policy": {"Name": old.restart_policy[0], "MaximumRetryCount": old.restart_policy[1]},
                "tag_text": old.tag_text,
                "repo_digest": old.repo_digest,
            },
            "new": None if new is None else {
                "id": new.id, "image_id": new.image_id, "digest": new.digest, "version": new.version,
            },
            "undo": self.undo,
            "code": self.code,
        }

    @classmethod
    def from_json(cls, obj: object, *, repository: str = policy.REPOSITORY) -> Journal:
        if not isinstance(obj, dict) or set(obj) != set(JOURNAL_KEYS):
            raise ValueError("journal: Schluessel")
        if type(obj["format"]) is not int or obj["format"] != JOURNAL_FORMAT:
            raise ValueError("journal: format")
        raw_old = obj["old"]
        if not isinstance(raw_old, dict) or set(raw_old) != set(OLD_KEYS):
            raise ValueError("journal: old")
        restart = raw_old["restart_policy"]
        if not isinstance(restart, dict) or set(restart) != set(RESTART_KEYS):
            raise ValueError("journal: old.restart_policy")
        old = OldContainer(
            id=raw_old["id"], name=raw_old["name"], image_id=raw_old["image_id"], version=raw_old["version"],
            restart_policy=(restart["Name"], restart["MaximumRetryCount"]), tag_text=raw_old["tag_text"],
            repo_digest=raw_old["repo_digest"],
        )
        raw_new = obj["new"]
        new = None
        if raw_new is not None:
            if not isinstance(raw_new, dict) or set(raw_new) != set(NEW_KEYS):
                raise ValueError("journal: new")
            new = NewImage(
                image_id=raw_new["image_id"], digest=raw_new["digest"], version=raw_new["version"], id=raw_new["id"],
            )
        journal = cls(
            request_id=obj["request_id"], action=obj["action"], step=obj["step"], started_at=obj["started_at"],
            deadline=obj["deadline"], old=old, new=new, undo=obj["undo"], code=obj["code"], repository=repository,
        )
        journal.validate()
        return journal


# ---------------------------------------------------------------------------
# Der Ordner /state
# ---------------------------------------------------------------------------


class StateStore:
    """Zugriff auf `/state`: erst `open()` (Ordner pruefen, Sperre nehmen), dann laden und speichern.

    `expected_uid` ist der erwartete Besitzer von Ordner und Dateien (im Betrieb root = 0). Nur fuer Tests
    ohne root anders -- als Parameter, nie aus der Umgebung.
    """

    def __init__(self, path: str | os.PathLike[str] = "/state", *, expected_uid: int = 0,
                 repository: str = policy.REPOSITORY) -> None:
        if type(expected_uid) is not int or expected_uid < 0:
            raise ValueError("expected_uid")
        self._path = os.fspath(path)
        self._uid = expected_uid
        self._repository = repository
        self._dir_fd: int | None = None
        self._lock_fd: int | None = None
        self._hold_floor = 0
        """Die in diesem Prozess gesetzte Sperre (Unix-Zeit): `save_state` schreibt nie weniger."""
        self.problem: str | None = None
        """Fester Bezeichner, wenn seit `open()` beim Laden etwas kaputt war (`state_corrupt`, `journal_corrupt`)."""

    # --- Oeffnen und Sperre -------------------------------------------------

    def open(self) -> None:
        """Prueft den Ordner (kein Symlink, Ordner, Besitzer), setzt 0700, nimmt die Sperre und raeumt Reste
        abgebrochener Schreibvorgaenge weg. Wirft `StateUnsafe` oder `SecondInstance`."""
        if self._dir_fd is not None:
            return
        self.problem = None
        self._hold_floor = 0
        try:
            dir_fd = os.open(self._path, DIR_FLAGS)
        except OSError:
            raise StateUnsafe("dir_open") from None
        try:
            st = os.fstat(dir_fd)
            if not stat.S_ISDIR(st.st_mode):
                raise StateUnsafe("dir_type")
            if st.st_uid != self._uid:
                raise StateUnsafe("dir_owner")
            if stat.S_IMODE(st.st_mode) != DIR_MODE:
                os.fchmod(dir_fd, DIR_MODE)
                if stat.S_IMODE(os.fstat(dir_fd).st_mode) != DIR_MODE:
                    raise StateUnsafe("dir_mode")
            lock_fd = self._lock(dir_fd)
        except Refusal:
            os.close(dir_fd)
            raise
        except OSError:
            os.close(dir_fd)
            raise StateUnsafe("dir_check") from None
        self._dir_fd, self._lock_fd = dir_fd, lock_fd
        with contextlib.suppress(OSError):
            remove_stale_tmp(dir_fd, _TMP_RE)

    def _lock(self, dir_fd: int) -> int:
        try:
            lock_fd = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY,
                              FILE_MODE, dir_fd=dir_fd)
        except OSError:
            raise StateUnsafe("lock_open") from None
        try:
            st = os.fstat(lock_fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != self._uid:
                raise StateUnsafe("lock_file")
            if stat.S_IMODE(st.st_mode) != FILE_MODE:
                os.fchmod(lock_fd, FILE_MODE)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SecondInstance() from None
            except OSError:
                raise StateUnsafe("lock_unsupported") from None
        except BaseException:
            os.close(lock_fd)
            raise
        return lock_fd

    def close(self) -> None:
        """Gibt die Sperre frei."""
        for fd in (self._lock_fd, self._dir_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self._dir_fd = self._lock_fd = None

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _fd(self) -> int:
        if self._dir_fd is None:
            raise RuntimeError("StateStore ist nicht geoeffnet")
        return self._dir_fd

    # --- state.json ---------------------------------------------------------

    def load_state(self, now: float) -> State:
        """Laedt `state.json`. Fehlt sie: leerer Zustand (war sie schon einmal kaputt, d. h. liegt
        `state.json.broken` daneben: sicherer Zustand). Ist sie ungueltig: sicherer Zustand mit 24-h-Sperre (siehe
        Modul-Doku), sofort geschrieben; `self.problem = "state_corrupt"`.

        Zeitstempel weit in der Zukunft (Uhrsprung) werden gekappt (`State.cap_future`, dazu die Sperre des Speichers,
        siehe `_expire_floor`) und der Stand zurueckgeschrieben -- aber nur, wenn sich dabei wirklich etwas geaendert
        hat (nicht bei jedem Laden). Der zurueckgegebene `State` enthaelt die Sperre des Speichers (`_apply_floor`).

        Wirft `StateUnsafe`, wenn auch das Schreiben des sicheren Zustands scheitert (`state_write`) oder die Datei
        gerade nicht lesbar ist (`state_read`; dann bleibt **alles** unveraendert, spaeter erneut versuchen)."""
        dir_fd = self._fd()
        self._expire_floor(now)
        raw: bytes | None = None
        try:
            raw = read_regular(dir_fd, STATE_NAME, max_bytes=MAX_STATE_BYTES, owner=self._uid)
        except FileNotFoundError:
            if self._exists(STATE_NAME + BROKEN_SUFFIX):
                return self._replace_corrupt_state(None, now)  # schon einmal kaputt gewesen: im Zweifel gesperrt
            return State()
        except ReadFailed:
            raise StateUnsafe("state_read") from None
        except UnsafeFile:
            return self._replace_corrupt_state(None, now)
        try:
            state = State.from_json(policy.loads_strict(raw, max_bytes=MAX_STATE_BYTES), repository=self._repository)
        except Exception:  # noqa: BLE001 - jede Abweichung im Inhalt: sicherer Zustand (fail-closed)
            return self._replace_corrupt_state(raw, now)
        before = state.to_json()
        state.cap_future(now)  # Uhrsprung: Zeitstempel in der Zukunft kappen ...
        self._apply_floor(state)  # ... aber nie unter die Sperre dieses Prozesses (die ist oben schon gekappt)
        if state.to_json() != before:  # den Stand nur festhalten, wenn er sich geaendert hat (best effort)
            with contextlib.suppress(OSError):
                self.save_state(state)
        return state

    def _exists(self, name: str) -> bool:
        try:
            os.stat(name, dir_fd=self._fd(), follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return True  # unklar: wie "da" behandeln (sicher)
        return True

    def _expire_floor(self, now: float) -> None:
        """Die Sperre des Speichers (`_hold_floor`) laesst man ablaufen und kappt sie wie `hold_until` (siehe
        `State.prune`): lag sie weiter als `HOLD_AFTER_CORRUPT_S + CLOCK_SLACK_S` in der Zukunft (die Uhr wurde
        zurueckgestellt), gilt `jetzt + HOLD_AFTER_CORRUPT_S`. Sonst finge sie die Kappung von `hold_until` bei jedem
        Laden wieder ein und erzwaenge jedes Mal einen Schreibvorgang."""
        now_s = int(now)
        if self._hold_floor and self._hold_floor <= now_s:
            self._hold_floor = 0
        elif self._hold_floor > now_s + HOLD_AFTER_CORRUPT_S + policy.CLOCK_SLACK_S:
            self._hold_floor = now_s + HOLD_AFTER_CORRUPT_S

    def _apply_floor(self, state: State) -> None:
        """Hebt `state.hold_until` auf mindestens die Sperre des Speichers an (auch im uebergebenen Objekt)."""
        if self._hold_floor:
            state.hold_until = max(state.hold_until or 0, self._hold_floor)

    def _replace_corrupt_state(self, raw: bytes | None, now: float) -> State:
        self.problem = "state_corrupt"
        dir_fd = self._fd()
        if raw is not None:
            with contextlib.suppress(OSError):
                write_atomic(dir_fd, STATE_NAME + BROKEN_SUFFIX, raw, mode=FILE_MODE, tmp_prefix=".broken-")
        held = State(hold_until=int(now) + HOLD_AFTER_CORRUPT_S)
        self._hold_floor = max(self._hold_floor, held.hold_until or 0)
        try:
            self.save_state(held)
        except OSError:
            # Der Notweg ist nur fuer *einen* Fall gedacht: `state.json` ist ein Ordner (das Ersetzen scheitert dann
            # an dem Namen). Bei jedem anderen Fehler (voller Datentraeger, zu viele offene Dateien, ...) bleibt die
            # Datei, wie sie ist: beim naechsten Start gilt wieder der sichere Zustand, nie ein leerer.
            if not self._is_directory(STATE_NAME):
                raise StateUnsafe("state_write") from None
            try:
                os.replace(STATE_NAME, STATE_NAME + BROKEN_SUFFIX, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                self.save_state(held)
            except OSError:
                # `state.json` fehlt jetzt, `state.json.broken` liegt da: `load_state` erkennt das (sicherer Zustand).
                raise StateUnsafe("state_write") from None
        return held

    def _is_directory(self, name: str) -> bool:
        try:
            return stat.S_ISDIR(os.stat(name, dir_fd=self._fd(), follow_symlinks=False).st_mode)
        except OSError:
            return False

    def save_state(self, state: State) -> None:
        """Schreibt `state.json` atomar mit fsync (Datei und Ordner). Prueft vorher, dass sich das Geschriebene
        wieder laden laesst -- nie eine Datei, die beim naechsten Start als kaputt gaelte.

        Eine in diesem Prozess gesetzte Sperre (`hold`, kaputtes `state.json` bzw. Journal) geht nie verloren:
        `state.hold_until` wird auf mindestens diese Sperre angehoben (auch im uebergebenen Objekt), ein vorher
        geladener alter `State` kann sie also nicht mehr ueberschreiben."""
        self._apply_floor(state)
        doc = state.to_json()
        State.from_json(doc, repository=self._repository)
        data = policy.dumps(doc)
        if len(data) > MAX_STATE_BYTES:
            raise ValueError("state: zu gross")
        write_atomic(self._fd(), STATE_NAME, data, mode=FILE_MODE, tmp_prefix=".state-")

    def hold(self, now: float) -> State:
        """Setzt die 24-h-Sperre im gespeicherten Zustand (z. B. nach einem kaputten Journal) und merkt sie sich:
        spaeteres `save_state` (auch mit einem frueher geladenen `State`) schreibt nie weniger."""
        state = self.load_state(now)
        until = int(now) + HOLD_AFTER_CORRUPT_S
        self._hold_floor = max(self._hold_floor, until)
        state.hold_until = max(state.hold_until or 0, until)
        self.save_state(state)
        return state

    def check(self, state: State, request: policy.Request, now: float) -> None:
        """Grenzen fuer diese Anforderung (`policy.check_limits`) -- **der Weg fuer den Ablauf**. Wendet vorher die
        Sperre des Speichers auf `state` an: ein `State`, der vor dem Erkennen eines kaputten Journals geladen wurde,
        kennt sie sonst nicht und liesse die Anforderung durch. Raeumt dann auf (`State.prune`): Zeitstempel weit in
        der Zukunft (Uhrsprung) werden dabei gekappt und bleiben so im Speicher -- gespeichert wird beim naechsten
        `save_state`. Wirft `Refusal` (`state_unsafe`, `replay`, `rate_limited`, `blocked_version`, ...)."""
        self._expire_floor(now)
        self._apply_floor(state)
        state.prune(now)
        policy.check_limits(
            request, now=now, actions=state.actions, blocked=state.blocked, seen=state.seen,
            hold_until=state.hold_until,
        )

    # --- journal.json -------------------------------------------------------

    def load_journal(self, now: float) -> Journal | None:
        """Laedt den laufenden Vorgang oder `None`. Ungueltig: 24-h-Sperre setzen, Journal als
        `journal.json.broken` beiseitelegen, `JournalCorrupt` werfen (siehe Modul-Doku). Gerade nicht lesbar:
        `StateUnsafe("journal_read")`, **nichts** wird veraendert -- der Ablauf liest in der naechsten Runde (bzw. in
        `recover()` beim naechsten Versuch) erneut."""
        dir_fd = self._fd()
        try:
            raw = read_regular(dir_fd, JOURNAL_NAME, max_bytes=MAX_JOURNAL_BYTES, owner=self._uid)
        except FileNotFoundError:
            return None
        except ReadFailed:
            raise StateUnsafe("journal_read") from None
        except UnsafeFile:
            raise self._quarantine_journal(now) from None
        try:
            return Journal.from_json(policy.loads_strict(raw, max_bytes=MAX_JOURNAL_BYTES),
                                     repository=self._repository)
        except Exception:  # noqa: BLE001 - jede Abweichung im Inhalt: kein automatischer Schritt (fail-closed)
            raise self._quarantine_journal(now) from None

    def _quarantine_journal(self, now: float) -> JournalCorrupt:
        """Sperre setzen und das Journal beiseitelegen; gibt die zu werfende Ausnahme zurueck. Die Sperre kommt
        zuerst: scheitert schon das (z. B. `state_read`), bleibt das Journal liegen und wird spaeter erneut gelesen."""
        dir_fd = self._fd()
        try:
            held = self.hold(now)
            os.replace(JOURNAL_NAME, JOURNAL_NAME + BROKEN_SUFFIX, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
        except OSError:
            raise StateUnsafe("journal_write") from None
        self.problem = "journal_corrupt"
        return JournalCorrupt(held)

    def write_journal(self, journal: Journal) -> None:
        """Write-ahead: vor dem Schritt aufrufen. Atomar, fsync auf Datei und Ordner."""
        journal.validate(repository=self._repository)
        data = policy.dumps(journal.to_json())
        if len(data) > MAX_JOURNAL_BYTES:
            raise ValueError("journal: zu gross")
        write_atomic(self._fd(), JOURNAL_NAME, data, mode=FILE_MODE, tmp_prefix=".journal-")

    def clear_journal(self) -> None:
        """Vorgang abgeschlossen: Journal loeschen, fsync des Ordners. Fehlt es schon, ist das kein Fehler."""
        dir_fd = self._fd()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(JOURNAL_NAME, dir_fd=dir_fd)
        os.fsync(dir_fd)
