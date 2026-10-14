"""Wächter-Vorlagen: Ordner mit fremden Dateien (Samba, NFS, Nextcloud) finden und per Klick zum Wächter hinzufügen."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from nodvard_sdk import Actor
from pydantic import BaseModel, Field

from . import antivirus as av
from .ids import EXT_ID, LOGGER

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext, Host

    from .defender import Defender

KINDS = ("samba", "nfs", "nextcloud")
# Der Server liefert nur Rohzeilen (smb.conf-Abschnitte und `path =`, /etc/exports); zerlegt wird in `parse_detection`,
# damit Anführungszeichen, `\040` und Drucker-Freigaben richtig behandelt werden.
DETECT_SCRIPT = (
    "echo @@samba; grep -iE '^[[:space:]]*(\\[[^]]*\\]|(path|directory)[[:space:]]*=)' /etc/samba/smb.conf 2>/dev/null "
    "| head -n 300; "
    "echo @@nfs; grep -vE '^[[:space:]]*(#|$)' /etc/exports 2>/dev/null | head -n 50; "
    "echo @@nextcloud; "
    "for f in /var/www/nextcloud/config/config.php /var/www/html/nextcloud/config/config.php "
    "/var/www/html/config/config.php; do "
    "sed -n \"s/^[[:space:]]*'datadirectory'[[:space:]]*=>[[:space:]]*'\\([^']*\\)'.*/\\1/p\" \"$f\" 2>/dev/null; done | head -n 10; "
    # Ein Datenordner der Nextcloud enthält immer `appdata_<Kennung>`: So findet sich das Volume des offiziellen Images
    # (`…/_data/data`) ebenso wie das von Nextcloud AIO (`…_nextcloud_data/_data`).
    "for d in /var/lib/docker/volumes/*nextcloud*/_data /var/lib/docker/volumes/*nextcloud*/_data/data; do "
    "for a in \"$d\"/appdata_*; do [ -d \"$a\" ] && echo \"$d\"; break; done; done 2>/dev/null | head -n 10; "
    "echo @@end"
)
"""Nur lesend. Jede Liste ist begrenzt; was fehlt (kein Samba, keine Nextcloud), bleibt leer."""
DETECT_TIMEOUT_S = 20
MAX_PARALLEL = 4
SYSTEM_DIRS = frozenset({"/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/var", "/var/lib", "/var/lib/docker"})
"""Ganze Systemordner sind keine Upload-Ordner (und würden den Wächter bei jedem Lauf lange beschäftigen)."""
SYSTEM_TREES = ("/proc", "/sys", "/dev", "/run", "/boot")
"""Auch nichts darunter: Kernel-, Geräte- und Laufzeit-Dateien."""
SAMBA_SKIP_SECTIONS = frozenset({"global", "printers", "print$", "homes"})
"""Keine Upload-Ordner: Drucker-Warteschlangen und `[homes]` (liegt unter /home, das der Wächter ohnehin prüft)."""
_OCTAL_RE = re.compile(r"\\([0-7]{3})")

_log = logging.getLogger(LOGGER)


def detect_command() -> str:
    # Als root, wenn das geht (ohne Passwortabfrage); als normaler Benutzer fehlt meist die Nextcloud.
    return av.as_root(DETECT_SCRIPT, required=False)


def clean_path(raw: str) -> str | None:
    """Ein gefundener Ordner als vereinfachter absoluter Pfad, oder None.

    Wie bei `av.exclude_folder`, dazu: keine Samba-Platzhalter (`%U`), keine Systemordner, nicht `/`."""
    path = raw.strip().strip('"')
    if "%" in path or any(ord(ch) < 32 for ch in path):
        return None
    folder = av.exclude_folder(path)
    if folder is None or folder in SYSTEM_DIRS or len(folder) > 255:
        return None
    if any(folder == tree or folder.startswith(tree + "/") for tree in SYSTEM_TREES):
        return None
    return folder


def _samba_paths(lines: list[str]) -> list[str]:
    """`path =`/`directory =` je Abschnitt (Groß-/Kleinschreibung egal, wie bei Samba), ohne Drucker und `[homes]`."""
    out: list[str] = []
    section = ""
    for line in lines:
        text = line.strip()
        if text.startswith("["):
            section = text.strip("[]").strip().lower()
            continue
        key, sep, value = text.partition("=")
        if sep and key.strip().lower() in ("path", "directory") and section not in SAMBA_SKIP_SECTIONS:
            out.append(value.strip())
    return out


def _nfs_paths(lines: list[str]) -> list[str]:
    """Das erste Feld jeder Zeile von /etc/exports, ggf. in Anführungszeichen, mit `\\ooo` (oktal) wie in exports(5)."""
    out: list[str] = []
    for line in lines:
        text = line.strip()
        if text.startswith('"'):
            end = text.find('"', 1)
            if end < 0:
                continue  # kaputte Zeile: lieber nichts als einen falschen Ordner
            field = text[1:end]
        else:
            field = text.split(None, 1)[0] if text else ""
        out.append(_OCTAL_RE.sub(lambda m: chr(int(m.group(1), 8)), field))
    return out


def parse_detection(output: str) -> tuple[list[tuple[str, str]], bool]:
    """-> ([(Art, Ordner)] in der Reihenfolge der Ausgabe, ohne Dopplungen je Art; vollständig?).

    Ohne `@@end` ist die Antwort abgeschnitten (Zeitlimit, Abbruch): Was da ist, zählt, aber es fehlt vielleicht etwas."""
    raw: dict[str, list[str]] = {kind: [] for kind in KINDS}
    kind: str | None = None
    complete = False
    for line in output.splitlines():
        marker = line.strip()
        if marker.startswith("@@"):
            complete = complete or marker == "@@end"
            kind = marker[2:] if marker[2:] in KINDS else None
            continue
        if kind is not None:
            raw[kind].append(line)
    candidates = (
        [("samba", p) for p in _samba_paths(raw["samba"])]
        + [("nfs", p) for p in _nfs_paths(raw["nfs"])]
        + [("nextcloud", p) for p in raw["nextcloud"]]
    )
    found: list[tuple[str, str]] = []
    for kind_, path in candidates:
        folder = clean_path(path)
        if folder is not None and (kind_, folder) not in found:
            found.append((kind_, folder))
    return found, complete


def covered_by(folder: str, watched: list[str]) -> str | None:
    """Der überwachte Ordner, der `folder` schon enthält (er selbst oder ein Ober-Ordner), oder None."""
    for entry in watched:
        base = av.exclude_folder(entry)
        if base is not None and (folder == base or folder.startswith(base.rstrip("/") + "/")):
            return base
    return None


def current_watch_paths(settings: dict[str, Any]) -> list[str]:
    # Der Standard (`av.WATCH_PATHS`) deckt nur Orte ab, an denen Schadsoftware typischerweise landet; Hochgeladenes
    # liegt oft woanders. Die Liste gilt für alle Server (`find` überspringt fehlende Ordner); ein großer Ordner kostet
    # bei jedem Lauf Zeit.
    return list(settings.get("watch_paths") or av.WATCH_PATHS)


def excluded_paths(settings: dict[str, Any]) -> list[str]:
    """Die „Ausgenommenen Ordner“ (`exclude_paths`): Dort sucht der Wächter nicht, ein Vorschlag wäre wirkungslos."""
    value = settings.get("exclude_paths") or []
    return [p for p in value if isinstance(p, str)] if isinstance(value, list) else []


async def find_templates(ctx: ExtensionContext, defender: Defender) -> dict[str, Any]:
    """Sucht auf allen prüfbaren Servern.

    -> `{"suggestions": [{"path", "kinds", "hosts", "covered_by", "excluded_by"}], "errors", "watch_paths"}`."""
    hosts = await defender.target_hosts()
    settings = await ctx.settings.get()
    watched = current_watch_paths(settings)
    excluded = excluded_paths(settings)
    gate = asyncio.Semaphore(MAX_PARALLEL)

    async def one(host: Host) -> tuple[Host, tuple[list[tuple[str, str]], bool] | None]:
        async with gate:
            try:
                result = await ctx.exec.run(host, detect_command(), timeout_s=DETECT_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - ein nicht erreichbarer Server kostet nur seine Vorschläge
                _log.info("shield_watch_templates_failed host=%s", host.id, exc_info=True)
                return host, None
            return host, parse_detection(result.stdout or "")

    by_path: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for host, parsed in await asyncio.gather(*(one(h) for h in hosts)):
        name = host.display_name or host.name
        if parsed is None:
            errors.append(f"{name}: keine Antwort (nicht erreichbar, kein Zugang oder Zeitlimit), dort wurde nicht gesucht.")
            continue
        found, complete = parsed
        if not complete:
            errors.append(f"{name}: Antwort unvollständig, dort fehlt vielleicht etwas.")
        for kind, folder in found:
            entry = by_path.setdefault(folder, {
                "path": folder, "kinds": [], "hosts": [], "covered_by": covered_by(folder, watched),
                "excluded_by": covered_by(folder, excluded),
            })
            if kind not in entry["kinds"]:
                entry["kinds"].append(kind)
            if name not in entry["hosts"]:
                entry["hosts"].append(name)
    suggestions = sorted(by_path.values(), key=lambda e: (e["covered_by"] is not None or e["excluded_by"] is not None, e["path"]))
    return {"suggestions": suggestions, "errors": errors, "watch_paths": watched}


class _WatchPathIn(BaseModel):
    path: str = Field(min_length=1, max_length=255)


def build_routers(ctx: ExtensionContext, defender: Defender) -> tuple[APIRouter, APIRouter]:
    """-> (lesen: `GET /defender/watch-templates`, ändern: `POST /defender/watch-paths`)."""
    read = APIRouter()
    change = APIRouter()
    # Lesen, anhängen und schreiben am Stück: Zwei gleichzeitige Klicks (zwei Tabs) tragen sonst doppelt ein oder
    # verlieren einen Eintrag.
    lock = asyncio.Lock()

    @read.get("/defender/watch-templates")
    async def watch_templates() -> dict:
        return await find_templates(ctx, defender)

    @change.post("/defender/watch-paths")
    async def add_watch_path(payload: _WatchPathIn, actor: Actor = Depends(ctx.api.current_actor)) -> dict:  # noqa: B008
        folder = clean_path(payload.path)
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bitte einen vollen Ordnerpfad angeben (beginnt mit /, ohne ..). Ganze Systemordner gehen nicht.",
            )
        async with lock:
            settings = await ctx.settings.get()
            watched = current_watch_paths(settings)
            already = covered_by(folder, watched)
            if already is not None:
                return {"added": False, "covered_by": already, "watch_paths": watched}
            skipped = covered_by(folder, excluded_paths(settings))
            if skipped is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"{folder} liegt in einem ausgenommenen Ordner ({skipped}) – dort prüft der Wächter nicht. "
                    "Erst die Ausnahme in den Einstellungen entfernen.",
                )
            settings["watch_paths"] = [*watched, folder]
            await ctx.settings.set(settings)
        await ctx.audit.log(
            action=f"{EXT_ID}.watch_path_added", outcome="success", target_type="extension", target_id=EXT_ID,
            reason=f"Echtzeit-Wächter: {folder}", detail={"path": folder}, actor=actor,
        )
        return {"added": True, "covered_by": None, "watch_paths": settings["watch_paths"]}

    return read, change
