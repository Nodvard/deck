"""CVE-Abgleich: Distributionsversionen, Datenquellen, Cache und rein lesende Paketlisten."""

from __future__ import annotations

import asyncio
import bz2
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from defusedxml.common import DefusedXmlException


@pytest.fixture
def cve(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))
    from nodvard_deck_ext_shield import cve

    yield cve
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


APT = """@@os
ID=debian
VERSION_ID="12"
VERSION_CODENAME=bookworm
@@packages
ii \tlibssl3:amd64\t3.0.11-1~deb12u1+b1\topenssl\t3.0.11-1~deb12u1\tamd64
rc \tremoved\t1.0-1\tremoved\t1.0-1\tall
@@end
"""


def inventory(cve, distro="debian", release="12", codename="bookworm", version="1.0-1"):
    return cve.Inventory(distro, release, codename, (cve.Package("libexample", version, "example", version, "x86_64"),))


@pytest.mark.parametrize(("a", "b", "expected"), [
    ("1:1.0-1", "2.0-1", 1), ("1.0~rc1-1", "1.0-1", -1),
    ("1.0-9", "1.0-10", -1), ("1.0", "1.0-0", 0),
    ("1.0-1+deb12u2", "1.0-1+deb12u10", -1),
    ("1.0-1+b1", "1.0-1", 1), ("1.01", "1.1", 0),
])
def test_debian_version_order(cve, a, b, expected):
    assert cve.deb_compare(a, b) == expected
    assert cve.deb_compare(b, a) == -expected


@pytest.mark.parametrize(("a", "b", "expected"), [
    ("1:1.0-1.el9", "0:9.0-1.el9", 1), ("0:1.0-9.el9", "1.0-10.el9", -1),
    ("1.0~rc1-1", "1.0-1", -1), ("1.0^git1", "1.0", 1),
    ("1.0^git1", "1.0.1", -1), ("1.01-1", "1.1-1", 0),
    ("1.0a", "1.0.1", -1),
])
def test_rpm_version_order(cve, a, b, expected):
    assert cve.rpm_compare(a, b) == expected
    assert cve.rpm_compare(b, a) == -expected


def test_inventory_uses_source_version_and_ignores_removed_packages(cve):
    parsed = cve.parse_inventory(APT)
    assert (parsed.distro, parsed.release, parsed.codename) == ("debian", "12", "bookworm")
    assert parsed.packages == (cve.Package("libssl3:amd64", "3.0.11-1~deb12u1+b1", "openssl", "3.0.11-1~deb12u1", "amd64"),)


def test_inventory_keeps_rpm_source_epoch_and_signature(cve):
    raw = "@@os\nID=fedora\nVERSION_ID=43\n@@packages\nii \tlibexample\t2:1.0-1.fc43\texample-1.0-1.fc43.src.rpm\t2:1.0-1.fc43\tx86_64\tRSA/SHA256, Key ID abcd\n@@end"
    parsed = cve.parse_inventory(raw)
    assert parsed.packages[0].source == "example"
    assert parsed.packages[0].source_version == "2:1.0-1.fc43"
    assert parsed.packages[0].signature.endswith("abcd")


def test_rpm_key_records_are_skipped_and_missing_source_has_binary_fallback(cve):
    raw = "@@os\nID=fedora\nVERSION_ID=43\n@@packages\nii \tgpg-pubkey\t0:abc-123\t(none)\t0:abc-123\t(none)\t(none)\nii \texample\t0:1.0-1.fc43\t(none)\t0:1.0-1.fc43\tx86_64\t(none)\n@@end"
    parsed = cve.parse_inventory(raw)
    assert len(parsed.packages) == 1
    assert parsed.packages[0].source == "example"


def test_incomplete_package_configuration_cannot_be_ignored(cve):
    with pytest.raises(cve.SourceError):
        cve.parse_inventory(APT.replace("ii ", "iF "))


@pytest.mark.parametrize("raw", [APT.replace("@@end", ""), APT.replace("\topenssl\t", "\tbad;name\t"), APT.replace("\tamd64\n", "\n")])
def test_incomplete_or_malformed_inventory_fails(cve, raw):
    with pytest.raises(cve.SourceError):
        cve.parse_inventory(raw)


def test_derivatives_are_not_silently_matched_as_their_parent_distribution(cve):
    with pytest.raises(cve.Unsupported):
        cve.parse_inventory(APT.replace("ID=debian", "ID=linuxmint\nID_LIKE=debian"))


def test_debian_source_release_and_backported_fix(cve):
    data = {"openssl": {
        "CVE-2026-1001": {"releases": {"bookworm": {"status": "resolved", "fixed_version": "3.0.11-1~deb12u2", "urgency": "high"}}},
        "CVE-2026-1002": {"releases": {"trixie": {"status": "open", "urgency": "high"}}},
        "CVE-2026-1003": {"releases": {"bookworm": {"status": "resolved", "fixed_version": "0", "urgency": "high"}}},
        "CVE-2026-1004": {"releases": {"bookworm": {"status": "open", "urgency": "not yet assigned"}}},
    }}
    findings = cve.match_debian(cve.parse_inventory(APT), data)
    assert [(row["cve"], row["severity"]) for row in findings] == [("CVE-2026-1001", "high"), ("CVE-2026-1004", "unknown")]
    fixed = APT.replace("3.0.11-1~deb12u1", "3.0.11-1~deb12u2")
    assert [row["cve"] for row in cve.match_debian(cve.parse_inventory(fixed), data)] == ["CVE-2026-1004"]


def archive(*records):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:xz") as stream:
        for index, record in enumerate(records):
            raw = json.dumps(record).encode()
            member = tarfile.TarInfo(f"osv/cve/2026/{index}.json")
            member.size = len(raw)
            stream.addfile(member, io.BytesIO(raw))
    return out.getvalue()


@pytest.mark.parametrize("release", [
    {"status": "undetermined"}, {"status": "resolved"},
    {"status": "resolved", "fixed_version": "not-a-version"},
])
def test_debian_unknown_or_malformed_relevant_status_cannot_report_clean(cve, release):
    data = {"example": {"CVE-2026-1001": {"releases": {"bookworm": release}}}}
    with pytest.raises(cve.SourceError):
        cve.match_debian(inventory(cve), data)


def osv(ecosystem="Ubuntu:24.04:LTS", events=None):
    return {"id": "UBUNTU-CVE-2026-1001", "severity": [{"type": "Ubuntu", "score": "critical"}],
            "affected": [{"package": {"ecosystem": ecosystem, "name": "example"},
                          "ranges": [{"type": "ECOSYSTEM", "events": events or [{"introduced": "0"}, {"fixed": "1.0-2ubuntu1"}]}]}]}


def test_ubuntu_bulk_json_release_source_version_and_fixed_boundary(cve):
    inv = inventory(cve, "ubuntu", "24.04", "noble", "1.0-1ubuntu1")
    body = archive(osv(), osv("Ubuntu:22.04:LTS"), {"id": "USN-1234-1"})
    rows = cve.match_ubuntu_osv(inv, body)
    assert len(rows) == 1 and rows[0]["severity"] == "critical"
    assert rows[0]["fixed_version"] == "1.0-2ubuntu1"
    assert cve.match_ubuntu_osv(inventory(cve, "ubuntu", "24.04", "noble", "1.0-2ubuntu1"), body) == []


def test_ubuntu_unfixed_and_reintroduced_ranges(cve):
    affected = {"ranges": [{"type": "ECOSYSTEM", "events": [
        {"introduced": "0"}, {"fixed": "1.0-2"}, {"introduced": "1.0-4"},
    ]}]}
    assert cve._osv_affected("1.0-1", affected) == (True, "1.0-2")
    assert cve._osv_affected("1.0-3", affected) == (False, None)
    assert cve._osv_affected("1.0-4", affected) == (True, None)


def test_ubuntu_missing_release_and_corrupt_archive_are_not_clean(cve):
    with pytest.raises(cve.Unsupported):
        cve.match_ubuntu_osv(inventory(cve, "ubuntu", "24.04", "noble"), archive(osv("Ubuntu:22.04:LTS")))
    with pytest.raises(cve.SourceError):
        cve.match_ubuntu_osv(inventory(cve, "ubuntu", "24.04", "noble"), b"not an archive")


OVAL = """<oval_definitions xmlns="http://oval.mitre.org/XMLSchema/oval-definitions-5" xmlns:linux="http://oval.mitre.org/XMLSchema/oval-definitions-5#linux">
<definitions><definition id="d"><metadata><reference ref_id="CVE-2026-1001"/><advisory><severity>Important</severity></advisory></metadata>
<criteria operator="AND"><criterion test_ref="t"/></criteria></definition></definitions>
<tests><linux:rpminfo_test id="t" check="at least one"><linux:object object_ref="o"/><linux:state state_ref="s"/></linux:rpminfo_test></tests>
<objects><linux:rpminfo_object id="o"><linux:name>libexample</linux:name></linux:rpminfo_object></objects>
<states><linux:rpminfo_state id="s"><linux:evr operation="less than">0:1.0-2.el9</linux:evr><linux:arch>x86_64</linux:arch></linux:rpminfo_state></states>
</oval_definitions>"""


def test_rhel_oval_architecture_and_rpm_fixed_version(cve):
    inv = inventory(cve, "rhel", "9.5", "", "0:1.0-1.el9")
    rows = cve.match_rhel_oval(inv, bz2.compress(OVAL.encode()))
    assert len(rows) == 1 and rows[0]["severity"] == "high"
    assert rows[0]["fixed_version"] == "0:1.0-2.el9"
    assert cve.match_rhel_oval(inventory(cve, "rhel", "9.5", "", "0:1.0-2.el9"), bz2.compress(OVAL.encode())) == []
    assert cve.match_rhel_oval(inv, bz2.compress(OVAL.replace("x86_64", "aarch64").encode())) == []


@pytest.mark.parametrize(("reference_negate", "expected"), [("true", 1), ("false", 0)])
def test_rhel_negated_definition_reference(cve, reference_negate, expected):
    rules = OVAL.replace(
        '<criterion test_ref="t"/>',
        '<criterion test_ref="t"/><extend_definition definition_ref="ref" negate="true"/>',
    ).replace(
        '</definitions>',
        f'<definition id="ref"><criteria><criterion test_ref="t" negate="{reference_negate}"/></criteria></definition></definitions>',
    )
    inv = inventory(cve, "rhel", "9", "", "0:1.0-1.el9")
    rows = cve.match_rhel_oval(inv, bz2.compress(rules.encode()))
    assert len(rows) == expected
    if rows:
        assert rows[0]["package"] == "libexample"
        assert rows[0]["fixed_version"] == "0:1.0-2.el9"


def test_rhel_unknown_relevant_predicate_and_entities_fail_closed(cve):
    unknown = OVAL.replace("<linux:arch>x86_64</linux:arch>", "<linux:unknown>value</linux:unknown>")
    with pytest.raises(cve.Unsupported):
        cve.match_rhel_oval(inventory(cve, "rhel", "9", "", "0:1.0-1.el9"), bz2.compress(unknown.encode()))
    entity = '<!DOCTYPE x [<!ENTITY boom "boom">]>' + OVAL.replace("Important", "&boom;")
    with pytest.raises(cve.SourceError) as raised:
        cve.match_rhel_oval(inventory(cve, "rhel", "9", ""), bz2.compress(entity.encode()))
    assert isinstance(raised.value.__cause__, DefusedXmlException)


@pytest.mark.parametrize("name", [
    '<linux:name operation="unknown">libexample</linux:name>',
    '<linux:name operation="pattern match">[</linux:name>',
])
def test_rhel_unknown_object_name_predicate_is_not_an_absent_package(cve, name):
    invalid = OVAL.replace("<linux:name>libexample</linux:name>", name)
    with pytest.raises(cve.Unsupported):
        cve.match_rhel_oval(inventory(cve, "rhel", "9", "", "0:1.0-1.el9"), bz2.compress(invalid.encode()))


def test_fedora_source_package_epoch_release_and_bug_cve(cve):
    row = {"alias": "FEDORA-2026-abc", "release": {"version": "43"}, "type": "security", "status": "stable",
           "severity": "urgent", "bugs": [{"title": "CVE-2026-1001: example issue"}],
           "builds": [{"nvr": "example-1.0-2.fc43", "epoch": 2, "type": "rpm"}]}
    inv = inventory(cve, "fedora", "43", "", "2:1.0-1.fc43")
    rows = cve.match_fedora(inv, [row])
    assert len(rows) == 1 and rows[0]["fixed_version"] == "2:1.0-2.fc43"
    assert rows[0]["package"] == "libexample" and rows[0]["severity"] == "critical"
    assert cve.match_fedora(inventory(cve, "fedora", "44", "", "2:1.0-1.fc44"), [row]) == []
    assert cve.match_fedora(inventory(cve, "fedora", "43", "", "3:1.0-1.fc43"), [row]) == []


def context(tmp_path, payload=b"{}"):
    return SimpleNamespace(data_dir=tmp_path, http=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=200, content=payload))),
                           hosts=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="host-1"))),
                           exec=SimpleNamespace(read_access=lambda *args, **kwargs: contextlib.nullcontext(),
                                                run=AsyncMock(return_value=SimpleNamespace(exit_code=0, stdout=APT, truncated=False))))


async def test_public_source_cache_is_shared_for_24_hours_and_serializes_downloads(cve, tmp_path):
    ctx = context(tmp_path, b'{"public":true}')
    assert await asyncio.gather(cve.source_bytes(ctx, cve.DEBIAN_URL), cve.source_bytes(ctx, cve.DEBIAN_URL)) == [b'{"public":true}'] * 2
    assert ctx.http.get.await_count == 1
    path = next((tmp_path / "cve-cache").iterdir())
    os.utime(path, (0, 0))
    await cve.source_bytes(ctx, cve.DEBIAN_URL)
    assert ctx.http.get.await_count == 2


async def test_failed_refresh_never_uses_expired_public_data_as_current(cve, tmp_path):
    ctx = context(tmp_path)
    await cve.source_bytes(ctx, cve.DEBIAN_URL)
    os.utime(next((tmp_path / "cve-cache").iterdir()), (0, 0))
    ctx.http.get.side_effect = RuntimeError("network failed")
    with pytest.raises(cve.SourceError):
        await cve.source_bytes(ctx, cve.DEBIAN_URL)


async def test_host_check_sends_no_package_data_and_reports_counts(cve, tmp_path):
    data = {"openssl": {"CVE-2026-1001": {"releases": {"bookworm": {"status": "open", "urgency": "high"}}}}}
    ctx = context(tmp_path, json.dumps(data).encode())
    result = await cve.check_host(ctx, "host-1")
    assert result["status"] == "ok" and result["error"] is None
    assert result["counts"] == {"high": 1, "critical": 0}
    ctx.http.get.assert_awaited_once_with(cve.DEBIAN_URL, timeout=60.0, follow_redirects=False)
    assert "libssl" not in str(ctx.http.get.call_args) and "openssl" not in str(ctx.http.get.call_args)
    assert "libssl" not in next((tmp_path / "cve-cache").iterdir()).name


async def test_truncated_inventory_and_network_failure_are_errors(cve, tmp_path):
    ctx = context(tmp_path)
    ctx.exec.run.return_value.truncated = True
    result = await cve.check_host(ctx, "host-1")
    assert result["status"] == "error" and result["error"]
    ctx.http.get.assert_not_awaited()
    ctx.exec.run.return_value.truncated = False
    ctx.http.get.side_effect = RuntimeError("sensitive remote text")
    result = await cve.check_host(ctx, "host-1")
    assert result["status"] == "error" and "sensitive" not in result["error"]


async def test_unsupported_distribution_does_not_fetch_wrong_feed(cve, tmp_path):
    ctx = context(tmp_path)
    ctx.exec.run.return_value.stdout = APT.replace("ID=debian", "ID=arch")
    result = await cve.check_host(ctx, "host-1")
    assert result["status"] == "unsupported"
    ctx.http.get.assert_not_awaited()


async def test_duplicate_findings_keep_highest_severity_and_earliest_fix(cve, tmp_path, monkeypatch):
    ctx = context(tmp_path, b'{"data":true}')
    rows = [{"package": "libssl3", "installed_version": "1.0-1", "cve": "CVE-2026-1001",
             "severity": level, "fixed_version": fixed}
            for level, fixed in [("critical", "1.0-3"), ("low", None), ("high", "1.0-2")]]
    monkeypatch.setattr(cve, "match_debian", lambda *args: rows)
    result = await cve.check_host(ctx, "host-1")
    assert len(result["findings"]) == 1
    assert result["findings"][0]["severity"] == "critical"
    assert result["findings"][0]["fixed_version"] == "1.0-2"
    assert result["counts"] == {"critical": 1, "high": 0}


async def test_fedora_pagination_reads_whole_release_without_package_filters(cve, tmp_path):
    ctx = context(tmp_path)
    ctx.http.get.side_effect = [SimpleNamespace(status_code=200, content=json.dumps({
        "updates": [{"alias": "FEDORA-1"}], "pages": 2,
    }).encode()), SimpleNamespace(status_code=200, content=json.dumps({
        "updates": [{"alias": "FEDORA-2"}], "pages": 2,
    }).encode())]
    rows = await cve.fedora_data(ctx, "43")
    assert [row["alias"] for row in rows] == ["FEDORA-1", "FEDORA-2"]
    urls = [call.args[0] for call in ctx.http.get.await_args_list]
    assert all("releases=F43" in url and "package" not in url for url in urls)
    assert "page=1" in urls[0] and "page=2" in urls[1]


async def test_fedora_repeated_pages_are_not_a_complete_result(cve, tmp_path):
    ctx = context(tmp_path, json.dumps({"updates": [{"alias": "same"}], "pages": 2}).encode())
    with pytest.raises(cve.SourceError):
        await cve.fedora_data(ctx, "43")


async def test_source_redirect_and_malformed_json_are_errors(cve, tmp_path):
    ctx = context(tmp_path, b"not JSON")
    ctx.http.get.return_value.status_code = 302
    with pytest.raises(cve.SourceError, match="HTTP 302"):
        await cve.source_json(ctx, cve.DEBIAN_URL)
    assert not (tmp_path / "cve-cache").exists()
    ctx.http.get.return_value.status_code = 200
    with pytest.raises(cve.SourceError, match="JSON"):
        await cve.source_json(ctx, cve.DEBIAN_URL)


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")
def test_inventory_helper_matches_fallback_and_rejects_arguments(cve, tmp_path):
    from shield_helper_kit import SCRIPT, helper_copy, stubs

    tools = stubs(tmp_path, {"dpkg-query": "printf 'ii \\tlibexample\\t1.0-1\\texample\\t1.0-1\\tamd64\\n'\n"})
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "os-release").write_text("ID=debian\nVERSION_ID=12\nVERSION_CODENAME=bookworm\n")
    path = f"{tools}:/usr/bin:/bin"
    helper = helper_copy(tmp_path, path=path, sdmark=tmp_path / "no-systemd")
    actual = subprocess.run(["sh", str(helper), "package-inventory"], capture_output=True, text=True, check=False)
    command = cve.INVENTORY_COMMAND.replace("/etc/os-release", str(etc / "os-release"))
    expected = subprocess.run(["sh", "-c", command], env={"PATH": path}, capture_output=True, text=True, check=False)
    assert actual.returncode == expected.returncode == 0
    assert actual.stdout == expected.stdout
    assert cve.parse_inventory(actual.stdout).packages[0].source == "example"
    bad = subprocess.run(["sh", str(helper), "package-inventory", "--install"], capture_output=True, text=True, check=False)
    assert bad.returncode == 64
    function = SCRIPT.read_text().split("package_inventory() {", 1)[1].split("\n}\n", 1)[0]
    assert all(word not in function for word in ("apt-get", "dnf ", "curl ", "wget ", " install "))
