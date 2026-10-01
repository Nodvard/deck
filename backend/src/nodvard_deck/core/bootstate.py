"""Zustand des Starts: `<Datenordner>/.boot/` (nicht Teil von Sicherungen).

Hier treffen sich `nodvard_deck.boot` (laeuft VOR der Anwendung), die Anwendung selbst, `nodvard_deck.admin`
und die Notseite (`nodvard_deck.rescue`, nur Standardbibliothek -- sie liest dieselben Dateien mit eigenem
kleinem Code; `tests/test_rescue.py` prueft, dass beide Seiten dasselbe meinen).

* `state.json` -- was der Start ueber die Datenbank weiss: welche Version zuletzt gebootet hat, auf
  welchen Migrationsstaenden die Datenbank steht, ob die Version nach der letzten Migration
  **erfolgreich gestartet** ist (`started_ok`), die letzte Migration samt Vorher-Kopie, ein noch nicht
  abgeschlossener Rueckweg (`revert`), solange eine eingespielte Sicherung noch nicht endgueltig ist der Stand
  der ALTEN Datenbank (`pre_restore`, siehe `boot.py`) und der Grund des letzten gescheiterten Starts (`failure`,
  bereinigt: keine Pfade, Passwoerter, SQL-Parameter). Alles darin wird beim Lesen auf Form und
  Typ geprueft -- die Datei liegt zwar im eigenen Datenordner, aber ein halb geschriebener oder von
  Hand veraenderter Stand darf nie zu einem Absturz fuehren.
* `app.lock` -- die Sperre. Die laufende Anwendung (und die Notseite) halten sie, `boot` und
  `admin restore-backup` pruefen sie: So spielt `boot` nie unter einer laufenden Anwendung ein
  (z. B. durch ein `compose run` ohne `--entrypoint`), und `admin` raeumt keinen laufenden Upload
  der Anwendung weg. `flock` gilt je geoeffneter Datei und endet mit dem Prozess -- auch wenn er
  abstuerzt. Wo es nicht geht (Windows, ein Netzlaufwerk ohne `flock`), gibt es eine Sperre ohne
  Wirkung (`held=False`), nie einen Fehler.
* `rollback.json` -- ausdrueckliche Vormerkung "Stand vor dem Update wiederherstellen" (von der
  Notseite oder spaeter vom Aktualisierungs-Helfer). Gilt 24 Stunden.
* `rescue_code.txt` -- der Notfallcode der Notseite (siehe `rescue.py`).
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup import restore as _restore

try:  # nicht unter Windows
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger("nodvard_deck.bootstate")

BOOT_DIR = ".boot"
STATE_NAME = "state.json"
LOCK_NAME = "app.lock"
ROLLBACK_NAME = "rollback.json"
RESCUE_CODE_NAME = "rescue_code.txt"
STATE_VERSION = 1
ROLLBACK_TTL_S = 24 * 3600

FAILURE_KINDS = (
    "migration_failed",   # die Migration ist gescheitert (die Datenbank ist, wie sie vorher war)
    "revert_failed",      # ... und der Rueckweg auf die Kopie auch
    "no_space",           # zu wenig Platz fuer die Kopie vor der Migration
    "no_copy",            # die Kopie liess sich nicht anlegen
    "newer_data",         # die Datenbank ist neuer als diese Version
    "copy_unusable",      # die Kopie fuer den Rueckweg fehlt oder ist kaputt
    "db_unreadable",      # die Datenbank ist nicht lesbar
    "rollback_failed",    # der Rueckweg einer Wiederherstellung ist gescheitert / unlesbares Journal
    "unexpected",         # sonst etwas Unerwartetes
)
MIGRATION_STATES = ("running", "ok", "failed", "reverted")

COPY_NAME_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9A-Za-z.+-]{1,32}_[0-9A-Za-z.+-]{1,32}\.db$")
REPLACED_RE = re.compile(r"^replaced-[0-9A-Za-z]{1,40}$")
_REVISION_RE = re.compile(r"^[0-9A-Za-z_.-]{1,64}$")
_MAX_HEADS = 64
_MAX_LOG_LINES = 50
_MAX_LINE = 300


def now_iso(now: float | None = None) -> str:
    return _restore.now_iso(now)


def boot_dir(data_dir: Path) -> Path:
    return Path(data_dir) / BOOT_DIR


def ensure_dir(data_dir: Path) -> Path:
    return _restore.make_private_dir(boot_dir(data_dir))


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


def _str(value: object, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(c for c in value if c.isprintable())
    return cleaned[:limit]


def _heads(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({v for v in value[:_MAX_HEADS] if isinstance(v, str) and _REVISION_RE.match(v)})


def _copy_name(value: object) -> str | None:
    return value if isinstance(value, str) and COPY_NAME_RE.match(value) else None


def _clean_migration(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    state = raw.get("state")
    clean: dict[str, Any] = {
        "at": _str(raw.get("at"), 32),
        "from_version": _str(raw.get("from_version"), 40),
        "to_version": _str(raw.get("to_version"), 40),
        "from_heads": _heads(raw.get("from_heads")),
        "to_heads": _heads(raw.get("to_heads")),
        "copy": _copy_name(raw.get("copy")),
        "state": state if state in MIGRATION_STATES else "failed",
    }
    if isinstance(raw.get("had_data"), bool):
        clean["had_data"] = raw["had_data"]  # gab es vor der Migration etwas zu schuetzen? (siehe boot.decide)
    return clean


def _clean_failure(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    log = raw.get("log")
    rollback = raw.get("rollback")
    clean_rollback = None
    if isinstance(rollback, dict) and _copy_name(rollback.get("copy")):
        clean_rollback = {
            "copy": rollback["copy"],
            "from_version": _str(rollback.get("from_version"), 40),
            "to_version": _str(rollback.get("to_version"), 40),
            "started_ok": rollback.get("started_ok") if isinstance(rollback.get("started_ok"), bool) else None,
            "started_at": _str(rollback.get("started_at"), 32),
        }
    out: dict[str, Any] = {
        "kind": kind if kind in FAILURE_KINDS else "unexpected",
        "at": _str(raw.get("at"), 32),
        "reason": (_str(raw.get("reason"), 1000) or ""),
        "app_version": _str(raw.get("app_version"), 40),
        "data_version": _str(raw.get("data_version"), 40),
        "previous_version": _str(raw.get("previous_version"), 40),
        "log": [line[:_MAX_LINE] for line in (_str(x, _MAX_LINE) for x in (log[-_MAX_LOG_LINES:] if isinstance(log, list) else [])) if line],
        "rollback": clean_rollback,
    }
    for number in ("needed_bytes", "free_bytes"):
        value = raw.get(number)
        out[number] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return out


def clean_state(raw: object) -> dict[str, Any]:
    """Nur bekannte Schluessel mit passendem Typ; alles andere faellt weg."""
    if not isinstance(raw, dict):
        return {}
    state: dict[str, Any] = {"version": STATE_VERSION}
    if (value := _str(raw.get("app_version"), 40)) is not None:
        state["app_version"] = value
    if isinstance(raw.get("db_heads"), list):
        state["db_heads"] = _heads(raw.get("db_heads"))
    if isinstance(raw.get("started_ok"), bool):
        state["started_ok"] = raw["started_ok"]
    if (value := _str(raw.get("started_at"), 32)) is not None:
        state["started_at"] = value
    if (migration := _clean_migration(raw.get("last_migration"))) is not None:
        state["last_migration"] = migration
    revert = raw.get("revert")
    if isinstance(revert, dict) and _copy_name(revert.get("copy")):
        replaced = revert.get("replaced")
        state["revert"] = {
            "copy": revert["copy"],
            "heads": _heads(revert.get("heads")),
            "replaced": replaced if isinstance(replaced, str) and REPLACED_RE.match(replaced) else None,
            "keep": bool(revert.get("keep")),
            "reason": _str(revert.get("reason"), 60),
            "at": _str(revert.get("at"), 32),
        }
    if (failure := _clean_failure(raw.get("failure"))) is not None:
        state["failure"] = failure
    pre = raw.get("pre_restore")
    if isinstance(pre, dict) and isinstance(pre.get("id"), str) and _restore.ID_RE.match(pre["id"]):
        state["pre_restore"] = {
            "id": pre["id"],
            "last_migration": _clean_migration(pre.get("last_migration")),
            "started_ok": pre.get("started_ok") if isinstance(pre.get("started_ok"), bool) else None,
        }
    return state


def state_path(data_dir: Path) -> Path:
    return boot_dir(data_dir) / STATE_NAME


def read_state(data_dir: Path) -> dict[str, Any]:
    """Der geprueft gelesene Stand; `{}`, wenn es ihn nicht gibt oder er unbrauchbar ist."""
    return clean_state(_restore.read_json(state_path(data_dir), max_bytes=1024 * 1024))


def write_state(data_dir: Path, state: dict[str, Any]) -> None:
    ensure_dir(data_dir)
    _restore.write_json_atomic(state_path(data_dir), clean_state(state))


def update_state(data_dir: Path, **changes: Any) -> dict[str, Any]:
    """Liest, setzt die genannten Schluessel (`None` entfernt sie) und schreibt zurueck."""
    state = read_state(data_dir)
    for key, value in changes.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
    write_state(data_dir, state)
    return read_state(data_dir)


def mark_started_ok(data_dir: Path, *, now: float | None = None) -> bool:
    """Die Anwendung ist (nach einer Migration) vollstaendig gestartet. Nur wenn es schon einen Stand
    gibt, also `boot` lief -- in Entwicklung und Tests entsteht so nichts im Datenordner."""
    if not os.path.lexists(state_path(data_dir)):
        return False
    state = read_state(data_dir)
    if not state:
        return False
    state["started_ok"] = True
    state["started_at"] = now_iso(now)
    write_state(data_dir, state)
    return True


# ---------------------------------------------------------------------------
# Sperre
# ---------------------------------------------------------------------------


class LockHeld(Exception):
    """Ein anderer Prozess haelt die Sperre (die laufende Anwendung, ein anderes `boot`)."""


@dataclass
class Lock:
    fd: int | None
    held: bool
    """`False`: keine Sperre moeglich (Windows, Ordner nicht beschreibbar, Dateisystem ohne `flock`)."""

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)  # beendet auch die Sperre
        self.held = False


def _try_lock(path: Path, purpose: str) -> Lock:
    """Ein Versuch. `LockHeld`, wenn ein anderer sie hat; sonst die Sperre oder eine ohne Wirkung."""
    if fcntl is None:
        return Lock(None, False)
    try:
        ensure_dir(path.parent.parent)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return Lock(None, False)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise LockHeld() from None
        return Lock(None, False)  # z. B. ENOLCK/ENOTSUP: das Dateisystem kann es nicht
    with contextlib.suppress(OSError):
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {purpose}\n".encode())
    return Lock(fd, True)


def acquire_lock(data_dir: Path, *, purpose: str = "app", wait_s: float = 0.0, poll_s: float = 0.2) -> Lock:
    """Nimmt die Sperre. Ist sie belegt, wird bis zu `wait_s` Sekunden gewartet, dann `LockHeld`."""
    path = boot_dir(data_dir) / LOCK_NAME
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            return _try_lock(path, purpose)
        except LockHeld:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)


def is_locked(data_dir: Path) -> bool:
    """Haelt gerade ein anderer Prozess die Sperre? (Ohne sie zu behalten.)"""
    try:
        lock = acquire_lock(data_dir, purpose="pruefung")
    except LockHeld:
        return True
    lock.release()
    return False


# ---------------------------------------------------------------------------
# Rueckweg-Vormerkung
# ---------------------------------------------------------------------------


def request_rollback(data_dir: Path, copy: str, *, by: str, now: float | None = None) -> None:
    """Merkt vor: beim naechsten Start den Stand der Kopie `copy` wiederherstellen, auch wenn die neue
    Version schon einmal gestartet ist (Aenderungen seit dem Update gehen dann verloren)."""
    if not _copy_name(copy):
        raise ValueError("ungueltiger Name der Kopie")
    ensure_dir(data_dir)
    _restore.write_json_atomic(
        boot_dir(data_dir) / ROLLBACK_NAME,
        {"copy": copy, "by": _str(by, 40) or "unbekannt", "requested_at": time.time() if now is None else now},
    )


def read_rollback(data_dir: Path, *, now: float | None = None) -> dict[str, Any] | None:
    """Die gueltige Vormerkung oder `None` (nicht da, kaputt, abgelaufen, aus der Zukunft)."""
    raw = _restore.read_json(boot_dir(data_dir) / ROLLBACK_NAME, max_bytes=4096)
    if not isinstance(raw, dict) or not _copy_name(raw.get("copy")):
        return None
    at = raw.get("requested_at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return None
    current = time.time() if now is None else now
    if at > current + 300 or current - at > ROLLBACK_TTL_S:
        return None
    return {"copy": raw["copy"], "by": _str(raw.get("by"), 40) or "unbekannt", "requested_at": float(at)}


def clear_rollback(data_dir: Path) -> None:
    with contextlib.suppress(OSError):
        os.unlink(boot_dir(data_dir) / ROLLBACK_NAME)


def clear_rescue_code(data_dir: Path) -> bool:
    try:
        os.unlink(boot_dir(data_dir) / RESCUE_CODE_NAME)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Bereinigtes Protokoll
# ---------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SQL_BLOCK = re.compile(r"\[(?:SQL|parameters):.*", re.IGNORECASE)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_KEY = re.compile(
    r"(?i)(?P<key>[A-Za-z0-9_.-]*(?:password|passwd|passwort|secret|token|api[_-]?key|authorization|cookie|private[_-]?key|credential)[A-Za-z0-9_.-]*)"
    r"(?P<sep>[\"']?\s*[=:]\s*[\"']?)(?P<value>[^\s\"',;)\]]+)"
)
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s\"']+\\)+")
_POSIX_PATH = re.compile(r"(?<![\w.:/-])/(?:[\w.+@~-]+/)+[\w.+@~-]*")
_OPAQUE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{32,}(?![A-Za-z0-9+/=_-])")


def _shorten_path(match: re.Match[str]) -> str:
    parts = [p for p in match.group(0).split("/") if p]
    return "…/" + "/".join(parts[-2:]) if len(parts) > 2 else match.group(0)


def sanitize_text(text: str, *, data_dir: Path | None = None, limit: int = _MAX_LINE) -> str:
    """Ein Protokolltext fuer die Anzeige: ohne Pfade (der Datenordner heisst `<Daten>`), Zugangsdaten,
    SQL-Parameter und lange undurchsichtige Zeichenketten. Nutzbar fuer die Fehlersuche bleibt die Art
    des Fehlers, die Dateinamen und Zeilennummern."""
    text = _ANSI.sub("", text)
    text = _CONTROL.sub("", text)
    if data_dir is not None:
        prefix = os.fspath(data_dir).rstrip("/\\")
        if prefix:
            text = text.replace(prefix, "<Daten>")
    text = _SQL_BLOCK.sub("[SQL und Parameter entfernt]", text)
    text = _URL_CREDENTIALS.sub(lambda m: m.group("scheme") + "***@", text)
    text = _BEARER.sub(lambda m: m.group(1) + " ***", text)
    text = _SECRET_KEY.sub(lambda m: f"{m.group('key')}{m.group('sep')}***", text)
    text = _WINDOWS_PATH.sub("…\\\\", text)
    text = _POSIX_PATH.sub(_shorten_path, text)
    text = _OPAQUE.sub("***", text)
    return text[:limit]


def sanitize_lines(lines: Iterable[str], *, data_dir: Path | None = None, limit: int = _MAX_LOG_LINES) -> list[str]:
    """Die letzten `limit` nicht leeren Zeilen, einzeln bereinigt."""
    flat: list[str] = []
    for entry in lines:
        for line in str(entry).splitlines():
            if line.strip():
                flat.append(line)
    return [sanitize_text(line, data_dir=data_dir) for line in flat[-limit:]]
