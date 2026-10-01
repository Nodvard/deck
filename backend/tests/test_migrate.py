"""`nodvard_deck.migrate` -- ersetzt seit der inventory-Extension das rohe
`alembic -c backend/alembic.ini upgrade head` (Einzahl) in `deploy/entrypoint.sh`
UND im README-Dev-Setup durch `python -m nodvard_deck.migrate` ("upgrade heads",
Mehrzahl -- docs/02-EXTENSION-API.md §7: "eigener Alembic-Branch" pro Extension).
Seit der documents-Extension (zweite mit eigenen Tabellen) drei unabhaengige
Koepfe statt zwei -- die Kopf-ANZAHL selbst ist deshalb nicht mehr das
interessante Signal (waechst mit jeder weiteren Extension mit eigenem Schema),
die einzelnen erwarteten Revisionen sind es.

Bewusst gegen eine ECHTE Datei-DB (nicht die schnelle In-Memory-Fixture aus
conftest.py) -- Alembic selbst braucht eine echte, dateibasierte SQLite-Verbindung
fuer einen realistischen End-zu-Ende-Beweis (dieselbe Haltung wie
`test_vault_use_audit_survives_caller_session_rollback`)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nodvard_deck import config


def test_upgrade_heads_migrates_core_and_inventory_branches_together(tmp_path: Path, monkeypatch):
    from nodvard_deck.migrate import upgrade_heads

    settings = config.Settings(
        env="dev",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
    )
    monkeypatch.setattr(config, "_settings", settings)
    # Echter chdir in den Repo-Root -- deckt genau die Klasse Bug ab, die live beim
    # ersten Docker-Deploy auftrat: `upgrade_heads()` haengt an `Path.cwd()`, nicht an
    # `Path(__file__)` (das im Container nach site-packages zeigt, siehe migrate.py).
    monkeypatch.chdir(Path(__file__).resolve().parents[2])  # backend/tests -> backend -> repo root

    locations = upgrade_heads()
    location_names = {str(loc).replace("\\", "/") for loc in locations}
    assert any(name.endswith("extensions/inventory/migrations/versions") for name in location_names)
    assert any(name.endswith("extensions/documents/migrations/versions") for name in location_names)
    assert any(name.endswith("extensions/nexus-soc/migrations/versions") for name in location_names)

    db = sqlite3.connect(str(tmp_path / "test.db"))
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        # Kern-Tabelle UND alle inventory- UND documents-Tabellen in EINEM Lauf.
        assert "users" in tables
        assert "roles" in tables
        # Wiederherstellungs-Codes (Kern-Branch): Tabelle mit Hash-Spalte und Benutzt-Zeitpunkt,
        # Fremdschluessel auf users mit ON DELETE CASCADE.
        assert "recovery_codes" in tables
        recovery_cols = {r[1] for r in db.execute("PRAGMA table_info(recovery_codes)").fetchall()}
        assert {"id", "user_id", "code_hash", "used_at", "created_at"} <= recovery_cols
        (fk,) = db.execute("PRAGMA foreign_key_list(recovery_codes)").fetchall()
        assert (fk[2], fk[3], fk[6]) == ("users", "user_id", "CASCADE")
        assert {
            "ext_inventory_categories", "ext_inventory_locations",
            "ext_inventory_items", "ext_inventory_item_images",
        } <= tables
        assert {
            "ext_documents_tags", "ext_documents_documents", "ext_documents_document_tags",
        } <= tables
        # Vorfalls-Historie: nexus-soc hat jetzt ebenfalls einen eigenen Branch.
        assert "ext_nexus_soc_incidents" in tables
        # Virenschutz: zweite nexus-soc-Revision auf demselben Branch.
        assert {"ext_nexus_soc_scans", "ext_nexus_soc_findings", "ext_nexus_soc_audits"} <= tables
        assert {"ext_nexus_soc_update_runs", "ext_nexus_soc_baselines", "ext_nexus_soc_events"} <= tables
        cols = {r[1] for r in db.execute("PRAGMA table_info(ext_nexus_soc_update_runs)").fetchall()}
        assert "remote_id" in cols
        # Vorfaelle des Sammelfensters ueberleben einen Neustart (b8c9d0e1f2a3).
        assert "ext_nexus_soc_incident_queue" in tables
        queue_cols = {r[1] for r in db.execute("PRAGMA table_info(ext_nexus_soc_incident_queue)").fetchall()}
        assert {"id", "host_id", "host_name", "target", "message", "details", "status", "created_at", "processed_at",
                "attempts", "action_id", "ai_summary", "proposal_started"} <= queue_cols

        heads = {r[0] for r in db.execute("SELECT version_num FROM alembic_version").fetchall()}
        # Drei UNABHAENGIGE Koepfe (Kern, inventory, documents) in EINER
        # alembic_version-Tabelle -- natives Alembic-Verhalten fuer getrennte
        # Branches (down_revision=None + eigener branch_labels-Wert je Extension),
        # kein zweiter Mechanismus noetig. Die inventory-/documents-Koepfe pruefen
        # wir auf den exakten Revisionswert (bewegen sich nur, wenn diese Extension
        # eine zweite Revision bekommt); den Kern-Kopf NUR auf Anzahl, nicht auf
        # exakten Wert -- der wandert mit jeder neuen Kern-Migration, unabhaengig
        # vom hier getesteten Multi-Branch-Mechanismus.
        assert len(heads) == 4
        # nexus-soc: b8c9d0e1f2a3 (dauerhafte Vorfalls-Warteschlange) auf a7b8c9d0e1f2
        # (remote_id der Update-Laeufe).
        assert {"a1b2c3d4e5f6", "9f1e2d3c4b5a", "b8c9d0e1f2a3"} <= heads
    finally:
        db.close()


def test_upgrade_heads_is_idempotent(tmp_path: Path, monkeypatch):
    """Ein zweiter Lauf gegen dieselbe DB darf nicht scheitern -- exakt das, was
    `entrypoint.sh` bei jedem Container-Neustart tatsaechlich tut."""
    from nodvard_deck.migrate import upgrade_heads

    settings = config.Settings(
        env="dev",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
    )
    monkeypatch.setattr(config, "_settings", settings)
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    upgrade_heads()
    upgrade_heads()  # darf nicht werfen
