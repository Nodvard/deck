"""Git-gestütztes Skript-Repository: Inhalt (`script.sh`) und Metadaten (`meta.json`) liegen je Skript im selben
Verlauf."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# dulwich ist reines Python (kein C-Build wie bei pygit2).
from dulwich import porcelain
from dulwich.repo import Repo

_AUTHOR = b"Nodvard Deck Scripts <scripts@nodvard-deck.local>"

_FILEMODE = "false" if os.name == "nt" else "true"
_GIT_CONFIG = (
    "[core]\n\trepositoryformatversion = 0\n"
    f"\tfilemode = {_FILEMODE}\n\tbare = false\n\tlogallrefupdates = true\n"
)
# Das sind die einzigen Einstellungen, mit denen das Repo geöffnet wird: Was sonst in `.git/config` stünde (Filter,
# Hooks, Signierprogramme, Einbindungen), wird bei jedem Öffnen überschrieben.

# Dateien, die Git-Einstellungen oder Programmaufrufe mitbringen können. Eine Sicherung enthält sie nicht mehr; das hier
# fängt Reste aus alten Sicherungen und Eingriffe von außen ab.
_GIT_DIR_REMOVE = (
    "hooks", "info", "commondir", "gitdir", "worktrees", "modules", "config.worktree", "logs", "description",
)
_WORK_TREE_REMOVE = (".gitattributes", ".gitmodules", ".gitconfig")


class _PlainNormalizer:
    """Nimmt jede Datei, wie sie ist: keine Filter, keine Zeilenenden-Umwandlung."""

    def checkin_normalize(self, blob: Any, path: Any) -> Any:
        return blob

    def checkout_normalize(self, blob: Any, path: Any) -> Any:
        return blob


class _SafeRepo(Repo):
    """Ein Repo, das nie Programme startet: weder Filter (`.gitattributes`, `filter.*`) noch Hooks."""

    def __init__(self, root: str) -> None:
        # `bare=False`: sonst nimmt dulwich den Arbeitsordner selbst als Repo, sobald dort `objects/` und `refs/` liegen
        # und `.git/objects` fehlt, mit `config` und `hooks/` von dort, an `_harden` vorbei.
        super().__init__(root, bare=False)
        self.hooks.clear()

    def get_blob_normalizer(self, config: Any = None) -> Any:
        return _PlainNormalizer()


def _remove(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


def _harden(root: Path) -> None:
    """Stellt sicher, dass `root/.git` ein gewöhnliches Repo ohne Filter, Hooks und fremde Einstellungen ist."""
    git_dir = root / ".git"
    if git_dir.is_symlink() or (git_dir.exists() and not git_dir.is_dir()):
        _remove(git_dir)  # eine Verweisdatei oder ein Link woandershin ist kein eigenes Repo
    if not git_dir.exists():
        porcelain.init(str(root))
    for name in _GIT_DIR_REMOVE:
        _remove(git_dir / name)
    for name in ("alternates", "http-alternates"):
        _remove(git_dir / "objects" / "info" / name)
    for name in _WORK_TREE_REMOVE:
        _remove(root / name)
    config = git_dir / "config"
    try:
        current = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        current = None
    if current != _GIT_CONFIG:
        _remove(config)
        config.write_text(_GIT_CONFIG, encoding="utf-8", newline="\n")
    _complete(git_dir)


def _complete(git_dir: Path) -> None:
    """Ergänzt, was eine Sicherung nicht mitbringt: leere Ordner (sonst fehlt `objects/`) und `HEAD`."""
    for sub in ("objects/info", "objects/pack", "refs/heads", "refs/tags"):
        path = git_dir
        for part in sub.split("/"):
            path = path / part
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                _remove(path)
            path.mkdir(exist_ok=True)
    head = git_dir / "HEAD"
    if head.is_symlink() or not head.is_file():
        _remove(head)
        heads = git_dir / "refs" / "heads"
        branches = sorted(p.name for p in heads.iterdir() if p.is_file() and not p.is_symlink())
        branch = "master" if "master" in branches or len(branches) != 1 else branches[0]
        head.write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8", newline="\n")


_SCRIPT_FILE = "script.sh"
_META_FILE = "meta.json"
MAX_ID_LENGTH = 128


class InvalidScriptId(ValueError):
    """Die Kennung taugt nicht als Ordnername im Skript-Repository."""


def safe_script_id(script_id: object) -> bool:
    """Darf diese Kennung ein Ordner direkt unter dem Repository werden?"""
    # Eine Kennung aus einer Anfrage wird zum Dateipfad (lesen, Verlauf, löschen). Deshalb nie `..`, Schrägstrich,
    # absoluter Pfad, Nullbyte oder versteckter Ordner wie `.git`, sonst käme man aus dem Repository heraus.
    return (
        isinstance(script_id, str)
        and 0 < len(script_id) <= MAX_ID_LENGTH
        and not script_id.startswith(".")
        and not any(ch in script_id for ch in ("/", "\\", "\x00", ":"))
    )


@dataclass
class ScriptMeta:
    id: str
    name: str
    description: str = ""
    # `{name: {"type": "string"|"secret", "label": str, "default"?: str}}`. Ein `secret` wird nie im Klartext
    # gespeichert, sondern erst beim Ausführen aus dem Tresor geholt (`ctx.vault_use()`).
    params_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    # `{"kind": "host", "host_id": str} | {"kind": "group", "group_id": str} | {"kind": "all"}`
    target: dict[str, Any] = field(default_factory=lambda: {"kind": "host", "host_id": None})
    schedule: str | None = None
    enabled: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "params_schema": self.params_schema,
            "target": self.target,
            "schedule": self.schedule,
            "enabled": self.enabled,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ScriptMeta":
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            description=data.get("description", ""),
            params_schema=data.get("params_schema", {}),
            target=data.get("target", {"kind": "host", "host_id": None}),
            schedule=data.get("schedule"),
            enabled=data.get("enabled", True),
        )


@dataclass
class Script:
    meta: ScriptMeta
    content: str


class ScriptRepo:
    """Ein Git-Repo pro Extension-Instanz unter `ctx.data_dir / "repo"`."""

    def __init__(self, root: Path) -> None:
        # Absolut machen: Bei einem relativen `root` (die Vorgabe für das Datenverzeichnis ist relativ) fügt
        # `porcelain.add()` ohne Fehlermeldung nichts hinzu.
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        _harden(self._root)

    def _dir(self, script_id: str) -> Path:
        """Ordner eines Skripts, immer direkt unter dem Repository; wirft `InvalidScriptId`, wenn die Kennung
        hinausführt."""
        if not safe_script_id(script_id):
            raise InvalidScriptId(script_id)
        path = self._root / script_id
        # Auch ein Link auf einen Nachbarordner im Repository wird abgelehnt.
        if path.is_symlink() or path.resolve().parent != self._root:
            raise InvalidScriptId(script_id)
        return path

    def list_ids(self) -> list[str]:
        return sorted(
            p.name
            for p in self._root.iterdir()
            if p.is_dir() and p.name != ".git" and (p / _META_FILE).exists()
        )

    def get(self, script_id: str) -> Script | None:
        try:
            script_dir = self._dir(script_id)
        except InvalidScriptId:
            return None
        meta_path = script_dir / _META_FILE
        if not meta_path.exists():
            return None
        meta = ScriptMeta.from_json(json.loads(meta_path.read_text(encoding="utf-8")))
        content_path = script_dir / _SCRIPT_FILE
        content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
        return Script(meta=meta, content=content)

    def list_all(self) -> list[Script]:
        return [s for sid in self.list_ids() if (s := self.get(sid)) is not None]

    def save(self, meta: ScriptMeta, content: str, *, commit_message: str) -> None:
        script_dir = self._dir(meta.id)
        script_dir.mkdir(exist_ok=True)
        script_path = script_dir / _SCRIPT_FILE
        meta_path = script_dir / _META_FILE
        script_path.write_text(content, encoding="utf-8", newline="\n")
        meta_path.write_text(json.dumps(meta.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

        repo = _SafeRepo(str(self._root))
        try:
            porcelain.add(repo, paths=[str(script_path), str(meta_path)])
            porcelain.commit(
                repo, message=commit_message.encode("utf-8"), author=_AUTHOR, committer=_AUTHOR,
                no_verify=True, sign=False,
            )
        finally:
            repo.close()

    def delete(self, script_id: str) -> bool:
        try:
            script_dir = self._dir(script_id)
        except InvalidScriptId:
            return False
        if not script_dir.is_dir() or script_dir.is_symlink():
            return False
        paths = [str(p) for p in script_dir.iterdir() if p.is_file()]
        repo = _SafeRepo(str(self._root))
        try:
            if paths:
                porcelain.remove(repo, paths=paths)
            porcelain.commit(
                repo,
                message=f"scripts: '{script_id}' entfernt".encode("utf-8"),
                author=_AUTHOR,
                committer=_AUTHOR,
                no_verify=True,
                sign=False,
            )
        finally:
            repo.close()
        shutil.rmtree(script_dir, ignore_errors=True)
        return True

    def history(self, script_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not safe_script_id(script_id):
            return []
        repo = _SafeRepo(str(self._root))
        try:
            entries: list[dict[str, Any]] = []
            for i, walk_entry in enumerate(repo.get_walker(paths=[script_id.encode("utf-8")])):
                if i >= limit:
                    break
                commit = walk_entry.commit
                entries.append(
                    {
                        "sha": commit.id.decode("ascii"),
                        "message": commit.message.decode("utf-8", errors="replace").strip(),
                        "author": commit.author.decode("utf-8", errors="replace"),
                        "commit_time": commit.commit_time,
                    }
                )
            return entries
        finally:
            repo.close()
