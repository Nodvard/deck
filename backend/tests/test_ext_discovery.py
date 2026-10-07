"""Entdeckung von Extensions: Verzeichnis-Scan + Entry-Points, ohne Code-Import
(docs/02-EXTENSION-API.md §1).
"""

from __future__ import annotations

from pathlib import Path

from nodvard_deck.ext import discovery

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


def _write_manifest(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "extension.toml").write_text(content, encoding="utf-8")


def test_scan_directory_finds_valid_manifest(tmp_path):
    ext_dir = tmp_path / "sample-ext"
    _write_manifest(
        ext_dir / "x",
        """
        [extension]
        id = "sample-ext"
        name = "Sample"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "sample_ext:Extension"
        permissions = ["audit.write"]
        """,
    )

    found = discovery.scan_directory(tmp_path)
    assert len(found) == 1
    assert found[0].ok
    assert found[0].id == "sample-ext"
    assert found[0].source == "bundled"
    assert found[0].manifest.permissions == ["audit.write"]


def test_scan_directory_isolates_broken_manifest_from_others(tmp_path):
    _write_manifest(
        tmp_path / "broken" / "x",
        "das ist kein gueltiges TOML {{{",
    )
    _write_manifest(
        tmp_path / "healthy" / "x",
        """
        [extension]
        id = "healthy"
        name = "Healthy"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "healthy:Extension"
        """,
    )

    found = {d.id if d.ok else d.source_path.name: d for d in discovery.scan_directory(tmp_path)}

    assert found["broken"].ok is False
    assert found["broken"].error is not None
    assert found["healthy"].ok is True


def test_scan_directory_ignores_subdirs_without_manifest(tmp_path):
    (tmp_path / "not-an-extension").mkdir()
    (tmp_path / "not-an-extension" / "readme.txt").write_text("x")

    assert discovery.scan_directory(tmp_path) == []


def test_scan_directory_missing_root_returns_empty(tmp_path):
    assert discovery.scan_directory(tmp_path / "does-not-exist") == []


def test_scan_entry_points_finds_manifest_via_distribution(tmp_path, monkeypatch):
    manifest_path = tmp_path / "extension.toml"
    manifest_path.write_text(
        """
        [extension]
        id = "pip-ext"
        name = "Pip Extension"
        version = "1.0.0"
        api_version = "0.1"
        entrypoint = "pip_ext:Extension"
        """,
        encoding="utf-8",
    )

    class _FakeDistribution:
        name = "pip-ext-dist"

        def locate_file(self, path: str):
            return tmp_path / path

    class _FakeEntryPoint:
        name = "pip-ext"
        dist = _FakeDistribution()

    monkeypatch.setattr(
        discovery.importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint()]
    )

    found = discovery.scan_entry_points()
    assert len(found) == 1
    assert found[0].ok
    assert found[0].id == "pip-ext"
    assert found[0].source == "pip"


def test_scan_entry_points_isolates_missing_manifest_file(tmp_path, monkeypatch):
    class _FakeDistribution:
        name = "broken-dist"

        def locate_file(self, path: str):
            return tmp_path / "does-not-exist" / path

    class _FakeEntryPoint:
        name = "broken"
        dist = _FakeDistribution()

    monkeypatch.setattr(
        discovery.importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint()]
    )

    found = discovery.scan_entry_points()
    assert len(found) == 1
    assert found[0].ok is False
    assert "extension.toml" in found[0].error


def test_scan_entry_points_no_matching_group_returns_empty():
    assert discovery.scan_entry_points(group="nodvard_deck.does-not-exist") == []


# -- alte Kennungen (`legacy_ids`) ---------------------------------------------------------


def _ext(
    tmp_path, folder: str, ext_id: str, legacy: list[str] | None = None, permissions: list[str] | None = None
) -> None:
    legacy_line = f"legacy_ids = {legacy!r}".replace("'", '"') if legacy else ""
    permissions_line = f"permissions = {permissions!r}".replace("'", '"') if permissions else ""
    _write_manifest(
        tmp_path / folder / "x",
        f"""
        [extension]
        id = "{ext_id}"
        name = "{ext_id}"
        version = "0.1.0"
        api_version = "0.1"
        entrypoint = "m:Extension"
        {legacy_line}
        {permissions_line}
        """,
    )


def test_resolve_legacy_without_legacy_ids_changes_nothing(tmp_path):
    _ext(tmp_path, "aaa", "aaa")
    _ext(tmp_path, "bbb", "bbb")
    _write_manifest(tmp_path / "kaputt" / "x", "kein TOML {{{")
    found = discovery.scan_directory(tmp_path)

    result = discovery.resolve_legacy(found)

    assert result.found == found
    assert all(a is b for a, b in zip(result.found, found))
    assert result.owner == {} and result.refused == [] and result.refused_old == {}


def test_resolve_legacy_maps_old_id_to_new_one(tmp_path):
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext", "older-ext"])
    _ext(tmp_path, "other", "other")

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    assert result.owner == {"old-ext": "renamed-ext", "older-ext": "renamed-ext"}
    assert result.refused == [] and result.refused_old == {}
    assert all(d.ok for d in result.found)


def test_resolve_legacy_refuses_new_extension_while_old_folder_is_still_there(tmp_path):
    """Liegt der alte Ordner noch neben dem neuen, laeuft die alte Erweiterung unveraendert weiter und
    die neue wird abgelehnt -- sonst liefe alles doppelt oder die neue nahme der alten den Stand weg."""
    _ext(tmp_path, "old-ext", "old-ext")
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    by_id = {d.id: d for d in result.found}
    assert by_id["old-ext"].ok
    assert not by_id["renamed-ext"].ok and by_id["renamed-ext"].manifest is None
    assert "„old-ext“" in by_id["renamed-ext"].error and "Ordner „old-ext“" in by_id["renamed-ext"].error
    assert [d.id for d in result.refused] == ["renamed-ext"]
    assert result.owner == {}
    # Die alte Kennung gehoert weiter der alten Erweiterung, ihre Zeile laedt wie bisher.
    assert result.refused_old == {}


def test_resolve_legacy_counts_an_old_folder_with_broken_manifest_as_still_there(tmp_path):
    _write_manifest(tmp_path / "old-ext" / "x", "kein TOML {{{")
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    assert [d.id for d in result.refused] == ["renamed-ext"]
    assert result.owner == {}


def test_resolve_legacy_refuses_both_when_two_extensions_claim_the_same_old_id(tmp_path):
    _ext(tmp_path, "first", "first", ["old-ext"])
    _ext(tmp_path, "second", "second", ["old-ext", "only-second"])
    _ext(tmp_path, "third", "third", ["third-old"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    assert sorted(d.id for d in result.refused) == ["first", "second"]
    assert all("mehreren Erweiterungen" in d.error for d in result.refused)
    # Eine abgelehnte Erweiterung bekommt auch ihre anderen alten Kennungen nicht.
    assert result.owner == {"third-old": "third"}
    # Die Zeilen der alten Kennungen nennen den Grund (der gemeinsame nur einmal).
    claimed = "Die alte Kennung „old-ext“ wird von mehreren Erweiterungen beansprucht („first“, „second“)."
    assert result.refused_old == {
        "old-ext": claimed,
        "only-second": f"Die neue Version heißt „second“. {claimed}",
    }


def test_resolve_legacy_counts_one_extension_found_twice_as_one_claimant(tmp_path):
    """Dieselbe Erweiterung als Ordner und als Paket ist kein Streit um die alte Kennung."""
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext"])
    found = discovery.scan_directory(tmp_path)

    result = discovery.resolve_legacy([*found, *found])

    assert result.refused == [] and result.owner == {"old-ext": "renamed-ext"}


HOSTS_WRITE_REFUSAL = (
    "Erweiterungen, die Server anlegen, können noch nicht umbenannt werden (legacy_ids zusammen mit hosts.write)."
)


def test_resolve_legacy_refuses_legacy_ids_together_with_hosts_write(tmp_path):
    """Wer Server anlegt, speichert sie unter seiner Kennung (`provider_ext_id`) und findet sie darueber wieder:
    eine Umbenennung ginge dort schief. Der Kern lehnt die Kombination bei der Entdeckung ab."""
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext"], ["hosts.read", "hosts.write", "net.outbound"])
    _ext(tmp_path, "other", "other", None, ["hosts.write"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    by_id = {d.id: d for d in result.found}
    assert not by_id["renamed-ext"].ok and by_id["renamed-ext"].manifest is None
    assert by_id["renamed-ext"].error == HOSTS_WRITE_REFUSAL
    assert by_id["other"].ok  # ohne legacy_ids bleibt hosts.write erlaubt
    assert [d.id for d in result.refused] == ["renamed-ext"]
    assert result.owner == {}  # die abgelehnte Erweiterung bekommt auch ihre alte Kennung nicht
    assert result.refused_old == {"old-ext": f"Die neue Version heißt „renamed-ext“. {HOSTS_WRITE_REFUSAL}"}


def test_resolve_legacy_refuses_hosts_write_with_an_argument_and_names_it_once(tmp_path):
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext", "older-ext"], ["hosts.write", "hosts.write:extra"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    assert [d.error for d in result.refused] == [HOSTS_WRITE_REFUSAL]
    reason = f"Die neue Version heißt „renamed-ext“. {HOSTS_WRITE_REFUSAL}"
    assert result.refused_old == {"old-ext": reason, "older-ext": reason}


def test_resolve_legacy_names_both_reasons_when_the_old_folder_is_there_too(tmp_path):
    _ext(tmp_path, "old-ext", "old-ext")
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-ext"], ["hosts.write"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    (refused,) = result.refused
    assert HOSTS_WRITE_REFUSAL in refused.error and "gehört noch zu einer installierten Erweiterung" in refused.error
    assert result.refused_old == {}


def test_resolve_legacy_names_the_reason_for_each_old_id_not_installed_any_more(tmp_path):
    """Abgelehnt wegen einer anderen alten Kennung: auch die Zeile der ersten nennt den Grund."""
    _ext(tmp_path, "old-b", "old-b")
    _ext(tmp_path, "renamed-ext", "renamed-ext", ["old-a", "old-b"])

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    (refused,) = result.refused
    assert result.refused_old == {"old-a": f"Die neue Version heißt „renamed-ext“. {refused.error}"}
    assert "„old-b“" in refused.error and result.owner == {}


def test_resolve_legacy_allows_legacy_ids_with_permissions_that_store_nothing_under_the_id(tmp_path):
    """Alle anderen Berechtigungen vertragen sich mit `legacy_ids`: Geheimnisse, Meldungen und Protokoll
    tragen die Kennung nur als Herkunftsangabe, nichts sucht danach."""
    _ext(
        tmp_path, "renamed-ext", "renamed-ext", ["old-ext"],
        ["hosts.read", "hosts.execute", "secrets.read:renamed-*", "secrets.write", "notify.send", "audit.write",
         "audit.read", "schedule.register", "net.outbound", "settings.read"],
    )

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    assert result.refused == [] and all(d.ok for d in result.found)
    assert result.owner == {"old-ext": "renamed-ext"} and result.refused_old == {}


def _copy_bundled_manifest(name: str, tmp_path, legacy: str | None = None):
    """Kopie des echten Manifests (mit Einstellungs-Schema) im tmp-Ordner, mit `legacy_ids` (ohne Angabe so, wie
    es ist) -- die echte Datei bleibt unveraendert."""
    source = REPO_EXTENSIONS_DIR / name
    target = tmp_path / name
    target.mkdir(parents=True)
    text = (source / "extension.toml").read_text(encoding="utf-8")
    if legacy is not None:
        assert "legacy_ids" not in text
        text = text.replace("[extension]\n", f'[extension]\nlegacy_ids = ["{legacy}"]\n', 1)
    (target / "extension.toml").write_text(text, encoding="utf-8")
    for extra in source.glob("*.json"):
        (target / extra.name).write_text(extra.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_shield_with_legacy_ids_is_not_refused(tmp_path):
    """Nodvard Shield (extensions/shield, bis 0.6 `nexus-soc`) nennt seine alte Kennung und legt keine Server an:
    der Kern lehnt die Umbenennung nicht ab."""
    _copy_bundled_manifest("shield", tmp_path)

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    (shield,) = result.found
    assert shield.ok, shield.error
    assert shield.manifest.id == "shield" and shield.manifest.legacy_ids == ["nexus-soc"]
    assert "hosts.write" not in shield.manifest.permissions
    assert result.refused == [] and result.owner == {"nexus-soc": "shield"}


def test_proxmox_with_legacy_ids_is_refused(tmp_path):
    """Proxmox legt Server an (`hosts.write`): mit `legacy_ids` wuerde der Kern es ablehnen."""
    _copy_bundled_manifest("proxmox", tmp_path, "old-pve")

    result = discovery.resolve_legacy(discovery.scan_directory(tmp_path))

    assert [d.error for d in result.refused] == [HOSTS_WRITE_REFUSAL]
