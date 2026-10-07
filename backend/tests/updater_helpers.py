"""Bausteine fuer die Tests zum Update-Helfer (Seite des Dashboards): ein Kanal wie ihn der Helfer einrichtet, ein
gueltiger Status und das Helfer-Paket selbst (fuer den Gleichlauf), geladen ohne Installation."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from restore_helpers import REPO_ROOT

HELPER_DIR = REPO_ROOT / "deploy" / "updater" / "nodvard_deck_updater"
VECTORS_DIR = REPO_ROOT / "deploy" / "updater" / "tests" / "vectors"
REQUEST_ID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"
OTHER_ID = "0b7e3a51-2c4d-4e6f-9a10-2b3c4d5e6f70"
_PACKAGE = "_nodvard_deck_updater_gleichlauf"


def make_channel(root: Path) -> Path:
    """Kanal wie vom Helfer eingerichtet: Wurzel 0755, `requests/` 1777 (Besitzer: wer den Test ausfuehrt)."""
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o755)
    requests = root / "requests"
    requests.mkdir(exist_ok=True)
    os.chmod(requests, 0o1777)
    return root


def status_doc(**changes: Any) -> dict[str, Any]:
    """Ein gueltiger Status: bereit, Ziel auf `latest`, frischer Herzschlag."""
    doc: dict[str, Any] = {
        "proto": 1, "helper_version": "0.7.0", "request_versions": [1], "seq": 1, "heartbeat_at": int(time.time()),
        "state": "idle", "ready": True, "reason": None,
        "target": {"current_version": "0.7.0", "floating_tag": "latest", "pinned": False},
        "busy": None, "previous": None, "results": [],
    }
    doc.update(changes)
    return doc


def write_status(root: Path, doc: Any, *, raw: bytes | None = None, mode: int = 0o644) -> Path:
    """Schreibt `status.json` (atomar, wie der Helfer). `raw` ersetzt den kodierten Inhalt."""
    path = root / "status.json"
    tmp = root / ".status-test.tmp"
    tmp.write_bytes(raw if raw is not None else json.dumps(doc).encode())
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return path


def result(request_id: str = REQUEST_ID, *, action: str = "update", outcome: str = "applied", code: str | None = None,
           frm: str | None = "0.7.0", to: str | None = "0.7.1", finished_at: int | None = None) -> dict[str, Any]:
    return {"id": request_id, "action": action, "from": frm, "to": to, "outcome": outcome, "code": code,
            "finished_at": int(time.time()) if finished_at is None else finished_at}


def vectors(name: str) -> Any:
    return json.loads((VECTORS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def helper_module(name: str):
    """Ein Modul des Helfer-Pakets (`policy`, `channel`, ...), geladen per `spec_from_file_location` unter einem eigenen
    Namen -- der Kern importiert es nie, und eine installierte Fassung spielt keine Rolle."""
    if _PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _PACKAGE, HELPER_DIR / "__init__.py", submodule_search_locations=[str(HELPER_DIR)],
        )
        assert spec is not None and spec.loader is not None
        package = importlib.util.module_from_spec(spec)
        sys.modules[_PACKAGE] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{_PACKAGE}.{name}")
