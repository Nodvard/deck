"""Volumes eines Stacks vor einem Update sichern: je Volume ein Archiv im Heimatordner des Zugangs, gepackt von einem
kurzen Container ohne Netz, der das Volume nur lesend sieht."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .commands import VOLUME_NAME_RE
from .image_apply import PROJECT_RE, _valid_path
from .resources import DockerResources, ResourceProblem
from .stack_actions import _connection_error, _in_folder, _lost
from .stacks import Stack

BACKUP_IMAGE = "busybox:1.37"
BACKUP_ROOT = "nodvard-sicherungen"
ARCHIVE_BUDGET_S = 1800
"""Für alle Archive zusammen: Die Freigabe bricht eine Aktion nach einer Stunde ab, und davor kann schon das Holen laufen."""
STOP_TIMEOUT_S = 900
SPACE_MARGIN = 1.1
"""Ein Archiv ist meist kleiner als das Volume; mit etwas Luft, falls nicht."""
# Ein halbes Archiv trägt nie den Endnamen: gepackt wird nach `.part`, umbenannt erst am Schluss.
# Ohne die Zwischenordner eines abgebrochenen Zurückspielens, sonst scheiterte jedes Zurückspielen dieser Sicherung.
_ARCHIVE_SCRIPT = (
    'umask 077; tar czf "/backup/$1.part" --exclude ./.nodvard-zurueck --exclude ./.nodvard-vorher -C /data . '
    '&& chown "$2" "/backup/$1.part" && chmod 600 "/backup/$1.part" '
    '&& mv "/backup/$1.part" "/backup/$1" || { rc=$?; rm -f "/backup/$1.part"; exit "$rc"; }'
)
PART_SUFFIX = ".tar.gz.part"
CONTENTS_FILE = "inhalt.json"
"""Liegt vor dem ersten Archiv im Zeitordner und nennt die Volumes, die hineingehören; so fällt ein fehlendes Archiv später auf."""
MAX_CONTENTS_BYTES = 64 * 1024
NO_VOLUMES = "Die Dienste mit Update haben keine benannten Volumes; ihre Daten liegen in Ordnern auf dem Server."
_STAMP_RE = re.compile(r"^[0-9]{8}-[0-9]{6}\Z")
_OWNER_RE = re.compile(r"^[0-9]{1,10}:[0-9]{1,10}\Z")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def backup_folder(home: str, project: str, stamp: str) -> str:
    folder = f"{home.rstrip('/')}/{BACKUP_ROOT}/{project}/{stamp}"
    if not PROJECT_RE.match(project) or not _STAMP_RE.match(stamp) or not _valid_path(folder) or ":" in folder:
        raise ValueError(f"Ungültiger Ordner für Sicherungen: {folder!r}")
    return folder


def cmd_free_space(path: str) -> list[str]:
    """Freier Platz unter `path` in KB, in der festen POSIX-Form von `df`."""
    if not _valid_path(path):
        raise ValueError(f"Ungültiger Pfad: {path!r}")
    return ["df", "-Pk", path]


def parse_free_space(text: str) -> int | None:
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines[1]) < 4 or not lines[1][3].isdigit():
        return None
    return int(lines[1][3]) * 1024


def archive_name(volume: str, folder: str) -> str:
    """Der Name des Containers, der ein Volume packt, damit er sich nach einem Abbruch beenden lässt; gleich lang für jedes Volume."""
    return "nodvard-sicherung-" + hashlib.sha256(f"{folder}\0{volume}".encode()).hexdigest()[:20]


def cmd_archive(volume: str, folder: str, owner: str) -> list[str]:
    """Packt ein Volume nach `<folder>/<volume>.tar.gz` und gibt das Archiv dem Zugang; das Volume ist nur lesend eingebunden."""
    if not VOLUME_NAME_RE.match(volume) or not _valid_path(folder) or ":" in folder or not _OWNER_RE.match(owner):
        raise ValueError(f"Ungültige Sicherung: {volume!r} -> {folder!r}")
    return [
        "docker", "run", "--rm", "--name", archive_name(volume, folder), "--network", "none", "-v", f"{volume}:/data:ro",
        "-v", f"{folder}:/backup", BACKUP_IMAGE, "sh", "-c", _ARCHIVE_SCRIPT, "sh", f"{volume}.tar.gz", owner,
    ]


def cmd_stop_archive(volume: str, folder: str) -> list[str]:
    """Beendet einen Archiv-Container, der nach einem Abbruch noch packt."""
    if not VOLUME_NAME_RE.match(volume) or not _valid_path(folder) or ":" in folder:
        raise ValueError(f"Ungültige Sicherung: {volume!r} -> {folder!r}")
    return ["docker", "rm", "-f", archive_name(volume, folder)]


def cmd_pull_backup_image() -> list[str]:
    """Das kleine Image für die Archive, vor dem Stoppen geholt, damit die Dienste nicht auf einen Download warten."""
    return ["docker", "pull", "-q", BACKUP_IMAGE]


def _checked_services(services: list[str]) -> list[str]:
    if not services or not all(_SERVICE_RE.match(s) for s in services):
        raise ValueError(f"Ungültige Dienste: {services!r}")
    return services


def cmd_stop_services(stack: Stack, services: list[str]) -> list[str]:
    return _in_folder(stack, "stop", *_checked_services(services))


def cmd_start_services(stack: Stack, services: list[str]) -> list[str]:
    return _in_folder(stack, "start", *_checked_services(services))


def contents(volumes: list[str]) -> bytes:
    """Der Inhalt der Inhaltsliste für diese Volumes."""
    if not all(VOLUME_NAME_RE.match(v) for v in volumes):
        raise ValueError(f"Ungültige Volumes: {volumes!r}")
    return json.dumps({"volumes": volumes}).encode()


def parse_contents(raw: bytes) -> list[str] | None:
    """Die Volumes aus einer Inhaltsliste; `None`, wenn sie nicht dieser Form entspricht."""
    if len(raw) > MAX_CONTENTS_BYTES:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    volumes = data.get("volumes") if isinstance(data, dict) else None
    if not isinstance(volumes, list) or not all(isinstance(v, str) and VOLUME_NAME_RE.match(v) for v in volumes):
        return None
    return volumes


def restore_hint() -> str:
    return "Zurückspielen geht im Reiter Stacks unter „Sicherungen“."


class VolumeBackup:
    """Wie groß die Volumes sind und ob im Heimatordner des Zugangs Platz für ihre Archive ist."""

    def __init__(self, ctx: Any, resources: DockerResources) -> None:
        self._ctx = ctx
        self._resources = resources

    async def home(self, host: Any) -> tuple[str, str] | None:
        """(Heimatordner, „uid:gid“ des Zugangs) oder `None`, wenn sich das nicht lesen lässt."""
        try:
            async with self._ctx.exec.sftp(host) as sftp:
                home = str(await sftp.realpath("."))
                attrs = await sftp.stat(home)
        except Exception:  # noqa: BLE001 - dann gibt es keine Sicherung
            return None
        if not _valid_path(home) or ":" in home or attrs.uid is None or attrs.gid is None:
            return None
        return home, f"{attrs.uid}:{attrs.gid}"

    async def plan(self, host: Any, project: str, volumes: list[str]) -> dict[str, Any]:
        """Größe je Volume, freier Platz und wohin die Archive kommen; `error`, wenn es so keine Sicherung geben kann."""
        sizes: dict[str, int | None] = dict.fromkeys(volumes)
        out: dict[str, Any] = {"volumes": sizes, "free_bytes": None, "folder": None, "error": None}
        if not volumes:
            out["error"] = NO_VOLUMES
            return out
        found = await self.home(host)
        if found is None:
            out["error"] = "Der Heimatordner des Zugangs von Nodvard Deck ließ sich nicht lesen."
            return out
        home, _ = found
        out["folder"] = f"{home}/{BACKUP_ROOT}/{project}/"
        self._resources.forget(host.id)
        try:
            known = {v["name"]: v.get("size_bytes") for v in (await self._resources.snapshot(host)).volumes}
        except ResourceProblem as exc:
            out["error"] = f"Die Größe der Volumes ließ sich nicht lesen: {exc}"
            return out
        sizes.update({name: known.get(name) for name in volumes})
        try:
            result = await self._ctx.exec.run_argv(host, cmd_free_space(home), timeout_s=60)
        except Exception as exc:
            if not _connection_error(exc):
                raise
            out["error"] = f"Der freie Platz ließ sich nicht lesen: {_lost(exc)}."
            return out
        free = parse_free_space(result.stdout) if result.exit_code == 0 else None
        out["free_bytes"] = free
        needed = sum(size or 0 for size in sizes.values())
        unknown = sorted(name for name, size in sizes.items() if size is None)
        if unknown:
            out["error"] = f"Die Größe von {', '.join(unknown)} ist unbekannt, deshalb lässt sich der Platz nicht prüfen."
        elif free is None:
            out["error"] = "Der freie Platz im Heimatordner ließ sich nicht lesen."
        elif needed * SPACE_MARGIN > free:
            out["error"] = f"Zu wenig Platz: Die Volumes brauchen bis zu {_gb(needed)}, frei sind {_gb(free)}."
        return out


def _gb(size: int) -> str:
    return f"{size / 1024 ** 3:.1f} GB".replace(".", ",")
