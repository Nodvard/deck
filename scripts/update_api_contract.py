#!/usr/bin/env python3
"""Schreibt die Schnappschuesse des oeffentlichen Vertrags neu (Nodvard Link).

    python scripts/update_api_contract.py                  # nachziehen, wenn nur etwas dazukam
    python scripts/update_api_contract.py --allow-breaking # Bruch, der in der Ausnahmeliste steht

Schnappschuesse (werden mit eingecheckt):
  backend/tests/contract/api_v1.json      /api/v1 samt aller mitgelieferten Extensions
  backend/tests/contract/sdk_public.json  oeffentliche Namen von nodvard_sdk (auch unter seinem alten Importnamen)

Regel (docs/04-API.md "Kompatibilitaet"): /api/v1 und nodvard_sdk aendern sich nur
abwaertskompatibel. Das Skript verweigert das Schreiben, wenn dabei ein Bruch entstuende.
Mit --allow-breaking geht es nur, wenn JEDER Bruch in
backend/tests/contract/breaking_exceptions.toml steht (mit Begruendung).

Exit-Code 0 = ok, 1 = verweigert (Bruch), 2 = Aufrufproblem.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests" / "contract"))

import contract_lib as cl


async def _current_api_doc() -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from nodvard_deck.config import Settings
    from nodvard_deck.db.session import reset_engine_cache, set_engine_for_testing
    from nodvard_deck.main import app
    from nodvard_deck.models import Base

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        set_engine_for_testing(engine)
        settings = Settings(
            env="dev",
            data_dir=tmp,
            database_url="sqlite+aiosqlite:///:memory:",
            master_key_path=tmp / "master.key",
            vault_keyring_path=tmp / "vault_keyring.json",
            ext_data_dir=tmp / "ext",
        )
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await cl.collect_openapi(app, session, settings, REPO_ROOT / "extensions")
        finally:
            await engine.dispose()
            reset_engine_cache()


def _load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--allow-breaking",
        action="store_true",
        help="Bruch zulassen -- nur wenn jeder Bruch in breaking_exceptions.toml mit Begruendung steht",
    )
    args = parser.parse_args()

    try:
        exceptions = cl.load_exceptions()
    except ValueError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2

    # Extension-Hintergrundaufgaben finden hier keine eigenen Tabellen und loggen Fehler -- Laerm.
    logging.disable(logging.CRITICAL)
    new_api = cl.build_api_snapshot(asyncio.run(_current_api_doc()))
    logging.disable(logging.NOTSET)
    new_sdk = cl.build_sdk_snapshot()
    jobs = [
        ("API", cl.API_SNAPSHOT, new_api, cl.find_api_breaks),
        ("SDK", cl.SDK_SNAPSHOT, new_sdk, cl.find_sdk_breaks),
    ]

    all_breaks: list[cl.Break] = []
    for _label, path, new, finder in jobs:
        old = _load(path)
        if old is not None:
            all_breaks += finder(old, new)

    if all_breaks:
        open_ones = cl.uncovered(all_breaks, exceptions)
        if not args.allow_breaking:
            print("VERWEIGERT: Das Aktualisieren würde die Abwärtskompatibilität brechen.\n", file=sys.stderr)
            print(cl.format_breaks(all_breaks), file=sys.stderr)
            return 1
        if open_ones:
            print("VERWEIGERT: Nicht jeder Bruch steht in der Ausnahmeliste.\n", file=sys.stderr)
            print(cl.format_breaks(open_ones), file=sys.stderr)
            return 1
        print(f"{len(all_breaks)} Bruch/Brüche stehen in der Ausnahmeliste und werden übernommen.")

    for label, path, new, _finder in jobs:
        text = cl.dump_json(new)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            print(f"{label}: {path.relative_to(REPO_ROOT)} ist aktuell.")
            continue
        path.write_text(text, encoding="utf-8")
        print(f"{label}: {path.relative_to(REPO_ROOT)} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
