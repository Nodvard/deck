"""Vault-Kern: Fernet-Verschluesselung + Key-Rotation + auditierte Materialisierung
(docs/00-DECISIONS.md D-06).

Die drei reinen Krypto-Tests (`load_or_create_master_key`, `encrypt_str`/`decrypt_str`)
sind unveraendert -- diese Bausteine hat die Key-Rotation bewusst nicht angefasst (siehe
core/vault.py Modul-Docstring). Alles DB-beruehrende (`create_secret`,
`read_secret_plaintext`, `vault_use`) ist jetzt Keyring- (also versions-) bewusst.
"""

from __future__ import annotations

import pytest

from nodvard_deck.core import vault
from nodvard_deck.models import AuditEntry, Secret


def _keyring(*, versions: int = 1) -> vault.Keyring:
    """Baut einen In-Memory-Keyring direkt, ohne Settings/Dateisystem -- fuer Tests,
    die nur die Verschluesselungslogik pruefen, nicht das Laden/Speichern."""
    from cryptography.fernet import Fernet

    return {v: Fernet.generate_key() for v in range(1, versions + 1)}


def test_master_key_is_created_on_first_use(tmp_path):
    key_path = tmp_path / "master.key"
    assert not key_path.exists()

    key1 = vault.load_or_create_master_key(key_path)
    assert key_path.exists()

    key2 = vault.load_or_create_master_key(key_path)
    assert key1 == key2, "zweiter Aufruf muss denselben, bereits erzeugten Key laden"


def test_encrypt_decrypt_roundtrip(tmp_path):
    from cryptography.fernet import Fernet

    fernet = Fernet(vault.load_or_create_master_key(tmp_path / "master.key"))
    ciphertext = vault.encrypt_str(fernet, "geheimer wert")
    assert ciphertext != b"geheimer wert"
    assert vault.decrypt_str(fernet, ciphertext) == "geheimer wert"


def test_decrypt_with_wrong_key_fails(tmp_path):
    from cryptography.fernet import Fernet

    fernet_a = Fernet(vault.load_or_create_master_key(tmp_path / "a.key"))
    fernet_b = Fernet(vault.load_or_create_master_key(tmp_path / "b.key"))

    ciphertext = vault.encrypt_str(fernet_a, "geheim")
    with pytest.raises(vault.VaultError):
        vault.decrypt_str(fernet_b, ciphertext)


# ---------------------------------------------------------------------------
# Keyring: laden/erzeugen, Migration des alten Einzelschluessels, Rotation
# ---------------------------------------------------------------------------


def test_load_keyring_creates_version_1_on_first_use(test_settings):
    assert not test_settings.vault_keyring_path.exists()
    keyring = vault.load_keyring(test_settings)

    assert set(keyring) == {1}
    assert test_settings.vault_keyring_path.exists()
    # Zweiter Aufruf laedt dieselbe Version 1 zurueck, statt eine neue zu erzeugen.
    assert vault.load_keyring(test_settings) == keyring


def test_load_keyring_migrates_existing_wp1_master_key(test_settings):
    """Ein bereits vorhandener `master.key` (vor dem Keyring angelegt) muss unveraendert
    als Version 1 uebernommen werden -- sonst waeren vorher verschluesselte Secrets
    nach dem Umstieg nicht mehr lesbar."""
    pre_existing_key = vault.load_or_create_master_key(test_settings.master_key_path)

    keyring = vault.load_keyring(test_settings)

    assert keyring[1] == pre_existing_key


def test_rotate_master_key_adds_new_version_without_touching_old(test_settings):
    keyring_v1 = vault.load_keyring(test_settings)
    keyring_v2 = vault.rotate_master_key(test_settings)

    assert set(keyring_v2) == {1, 2}
    assert keyring_v2[1] == keyring_v1[1], "Rotation darf alte Versionen nicht veraendern"
    assert keyring_v2[2] != keyring_v1[1]
    assert vault.current_version(keyring_v2) == 2


def test_fernet_for_unknown_version_raises():
    with pytest.raises(vault.VaultError):
        vault.fernet_for_version(_keyring(), 99)


# ---------------------------------------------------------------------------
# Secrets: anlegen, lesen, ersetzen -- jetzt Keyring-basiert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_read_secret_roundtrip(db_session):
    keyring = _keyring()

    secret = await vault.create_secret(
        db_session, keyring, label="test:1", kind="totp", plaintext="ABCDEFGH"
    )
    assert secret.id is not None
    assert secret.ciphertext != b"ABCDEFGH"
    assert secret.key_version == 1

    plaintext = await vault.read_secret_plaintext(db_session, keyring, secret.id)
    assert plaintext == "ABCDEFGH"


@pytest.mark.asyncio
async def test_no_endpoint_invariant_is_at_least_structurally_true(db_session):
    """Kein direkter API-Test hier (das prueft test_secrets_api.py) -- dieser Test
    haelt nur fest, dass `Secret.ciphertext` niemals dem Klartext entspricht, als
    Regression gegen einen versehentlichen "erstmal unverschluesselt speichern"-Fix."""
    keyring = _keyring()
    secret = await vault.create_secret(
        db_session, keyring, label="test:2", kind="totp", plaintext="KLARTEXT123"
    )
    assert b"KLARTEXT123" not in secret.ciphertext


@pytest.mark.asyncio
async def test_read_secret_plaintext_lazily_migrates_to_current_key_version(db_session):
    """docs/03-DATA-MODEL.md §3: "neue Secrets mit v2, alte werden beim naechsten
    Zugriff migriert" -- kein Big-Bang-Umschluesseln, sondern lazy bei Lesezugriff."""
    keyring = _keyring(versions=1)
    secret = await vault.create_secret(
        db_session, keyring, label="test:3", kind="generic", plaintext="wert-v1"
    )
    assert secret.key_version == 1

    from cryptography.fernet import Fernet

    keyring[2] = Fernet.generate_key()  # Rotation: Version 2 kommt hinzu

    plaintext = await vault.read_secret_plaintext(db_session, keyring, secret.id)
    assert plaintext == "wert-v1"

    refreshed = await db_session.get(Secret, secret.id)
    assert refreshed.key_version == 2, "Secret haette bei diesem Zugriff migriert werden muessen"
    # Und die migrierte Ciphertext muss tatsaechlich mit Version 2 entschluesselbar sein.
    assert vault.decrypt_str(vault.fernet_for_version(keyring, 2), refreshed.ciphertext) == "wert-v1"


@pytest.mark.asyncio
async def test_replace_secret_value_overwrites_at_current_version(db_session):
    keyring = _keyring()
    secret = await vault.create_secret(
        db_session, keyring, label="test:4", kind="generic", plaintext="alt"
    )

    await vault.replace_secret_value(db_session, keyring, secret.id, "neu")

    plaintext = await vault.read_secret_plaintext(db_session, keyring, secret.id)
    assert plaintext == "neu"
    refreshed = await db_session.get(Secret, secret.id)
    assert refreshed.rotated_at is not None


@pytest.mark.asyncio
async def test_replace_secret_value_unknown_id_raises(db_session):
    with pytest.raises(vault.VaultError):
        await vault.replace_secret_value(db_session, _keyring(), "unbekannt", "wert")


@pytest.mark.asyncio
async def test_read_secret_plaintext_does_not_write_audit_entry(db_session):
    """Bewusster Unterschied zu `vault_use()` (siehe dortiger Docstring): der interne
    Verifikationspfad (z. B. TOTP-Codepruefung bei jedem Login) auditiert nicht."""
    keyring = _keyring()
    secret = await vault.create_secret(
        db_session, keyring, label="test:5", kind="totp", plaintext="geheim"
    )
    await vault.read_secret_plaintext(db_session, keyring, secret.id)

    from sqlalchemy import select

    entries = (await db_session.execute(select(AuditEntry))).scalars().all()
    assert entries == []


# ---------------------------------------------------------------------------
# SecretHandle + vault_use -- der auditierte Weg
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_handle_returns_reference_without_ciphertext(db_session):
    keyring = _keyring()
    await vault.create_secret(
        db_session, keyring, label="ssh-fleet", kind="ssh_private_key", plaintext="-----BEGIN...."
    )

    handle = await vault.get_handle(db_session, "ssh-fleet")
    assert handle is not None
    assert handle.label == "ssh-fleet"
    assert handle.kind == "ssh_private_key"
    assert not hasattr(handle, "ciphertext")


@pytest.mark.asyncio
async def test_get_handle_unknown_label_returns_none(db_session):
    assert await vault.get_handle(db_session, "does-not-exist") is None


@pytest.mark.asyncio
async def test_vault_use_yields_plaintext_and_writes_audit_entry(db_session):
    keyring = _keyring()
    secret = await vault.create_secret(
        db_session, keyring, label="ext-secret", kind="api_token", plaintext="tok_abc123"
    )
    # Committen, nicht nur flushen: vault_use() liest/schreibt die Nebenwirkungen seit
    # dem Isolations-Fix ueber eine EIGENE Session (siehe dortiger Docstring) -- die
    # sieht nur, was bereits durable ist, kein nur-geflushter Zustand der Aufrufer-
    # Session. Genau das ist der realistische Fall: eine Extension bekommt ein Handle
    # fuer ein bereits bestehendes Secret, nicht eines, das sie in derselben,
    # unfertigen Transaktion selbst gerade erst angelegt hat.
    await db_session.commit()
    handle = vault.SecretHandle(id=secret.id, label=secret.label, kind=secret.kind)

    async with vault.vault_use(
        db_session, keyring, handle, actor_type="extension", actor_id="hello-world"
    ) as value:
        assert value == "tok_abc123"

    from sqlalchemy import select

    entries = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.action == "secret.used"))
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "secret.used"
    assert entry.outcome == "success"
    assert entry.actor_type == "extension"
    assert entry.actor_id == "hello-world"
    assert entry.target_id == secret.id
    assert entry.detail == {"label": "ext-secret"}
    # Die zentrale Zusicherung: der Wert selbst darf NIRGENDS im Audit-Eintrag landen.
    assert "tok_abc123" not in str(entry.detail)
    assert "tok_abc123" not in (entry.reason or "")


@pytest.mark.asyncio
async def test_vault_use_unknown_handle_raises(db_session):
    handle = vault.SecretHandle(id="unbekannt", label="x", kind="generic")
    with pytest.raises(vault.VaultError):
        async with vault.vault_use(db_session, _keyring(), handle, actor_type="user", actor_id="u1"):
            pass


@pytest.mark.asyncio
async def test_vault_use_audit_survives_caller_session_rollback(tmp_path):
    """Die Auflage aus Commit 1ee1dbe: die Isolations-Entscheidung fuer den
    Audit-Write in vault_use() muss gegen einen echten zweiten Aufrufer verifiziert
    werden -- nicht gegen die In-Memory-StaticPool-Fixture (dort teilen sich alle
    Sessions dieselbe physische Verbindung, das haette die eigentliche Frage gar nicht
    pruefen koennen), sondern gegen eine echte Datei-DB mit echten, unabhaengigen
    Verbindungen -- exakt wie im spaeteren Boot-Test gegen den echten Extension-Host."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.config import Settings
    from nodvard_deck.db.session import create_engine_for, reset_engine_cache, set_engine_for_testing
    from nodvard_deck.models import Base

    db_path = tmp_path / "isolation.db"
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db_path}")
    engine = create_engine_for(settings)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        set_engine_for_testing(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        keyring = _keyring()

        async with sessionmaker() as setup_session:
            secret = await vault.create_secret(
                setup_session, keyring, label="iso-test", kind="generic", plaintext="wert"
            )
            await setup_session.commit()
            secret_id = secret.id

        handle = vault.SecretHandle(id=secret_id, label="iso-test", kind="generic")

        class _CallerFailure(Exception):
            pass

        with pytest.raises(_CallerFailure):
            async with sessionmaker() as caller_session:
                async with vault.vault_use(
                    caller_session, keyring, handle, actor_type="extension", actor_id="hello-world"
                ) as value:
                    assert value == "wert"
                    # Der Aufrufer scheitert NACH der Materialisierung an etwas voellig
                    # anderem -- die Session wurde nie eigens committet.
                    raise _CallerFailure("Aufrufer-Code scheitert nach vault_use")

        async with sessionmaker() as verify_session:
            entries = (
                await verify_session.execute(
                    select(AuditEntry).where(AuditEntry.action == "secret.used")
                )
            ).scalars().all()
            assert len(entries) == 1
            assert entries[0].actor_id == "hello-world"
            assert entries[0].detail == {"label": "iso-test"}

            refreshed = await verify_session.get(Secret, secret_id)
            assert refreshed.last_used_at is not None
    finally:
        await engine.dispose()
        reset_engine_cache()
