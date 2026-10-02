"""Die Notseite: `python -m nodvard_deck.rescue` (startet `deploy/entrypoint.sh`, wenn `nodvard_deck.boot` scheitert).

**Nur Standardbibliothek.** Dieses Modul importiert nichts, was nicht zu Python gehoert (ein Test prueft das
ueber den Syntaxbaum und startet es ohne jedes installierte Paket). Der Grund: Die Notseite ist fuer genau
den Fall da, dass im neuen Image etwas fehlt oder kaputt ist -- eine fehlende Abhaengigkeit (pydantic,
SQLAlchemy, ...) darf sie nie mitreissen. Darum auch kein Import aus `nodvard_deck.core`: was sie von dort
braucht (Zustandsdatei lesen, Sperre, Notfallcode, Rueckweg-Vormerkung), hat sie als kleinen eigenen Code;
`tests/test_rescue.py` stellt sicher, dass beide Seiten dasselbe meinen. Auch die Seite selbst kommt ohne
fremde Dateien aus: ein Stueck HTML mit eingebettetem Stil, kein Skript, keine Schrift, kein Bild.

**Ein eigener Server** (`http.server.ThreadingHTTPServer`) auf dem Port der Anwendung (Standard 8080;
`--port`/`--host` uebernimmt der Entrypoint aus dem Startbefehl).

* `GET /api/v1/health` -> **503** `{"status":"rescue"}`. Nie `ok` im Inhalt: Docker-Healthcheck,
  `scripts/deploy_pi.sh` (`grep "status":"ok"`) und ein Aktualisierungs-Helfer erkennen so, dass NICHT alles
  in Ordnung ist, und schalten zurueck. Jeder andere Pfad unter `/api/` -> 503 JSON.
* `GET <alles andere>` -> die Seite (503, Deutsch). **Ohne Code** nur Allgemeines: was passiert ist (dass
  Nodvard Deck nicht gestartet ist), wo der Notfallcode steht, die allgemeinen Wege zurueck. Keine
  Fehlerdetails, keine Versionen, keine Aktionen.
* **Notfallcode** wie der Einrichtungscode (`core/setup_code.py`): 3 x 4 Zeichen ohne verwechselbare, in der Datei
  `<Datenordner>/.boot/rescue_code.txt` (0600), als Banner im Protokoll des Containers. Eingabe gedrosselt
  (je Absender und insgesamt), danach ein kurzlebiges Cookie (HttpOnly, SameSite=Strict, 15 Minuten).
* **Mit Code**: Fehlergrund, Versionen, bereinigtes Protokoll und die Aktionen
  - `POST /rescue/retry` -> Prozessende mit 75, der Container startet neu (Restart-Regel) und `boot` laeuft noch einmal;
  - `POST /rescue/rollback` -> "Stand vor dem Update wiederherstellen" **vormerken** (`rollback.json`, die Kopie
    kommt aus dem Zustand von `boot`, nie aus der Anfrage), dann wie `retry`; eingespielt wird beim Start;
  - `GET /rescue/log` (bereinigt), `POST /rescue/unlock`, `POST /rescue/lock`.
* **Keine Aktion "Kopie herunterladen".** Eine unverschluesselte Datenbank ueber HTTP herauszugeben waere
  unverantwortlich, und die Verschluesselung (age) braucht `pyrage`, das nicht zur Standardbibliothek gehoert.
  Die Kopie liegt im Datenordner (`backups/vor-update/`) und laesst sich dort sichern.
* Aktionen (POST) pruefen `Origin` gegen `Host`; das Cookie ist SameSite=Strict.
* Haertung: kleine Obergrenzen (Inhalt 4 KiB, Kopfzeilen zusammen 32 KiB und hoechstens 100, 64 gleichzeitige
  Verbindungen, davon hoechstens 8 je Absender -- bei IPv6 je /64-Netz --, dazu ein eigener kleiner Vorrat fuer
  127.0.0.1/::1, damit der Health-Check des Containers immer durchkommt; 3 s fuer die Kopfzeilen und 10 s fuer die
  ganze Verbindung, beides als Frist fuer das Ganze und nicht je Lesevorgang), nie Dateien ausliefern, alles
  Dynamische maskiert, auch im Protokoll (Steuerzeichen aus der Anfrage erscheinen dort als `\\x1b` usw.).
* Eine Sperre nach Fehlversuchen weist nur FALSCHE Codes ab, jeden erst nach einer kurzen Bremse; der richtige Code geht
  immer sofort durch (siehe `try_code`).

Die Notseite haelt die Sperre des Datenordners (`.boot/app.lock`), solange sie laeuft: ein `compose run` mit `boot`
daneben aendert dann nichts.

Rueckgabe von `main`: 75 = Neustart gewuenscht (`retry`/`rollback`), 0 = beendet (SIGTERM), 2 = Port belegt o. ae.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import http.server
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:  # nicht unter Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# --- Namen und Formate: dieselben wie `core/bootstate.py` (ein Test vergleicht) ---------------------------------
BOOT_DIR = ".boot"
STATE_NAME = "state.json"
LOCK_NAME = "app.lock"
ROLLBACK_NAME = "rollback.json"
CODE_NAME = "rescue_code.txt"
COPY_NAME_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9A-Za-z.+-]{1,32}_[0-9A-Za-z.+-]{1,32}\.db$")
FAILURE_KINDS = (
    "migration_failed", "revert_failed", "no_space", "no_copy", "newer_data", "copy_unusable", "db_unreadable", "rollback_failed", "unexpected",
)

DEFAULT_PORT = 8080
EXIT_RETRY = 75

# --- Notfallcode (wie `core/setup_code.py`) ---------------------------------------------------------------------
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 3, 4
_STRIP = re.compile(r"[^A-Za-z0-9]")

# --- Grenzen -----------------------------------------------------------------------------------------------------
MAX_BODY = 4096
MAX_CONNECTIONS = 64
MAX_CONNECTIONS_PER_CLIENT = 8
"""Ein einzelner Rechner kann so nie alle Plaetze belegen (z. B. mit vielen halb geschickten Anfragen). Bei IPv6 zaehlt das
ganze /64-Netz als ein Absender: wer ein Netz hat, hat auch Milliarden Adressen."""
RESERVED_LOCAL_CONNECTIONS = 8
"""Eigener Vorrat fuer 127.0.0.1 und ::1 (Health-Check im Container, `docker exec`): zaehlt nicht zu `MAX_CONNECTIONS`, damit
auch dann noch jemand durchkommt, wenn andere alle Plaetze belegen. Von aussen laesst sich diese Adresse nicht vortaeuschen."""
MAX_HEADER_BYTES = 32 * 1024
"""Anfragezeile und Kopfzeilen zusammen. Die Standardbibliothek erlaubt sonst 100 Zeilen zu je 64 KiB je Verbindung.
Grosszuegig bemessen, weil Cookies je Adresse gelten und nicht je Port: Liegen auf derselben Adresse noch andere Dienste
(Anmeldung ueber einen Proxy, Nextcloud, Proxmox ...), schickt der Browser deren Cookies mit, und die Notseite muss
gerade im Notfall erreichbar bleiben. Die Kopfzeilen selbst belegen bei 72 Verbindungen (64 plus der Vorrat fuer
127.0.0.1) zusammen hoechstens rund 2,3 MiB; beim Zerlegen kommen ein paar Kopien dazu."""
MAX_HEADERS = 100
"""Hoechstzahl der Kopfzeilen; die Standardbibliothek erzwingt sie selbst (`http.client._MAXHEADERS`), hier steht sie der Klarheit wegen."""
HEAD_TIMEOUT_S = 3
"""Frist fuer Anfragezeile und Kopfzeilen (eine ehrliche Anfrage ist in Millisekunden da)."""
REQUEST_TIMEOUT_S = 10
"""Frist fuer die GANZE Verbindung (siehe `_Handler.setup`), nicht nur fuer jeden einzelnen Lesevorgang."""
SESSION_TTL_S = 15 * 60
MAX_SESSIONS = 20
MAX_FAILURES_PER_IP = 5
MAX_FAILURES_GLOBAL = 25
LOCK_S = 10 * 60
"""Fenster der Fehlversuche UND Dauer der Sperre: eine Sperre endet, wenn der aelteste Fehlversuch aus dem Fenster faellt."""
LOCKED_DELAY_S = 0.5
"""Bremse waehrend einer Sperre: die Antwort auf einen falschen Code kommt erst nach dieser Zeit. Der richtige Code wartet nie.
Mit 64 Plaetzen sind so hoechstens 128 Versuche je Sekunde moeglich, egal wie schnell der Rechner ist."""
MAX_TRACKED_CLIENTS = 512
ROLLBACK_BY = "notseite"


def normalize(code: object) -> str:
    return _STRIP.sub("", code if isinstance(code, str) else "").upper()


def _generate_code() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS))


def _pretty(normalized: str) -> str:
    return "-".join(normalized[i : i + GROUP_LEN] for i in range(0, len(normalized), GROUP_LEN))


# ---------------------------------------------------------------------------
# Dateien (eigener kleiner Code, siehe Modul-Docstring)
# ---------------------------------------------------------------------------


def _read_bytes(path: Path, limit: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    return raw if len(raw) <= limit else None


def _read_json(path: Path, limit: int = 1024 * 1024) -> Any:
    raw = _read_bytes(path, limit)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


def _boot_dir(data_dir: Path, *, create: bool = False) -> Path:
    path = Path(data_dir) / BOOT_DIR
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def _write_private(path: Path, text: str) -> None:
    """Ganz oder gar nicht, von Anfang an 0600 (Nachbardatei, dann umbenennen)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(c for c in value if c.isprintable())[:limit]


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def read_state(data_dir: Path) -> dict[str, Any]:
    """Das, was die Seite aus `state.json` braucht -- geprueft und begrenzt, nie ein Absturz."""
    raw = _read_json(_boot_dir(data_dir) / STATE_NAME)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    if (version := _text(raw.get("app_version"), 40)) is not None:
        out["app_version"] = version
    if isinstance(raw.get("started_ok"), bool):
        out["started_ok"] = raw["started_ok"]
    migration = raw.get("last_migration")
    if isinstance(migration, dict):
        out["last_migration"] = {"from_version": _text(migration.get("from_version"), 40), "to_version": _text(migration.get("to_version"), 40)}
    failure = raw.get("failure")
    if isinstance(failure, dict):
        log = failure.get("log")
        rollback = failure.get("rollback")
        clean_rollback = None
        if isinstance(rollback, dict) and isinstance(rollback.get("copy"), str) and COPY_NAME_RE.match(rollback["copy"]):
            clean_rollback = {
                "copy": rollback["copy"], "from_version": _text(rollback.get("from_version"), 40), "to_version": _text(rollback.get("to_version"), 40),
                "started_ok": rollback.get("started_ok") if isinstance(rollback.get("started_ok"), bool) else None,
                "started_at": _text(rollback.get("started_at"), 32),
            }
        kind = failure.get("kind")
        out["failure"] = {
            "kind": kind if kind in FAILURE_KINDS else "unexpected",
            "at": _text(failure.get("at"), 32),
            "reason": _text(failure.get("reason"), 1000) or "",
            "app_version": _text(failure.get("app_version"), 40),
            "data_version": _text(failure.get("data_version"), 40),
            "previous_version": _text(failure.get("previous_version"), 40),
            "log": [line for line in (_text(x, 300) for x in (log[-50:] if isinstance(log, list) else [])) if line],
            "rollback": clean_rollback,
            "needed_bytes": _int(failure.get("needed_bytes")),
            "free_bytes": _int(failure.get("free_bytes")),
        }
    return out


def write_rollback(data_dir: Path, copy: str, *, now: float | None = None) -> None:
    """Merkt vor: beim naechsten Start (`nodvard_deck.boot`) den Stand der Kopie `copy` wiederherstellen."""
    if not isinstance(copy, str) or not COPY_NAME_RE.match(copy):
        raise ValueError("ungueltiger Name der Kopie")
    directory = _boot_dir(data_dir, create=True)
    payload = {"copy": copy, "by": ROLLBACK_BY, "requested_at": time.time() if now is None else now}
    _write_private(directory / ROLLBACK_NAME, json.dumps(payload, sort_keys=True))


class _Lock:
    def __init__(self, fd: int | None) -> None:
        self.fd = fd

    @property
    def held(self) -> bool:
        return self.fd is not None

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def take_lock(data_dir: Path) -> _Lock:
    """Haelt die Sperre des Datenordners (wie die Anwendung). Geht das nicht, laeuft die Notseite trotzdem."""
    if fcntl is None:
        return _Lock(None)
    try:
        path = _boot_dir(data_dir, create=True) / LOCK_NAME
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return _Lock(None)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return _Lock(None)
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} notseite\n".encode())
    except OSError:
        pass
    return _Lock(fd)


# ---------------------------------------------------------------------------
# Zustand der Notseite: Code, Drosselung, Sitzungen
# ---------------------------------------------------------------------------


class Rescue:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.exit_code: int | None = None
        self.server: http.server.HTTPServer | None = None
        self._code: str | None = None
        self._lock = threading.Lock()
        self._failures_by_ip: dict[str, list[float]] = {}
        self._failures_global: list[float] = []
        self._sessions: dict[str, float] = {}  # sha256(Token) -> Ablauf

    # --- Code ---
    def ensure_code(self) -> str:
        """Der Notfallcode: aus der Datei, sonst neu gewuerfelt und gespeichert. Geht das Schreiben nicht
        (Datenordner schreibgeschuetzt), gilt er nur fuer diesen Lauf -- er steht ja im Protokoll."""
        path = _boot_dir(self.data_dir) / CODE_NAME
        raw = _read_bytes(path, 200)
        existing = normalize(raw.decode("utf-8", "replace")) if raw else ""
        if len(existing) == GROUPS * GROUP_LEN and all(c in ALPHABET for c in existing):
            self._code = _pretty(existing)
            return self._code
        code = _generate_code()
        try:
            _boot_dir(self.data_dir, create=True)
            _write_private(path, code + "\n")
        except OSError:
            pass
        self._code = code
        return code

    # --- Drosselung und Sitzungen ---
    def try_code(self, ip: str, candidate: object, *, now: float | None = None) -> tuple[str, Any]:
        """-> ("ok", Sitzungstoken) | ("wrong", None) | ("locked", Sekunden bis zum naechsten Versuch).

        Der Code wird IMMER geprueft, auch waehrend einer Sperre (immer mit `compare_digest`, vor jeder Auskunft ueber
        die Sperre). Eine Sperre, ob je Absender oder insgesamt, weist nur FALSCHE Versuche mit "locked" ab; der richtige
        Code kommt trotzdem durch. Sonst koennte jeder im Netz mit ein paar Fehlversuchen von wenigen Adressen die Notseite
        fuer alle verschliessen -- gerade den Rueckweg, der sie braucht.

        Raten bleibt aussichtslos: der Code hat 32^12 = 2^60 (rund 10^18) Moeglichkeiten. Waehrend einer Sperre darf zwar
        jeder weiterraten, aber jede Antwort auf einen falschen Code kommt erst nach `LOCKED_DELAY_S` (siehe `_Handler._post`):
        bei 64 Plaetzen hoechstens 128 Versuche je Sekunde. Im Mittel braeuchte man 2^59 / 128, rund 4,5 * 10^15 Sekunden,
        weit ueber hundert Millionen Jahre (selbst ohne Bremse, bei 10.000 je Sekunde, knapp zwei Millionen). Die Antwort
        verraet nichts ausser dem Erfolg: "locked" und "wrong" sagen bei einem falschen Code nichts darueber, wie nah er war.

        Fehlversuche zaehlen nur, solange keine Sperre greift: waehrend einer Sperre kommt nichts mehr dazu (die Sperre
        laesst sich nicht verlaengern, und die Listen im Speicher bleiben klein, egal wie viel jemand schickt)."""
        now = time.monotonic() if now is None else now
        expected = normalize(self._code).encode()
        good = secrets.compare_digest(normalize(candidate).encode(), expected) and bool(expected)
        with self._lock:
            if good:
                return "ok", self._new_session(now)
            horizon = now - LOCK_S
            self._failures_global = [t for t in self._failures_global if t > horizon]
            attempts = [t for t in self._failures_by_ip.get(ip, []) if t > horizon]
            for limit, window in ((MAX_FAILURES_GLOBAL, self._failures_global), (MAX_FAILURES_PER_IP, attempts)):
                if len(window) >= limit:
                    return "locked", max(1, int(min(window) + LOCK_S - now) + 1)
            self._failures_global.append(now)
            attempts.append(now)
            self._failures_by_ip[ip] = attempts
            self._trim_clients(now)
            return "wrong", None

    def _trim_clients(self, now: float) -> None:
        if len(self._failures_by_ip) <= MAX_TRACKED_CLIENTS:
            return
        horizon = now - LOCK_S
        for key in [k for k, v in self._failures_by_ip.items() if not v or v[-1] <= horizon]:
            del self._failures_by_ip[key]
        excess = len(self._failures_by_ip) - MAX_TRACKED_CLIENTS
        if excess > 0:
            for key in sorted(self._failures_by_ip, key=lambda k: self._failures_by_ip[k][-1])[:excess]:
                del self._failures_by_ip[key]

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _new_session(self, now: float) -> str:
        self._sessions = {k: v for k, v in self._sessions.items() if v > now}
        while len(self._sessions) >= MAX_SESSIONS:
            del self._sessions[min(self._sessions, key=self._sessions.__getitem__)]
        token = secrets.token_urlsafe(32)
        self._sessions[self._digest(token)] = now + SESSION_TTL_S
        return token

    def session_valid(self, token: object, *, now: float | None = None) -> bool:
        if not isinstance(token, str) or not token:
            return False
        now = time.monotonic() if now is None else now
        with self._lock:
            expiry = self._sessions.get(self._digest(token))
            if expiry is None:
                return False
            if expiry <= now:
                del self._sessions[self._digest(token)]
                return False
            return True

    def end_session(self, token: object) -> None:
        if isinstance(token, str) and token:
            with self._lock:
                self._sessions.pop(self._digest(token), None)

    def request_exit(self, code: int = EXIT_RETRY) -> None:
        """Beendet den Server kurz nach der Antwort; `main` gibt `code` zurueck."""
        self.exit_code = code
        server = self.server
        if server is not None:
            threading.Timer(0.3, server.shutdown).start()

    # --- Seite ---
    def render(self, *, unlocked: bool, flash: str | None = None) -> str:
        return render_page(read_state(self.data_dir), unlocked=unlocked, flash=flash)


# ---------------------------------------------------------------------------
# Die Seite
# ---------------------------------------------------------------------------

_FLASH = {
    "falsch": ("bad", "Der Notfallcode stimmt nicht."),
    "entsperrt": ("good", "Entsperrt. Die Einzelheiten und die Möglichkeiten stehen unten."),
    "abgemeldet": ("good", "Abgemeldet."),
    "nicht_moeglich": ("bad", "Das geht in dieser Lage nicht."),
}

_LOCKED_TEXT = (
    "Der eingegebene Code stimmt nicht – prüfe die Eingabe. "
    "Der richtige Code funktioniert weiterhin sofort, auch wenn du schon mehrmals falsch getippt hast."
)
"""Antwort auf einen falschen Code waehrend einer Sperre (siehe `Rescue.try_code`): Die Sperre weist nur falsche Codes ab."""

_CSS = """
:root{color-scheme:light dark;--bg:#f4f5f7;--fg:#1b1f24;--muted:#5b6470;--card:#fff;--line:#d5d9e0;--accent:#2b5fd9;--bad:#b3261e;--good:#1b7a3a;--warn:#8a5a00;--warnbg:#fff4d6;--code:#eef0f4}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e8eaee;--muted:#a3abb8;--card:#1d2128;--line:#343a45;--accent:#7aa2ff;--bad:#ff8a80;--good:#7ee2a0;--warn:#ffd27a;--warnbg:#3a2f12;--code:#0f1216}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:46rem;margin:0 auto;padding:1.25rem 16px 3rem}
h1{font-size:1.5rem;margin:.2rem 0 .1rem}
h2{font-size:1.1rem;margin:0 0 .6rem}
.sub{color:var(--muted);margin:0 0 1.2rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1rem 1.1rem;margin:0 0 1rem}
.banner{border-left:4px solid var(--warn);background:var(--warnbg);color:var(--fg)}
.flash{border-radius:8px;padding:.6rem .8rem;margin:0 0 1rem;border:1px solid var(--line)}
.flash.bad{color:var(--bad);border-color:var(--bad)}.flash.good{color:var(--good);border-color:var(--good)}
ol,ul{padding-left:1.3rem;margin:.4rem 0}li{margin:.35rem 0}
code,pre{background:var(--code);border-radius:6px;font:.88rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
code{padding:.1rem .35rem}
pre{padding:.7rem;overflow:auto;max-height:22rem;white-space:pre-wrap;word-break:break-word}
label{display:block;font-weight:600;margin:0 0 .3rem}
input[type=text]{width:100%;max-width:22rem;padding:.6rem .7rem;font:1.05rem ui-monospace,Menlo,Consolas,monospace;letter-spacing:.08em;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
button{font:inherit;font-weight:600;padding:.6rem 1rem;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
button.danger{background:transparent;color:var(--bad);border-color:var(--bad)}
button.plain{background:transparent;color:var(--fg);border-color:var(--line);font-weight:500}
.row{display:flex;flex-wrap:wrap;gap:.6rem;margin:.6rem 0 0}.row form{margin:0}
.muted{color:var(--muted)}.small{font-size:.9rem}
details summary{cursor:pointer;font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.2rem 1rem;margin:.4rem 0}dt{color:var(--muted)}dd{margin:0}
@media (max-width:480px){dl{grid-template-columns:1fr}dt{margin-top:.4rem}}
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _version(value: str | None) -> str:
    return f"Version {_e(value)}" if value else "einer früheren Version"


def _mb(size: int | None) -> str:
    return "unbekannt" if size is None else (f"{size // (1024 * 1024)} MB" if size >= 1024 * 1024 else f"{size} Byte")


_BACK_STEP = (
    "Stelle die Image-Version in der Compose-Datei (oder dort, wo du das Image deines Systems einträgst) auf {target} zurück "
    "und starte den Container neu."
)
_RETRY_STEP = "Behebe den Grund (siehe Protokoll) und wähle dann „Neu versuchen“."
_RESTORE_DOC_URL = "https://github.com/nodvard/deck/blob/main/docs/11-ERST-EINRICHTUNG.md#102-wiederherstellen-sicherung-einspielen"
_NEEDS_FEATURE = (
    "Der Rückweg auf eine ältere Version klappt nur, wenn diese Version selbst schon die Kopie vor dem Update und diese Notseite "
    "kennt. Ältere Versionen können eine Kopie nicht selbst wieder einspielen."
)


def _kind_text(kind: str, failure: dict[str, Any], rollback: dict[str, Any] | None) -> tuple[str, str, list[str]]:
    """(Titel, Einleitung, Schritte) -- alles schon maskiert."""
    app = _version(failure.get("app_version"))
    data = _version(failure.get("data_version"))
    previous = failure.get("previous_version")
    back_to_previous = _BACK_STEP.format(target=_version(previous) if previous else "die vorherige Version")
    back_to_data = _BACK_STEP.format(target=f"{data} oder neuer")
    no_copy_exit = (
        "Notausgang, nur wenn es gar nicht anders geht: Mit der Umgebungsvariable <code>NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1</code> "
        "wird ohne Kopie migriert. Scheitert die Migration dann, gibt es keinen Rückweg."
    )
    if kind == "newer_data":
        steps = [back_to_data]
        if rollback:
            since = rollback.get("started_at")
            lost = f"Alles, was seit {_e(since)} neu dazugekommen ist, geht dabei verloren." if since else "Alles, was seit dem Update neu dazugekommen ist, geht dabei verloren."
            steps.append(
                f"Oder geh bewusst zurück: „Stand vor dem Update wiederherstellen“ setzt die Daten auf den Stand von vor dem Update zurück. "
                f"{lost} Die neueren Daten bleiben 30 Tage im Datenordner liegen (<code>restore/replaced-…</code>)."
            )
        return (
            "Die Daten sind neuer als diese Version",
            f"Diese Installation läuft jetzt mit {app}. Die Daten wurden aber zuletzt von {data} verändert und passen nicht zu dieser älteren Version.",
            steps,
        )
    if kind == "migration_failed":
        return (
            "Das Update der Datenbank ist gescheitert",
            f"Nodvard Deck sollte auf {app} gebracht werden, aber die Datenbank ließ sich nicht darauf umstellen.",
            [back_to_previous, _RETRY_STEP, _NEEDS_FEATURE],
        )
    if kind == "revert_failed":
        return (
            "Das Zurücksetzen auf die Kopie ist gescheitert",
            "Die Kopie von vor dem Update bleibt erhalten (Datenordner, <code>backups/vor-update/</code>). Der nächste Start versucht das Zurücksetzen noch einmal.",
            ["Prüfe Platz und Schreibrechte des Datenordners und wähle dann „Neu versuchen“.", "Bitte lösche bis dahin nichts im Datenordner."],
        )
    if kind == "no_space":
        needed, free = _mb(failure.get("needed_bytes")), _mb(failure.get("free_bytes"))
        return (
            "Zu wenig Speicherplatz für die Kopie vor dem Update",
            f"Vor jedem Update legt Nodvard Deck eine Kopie der Datenbank an und migriert nie ohne sie. Dafür fehlt Platz (nötig: {needed}, frei: {free}). Es wurde nichts verändert.",
            ["Schaffe Platz auf dem Laufwerk des Datenordners und wähle dann „Neu versuchen“.", back_to_previous, no_copy_exit],
        )
    if kind == "no_copy":
        return (
            "Die Kopie vor dem Update ließ sich nicht anlegen",
            "Ohne Kopie wird nicht migriert. Es wurde nichts verändert.",
            ["Prüfe Platz und Schreibrechte des Datenordners (Unterordner <code>backups/vor-update</code>) und wähle dann „Neu versuchen“.", back_to_previous, no_copy_exit],
        )
    if kind == "copy_unusable":
        return (
            "Die Kopie von vor dem Update ist nicht brauchbar",
            f"Die Daten sind neuer als die laufende Version ({app}), und die Kopie von vor dem Update fehlt oder ist beschädigt. Ein automatischer Rückweg ist nicht möglich.",
            [back_to_data, "Oder spiele eine eigene Sicherung ein (Einstellungen, System, Wiederherstellen; im Container <code>python -m nodvard_deck.admin restore-backup</code>)."],
        )
    if kind == "db_unreadable":
        return (
            "Die Datenbank ist nicht lesbar",
            "Es wurde nichts verändert. Vermutlich ist die Datenbankdatei beschädigt.",
            ["Spiele eine eigene Sicherung ein (im Container <code>python -m nodvard_deck.admin restore-backup</code>) und starte den Container danach neu.", _RETRY_STEP],
        )
    if kind == "rollback_failed":
        return (
            "Das Einspielen einer Sicherung ist nicht sauber zu Ende gegangen",
            "Der alte Stand liegt noch im Datenordner unter <code>restore/replaced-…</code>. Bitte nichts löschen.",
            [
                "Die Schritte stehen in der Anleitung, Abschnitt 10.2 „Wiederherstellen (Sicherung einspielen)“: "
                f'<a href="{_RESTORE_DOC_URL}" rel="noreferrer" style="word-break:break-all">{_RESTORE_DOC_URL}</a>.',
                "Zum Zurücksetzen von Hand wird der Inhalt dieses Ordners wieder in den Datenordner kopiert.",
            ],
        )
    return (
        "Beim Start ist etwas Unerwartetes schiefgegangen",
        "Nodvard Deck konnte nicht starten. Es wurde nichts absichtlich verändert.",
        [_RETRY_STEP, back_to_previous],
    )


def render_page(state: dict[str, Any], *, unlocked: bool, flash: str | None = None) -> str:
    parts: list[str] = []
    add = parts.append
    add(
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        "<title>Nodvard Deck – Notfallmodus</title>"
        f"<style>{_CSS}</style></head><body><main>"
    )
    add("<h1>Nodvard Deck</h1><p class=\"sub\">Notfallmodus</p>")
    if flash in _FLASH:
        tone, text = _FLASH[flash]
        add(f'<p class="flash {tone}" role="status">{_e(text)}</p>')
    add(
        '<section class="card banner"><h2>Nodvard Deck konnte nicht normal starten</h2>'
        "<p>Beim Start ist etwas schiefgegangen. Damit nichts kaputtgeht, läuft deshalb nur diese Notseite. "
        "Die Daten auf dem Server ändert diese Seite nicht von selbst.</p></section>"
    )
    if not unlocked:
        add(
            '<section class="card"><h2>Notfallcode eingeben</h2>'
            "<p>Einzelheiten und Möglichkeiten gibt es nur mit dem Notfallcode. Er steht im <strong>Protokoll des Containers</strong> "
            "(Zeile „Notfallcode“, zum Beispiel in der Ausgabe von „compose logs“ des Dienstes oder in der Protokoll-Ansicht deiner Verwaltungsoberfläche) "
            "und in der Datei <code>.boot/rescue_code.txt</code> im Datenordner.</p>"
            '<form method="post" action="/rescue/unlock" autocomplete="off">'
            '<label for="code">Notfallcode</label>'
            '<input id="code" name="code" type="text" inputmode="text" autocapitalize="characters" autocomplete="off" spellcheck="false" '
            'maxlength="40" placeholder="XXXX-XXXX-XXXX" required>'
            '<div class="row"><button type="submit">Entsperren</button></div></form></section>'
        )
        add(
            '<section class="card"><h2>Was du allgemein tun kannst</h2><ul>'
            "<li>Ist das Problem gleich nach einem Update aufgetreten: Stelle die Image-Version in der Compose-Datei auf die Version zurück, "
            "die vorher lief, und starte den Container neu. Das Update legt vor jeder Migration eine Kopie der Datenbank an; "
            "eine Version, die diese Notseite schon kennt, spielt sie bei Bedarf selbst wieder ein.</li>"
            "<li>Sonst hilft oft ein Neustart des Containers, wenn der Grund ein vorübergehender war (zum Beispiel zu wenig Platz).</li>"
            "<li>Deine Daten liegen im Datenordner des Containers. Sichere ihn, bevor du etwas von Hand änderst.</li></ul></section>"
        )
        add('<p class="muted small">Diese Seite ersetzt Nodvard Deck, bis der Start wieder gelingt.</p></main></body></html>')
        return "".join(parts)

    failure = state.get("failure")
    if failure:
        kind = failure["kind"]
        rollback = failure.get("rollback") if kind == "newer_data" else None
        title, intro, steps = _kind_text(kind, failure, rollback)
        add(f'<section class="card"><h2>{_e(title)}</h2><p>{intro}</p>')
        if failure.get("reason"):
            add(f'<p><strong>Meldung:</strong> {_e(failure["reason"])}</p>')
        facts = [
            ("Zeitpunkt", failure.get("at")), ("Laufende Version", failure.get("app_version")),
            ("Version der Daten", failure.get("data_version")), ("Vorherige Version", failure.get("previous_version")),
        ]
        shown = [(k, v) for k, v in facts if v]
        if shown:
            add("<dl>" + "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in shown) + "</dl>")
        add("</section>")
        add('<section class="card"><h2>Was du jetzt tun kannst</h2><ol>' + "".join(f"<li>{step}</li>" for step in steps) + "</ol></section>")
        if failure.get("log"):
            add(
                '<section class="card"><details><summary>Protokoll des Starts (bereinigt)</summary><pre>'
                + _e("\n".join(failure["log"])) + "</pre></details></section>"
            )
    else:
        rollback = None
        add(
            '<section class="card"><h2>Der Start ist abgebrochen</h2>'
            "<p>Nodvard Deck hat keinen Grund hinterlegt. Die Fehlermeldung steht im <strong>Protokoll des Containers</strong>.</p></section>"
            '<section class="card"><h2>Was du jetzt tun kannst</h2><ol>'
            f"<li>{_RETRY_STEP}</li><li>{_BACK_STEP.format(target='die vorherige Version')}</li></ol></section>"
        )
    add('<section class="card"><h2>Aktionen</h2><div class="row">')
    add('<form method="post" action="/rescue/retry"><button type="submit">Neu versuchen</button></form>')
    if rollback:
        add('<form method="post" action="/rescue/rollback"><button type="submit" class="danger">Stand vor dem Update wiederherstellen</button></form>')
    add('<form method="post" action="/rescue/lock"><button type="submit" class="plain">Abmelden</button></form></div>')
    add(
        '<p class="muted small">„Neu versuchen“ beendet den Container-Prozess; der Container startet danach von selbst neu, wenn er eine Neustart-Regel hat '
        "(sonst bitte von Hand starten).</p></section>"
    )
    add('<p class="muted small">Das bereinigte Protokoll gibt es auch als Text unter <code>/rescue/log</code>.</p></main></body></html>')
    return "".join(parts)


def _info_page(title: str, text: str) -> str:
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="refresh" content="20;url=/"><title>Nodvard Deck – {_e(title)}</title><style>{_CSS}</style></head><body><main>'
        f'<h1>Nodvard Deck</h1><p class="sub">Notfallmodus</p><section class="card"><h2>{_e(title)}</h2><p>{_e(text)}</p></section></main></body></html>'
    )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # NICHT `no-referrer`: Browser schicken dann bei einem Formular `Origin: null`, und die Pruefung von `Origin` unten wuerde
    # jede Eingabe abweisen. `same-origin` schickt den Ursprung nur an die eigene Seite und sonst nichts.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}
_LOG_ESCAPE = {c: f"\\x{c:02x}" for c in (*range(0x20), *range(0x7F, 0xA0))}
"""Steuerzeichen fuers Protokoll maskieren -- wie die Standardbibliothek in ihrem `log_message` (gh-100001), das hier
ersetzt ist. Sonst koennte eine Anfrage ohne Code Escape-Folgen ins Protokoll schreiben, die beim Lesen im Terminal
wirken (Zeilen loeschen, Zwischenablage setzen)."""
_HEALTH_BODY = b'{"status":"rescue"}'
_API_BODY = json.dumps({"status": "rescue", "detail": "Nodvard Deck läuft im Notfallmodus."}, ensure_ascii=False, separators=(",", ":")).encode()


def _sender(address: Any) -> tuple[str, bool]:
    """-> (Schluessel fuer die Zaehlung je Absender, ob die Verbindung von diesem Rechner selbst kommt).

    IPv6 zaehlt je /64-Netz, IPv4-in-IPv6 als IPv4. Nur 127.0.0.1 und ::1 gelten als "lokal" (Health-Check im Container),
    nicht jede Adresse aus 127.0.0.0/8."""
    raw = str(address[0]) if isinstance(address, tuple) and address else ""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return raw, False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip == ipaddress.ip_address("127.0.0.1") or ip == ipaddress.ip_address("::1"):
        return str(ip), True
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False)), False
    return str(ip), False


class _HeadReader:
    """Umhuellt `rfile` und begrenzt Anfragezeile und Kopfzeilen zusammen auf `MAX_HEADER_BYTES`.

    Die Standardbibliothek erlaubt je Kopfzeile 64 KiB und 100 Zeilen: 64 Verbindungen koennten so Hunderte MB belegen.
    Ist das Budget aufgebraucht, loest die naechste Zeile `http.client.HTTPException` aus; `parse_request` beantwortet das
    mit 431. Den Inhalt (`read`) und alles andere reicht der Umschlag unveraendert durch (der Inhalt ist anderweitig begrenzt)."""

    def __init__(self, raw: Any, limit: int) -> None:
        self._raw = raw
        self._left = limit

    def readline(self, size: int = -1) -> bytes:
        if self._left <= 0:
            raise http.client.HTTPException("Kopfzeilen zu gross")
        n = self._left if size is None or size < 0 else min(size, self._left)
        line = self._raw.readline(n)
        self._left -= len(line)
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "NodvardDeckRescue"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # eine Anfrage je Verbindung: nichts bleibt haengen
    timeout = REQUEST_TIMEOUT_S

    # --- Frist fuer die ganze Verbindung ---
    def setup(self) -> None:
        super().setup()
        self._expired = threading.Event()
        self.rfile = _HeadReader(self.rfile, MAX_HEADER_BYTES)
        # Zwei Fristen: kurz fuer die Kopfzeilen (laeuft bis `parse_request` fertig ist), lang fuer die ganze Verbindung.
        self._head_deadline = threading.Timer(HEAD_TIMEOUT_S, self._expire)
        self._deadline = threading.Timer(REQUEST_TIMEOUT_S, self._expire)
        for timer in (self._head_deadline, self._deadline):
            timer.daemon = True
            timer.start()

    def _expire(self) -> None:
        """Die Frist ist um: Verbindung zu. `timeout` allein gilt nur je Lesevorgang -- wer alle paar Sekunden ein Byte
        schickt, hielte sie sonst beliebig lange offen."""
        self._expired.set()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self) -> None:
        self._head_deadline.cancel()
        self._deadline.cancel()
        try:
            super().finish()
        except OSError:
            pass

    def parse_request(self) -> bool:
        # Nach dem Abbruch liefert das Lesen "Ende": eine abgeschnittene Anfrage darf nie als vollstaendig gelten.
        ok = super().parse_request()
        self._head_deadline.cancel()  # die Kopfzeilen sind da; Inhalt und Antwort haben die Frist der ganzen Verbindung
        return ok and not self._expired.is_set()

    # --- Helfer ---
    @property
    def rescue(self) -> Rescue:
        return self.server.rescue  # type: ignore[attr-defined]

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        if self.command == "POST":  # Seitenaufrufe und der Health-Check des Containers bleiben still
            self.log_message("%s %s -> %s", self.command, urlsplit(self.path).path, code)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        text = (format % args).translate(_LOG_ESCAPE)
        print(f"[notseite] {self.client_address[0]} {text}", file=sys.stderr, flush=True)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any, extra: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8", extra)

    def _text(self, status: int, text: str) -> None:
        self._send(status, text.encode(), "text/plain; charset=utf-8")

    def _redirect(self, flash: str | None = None, cookie: str | None = None) -> None:
        extra = {"Location": f"/?m={flash}" if flash else "/"}
        if cookie:
            extra["Set-Cookie"] = cookie
        self._send(303, b"", "text/plain; charset=utf-8", extra)

    def _session_token(self) -> str | None:
        for header in self.headers.get_all("Cookie") or []:
            for part in header.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "rescue_session":
                    return value
        return None

    def _unlocked(self) -> bool:
        return self.rescue.session_valid(self._session_token())

    def _origin_ok(self) -> bool:
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if site and site not in ("same-origin", "none"):
            return False  # der Browser sagt selbst, dass die Anfrage von einer anderen Seite kommt
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # kein Browser-Formular (curl o. ae.); das Cookie gilt trotzdem nur mit gueltiger Sitzung
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        allowed = {(self.headers.get("Host") or "").lower()}
        forwarded = (self.headers.get("X-Forwarded-Host") or "").split(",")[0].strip().lower()
        if forwarded:
            allowed.add(forwarded)
        return parts.netloc.lower() in allowed

    def _read_body(self) -> bytes | None:
        """Der Inhalt einer POST-Anfrage -- oder `None` (die Antwort ist dann schon gesendet, oder die Verbindung ist
        abgebrochen bzw. ihre Frist um)."""
        if self.headers.get("Transfer-Encoding"):
            self._json(411, {"status": "rescue", "detail": "Content-Length fehlt."})
            return None
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            self._json(411, {"status": "rescue", "detail": "Content-Length fehlt."})
            return None
        try:
            length = int(length_raw)
        except ValueError:
            length = -1
        if length < 0:
            self._json(400, {"status": "rescue", "detail": "Ungültige Anfrage."})
            return None
        if length > MAX_BODY:
            self._json(413, {"status": "rescue", "detail": "Anfrage zu groß."}, {"Connection": "close"})
            return None
        body = self.rfile.read(length) if length else b""
        if len(body) != length or self._expired.is_set():
            self.close_connection = True
            return None  # abgebrochen oder Frist um: es gibt niemanden mehr, dem man antworten koennte
        return body

    @staticmethod
    def _fields(body: bytes, content_type: str) -> dict[str, str] | None:
        try:
            if content_type.split(";")[0].strip().lower() == "application/json":
                data = json.loads(body.decode("utf-8"))
                if not isinstance(data, dict):
                    return None
                return {str(k): v for k, v in data.items() if isinstance(v, str)}
            if body and content_type.split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
                return None
            return {k: v[0] for k, v in parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=bool(body)).items() if v}
        except (ValueError, RecursionError, UnicodeDecodeError):
            return None

    # --- Anfragen ---
    def do_GET(self) -> None:  # noqa: N802
        try:
            self._get()
        except Exception as exc:  # noqa: BLE001 - nie etwas verraten, nie abstuerzen
            print(f"[notseite] Fehler bei GET: {type(exc).__name__}", file=sys.stderr, flush=True)
            self._json(500, {"status": "rescue"})

    do_HEAD = do_GET  # noqa: N815

    def _get(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/api/v1/health":
            return self._send(503, _HEALTH_BODY, "application/json")
        if path == "/api" or path.startswith("/api/"):
            return self._send(503, _API_BODY, "application/json; charset=utf-8")
        if path == "/rescue/status":
            data: dict[str, Any] = {"status": "rescue", "unlocked": self._unlocked()}
            if data["unlocked"]:
                state = read_state(self.rescue.data_dir)
                data["failure"] = state.get("failure")
                data["app_version"] = state.get("app_version")
            return self._json(200, data)
        if path == "/rescue/log":
            if not self._unlocked():
                return self._text(401, "Notfallcode nötig.")
            failure = read_state(self.rescue.data_dir).get("failure") or {}
            return self._text(200, "\n".join(failure.get("log") or []) + "\n")
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        flash = parse_qs(parts.query).get("m", [None])[0]
        page = self.rescue.render(unlocked=self._unlocked(), flash=flash if flash in _FLASH else None)
        self._send(503, page.encode(), "text/html; charset=utf-8", {"Retry-After": "60"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._post()
        except Exception as exc:  # noqa: BLE001
            print(f"[notseite] Fehler bei POST: {type(exc).__name__}", file=sys.stderr, flush=True)
            self._json(500, {"status": "rescue"})

    def _post(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api" or path.startswith("/api/"):
            self._read_body()
            return self._send(503, _API_BODY, "application/json; charset=utf-8")
        if path not in ("/rescue/unlock", "/rescue/retry", "/rescue/rollback", "/rescue/lock"):
            return self._json(404, {"status": "rescue", "detail": "Unbekannt."})
        if not self._origin_ok():
            return self._json(403, {"status": "rescue", "detail": "Anfrage von einer fremden Seite."})
        body = self._read_body()
        if body is None:
            return
        if path == "/rescue/unlock":
            fields = self._fields(body, self.headers.get("Content-Type", ""))
            if fields is None:
                return self._json(400, {"status": "rescue", "detail": "Ungültige Anfrage."})
            outcome, value = self.rescue.try_code(self.client_address[0], fields.get("code"))
            if outcome == "locked":
                self._expired.wait(LOCKED_DELAY_S)  # Bremse fuers Raten waehrend der Sperre; der richtige Code kommt nie hierher
                print(f"[notseite] {self.client_address[0]}: zu viele Fehlversuche beim Notfallcode, gesperrt.", file=sys.stderr, flush=True)
                return self._send(
                    429, _info_page("Zu viele Fehlversuche", _LOCKED_TEXT).encode(),
                    "text/html; charset=utf-8", {"Retry-After": str(value)},
                )
            if outcome == "wrong":
                print(f"[notseite] {self.client_address[0]}: falscher Notfallcode.", file=sys.stderr, flush=True)
                return self._redirect("falsch")
            cookie = f"rescue_session={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL_S}"
            print(f"[notseite] {self.client_address[0]}: entsperrt.", file=sys.stderr, flush=True)
            return self._redirect("entsperrt", cookie)
        if path == "/rescue/lock":
            self.rescue.end_session(self._session_token())
            return self._redirect("abgemeldet", "rescue_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        if not self._unlocked():
            return self._json(401, {"status": "rescue", "detail": "Notfallcode nötig."})
        if path == "/rescue/rollback":
            failure = read_state(self.rescue.data_dir).get("failure") or {}
            rollback = failure.get("rollback") if failure.get("kind") == "newer_data" else None
            if not rollback:
                return self._json(409, {"status": "rescue", "detail": "Ein Rückweg ist in dieser Lage nicht möglich."})
            try:
                write_rollback(self.rescue.data_dir, rollback["copy"])
            except (OSError, ValueError):
                return self._json(500, {"status": "rescue", "detail": "Der Rückweg ließ sich nicht vormerken (Datenordner nicht beschreibbar?)."})
            print("[notseite] Rückweg vorgemerkt, Neustart.", file=sys.stderr, flush=True)
            self.rescue.request_exit(EXIT_RETRY)
            return self._send(
                202, _info_page("Der Rückweg ist vorgemerkt", "Nodvard Deck wird neu gestartet und stellt dabei den Stand von vor dem Update wieder her. "
                                "Startet der Container nicht von selbst neu, starte ihn bitte von Hand.").encode(), "text/html; charset=utf-8",
            )
        # retry
        print("[notseite] Neustart auf Wunsch.", file=sys.stderr, flush=True)
        self.rescue.request_exit(EXIT_RETRY)
        return self._send(
            202, _info_page("Es wird neu gestartet", "Nodvard Deck wird neu gestartet und versucht den Start noch einmal. "
                            "Startet der Container nicht von selbst neu, starte ihn bitte von Hand.").encode(), "text/html; charset=utf-8",
        )

    def _method_not_allowed(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api" or path.startswith("/api/"):
            return self._send(503, _API_BODY, "application/json; charset=utf-8")  # fuer die Anwendung ist es immer "Notfallmodus"
        self._json(405, {"status": "rescue", "detail": "Methode nicht erlaubt."}, {"Allow": "GET, HEAD, POST"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed  # noqa: N815


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], handler: type[http.server.BaseHTTPRequestHandler], rescue: Rescue) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.rescue = rescue
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._local_slots = threading.BoundedSemaphore(RESERVED_LOCAL_CONNECTIONS)
        self._per_client: dict[str, int] = {}
        self._per_client_lock = threading.Lock()
        super().__init__(address, handler)
        rescue.server = self

    def process_request(self, request: Any, client_address: Any) -> None:
        sender, local = _sender(client_address)
        with self._per_client_lock:
            if local:
                taken = self._local_slots.acquire(blocking=False)  # eigener Vorrat: andere koennen ihn nicht leeren
            else:
                taken = self._per_client.get(sender, 0) < MAX_CONNECTIONS_PER_CLIENT and self._slots.acquire(blocking=False)
            if not taken:
                self.shutdown_request(request)  # zu viele gleichzeitige Verbindungen (insgesamt oder von diesem Rechner)
                return
            self._per_client[sender] = self._per_client.get(sender, 0) + 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release(client_address)  # der Thread ist nie gestartet
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(client_address)

    def _release(self, client_address: Any) -> None:
        sender, local = _sender(client_address)
        with self._per_client_lock:
            left = self._per_client.get(sender, 0) - 1
            if left > 0:
                self._per_client[sender] = left
            else:
                self._per_client.pop(sender, None)
        (self._local_slots if local else self._slots).release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], OSError):
            return  # Verbindung abgebrochen oder Frist um: kein Traceback im Protokoll
        super().handle_error(request, client_address)


def make_server(rescue: Rescue, host: str, port: int) -> _Server:
    return _Server((host, port), _Handler, rescue)


# ---------------------------------------------------------------------------
# Aufruf
# ---------------------------------------------------------------------------


def resolve_data_dir(arg: str | None) -> Path:
    """`--data-dir`, sonst `NODVARD_DECK_DATA_DIR`, sonst der alte Name `LATTICE_DATA_DIR`, sonst `./data` (wie die Anwendung)."""
    if arg:
        return Path(arg)
    return Path(os.environ.get("NODVARD_DECK_DATA_DIR") or os.environ.get("LATTICE_DATA_DIR") or "./data")


def print_banner(code: str, port: int) -> None:
    line = "=" * 64
    print(
        f"\n{line}\n"
        "  Nodvard Deck – Notfallmodus\n"
        f"  Notfallcode: {code} – im Browser eingeben (Port {port})\n"
        "\n"
        f"      {code}\n"
        "\n"
        "  (Nodvard Deck konnte nicht starten. Die Notseite zeigt ohne diesen\n"
        "   Code nur Allgemeines. Er bleibt bis zum nächsten erfolgreichen Start gleich.)\n"
        f"{line}\n",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nodvard_deck.rescue", description="Notseite von Nodvard Deck (nur Standardbibliothek).")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    data_dir = resolve_data_dir(args.data_dir)
    rescue = Rescue(data_dir)
    code = rescue.ensure_code()
    try:
        server = make_server(rescue, args.host, args.port)
    except OSError as exc:
        print(f"[notseite] Der Port {args.port} ließ sich nicht öffnen ({exc.strerror or type(exc).__name__}).", file=sys.stderr, flush=True)
        return 2
    port = server.server_address[1]
    lock = take_lock(data_dir)

    def stop(signum: int, frame: Any) -> None:
        if rescue.exit_code is None:
            rescue.exit_code = 0
        threading.Thread(target=server.shutdown, daemon=True).start()

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, stop)
            except (ValueError, OSError):
                pass
    print_banner(code, port)
    print(f"[notseite] Notseite läuft auf {args.host}:{port}.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        lock.release()
    return rescue.exit_code if rescue.exit_code is not None else 0


if __name__ == "__main__":
    sys.exit(main())
