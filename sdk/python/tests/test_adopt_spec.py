"""`adopts` im Manifest: Übernahme eines Teils aus einem anderen Modul (siehe `AdoptSpec`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nodvard_sdk import AdoptSpec, ExtensionManifest

BASE = {"id": "pihole", "name": "Pi-hole", "version": "0.1.0", "api_version": "0.1.0", "entrypoint": "x:Extension"}


def _manifest(*adopts: dict, **extra) -> ExtensionManifest:
    return ExtensionManifest(**BASE, adopts=list(adopts), **extra)


def test_a_full_entry_is_read_with_the_keys_of_the_toml():
    m = _manifest({
        "from": "network", "settings": "pihole", "widgets": {"pihole": "status"},
        "routes": {"/pihole": "/summary", "/npm/hosts/{host_id}/enable": "/hosts/{host_id}/enable"},
        "secrets": ["network-pihole-password"],
    })
    spec = m.adopts[0]
    assert isinstance(spec, AdoptSpec) and spec.from_ == "network" and spec.settings == "pihole"
    assert m.model_dump(mode="json", by_alias=True)["adopts"][0]["from"] == "network"
    assert ExtensionManifest(**m.model_dump(mode="json", by_alias=True)).adopts == m.adopts, "gespeichert und wieder gelesen"


def test_without_adopts_nothing_is_taken_over():
    assert ExtensionManifest(**BASE).adopts == []
    assert _manifest({"from": "network"}).adopts[0].routes == {}
    assert _manifest({"from": "network"}).adopts[0].secrets == []


@pytest.mark.parametrize("entry", [
    {"from": "Netzwerk"},
    {"from": "network\n"},
    {"from": "network", "unbekannt": 1},
    {"from": "network", "settings": "a-b"},
    {"from": "network", "widgets": {"Pihole": "status"}},
    {"from": "network", "routes": {"pihole": "/summary"}},
    {"from": "network", "routes": {"/pihole": "/summary/"}},
    {"from": "network", "routes": {"/a b": "/summary"}},
    {"from": "network", "routes": {"/a/../b": "/summary"}},
    {"from": "network", "routes": {"/a/./b": "/summary"}},
    {"from": "network", "routes": {"/..": "/summary"}},
    {"from": "network", "routes": {"/teil": "/x/.."}},
    {"from": "network", "routes": {"/hosts/{id}": "/hosts/{host_id}"}},
    {"from": "network", "routes": {"/hosts/{id}": "/hosts"}},
    {"from": "network", "secrets": ["Netz-Passwort"]},
    {"from": "network", "secrets": ["a", "a"]},
    {"from": "network", "secrets": "network-pihole-password"},
], ids=["kennung", "zeilenumbruch", "unbekannter-schluessel", "abschnitt", "kachel", "ohne-schraegstrich",
        "schraegstrich-am-ende", "leerzeichen", "punkt-punkt", "punkt", "nur-punkt-punkt", "ziel-punkt-punkt",
        "anderer-platzhalter", "platzhalter-fehlt", "tresor-name",
        "tresor-doppelt", "tresor-keine-liste"])
def test_odd_entries_are_refused(entry):
    with pytest.raises(ValidationError):
        _manifest(entry)


def test_dots_inside_a_part_of_the_address_are_fine():
    spec = _manifest({"from": "network", "routes": {"/v1.2/status.json": "/a..b/.well-known"}}).adopts[0]
    assert spec.routes == {"/v1.2/status.json": "/a..b/.well-known"}


def test_an_own_old_or_doubled_source_is_refused():
    with pytest.raises(ValidationError):
        _manifest({"from": "pihole"})
    with pytest.raises(ValidationError):
        _manifest({"from": "alt"}, legacy_ids=["alt"])
    with pytest.raises(ValidationError):
        _manifest({"from": "network"}, {"from": "network", "settings": "pihole"})
    assert len(_manifest({"from": "network"}, {"from": "proxy"}).adopts) == 2
