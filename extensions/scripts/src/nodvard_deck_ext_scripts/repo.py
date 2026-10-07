"""Git-gestuetztes Skript-Repository (docs/02-EXTENSION-API.md Paragraph 6).

**Ehrlich abgewichen von der woertlichen Doku-Zeile "Metadaten, Parameter: eigene
Tabellen + JSON-Schema pro Skript":** Es gibt noch keinen Alembic-Branch-pro-Extension
(siehe `ext/tables.py`-Docstring -- der Mechanismus fehlt bis heute im Kern). Eine eigene
Tabelle nur fuer diese WP zu bauen waere ausser Proportion -- dieselbe Abwaegung wie
der In-Memory-`IncidentStore` von Nodvard Shield (WP-9). Metadaten UND Inhalt leben stattdessen als
Dateien IM SELBEN Git-Repo, das ohnehin fuer die Versionierung noetig ist
(`<script_id>/script.sh` + `<script_id>/meta.json`) -- eine Metadatenaenderung (Name,
Zeitplan, Parameter) landet dadurch im selben Commit-Verlauf wie eine Inhaltsaenderung,
statt in einer zweiten, getrennt zu betrachtenden Historie.

`dulwich` statt `pygit2`: ein reines Python-Wheel (kein C-Build in der Windows-
Entwicklungsumgebung noetig), siehe docs/00-DECISIONS.md zu Plattformunterschieden (D-10).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dulwich import porcelain
from dulwich.repo import Repo

_AUTHOR = b"Nodvard Deck Scripts <scripts@nodvard-deck.local>"

_FILEMODE = "false" if os.name == "nt" else "true"
_GIT_CONFIG = (
    "[core]\n\trepositoryformatversion = 0\n"
    f"\tfilemode = {_FILEMODE}\n\tbare = false\n\tlogallrefupdates = true\n"
)
"""Die einzigen Einstellungen, mit denen das Repo je geoeffnet wird. Was sonst in `.git/config`
stuende (Filter, Hooks, Signierprogramme, Einbindungen), wird bei jedem Oeffnen ueberschrieben."""

# Alles unter `.git` und im Arbeitsordner, was Git-Einstellungen oder Programmaufrufe mitbringen
# kann. Eine Sicherung bringt solche Dateien nicht mehr mit; das hier faengt auch Reste aus
# aelteren Sicherungen und Eingriffe von aussen ab.
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
    """Ein Repo, das nie Programme startet: weder Filter (`.gitattributes`/`filter.*`) noch Hooks.
    Die Dateien dafuer raeumt `ScriptRepo` ohnehin weg; das hier gilt zusaetzlich, falls dulwich
    kuenftig noch weitere Wege kennt."""

    def __init__(self, root: str) -> None:
        # `bare=False`: sonst nimmt dulwich den Arbeitsordner selbst als Repo, sobald dort `objects/`
        # und `refs/` liegen und `.git/objects` fehlt -- mit `config`, `hooks/` und `info/` aus dem
        # Arbeitsordner, an `_harden` vorbei.
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
    """Stellt sicher, dass `root/.git` ein gewoehnliches Repo ohne Filter, Hooks und fremde
    Einstellungen ist (Verlauf und Index bleiben unberuehrt)."""
    git_dir = root / ".git"
    if git_dir.is_symlink() or (git_dir.exists() and not git_dir.is_dir()):
        _remove(git_dir)  # eine Verweisdatei oder ein Link woanders hin ist kein Repo von uns
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
    """Ergaenzt, was eine Sicherung nicht mitbringt: leere Ordner (ein Repo ohne Commit hat sonst
    kein `objects/`) und `HEAD` (ohne `HEAD` landete der naechste Commit neben dem Verlauf)."""
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


@dataclass
class ScriptMeta:
    id: str
    name: str
    description: str = ""
    params_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    """`{param_name: {"type": "string"|"secret", "label": str, "default"?: str}}`.
    `type="secret"` heisst: der Wert wird NIE im Klartext gespeichert, sondern beim
    Ausfuehren frisch ueber `ctx.secrets.get_handle()`/`ctx.vault_use()` aufgeloest
    (docs/02 Paragraph 6, Zeile "Secrets")."""
    target: dict[str, Any] = field(default_factory=lambda: {"kind": "host", "host_id": None})
    """`{"kind": "host", "host_id": str} | {"kind": "group", "group_id": str} | {"kind": "all"}`."""
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
    """Ein Git-Repo pro Extension-Instanz unter `ctx.data_dir / "repo"` -- entspricht
    docs/02 Paragraph 6 woertlich ("eigenes Git-Repo unter /data/ext/scripts/repo")."""

    def __init__(self, root: Path) -> None:
        # Live gefunden (WP-10-Boot-Test gegen den echten Dev-Server): `Settings.
        # ext_data_dir` ist standardmaessig ein RELATIVER Pfad ("./data/ext") --
        # `porcelain.add()` staged dann STILL GAR NICHTS (kein Fehler, einfach ein
        # leerer Baum), sobald `root` (und damit jeder abgeleitete Dateipfad) relativ
        # ist. `pytest`s `tmp_path` ist dagegen IMMER absolut, weshalb kein Unit-Test
        # das gezeigt hat -- erst der echte Server-Lauf (relatives CWD-basiertes
        # `./data/...`) hat es aufgedeckt. `.resolve()` hier macht JEDEN abgeleiteten
        # Pfad (`_dir()`, `script_path`, `meta_path`) unabhaengig vom Aufrufer absolut.
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        _harden(self._root)

    def _dir(self, script_id: str) -> Path:
        return self._root / script_id

    def list_ids(self) -> list[str]:
        return sorted(
            p.name
            for p in self._root.iterdir()
            if p.is_dir() and p.name != ".git" and (p / _META_FILE).exists()
        )

    def get(self, script_id: str) -> Script | None:
        script_dir = self._dir(script_id)
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
        script_dir = self._dir(script_id)
        if not script_dir.exists():
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
