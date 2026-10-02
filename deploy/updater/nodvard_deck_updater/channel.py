"""Der Kanal zwischen Dashboard und Helfer.

Ein Volume, im Helfer unter `/channel`, im Dashboard unter `/app/updater`::

    /channel/            root 0755   Wurzel: gehoert root, fuer Gruppe/Welt nicht beschreibbar (sonst channel_unsafe)
      status.json        root 0644   schreibt nur der Helfer (atomar, Zufallsname + O_CREAT|O_EXCL|O_NOFOLLOW)
      requests/          root 1777   legt der Helfer an (sticky: das Dashboard kann anlegen, aber nichts ersetzen)
        <uuid4>.json     1000 0644   Anforderungen des Dashboards (hoechstens 4096 Byte)

Das Dashboard gilt als nicht vertrauenswuerdig. Darum:

* **Wurzel und `requests/` werden ueber Deskriptoren angesprochen** (`O_DIRECTORY|O_NOFOLLOW`, danach nur noch
  `dir_fd`). `O_NOFOLLOW` wirkt nur auf die letzte Pfadkomponente -- deshalb nie ein Pfad wie
  `/channel/requests/x.json`, sondern immer `openat` relativ zum geprueften Ordner.
* **`requests/` muss bleiben, was es beim Start war:** `(st_dev, st_ino)` wird bei jeder Runde mit dem Startwert
  verglichen (ein ausgetauschter Ordner -> `channel_unsafe`).
* **Je Runde hoechstens 1000 Dateien ansehen und 32 Anforderungen bearbeiten** -- ein voller Ordner haelt den
  Helfer nicht auf. **Ordner zaehlen dabei nicht** (sie werden schon beim Durchlesen an `d_type` erkannt) und
  verdraengen so nie eine echte Anforderung; fuer das reine Durchlesen gilt eine eigene, hohe Obergrenze
  (`MAX_READ`). `handled` zaehlt erst, wenn wirklich gelesen oder geloescht wurde.
* **Ordner in `requests/`:** das Dashboard legt dort nie welche an. Leere Ordner entfernt der Helfer mit
  `rmdir` (nur dieser eine Name, nie rekursiv -- in einem Baum, den das Dashboard kontrolliert, waere das
  ein Wettlauf um Symlinks). Bleiben welche stehen (nicht leer), meldet `Poll.directories` sie, und der
  Status zeigt den festen Code `channel_cluttered`. **Je Runde hoechstens `MAX_RMDIR` `rmdir`-Versuche**
  (jeder ist ein Systemaufruf, und ein Angreifer kann viele volle Ordner anlegen); der Rest wird ohne Aufruf
  als "uebrig" gezaehlt, `directories` und `cluttered` stimmen also trotzdem, und leere Ordner verschwinden
  ueber mehrere Runden. Mehr als `MAX_READ` Eintraege je Runde werden nicht durchgelesen; wer den Ordner so
  fuellt, bekommt `channel_cluttered` und von Hand aufzuraeumen.
* **Anforderungen lesen:** nur Namen `<uuid4>.json`; erst `lstat` (nur regulaere Dateien werden ueberhaupt
  geoeffnet), dann `openat(O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC|O_NOCTTY)` und `fstat` am Deskriptor:
  regulaere Datei, dieselbe wie beim `lstat`, genau ein Link, hoechstens 4096 Byte; gelesen werden hoechstens
  4097 Byte. Ein FIFO blockiert so nie (sonst fiele auch der automatische Rueckweg aus), ein Symlink oder
  Hardlink auf eine fremde Datei wird nie gelesen.
* **Nie in Anforderungen schreiben.** Erledigtes und Fremdes (ausser Ordnern, siehe oben) wird per `unlinkat` geloescht.
  Halb geschriebene Anforderungen des Dashboards (`.<uuid4>.tmp`) bleiben 10 Minuten liegen.
* **Status:** nur feste Codes, strenges Schema (`validate_status`), hoechstens 16 KiB, atomar geschrieben.

Ist die Wurzel unsicher, bleibt der Deskriptor trotzdem offen: `write_status()` kann dann noch `channel_unsafe`
melden (neue Datei mit `O_EXCL|O_NOFOLLOW`, `rename` ersetzt nur den Eintrag), `poll()` verweigert aber jede
Anforderung.

Fuer Tests ohne root nimmt der Konstruktor die erwartete uid (`expected_uid`, Vorgabe 0). Nie aus der Umgebung.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import time
from dataclasses import dataclass
from typing import Any, Self

from . import policy
from .policy import Refusal
from .state import (
    DIR_FLAGS,
    ReadFailed,
    UnsafeFile,
    read_regular,
    remove_stale_tmp,
    write_atomic,
)

REQUESTS_DIR = "requests"
STATUS_NAME = "status.json"
REQUESTS_MODE = 0o1777
STATUS_MODE = 0o644
REQUEST_MODE = 0o644
"""Modus der Anforderungen des Dashboards (nur zur Abstimmung in `vectors/protocol.json`): root ohne
`CAP_DAC_OVERRIDE` kann eine `0600`-Datei von uid 1000 nicht lesen."""
MAX_SCAN = 1000
"""Hoechstens so viele Dateien (keine Ordner) je Runde ansehen; der Rest bleibt fuer die naechste Runde liegen."""
MAX_HANDLE = 32
"""Hoechstens so viele Anforderungen je Runde bearbeiten; der Rest bleibt fuer die naechste Runde liegen."""
MAX_READ = 20_000
"""Hoechstens so viele Verzeichniseintraege (Ordner eingeschlossen) je Runde durchlesen. Ordner zaehlen nicht auf
`MAX_SCAN`/`MAX_HANDLE`; diese Grenze haelt nur die Rundendauer beschraenkt."""
MAX_RMDIR = MAX_SCAN
"""Hoechstens so viele `rmdir`-Versuche je Runde. Ein voller Ordner lehnt `rmdir` ab, kostet aber trotzdem einen
Systemaufruf (rund 6 us): 100 000 davon dauerten 0,6 s je Runde. Was darueber liegt, bleibt fuer die naechste Runde
und zaehlt als stehen geblieben (`Poll.directories`)."""
TMP_GRACE_S = 600
"""So lange bleibt eine halb geschriebene Anforderung des Dashboards liegen (danach waere sie ohnehin abgelaufen)."""

REQUEST_NAME_RE = re.compile(rf"({policy.UUID4_RE.pattern})\.json", re.ASCII)
DASHBOARD_TMP_RE = re.compile(rf"\.(?:{policy.UUID4_RE.pattern})\.tmp", re.ASCII)
_STATUS_TMP_RE = re.compile(r"\.status-[0-9a-f]{16}\.tmp", re.ASCII)


class ChannelUnsafe(Refusal):
    def __init__(self, detail: str | None = None) -> None:
        super().__init__(policy.CHANNEL_UNSAFE, detail)


@dataclass(frozen=True)
class Incoming:
    """Eine Anforderung aus `requests/`. Entweder `raw` (der Inhalt, hoechstens 4096 Byte, noch ungeprueft --
    weiter mit `policy.parse_request(raw, now=..., file_id=id)`) oder `code` (`bad_request`, die Datei war
    keine lesbare regulaere Datei). `id` stammt aus dem Dateinamen."""

    id: str
    raw: bytes | None
    code: str | None = None


@dataclass(frozen=True)
class Poll:
    """Ergebnis einer Runde: die Anforderungen, wie viele Eintraege angesehen bzw. geloescht wurden (leere Ordner
    eingeschlossen) und wie viele Ordner stehen geblieben sind (`directories`: nicht leer, vom Dashboard in
    `requests/` angelegt; der Ablauf meldet dann `channel_cluttered` im Status)."""

    items: tuple[Incoming, ...]
    scanned: int
    removed: int
    directories: int = 0

    @property
    def cluttered(self) -> bool:
        """Es liegen Ordner in `requests/`, die der Helfer nicht entfernen konnte."""
        return self.directories > 0


class Channel:
    """Zugriff auf den Kanal. Erst `setup()`, dann je Runde `poll()`; `write_status()` jederzeit nach `setup()`
    (auch wenn es `ChannelUnsafe` geworfen hat, solange die Wurzel geoeffnet werden konnte)."""

    def __init__(self, root: str | os.PathLike[str] = "/channel", *, expected_uid: int = 0) -> None:
        if type(expected_uid) is not int or expected_uid < 0:
            raise ValueError("expected_uid")
        self._root = os.fspath(root)
        self._uid = expected_uid
        self._root_fd: int | None = None
        self._requests_id: tuple[int, int] | None = None

    @property
    def ready(self) -> bool:
        """`setup()` war erfolgreich: Anforderungen duerfen gelesen werden."""
        return self._requests_id is not None

    # --- Einrichten ---------------------------------------------------------

    def setup(self) -> None:
        """Wurzel pruefen, `requests/` anlegen bzw. pruefen (1777, Besitzer), Startwert merken, Reste alter
        Status-Schreibvorgaenge entfernen. Wirft `ChannelUnsafe`."""
        self.close()
        try:
            self._root_fd = os.open(self._root, DIR_FLAGS)
        except OSError:
            raise ChannelUnsafe("root_open") from None
        self._check_root()
        self._requests_id = self._setup_requests()
        with contextlib.suppress(OSError):
            remove_stale_tmp(self._root_fd, _STATUS_TMP_RE)

    def close(self) -> None:
        if self._root_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._root_fd)
        self._root_fd = None
        self._requests_id = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _check_root(self) -> None:
        assert self._root_fd is not None
        st = os.fstat(self._root_fd)
        if not stat.S_ISDIR(st.st_mode):
            raise ChannelUnsafe("root_type")
        if st.st_uid != self._uid:
            raise ChannelUnsafe("root_owner")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ChannelUnsafe("root_mode")

    def _setup_requests(self) -> tuple[int, int]:
        assert self._root_fd is not None
        try:
            # Erst 0700 anlegen, dann am Deskriptor auf 1777: kein Moment, in dem ein ungepruefter Ordner offen ist.
            os.mkdir(REQUESTS_DIR, 0o700, dir_fd=self._root_fd)
        except FileExistsError:
            pass
        except OSError:
            raise ChannelUnsafe("requests_create") from None
        dfd = self._open_requests(None)
        try:
            st = os.fstat(dfd)
            if stat.S_IMODE(st.st_mode) != REQUESTS_MODE:
                os.fchmod(dfd, REQUESTS_MODE)
                st = os.fstat(dfd)
                if stat.S_IMODE(st.st_mode) != REQUESTS_MODE:
                    raise ChannelUnsafe("requests_mode")
            return st.st_dev, st.st_ino
        except OSError:
            raise ChannelUnsafe("requests_mode") from None
        finally:
            os.close(dfd)

    def _open_requests(self, expected: tuple[int, int] | None) -> int:
        """`requests/` relativ zur Wurzel oeffnen und am Deskriptor pruefen."""
        assert self._root_fd is not None
        try:
            dfd = os.open(REQUESTS_DIR, DIR_FLAGS, dir_fd=self._root_fd)
        except OSError:
            raise ChannelUnsafe("requests_open") from None
        try:
            st = os.fstat(dfd)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != self._uid:
                raise ChannelUnsafe("requests_owner")
            if expected is not None:
                if (st.st_dev, st.st_ino) != expected:
                    raise ChannelUnsafe("requests_replaced")
                if stat.S_IMODE(st.st_mode) != REQUESTS_MODE:
                    raise ChannelUnsafe("requests_mode")
        except BaseException:
            os.close(dfd)
            raise
        return dfd

    # --- Anforderungen lesen ------------------------------------------------

    def poll(self, *, now: float | None = None) -> Poll:
        """Eine Runde: Ordner erkennen (leere entfernen, hoechstens `MAX_RMDIR` Versuche), hoechstens `MAX_SCAN`
        Dateien ansehen, hoechstens `MAX_HANDLE` Anforderungen lesen (und loeschen), Fremdes loeschen. Wirft
        `ChannelUnsafe`, wenn Wurzel oder `requests/` nicht mehr stimmen; jeder Fehler an **einem** Eintrag betrifft
        nur diesen."""
        if not self.ready or self._root_fd is None:
            raise ChannelUnsafe("not_ready")
        now = time.time() if now is None else now
        self._check_root()
        dfd = self._open_requests(self._requests_id)
        try:
            names: list[str] = []
            folders: list[str] = []
            read = 0
            with os.scandir(dfd) as entries:
                for entry in entries:
                    read += 1
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)  # d_type, meist ohne eigenen Systemaufruf
                    except OSError:
                        is_dir = False
                    (folders if is_dir else names).append(entry.name)
                    if len(names) >= MAX_SCAN or read >= MAX_READ:
                        break
            items: list[Incoming] = []
            handled = removed = left = 0
            for tried, name in enumerate(folders):
                if tried >= MAX_RMDIR:
                    left += len(folders) - tried  # Budget der Runde verbraucht: ohne Systemaufruf als uebrig gezaehlt
                    break
                try:
                    os.rmdir(name, dir_fd=dfd)  # `unlinkat(AT_REMOVEDIR)`: nur dieser Name, nur leer, nie rekursiv
                except FileNotFoundError:
                    pass
                except OSError:
                    left += 1  # nicht leer (oder sonst nicht entfernbar): bleibt stehen und wird gemeldet
                else:
                    removed += 1
            for name in names:
                match = REQUEST_NAME_RE.fullmatch(name)
                if match is None:
                    removed += self._remove_foreign(dfd, name, now)
                elif handled < MAX_HANDLE:
                    item = self._take(dfd, name, match[1])
                    if item is not None:  # nur was wirklich gelesen oder geloescht wurde zaehlt
                        handled += 1
                        items.append(item)
            return Poll(items=tuple(items), scanned=len(names) + len(folders), removed=removed, directories=left)
        finally:
            os.close(dfd)

    def _take(self, dfd: int, name: str, request_id: str) -> Incoming | None:
        """Liest eine Anforderung und loescht sie. `None`, wenn sie verschwunden ist oder ein Ordner ist."""
        try:
            try:
                before = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if stat.S_ISDIR(before.st_mode):
                return None  # kurz nach dem Durchlesen zu einem Ordner geworden: nicht gelesen, nicht geloescht
            raw: bytes | None = None
            if stat.S_ISREG(before.st_mode):
                try:
                    raw = read_regular(dfd, name, max_bytes=policy.REQUEST_MAX_BYTES,
                                       identity=(before.st_dev, before.st_ino))
                except FileNotFoundError:
                    return None
                except (UnsafeFile, ReadFailed, OSError):
                    raw = None  # ungueltig oder gerade nicht lesbar: `bad_request`, das Dashboard schickt neu
            _unlink(dfd, name)
        except Exception:  # noqa: BLE001 - ein Fehler an diesem Eintrag betrifft nur ihn
            _unlink(dfd, name)
            return Incoming(id=request_id, raw=None, code=policy.BAD_REQUEST)
        if raw is None:
            return Incoming(id=request_id, raw=None, code=policy.BAD_REQUEST)
        return Incoming(id=request_id, raw=raw)

    def _remove_foreign(self, dfd: int, name: str, now: float) -> int:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except OSError:
            return 0
        if stat.S_ISDIR(st.st_mode):
            return 0  # erst nach dem Durchlesen zu einem Ordner geworden: die naechste Runde raeumt ihn auf
        if DASHBOARD_TMP_RE.fullmatch(name) and abs(now - st.st_mtime) <= TMP_GRACE_S:
            return 0  # das Dashboard schreibt gerade
        return _unlink(dfd, name)

    # --- Status schreiben ---------------------------------------------------

    def write_status(self, doc: dict[str, Any], *, durable: bool = True) -> None:
        """Schreibt `status.json` (0644) atomar. `doc` muss `validate_status` bestehen, sonst `ValueError` und
        nichts wird geschrieben. `durable=False` (ohne fsync) ist fuer den reinen Heartbeat gedacht."""
        if self._root_fd is None:
            raise ChannelUnsafe("not_open")
        write_atomic(self._root_fd, STATUS_NAME, encode_status(doc), mode=STATUS_MODE, tmp_prefix=".status-",
                     durable=durable)


def _unlink(dfd: int, name: str) -> int:
    try:
        os.unlink(name, dir_fd=dfd)
    except OSError:
        return 0
    return 1


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

STATUS_KEYS = (
    "proto", "helper_version", "request_versions", "seq", "heartbeat_at", "state", "ready", "reason", "target",
    "busy", "previous", "results",
)
TARGET_KEYS = ("current_version", "floating_tag", "pinned")
BUSY_KEYS = ("id", "action", "step", "since")
PREVIOUS_KEYS = ("version", "until")
RESULT_KEYS = ("id", "action", "from", "to", "outcome", "code", "finished_at")


def _keys(obj: object, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise ValueError(f"status: {where}")
    return obj


def _int(value: object, where: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"status: {where}")


def _check(ok: bool, where: str) -> None:
    if not ok:
        raise ValueError(f"status: {where}")


def validate_status(doc: object) -> None:
    """Strenges Schema von `status.json`; wirft `ValueError` mit dem Namen des Feldes.

    Jeder Text ist ein fester Code, eine Version, eine UUID4 oder ein bewegliches Tag -- Freitext (Fehler der
    Engine, Umgebung, Inhalte aus dem Kanal) kann so gar nicht in den Status gelangen.
    """
    doc = _keys(doc, STATUS_KEYS, "Schluessel")
    _check(type(doc["proto"]) is int and doc["proto"] == policy.PROTOCOL, "proto")
    _check(policy.is_version(doc["helper_version"]), "helper_version")
    _check(doc["request_versions"] == list(policy.REQUEST_VERSIONS)
           and all(type(v) is int for v in doc["request_versions"]), "request_versions")
    _int(doc["seq"], "seq")
    _int(doc["heartbeat_at"], "heartbeat_at")
    _check(isinstance(doc["state"], str) and doc["state"] in policy.STATES, "state")
    _check(isinstance(doc["ready"], bool), "ready")
    _check(doc["reason"] is None or policy.is_code(doc["reason"]), "reason")
    target = doc["target"]
    if target is not None:
        target = _keys(target, TARGET_KEYS, "target")
        _check(target["current_version"] is None or policy.is_version(target["current_version"]),
               "target.current_version")
        tag = target["floating_tag"]
        _check(tag is None or tag == "latest" or (isinstance(tag, str) and policy.MINOR_TAG_RE.fullmatch(tag)),
               "target.floating_tag")
        _check(isinstance(target["pinned"], bool), "target.pinned")
    busy = doc["busy"]
    if busy is not None:
        busy = _keys(busy, BUSY_KEYS, "busy")
        _check(policy.is_request_id(busy["id"]), "busy.id")
        _check(isinstance(busy["action"], str) and busy["action"] in policy.ACTIONS, "busy.action")
        _check(isinstance(busy["step"], str) and busy["step"] in policy.STEPS, "busy.step")
        _int(busy["since"], "busy.since")
    previous = doc["previous"]
    if previous is not None:
        previous = _keys(previous, PREVIOUS_KEYS, "previous")
        _check(policy.is_version(previous["version"]), "previous.version")
        _int(previous["until"], "previous.until")
    results = doc["results"]
    _check(isinstance(results, list) and len(results) <= policy.RESULTS_MAX, "results")
    for result in results:
        result = _keys(result, RESULT_KEYS, "results[]")
        _check(policy.is_request_id(result["id"]), "results[].id")
        _check(isinstance(result["action"], str) and result["action"] in policy.ACTIONS, "results[].action")
        _check(result["from"] is None or policy.is_version(result["from"]), "results[].from")
        _check(result["to"] is None or policy.is_version(result["to"]), "results[].to")
        _check(isinstance(result["outcome"], str) and result["outcome"] in policy.OUTCOMES, "results[].outcome")
        _check(result["code"] is None or policy.is_code(result["code"]), "results[].code")
        _int(result["finished_at"], "results[].finished_at")


def encode_status(doc: object) -> bytes:
    """Prueft und kodiert den Status (kompaktes ASCII-JSON, hoechstens `policy.STATUS_MAX_BYTES`)."""
    validate_status(doc)
    data = policy.dumps(doc)
    if len(data) > policy.STATUS_MAX_BYTES:
        raise ValueError("status: zu gross")
    return data
