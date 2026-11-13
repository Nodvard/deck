"""RHEL-CSAF: vollständige öffentliche Daten und genaue Produktzuordnung."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"


@pytest.fixture
def csaf(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    from nodvard_deck_ext_shield import cve_csaf

    yield cve_csaf
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _inventory(csaf, name="openssl", version="0:1.0-1.el10", arch="x86_64", release="10.1", source=None):
    return csaf.Inventory("rhel", release, "", (csaf.Package(name, version, source or name, version, arch),))


def _product(identifier, helper):
    return {"product": {"product_id": identifier, "product_identification_helper": helper}}


def _advisory(identifier="RHSA-2026:1001", name="openssl", version="1.1-1.el10", arch="x86_64", epoch=0):
    return {
        "document": {"tracking": {"id": identifier}, "aggregate_severity": {"text": "Important"}},
        "product_tree": {
            "branches": [
                _product("os", {"cpe": "cpe:/o:redhat:enterprise_linux:10.1"}),
                _product("rpm", {"purl": f"pkg:rpm/redhat/{name}@{version}?arch={arch}&epoch={epoch}"}),
            ],
            "relationships": [{
                "category": "default_component_of", "product_reference": "rpm", "relates_to_product_reference": "os",
                "full_product_name": {"product_id": "combined"},
            }],
        },
        "vulnerabilities": [{"cve": "CVE-2026-12345", "product_status": {"fixed": ["combined"]}}],
    }


def _index(identifier="RHSA-2026:1001", name="openssl", release="el10"):
    return {"RHSA": identifier, "released_packages": [f"{name}-1.1-1.{release}.x86_64"]}


def test_matches_only_fixed_product_relationship(csaf):
    document = _advisory()
    document["product_tree"]["branches"].append(_product(
        "other-rpm", {"purl": "pkg:rpm/redhat/curl@9.0-1.el10?arch=x86_64&epoch=0"},
    ))
    document["product_tree"]["relationships"].append({
        "category": "default_component_of", "product_reference": "other-rpm", "relates_to_product_reference": "os",
        "full_product_name": {"product_id": "other-combined"},
    })
    document["vulnerabilities"].append({
        "cve": "CVE-2026-54321", "product_status": {"fixed": ["other-combined"]},
    })
    findings, covered = csaf.match_advisory(_inventory(csaf), document)
    assert covered
    assert [(row["package"], row["cve"], row["fixed_version"]) for row in findings] == [
        ("openssl", "CVE-2026-12345", "0:1.1-1.el10"),
    ]


@pytest.mark.parametrize("arch", ["aarch64", "noarch"])
def test_different_binary_arch_is_not_matched(csaf, arch):
    findings, covered = csaf.match_advisory(_inventory(csaf), _advisory(arch=arch))
    assert covered
    assert findings == []


@pytest.mark.parametrize("cpe", [
    "cpe:/o:redhat:enterprise_linux:9", "cpe:/o:redhat:enterprise_linux:10.2", "cpe:/o:other:enterprise_linux:10.1",
])
def test_other_distribution_or_minor_release_is_excluded(csaf, cpe):
    document = _advisory()
    document["product_tree"]["branches"][0]["product"]["product_identification_helper"]["cpe"] = cpe
    assert csaf.match_advisory(_inventory(csaf), document) == ([], False)


def test_other_product_with_non_rpm_component_is_excluded(csaf):
    document = _advisory()
    document["product_tree"]["branches"][0]["product"]["product_identification_helper"]["cpe"] = (
        "cpe:/a:redhat:openshift:4.19"
    )
    document["product_tree"]["branches"][1]["product"]["product_identification_helper"]["purl"] = (
        "pkg:oci/example@sha256:abc"
    )
    assert csaf.match_advisory(_inventory(csaf), document) == ([], False)


def test_eus_channel_is_not_assumed_from_os_release(csaf):
    document = _advisory()
    document["product_tree"]["branches"][0]["product"]["product_identification_helper"]["cpe"] = (
        "cpe:/o:redhat:enterprise_linux_eus:10.1"
    )
    with pytest.raises(csaf.Unsupported, match="EUS"):
        csaf.match_advisory(_inventory(csaf), document)


def test_epoch_and_source_version_are_compared(csaf):
    inventory = _inventory(csaf, name="openssl-libs", version="1:1.0-1.el10", source="openssl")
    older_epoch = _advisory(version="99.0-1.el10", arch="src", epoch=0)
    assert csaf.match_advisory(inventory, older_epoch)[0] == []
    newer_epoch = _advisory(version="0.5-1.el10", arch="src", epoch=2)
    findings, _ = csaf.match_advisory(inventory, newer_epoch)
    assert findings[0]["package"] == "openssl-libs"
    assert findings[0]["fixed_version"] == "2:0.5-1.el10"


def test_product_scoped_impact_wins_over_unrelated_impact(csaf):
    document = _advisory()
    document["vulnerabilities"][0]["threats"] = [
        {"category": "impact", "details": "Critical", "product_ids": ["elsewhere"]},
        {"category": "impact", "details": "Low", "product_ids": ["combined"]},
    ]
    findings, _ = csaf.match_advisory(_inventory(csaf), document)
    assert findings[0]["severity"] == "low"


@pytest.mark.parametrize("change", ["unknown-product", "missing-purl", "missing-fixed", "bad-epoch", "empty-vulnerabilities"])
def test_ambiguous_document_never_becomes_empty_success(csaf, change):
    document = _advisory()
    if change == "unknown-product":
        document["vulnerabilities"][0]["product_status"]["fixed"] = ["missing"]
    elif change == "missing-purl":
        document["product_tree"]["branches"][1]["product"]["product_identification_helper"] = {}
    elif change == "missing-fixed":
        document["vulnerabilities"][0]["product_status"] = {"known_affected": ["combined"]}
    elif change == "bad-epoch":
        document["product_tree"]["branches"][1]["product"]["product_identification_helper"]["purl"] = (
            "pkg:rpm/redhat/openssl@1.1-1.el10?arch=x86_64&epoch=unknown"
        )
    else:
        document["vulnerabilities"] = []
    with pytest.raises(csaf.SourceError):
        csaf.match_advisory(_inventory(csaf), document)


@pytest.mark.asyncio
async def test_requests_are_identical_for_different_installed_packages(csaf, monkeypatch):
    source = AsyncMock(side_effect=[
        [_index(), _index("RHSA-2026:1002", "curl"), _index("RHSA-2026:1003", "openssl", "el9")],
        _advisory(), _advisory("RHSA-2026:1002", "curl"),
    ])
    monkeypatch.setattr(csaf, "source_json", source)
    await csaf.check_rhel(object(), _inventory(csaf))
    first = [call.args[1] for call in source.call_args_list]
    source.reset_mock(side_effect=True)
    source.side_effect = [
        [_index(), _index("RHSA-2026:1002", "curl"), _index("RHSA-2026:1003", "openssl", "el9")],
        _advisory(), _advisory("RHSA-2026:1002", "curl"),
    ]
    await csaf.check_rhel(object(), _inventory(csaf, name="private-package"))
    second = [call.args[1] for call in source.call_args_list]
    assert first == second
    assert len(first) == 3
    assert all("package=" not in url and "private-package" not in url and "openssl" not in url for url in first)


@pytest.mark.asyncio
async def test_package_specific_unsupported_does_not_change_download_set(csaf, monkeypatch):
    eus = _advisory()
    eus["product_tree"]["branches"][0]["product"]["product_identification_helper"]["cpe"] = (
        "cpe:/o:redhat:enterprise_linux_eus:10.1"
    )
    source = AsyncMock(side_effect=[
        [_index(), _index("RHSA-2026:1002", "curl"), _index("RHSA-2026:1003", "bash")],
        eus, _advisory("RHSA-2026:1002", "curl"), _advisory("RHSA-2026:1003", "bash"),
    ])
    monkeypatch.setattr(csaf, "source_json", source)
    with pytest.raises(csaf.Unsupported):
        await csaf.check_rhel(object(), _inventory(csaf))
    assert source.await_count == 4


@pytest.mark.asyncio
async def test_pagination_continues_through_full_pages(csaf, monkeypatch):
    monkeypatch.setattr(csaf, "PAGE_SIZE", 1)
    source = AsyncMock(side_effect=[[_index()], [_index("RHSA-2026:1002")], []])
    monkeypatch.setattr(csaf, "source_json", source)
    assert await csaf.advisory_ids(object(), "10") == ["RHSA-2026:1001", "RHSA-2026:1002"]
    assert "page=3" in source.call_args.args[1]


@pytest.mark.asyncio
async def test_index_accepts_at_sign_architecture(csaf, monkeypatch):
    row = {"RHSA": "RHSA-2026:1001", "released_packages": ["openssl-0:1.1-1.el10@x86_64"]}
    monkeypatch.setattr(csaf, "source_json", AsyncMock(return_value=[row]))
    assert await csaf.advisory_ids(object(), "10") == ["RHSA-2026:1001"]


@pytest.mark.asyncio
async def test_duplicate_pages_and_limits_are_errors(csaf, monkeypatch):
    monkeypatch.setattr(csaf, "PAGE_SIZE", 1)
    source = AsyncMock(side_effect=[[_index()], [_index()]])
    monkeypatch.setattr(csaf, "source_json", source)
    with pytest.raises(csaf.SourceError, match="doppelte"):
        await csaf.advisory_ids(object(), "10")
    source.side_effect = [[_index()]]
    monkeypatch.setattr(csaf, "MAX_PAGES", 1)
    with pytest.raises(csaf.SourceError, match="vollständig"):
        await csaf.advisory_ids(object(), "10")


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [[], {}, [{"RHSA": "RHSA-2026:1001", "affected_packages": ["openssl-1.el10"]}]])
async def test_empty_or_malformed_index_is_not_safe(csaf, monkeypatch, index):
    monkeypatch.setattr(csaf, "source_json", AsyncMock(return_value=index))
    with pytest.raises(csaf.SourceError):
        await csaf.advisory_ids(object(), "10")


@pytest.mark.asyncio
async def test_mismatched_advisory_identifier_is_rejected(csaf, monkeypatch):
    source = AsyncMock(side_effect=[[_index()], _advisory("RHSA-2026:9999")])
    monkeypatch.setattr(csaf, "source_json", source)
    with pytest.raises(csaf.SourceError, match="angeforderten"):
        await csaf.check_rhel(object(), _inventory(csaf))


def test_duplicate_product_relationship_is_rejected(csaf):
    document = _advisory()
    document["product_tree"]["relationships"].append(copy.deepcopy(document["product_tree"]["relationships"][0]))
    with pytest.raises(csaf.SourceError, match="doppelte"):
        csaf.match_advisory(_inventory(csaf), document)
