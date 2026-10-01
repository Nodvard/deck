"""Git-gestuetztes Skript-Repository (docs/02-EXTENSION-API.md Paragraph 6).

**Ehrlich abgewichen von der woertlichen Doku-Zeile "Metadaten, Parameter: eigene
Tabellen + JSON-Schema pro Skript":** Es gibt noch keinen Alembic-Branch-pro-Extension
(siehe `ext/tables.py`-Docstring -- der Mechanismus fehlt bis heute im Kern). Eine eigene
Tabelle nur fuer diese WP zu bauen waere ausser Proportion -- dieselbe Abwaegung wie
nexus-socs In-Memory-`IncidentStore` (WP-9). Metadaten UND Inhalt leben stattdessen als
Dateien IM SELBEN Git-Repo, das ohnehin fuer die Versionierung noetig ist
(`<script_id>/script.sh` + `<script_id>/meta.json`) -- eine Metadatenaenderung (Name,
Zeitplan, Parameter) landet dadurch im selben Commit-Verlauf wie eine Inhaltsaenderung,
statt in einer zweiten, getrennt zu betrachtenden Historie.

`dulwich` statt `pygit2`: ein reines Python-Wheel (kein C-Build in der Windows-
Entwicklungsumgebung noetig), siehe docs/00-DECISIONS.md zu Plattformunterschieden (D-10).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dulwich import porcelain
from dulwich.repo import Repo

_AUTHOR = b"Nodvard Deck Scripts <scripts@nodvard-deck.local>"

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
        if not (self._root / ".git").exists():
            porcelain.init(str(self._root))

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

        repo = Repo(str(self._root))
        try:
            porcelain.add(repo, paths=[str(script_path), str(meta_path)])
            porcelain.commit(
                repo, message=commit_message.encode("utf-8"), author=_AUTHOR, committer=_AUTHOR
            )
        finally:
            repo.close()

    def delete(self, script_id: str) -> bool:
        script_dir = self._dir(script_id)
        if not script_dir.exists():
            return False
        paths = [str(p) for p in script_dir.iterdir() if p.is_file()]
        repo = Repo(str(self._root))
        try:
            if paths:
                porcelain.remove(repo, paths=paths)
            porcelain.commit(
                repo,
                message=f"scripts: '{script_id}' entfernt".encode("utf-8"),
                author=_AUTHOR,
                committer=_AUTHOR,
            )
        finally:
            repo.close()
        shutil.rmtree(script_dir, ignore_errors=True)
        return True

    def history(self, script_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        repo = Repo(str(self._root))
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
