"""Kanal zum Update-Helfer: Status lesen, Anforderungen schreiben.

Der Helfer ist ein eigenes kleines Programm neben dem Dashboard. Beide teilen sich nur ein Volume (im Dashboard
`Settings.updater_dir`, Vorgabe `/app/updater`), das der Helfer selbst einrichtet::

    /app/updater/          root 0755   Wurzel: gehoert root, fuer Gruppe/Welt nicht beschreibbar
      status.json          root 0644   schreibt nur der Helfer (feste Codes, hoechstens 16 KiB)
      requests/            root 1777   sticky: das Dashboard legt an, kann aber nichts Fremdes ersetzen
        <uuid4>.json       0644        Anforderungen des Dashboards (hoechstens 4096 Byte)

Hier steht nur die Leitung, keine Entscheidung (die steht in `services.update_helper`, verbindlich entscheidet
ohnehin der Helfer):

* **Status lesen** (`read_status`): Wurzel und Datei werden ueber Deskriptoren geoeffnet (`O_NOFOLLOW`, danach nur
  noch `dir_fd`), am Deskriptor geprueft (Besitzer root, Modus, regulaere Datei, ein Link, Groesse) und streng
  gelesen (keine doppelten Schluessel, kein `NaN`). Bekannte Felder muessen genau stimmen, unbekannte fallen weg (ein
  neuerer Helfer darf Felder dazunehmen). Gruende und Codes sind nur Bezeichner (`CODE_RE`), nie Freitext. Stimmt
  etwas nicht, ist der Helfer nicht da (`present=False`) mit einem Grund: `missing`, `stale` (Herzschlag aelter als
  90 s oder mehr als 300 s in der Zukunft), `unsafe`, `proto` oder `invalid`.
* **Anforderung schreiben** (`write_request`): `requests/` muss root gehoeren und genau 1777 haben, sonst
  `ChannelError`. Die Datei entsteht unter `.<id>.tmp` (`O_CREAT|O_EXCL|O_NOFOLLOW`), bekommt ausdruecklich 0644
  (`fchmod`: das Dashboard laeuft mit umask 077, und root ohne `CAP_DAC_OVERRIDE` liest keine 0600-Datei eines anderen
  Nutzers), wird mit fsync geschrieben und dann umbenannt; danach fsync des Ordners.

Die Werte hier muessen zum Helfer passen (`deploy/updater/tests/vectors/*.json`); ein Gleichlauf-Test prueft das.
Der Kern importiert nichts aus dem Helfer-Paket.

Der erwartete Besitzer ist root (`EXPECTED_UID`). Tests ohne root setzen den Modulwert um, nie ueber die Umgebung.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Protokoll (gleich dem Helfer)
# ---------------------------------------------------------------------------

PROTOCOL = 1
"""Version von `status.json` (Feld `proto`)."""
REQUEST_VERSION = 1
"""Version der Anforderung (Feld `v`), die dieses Dashboard schreibt."""

STATUS_NAME = "status.json"
REQUESTS_DIR = "requests"
REQUESTS_MODE = 0o1777
REQUEST_MODE = 0o644
STATUS_MAX_BYTES = 16 * 1024
REQUEST_MAX_BYTES = 4096
REQUEST_MAX_AGE_S = 600
"""So lange nimmt der Helfer eine Anforderung an (danach `expired`)."""
RESULTS_MAX = 10
HEARTBEAT_MAX_AGE_S = 90
HEARTBEAT_MAX_FUTURE_S = 300
MAX_PENDING_SCAN = 1000
"""Hoechstens so viele Eintraege in `requests/` ansehen, wenn nach offenen Anforderungen gesucht wird."""

EXPECTED_UID = 0
"""Besitzer von Wurzel, `requests/` und `status.json`: root (der Helfer). Nur Tests setzen das um."""

ACTIONS = ("update", "rollback")
STATES = ("idle", "busy", "unsafe", "error")
OUTCOMES = ("applied", "reverted", "rolled_back", "refused", "aborted", "failed_manual", "external_change")
STEPS = (
    "begin", "pulled", "protected", "renamed", "tagged", "creating", "created", "old_stopped", "started",
    "committed",
)
REQUEST_KEYS = ("action", "created_at", "id", "v", "version")

CODE_RE = re.compile(r"[a-z_]{1,40}", re.ASCII)
"""Gruende und Codes des Helfers. Unbekannte, aber passende Codes werden angenommen (ein neuerer Helfer darf neue
dazunehmen; die Oberflaeche zeigt dann einen allgemeinen Text), alles andere gilt als Freitext und wird abgelehnt."""
_NUM = r"(0|[1-9][0-9]{0,8})"
VERSION_RE = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}", re.ASCII)
"""Fertige Version `X.Y.Z`: nur ASCII-Ziffern, ohne fuehrende Nullen, je Teil hoechstens 9 Stellen."""
MINOR_TAG_RE = re.compile(rf"{_NUM}\.{_NUM}", re.ASCII)
UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.ASCII)
REQUEST_NAME_RE = re.compile(rf"({UUID4_RE.pattern})\.json", re.ASCII)
TMP_NAME_RE = re.compile(rf"\.(?:{UUID4_RE.pattern})\.tmp", re.ASCII)

# Gruende fuer `present=False`
MISSING = "missing"
STALE = "stale"
UNSAFE = "unsafe"
PROTO = "proto"
INVALID = "invalid"

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _O_NOFOLLOW | _O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | _O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | _O_CLOEXEC | getattr(os, "O_NOCTTY", 0)
_SUPPORTED = os.name == "posix" and os.open in os.supports_dir_fd


class ChannelError(Exception):
    """Der Kanal fehlt oder ist nicht sicher (`code`: `missing` oder `unsafe`). Der Text ist nur der Code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Invalid(Exception):
    def __init__(self, reason: str = INVALID) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class HelperStatus:
    """Ergebnis von `read_status`. `doc` ist der bereinigte Status, auch wenn er nur veraltet ist (`reason="stale"`:
    die Ergebnisse darin stimmen trotzdem); bei `missing`, `unsafe`, `proto` und `invalid` ist er `None`."""

    present: bool
    reason: str | None
    doc: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Kleine Pruefungen
# ---------------------------------------------------------------------------


def is_version(value: object) -> bool:
    """Fertige Version im Sinne des Helfers (keine Vorabversion, keine fuehrende Null)."""
    return isinstance(value, str) and len(value) <= 32 and VERSION_RE.fullmatch(value) is not None


def is_code(value: object) -> bool:
    return isinstance(value, str) and CODE_RE.fullmatch(value) is not None


def is_request_id(value: object) -> bool:
    return isinstance(value, str) and UUID4_RE.fullmatch(value) is not None


def is_floating_tag(value: object) -> bool:
    return value == "latest" or (isinstance(value, str) and MINOR_TAG_RE.fullmatch(value) is not None)


def tag_fits_version(tag: object, version: object) -> bool:
    """Kann das bewegliche Tag auf `version` zeigen? `latest` immer, `X.Y` nur bei `X.Y.*` (wie beim Helfer)."""
    if not is_version(version):
        return False
    if tag == "latest":
        return True
    match = MINOR_TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    major, minor, _patch = (int(part) for part in str(version).split("."))
    return match is not None and (int(match[1]), int(match[2])) == (major, minor)


MAX_INT = 2 ** 53
"""Groesste Zahl im Status (Zeiten, Zaehler). JSON erlaubt beliebig lange Zahlen; eine Zeit wie `10**400` liesse
jede Rechnung mit der Uhr ueberlaufen (`OverflowError`) und darf deshalb gar nicht erst durchkommen."""


def _int(value: object) -> bool:
    """Echte, nicht negative Ganzzahl bis `MAX_INT` (`True` ist keine)."""
    return type(value) is int and 0 <= value <= MAX_INT


# ---------------------------------------------------------------------------
# Status bereinigen
# ---------------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("doppelter Schluessel")
        out[key] = value
    return out


def _no_constant(_name: str) -> Any:
    raise ValueError("NaN/Infinity")


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("unendlich")
    return value


def loads_strict(data: bytes) -> Any:
    """JSON ohne doppelte Schluessel, `NaN`, `Infinity` und Ueberlauf; jede Ausnahme wird `ValueError` ohne Inhalt."""
    try:
        return json.loads(bytes(data).decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant,
                          parse_float=_finite)
    except Exception:  # noqa: BLE001 - auch RecursionError/MemoryError: "unlesbar"
        raise ValueError("kein gueltiges JSON") from None


def _pick(obj: object, keys: tuple[str, ...]) -> dict[str, Any]:
    """Genau diese Schluessel muessen da sein; weitere fallen weg."""
    if not isinstance(obj, dict) or any(key not in obj for key in keys):
        raise _Invalid()
    return {key: obj[key] for key in keys}


def _clean_target(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    target = _pick(raw, ("current_version", "floating_tag", "pinned"))
    if not (target["current_version"] is None or is_version(target["current_version"])):
        raise _Invalid()
    if not (target["floating_tag"] is None or is_floating_tag(target["floating_tag"])):
        raise _Invalid()
    if not isinstance(target["pinned"], bool):
        raise _Invalid()
    return target


def _clean_busy(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    busy = _pick(raw, ("id", "action", "step", "since"))
    if not (is_request_id(busy["id"]) and busy["action"] in ACTIONS and is_code(busy["step"]) and _int(busy["since"])):
        raise _Invalid()
    return busy


def _clean_previous(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    previous = _pick(raw, ("version", "until"))
    if not (is_version(previous["version"]) and _int(previous["until"])):
        raise _Invalid()
    return previous


def _clean_result(raw: object) -> dict[str, Any] | None:
    """Ein Ergebnis oder `None`, wenn es nicht passt (dann faellt nur dieser Eintrag weg)."""
    try:
        result = _pick(raw, ("id", "action", "from", "to", "outcome", "code", "finished_at"))
    except _Invalid:
        return None
    ok = (
        is_request_id(result["id"]) and result["action"] in ACTIONS
        and (result["from"] is None or is_version(result["from"]))
        and (result["to"] is None or is_version(result["to"]))
        and result["outcome"] in OUTCOMES
        and (result["code"] is None or is_code(result["code"]))
        and _int(result["finished_at"])
    )
    return result if ok else None


def clean_status(raw: object) -> dict[str, Any]:
    """Der bereinigte Status oder `_Invalid` (mit `reason` `proto` oder `invalid`).

    Fuer die Felder, nach denen das Dashboard handelt (`ready`, `state`, `reason`, `target`, `busy`, `previous`),
    gilt: falsch heisst, der ganze Status gilt nicht. Ein unpassendes Ergebnis faellt nur einzeln weg, eine kaputte
    Helfer-Version wird `None`. Nach dem Muster von `bootstate.clean_state`."""
    if not isinstance(raw, dict):
        raise _Invalid()
    if type(raw.get("proto")) is not int or raw["proto"] != PROTOCOL:
        raise _Invalid(PROTO)
    versions = raw.get("request_versions")
    if not isinstance(versions, list) or not all(type(v) is int for v in versions):
        raise _Invalid(PROTO)
    if REQUEST_VERSION not in versions:
        raise _Invalid(PROTO)
    if not _int(raw.get("heartbeat_at")):
        raise _Invalid()
    if raw.get("state") not in STATES or not isinstance(raw.get("ready"), bool):
        raise _Invalid()
    reason = raw.get("reason")
    if not (reason is None or is_code(reason)):
        raise _Invalid()
    results_raw = raw.get("results")
    if not isinstance(results_raw, list) or len(results_raw) > RESULTS_MAX:
        raise _Invalid()
    for key in ("target", "busy", "previous"):
        if key not in raw:
            raise _Invalid()
    results = [r for r in (_clean_result(entry) for entry in results_raw) if r is not None]
    seq = raw.get("seq")
    return {
        "proto": PROTOCOL,
        "helper_version": raw["helper_version"] if is_version(raw.get("helper_version")) else None,
        "request_versions": sorted(set(versions)),
        "seq": seq if _int(seq) else None,
        "heartbeat_at": raw["heartbeat_at"],
        "state": raw["state"],
        "ready": raw["ready"],
        "reason": reason,
        "target": _clean_target(raw["target"]),
        "busy": _clean_busy(raw["busy"]),
        "previous": _clean_previous(raw["previous"]),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def _expected_uid(value: int | None) -> int:
    return EXPECTED_UID if value is None else value


def _open_root(updater_dir: Path, uid: int) -> int:
    """Wurzel per `O_NOFOLLOW` oeffnen und am Deskriptor pruefen. `ChannelError("missing"/"unsafe")`."""
    try:
        fd = os.open(os.fspath(updater_dir), _DIR_FLAGS)
    except FileNotFoundError:
        raise ChannelError(MISSING) from None
    except OSError:
        raise ChannelError(UNSAFE) from None  # ELOOP/ENOTDIR: die Wurzel ist ein Symlink oder keine Ordner
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ChannelError(UNSAFE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_requests(root_fd: int, uid: int) -> int:
    try:
        fd = os.open(REQUESTS_DIR, _DIR_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        raise ChannelError(MISSING) from None
    except OSError:
        raise ChannelError(UNSAFE) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or stat.S_IMODE(st.st_mode) != REQUESTS_MODE:
            raise ChannelError(UNSAFE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_status_bytes(root_fd: int, uid: int) -> bytes:
    try:
        fd = os.open(STATUS_NAME, _READ_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        raise ChannelError(MISSING) from None
    except OSError:
        raise ChannelError(UNSAFE) from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_nlink != 1
                or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) or st.st_size > STATUS_MAX_BYTES):
            raise ChannelError(UNSAFE)
        chunks: list[bytes] = []
        size = 0
        while size <= STATUS_MAX_BYTES:
            chunk = os.read(fd, STATUS_MAX_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > STATUS_MAX_BYTES:
            raise ChannelError(UNSAFE)
        return b"".join(chunks)
    except OSError:
        raise ChannelError(UNSAFE) from None
    finally:
        os.close(fd)


def read_status(updater_dir: Path, *, now: float | None = None, expected_uid: int | None = None) -> HelperStatus:
    """Liest `status.json` sicher (siehe Modulkopf). Wirft nie."""
    if not _SUPPORTED:
        return HelperStatus(False, MISSING, None)
    uid = _expected_uid(expected_uid)
    try:
        root_fd = _open_root(Path(updater_dir), uid)
        try:
            data = _read_status_bytes(root_fd, uid)
        finally:
            os.close(root_fd)
    except ChannelError as exc:
        return HelperStatus(False, exc.code, None)
    except Exception:  # noqa: BLE001 - ein Lesefehler heisst nur: kein Helfer
        return HelperStatus(False, UNSAFE, None)
    try:
        doc = clean_status(loads_strict(data))
    except _Invalid as exc:
        return HelperStatus(False, exc.reason, None)
    except ValueError:
        return HelperStatus(False, INVALID, None)
    current = time.time() if now is None else now
    beat = doc["heartbeat_at"]
    if current - beat > HEARTBEAT_MAX_AGE_S or beat - current > HEARTBEAT_MAX_FUTURE_S:
        return HelperStatus(False, STALE, doc)
    return HelperStatus(True, None, doc)


def pending_requests(updater_dir: Path, *, expected_uid: int | None = None) -> list[str]:
    """IDs der Anforderungen, die noch in `requests/` liegen (der Helfer hat sie noch nicht abgeholt). Fehlt der Kanal
    oder ist er unsicher: `ChannelError`."""
    if not _SUPPORTED:
        raise ChannelError(MISSING)
    uid = _expected_uid(expected_uid)
    root_fd = _open_root(Path(updater_dir), uid)
    try:
        dfd = _open_requests(root_fd, uid)
    finally:
        os.close(root_fd)
    try:
        found: list[str] = []
        with os.scandir(dfd) as entries:
            for seen, entry in enumerate(entries):
                if seen >= MAX_PENDING_SCAN:
                    break
                match = REQUEST_NAME_RE.fullmatch(entry.name)
                if match is not None:
                    found.append(match[1])
        return sorted(found)
    except OSError:
        raise ChannelError(UNSAFE) from None
    finally:
        os.close(dfd)


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


def encode_request(*, request_id: str, action: str, version: str, created_at: int) -> bytes:
    """Die Anforderung als kompaktes ASCII-JSON (genau die fuenf Schluessel)."""
    if not is_request_id(request_id) or action not in ACTIONS or not is_version(version) or not _int(created_at):
        raise ValueError("ungueltige Anforderung")
    body = {"v": REQUEST_VERSION, "id": request_id, "action": action, "version": version, "created_at": created_at}
    data = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(data) > REQUEST_MAX_BYTES:
        raise ValueError("Anforderung zu gross")
    return data


def write_request(
    updater_dir: Path,
    *,
    action: str,
    version: str,
    now: float | None = None,
    request_id: str | None = None,
    expected_uid: int | None = None,
) -> dict[str, Any]:
    """Schreibt eine Anforderung atomar mit Modus 0644 (siehe Modulkopf) und gibt sie zurueck. `ChannelError`, wenn der
    Kanal fehlt oder nicht sicher ist; `ValueError` bei ungueltigen Werten. Nur Owner-Code ruft das auf."""
    if not _SUPPORTED:
        raise ChannelError(MISSING)
    request_id = request_id or str(uuid.uuid4())
    created_at = int(time.time() if now is None else now)
    data = encode_request(request_id=request_id, action=action, version=version, created_at=created_at)
    uid = _expected_uid(expected_uid)
    root_fd = _open_root(Path(updater_dir), uid)
    try:
        dfd = _open_requests(root_fd, uid)
    finally:
        os.close(root_fd)
    tmp = f".{request_id}.tmp"
    final = f"{request_id}.json"
    try:
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, REQUEST_MODE,
                         dir_fd=dfd)
        except OSError as exc:
            raise ChannelError(UNSAFE) from exc
        try:
            try:
                os.fchmod(fd, REQUEST_MODE)  # die umask (077) haette sonst 0600 daraus gemacht
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmp, final, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
            raise
        with contextlib.suppress(OSError):
            os.fsync(dfd)
    except ChannelError:
        raise
    except OSError as exc:
        raise ChannelError(UNSAFE) from exc
    finally:
        os.close(dfd)
    return {"v": REQUEST_VERSION, "id": request_id, "action": action, "version": version, "created_at": created_at}
