"""Installierte Pakete lokal mit öffentlichen Sicherheitsdaten der Distribution vergleichen."""

from __future__ import annotations

import asyncio
import bz2
import hashlib
import io
import json
import re
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

from .antivirus import via_helper

CACHE_TTL = 24 * 60 * 60
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_XML_BYTES = 256 * 1024 * 1024
HELPER_CVE_VERSION = 24
DEBIAN_URL = "https://security-tracker.debian.org/tracker/data/json"
UBUNTU_OSV_URL = "https://security-metadata.canonical.com/osv/osv-all.tar.xz"
RHEL_URL = "https://security.access.redhat.com/data/oval/v2"
FEDORA_URL = "https://bodhi.fedoraproject.org/updates/"
_LOCKS: dict[str, asyncio.Lock] = {}
_CVE = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._:~-]*")
_VERSION = re.compile(r"[0-9][A-Za-z0-9+.:~^_-]*")
_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}

INVENTORY_COMMAND = r"""set -eu
echo @@os
cat /etc/os-release
echo @@packages
if command -v dpkg-query >/dev/null 2>&1; then
    dpkg-query -W -f='${db:Status-Abbrev}\t${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}\n'
elif command -v rpm >/dev/null 2>&1; then
    rpm -qa --qf 'ii \t%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{SOURCERPM}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\t%{RSAHEADER:pgpsig}\n'
else
    echo @@unsupported
fi
echo @@end
"""


class SourceError(ValueError):
    """Die Datenquelle oder Paketliste lässt keine vollständige Prüfung zu."""


class Unsupported(SourceError):
    """Für diese Distribution fehlt eine passende Datenquelle."""


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    source: str
    source_version: str
    arch: str
    signature: str = ""


@dataclass(frozen=True)
class Inventory:
    distro: str
    release: str
    codename: str
    packages: tuple[Package, ...]


def inventory_command() -> str:
    return via_helper(["package-inventory"], INVENTORY_COMMAND, version=HELPER_CVE_VERSION, required=False)


def parse_inventory(output: str) -> Inventory:
    if len(output) > 16 * 1024 * 1024 or "@@end" not in output.splitlines():
        raise SourceError("Die Paketliste ist unvollständig.")
    if "@@unsupported" in output.splitlines():
        raise Unsupported("Der Paketmanager wird für den CVE-Abgleich noch nicht unterstützt.")
    section = ""
    os_release: dict[str, str] = {}
    packages = []
    for line in output.splitlines():
        if line in {"@@os", "@@packages", "@@end"}:
            section = line
        elif section == "@@os":
            key, sep, value = line.partition("=")
            if sep and key in {"ID", "VERSION_ID", "VERSION_CODENAME", "UBUNTU_CODENAME"}:
                os_release[key] = value.strip().strip("\"'")
        elif section == "@@packages" and line:
            fields = line.split("\t")
            if len(fields) not in {6, 7}:
                raise SourceError("Die Paketliste hat ein unbekanntes Format.")
            state, name, version, source, source_version, arch = fields[:6]
            signature = fields[6] if len(fields) == 7 else ""
            if state[1:2] in {"U", "F", "H", "W", "t"}:
                raise SourceError("Die Paketverwaltung meldet nicht vollständig eingerichtete Pakete.")
            if state[1:2] != "i":
                continue
            if name == "gpg-pubkey":
                continue
            source = name.split(":", 1)[0] if source in {"", "(none)"} else source
            source_version = version if source_version in {"", "(none)"} else source_version
            if source.endswith(".src.rpm"):
                parts = source[:-8].rsplit("-", 2)
                if len(parts) != 3:
                    raise SourceError("Die RPM-Quellpaketangabe ist ungültig.")
                source = parts[0]
                source_version = f"{_epoch(version)[0]}:{parts[1]}-{parts[2]}"
            if (not all(_NAME.fullmatch(v) for v in (name, source, arch))
                    or not all(_VERSION.fullmatch(v) for v in (version, source_version))):
                raise SourceError("Die Paketliste enthält ungültige Paketangaben.")
            packages.append(Package(name, version, source, source_version, arch, signature))
    distro = os_release.get("ID", "")
    release = os_release.get("VERSION_ID", "")
    codename = os_release.get("VERSION_CODENAME") or os_release.get("UBUNTU_CODENAME", "")
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", distro) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", release):
        raise Unsupported("Die Linux-Distribution konnte nicht eindeutig bestimmt werden.")
    if distro not in {"debian", "ubuntu", "rhel", "fedora"}:
        raise Unsupported("Für diese Linux-Distribution ist noch keine passende CVE-Datenquelle eingerichtet.")
    if distro in {"debian", "ubuntu"} and not re.fullmatch(r"[a-z]{2,30}", codename):
        raise Unsupported("Der Distributionscodename fehlt für den CVE-Abgleich.")
    if not packages:
        raise SourceError("Die Liste der installierten Pakete ist leer.")
    return Inventory(distro, release, codename, tuple(packages))


def _epoch(version: str) -> tuple[int, str]:
    if ":" not in version:
        return 0, version
    epoch, rest = version.split(":", 1)
    if not epoch.isascii() or not epoch.isdigit():
        raise SourceError("Eine Paketversion hat ein ungültiges Epoch-Feld.")
    return int(epoch), rest


def _deb_part(left: str, right: str) -> int:
    def order(char: str) -> int:
        if char == "~":
            return -1
        if not char or char.isdigit():
            return 0
        return ord(char) if char.isalpha() else ord(char) + 256

    while left or right:
        while (left and not left[0].isdigit()) or (right and not right[0].isdigit()):
            a, b = order(left[:1]), order(right[:1])
            if a != b:
                return 1 if a > b else -1
            left, right = left[1:], right[1:]
        a = re.match(r"[0-9]*", left).group()
        b = re.match(r"[0-9]*", right).group()
        left, right = left[len(a):], right[len(b):]
        a, b = a.lstrip("0"), b.lstrip("0")
        if (len(a), a) != (len(b), b):
            return 1 if (len(a), a) > (len(b), b) else -1
    return 0


def deb_compare(left: str, right: str) -> int:
    """dpkg-Reihenfolge mit Epoch, Revision, Tilde und numerischen Abschnitten."""
    a, left = _epoch(left)
    b, right = _epoch(right)
    if a != b:
        return 1 if a > b else -1
    lu, _, lr = left.rpartition("-")
    ru, _, rr = right.rpartition("-")
    if not lu:
        lu, lr = left, "0"
    if not ru:
        ru, rr = right, "0"
    return _deb_part(lu, ru) or _deb_part(lr, rr)


def _rpm_part(left: str, right: str) -> int:
    while left or right:
        left, right = left.lstrip("._+-"), right.lstrip("._+-")
        if left.startswith("~") or right.startswith("~"):
            if not left.startswith("~"):
                return 1
            if not right.startswith("~"):
                return -1
            left, right = left[1:], right[1:]
            continue
        if left.startswith("^") or right.startswith("^"):
            if not left:
                return -1
            if not right:
                return 1
            if not left.startswith("^"):
                return 1
            if not right.startswith("^"):
                return -1
            left, right = left[1:], right[1:]
            continue
        if not left or not right:
            return (bool(left) > bool(right)) - (bool(left) < bool(right))
        numeric = left[0].isdigit()
        a = re.match(r"[0-9]+" if numeric else r"[A-Za-z]+", left)
        b = re.match(r"[0-9]+" if numeric else r"[A-Za-z]+", right)
        if not a:
            raise SourceError("Eine RPM-Version hat ein unbekanntes Format.")
        if not b:
            return 1 if numeric else -1
        left, right = left[a.end():], right[b.end():]
        av, bv = a.group(), b.group()
        if numeric:
            av, bv = av.lstrip("0"), bv.lstrip("0")
            if len(av) != len(bv):
                return 1 if len(av) > len(bv) else -1
        if av != bv:
            return 1 if av > bv else -1
    return 0


def rpm_compare(left: str, right: str) -> int:
    a, left = _epoch(left)
    b, right = _epoch(right)
    if a != b:
        return 1 if a > b else -1
    lv, _, lr = left.partition("-")
    rv, _, rr = right.partition("-")
    return _rpm_part(lv, rv) or _rpm_part(lr, rr)


def severity(value: Any) -> str:
    value = str(value or "").lower().split("*", 1)[0].strip()
    value = {"important": "high", "moderate": "medium", "negligible": "low", "unimportant": "low"}.get(value, value)
    return value if value in _SEVERITY else "unknown"


def finding(package: Package, cve: str, level: Any, fixed: str | None) -> dict[str, Any]:
    return {"package": package.name, "installed_version": package.version,
            "cve": cve, "severity": severity(level), "fixed_version": fixed}


async def source_bytes(ctx: Any, url: str) -> bytes:
    """Nur öffentliche Quellen werden gespeichert, niemals Paketlisten."""
    key = hashlib.sha256(url.encode()).hexdigest()
    directory = Path(str(ctx.data_dir)) / "cve-cache"
    path = directory / key
    async with _LOCKS.setdefault(str(path), asyncio.Lock()):
        try:
            age = time.time() - path.stat().st_mtime
            if 0 <= age < CACHE_TTL and path.stat().st_size <= MAX_SOURCE_BYTES:
                return await asyncio.to_thread(path.read_bytes)
        except OSError:
            pass
        try:
            response = await ctx.http.get(url, timeout=60.0, follow_redirects=False)
        except Exception as exc:
            raise SourceError("Die öffentliche CVE-Datenquelle ist derzeit nicht erreichbar.") from exc
        if response.status_code != 200:
            raise SourceError(f"Die öffentliche CVE-Datenquelle antwortet mit HTTP {response.status_code}.")
        body = response.content
        if not body or len(body) > MAX_SOURCE_BYTES:
            raise SourceError("Die öffentliche CVE-Datenquelle liefert eine leere oder zu große Antwort.")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            await asyncio.to_thread(temp.write_bytes, body)
            temp.replace(path)
        except OSError as exc:
            raise SourceError("Der lokale Zwischenspeicher für CVE-Daten ist nicht beschreibbar.") from exc
        return body


async def source_json(ctx: Any, url: str) -> Any:
    body = await source_bytes(ctx, url)
    try:
        return await asyncio.to_thread(json.loads, body)
    except (ValueError, RecursionError) as exc:
        raise SourceError("Die öffentliche CVE-Datenquelle liefert ungültiges JSON.") from exc


def match_debian(inventory: Inventory, data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not data:
        raise SourceError("Die Debian-Sicherheitsdaten sind leer oder ungültig.")
    findings = []
    covered = False
    for records in data.values():
        if not isinstance(records, dict):
            raise SourceError("Die Debian-Sicherheitsdaten haben ein unbekanntes Format.")
        if any(inventory.codename in row.get("releases", {}) for row in records.values() if isinstance(row, dict)):
            covered = True
            break
    if not covered:
        raise Unsupported("Für diese Debian-Version enthält der Security Tracker keine Daten.")
    for package in inventory.packages:
        for cve, record in data.get(package.source, {}).items():
            if not _CVE.fullmatch(cve):
                continue
            release = record.get("releases", {}).get(inventory.codename)
            if not isinstance(release, dict):
                continue
            status = release.get("status")
            fixed = release.get("fixed_version")
            if fixed == "0":
                continue
            if status not in {"resolved", "open"}:
                raise Unsupported("Der Debian Security Tracker hat die Betroffenheit eines installierten Pakets noch nicht bestimmt.")
            if fixed and (not isinstance(fixed, str) or not _VERSION.fullmatch(fixed)):
                raise SourceError("Die Debian-Sicherheitsdaten enthalten eine ungültige Korrekturversion.")
            if fixed:
                affected = deb_compare(package.source_version, fixed) < 0
            else:
                if status == "resolved":
                    raise SourceError("In den Debian-Sicherheitsdaten fehlt eine Korrekturversion.")
                fixed = None
                affected = status == "open"
            if affected:
                findings.append(finding(package, cve, release.get("urgency"), fixed))
    return findings


def match_ubuntu_osv(inventory: Inventory, body: bytes) -> list[dict[str, Any]]:
    """Canonical stellt den CVE-Tracker als vollständiges Archiv mit OSV-JSON bereit."""
    packages: dict[str, list[Package]] = {}
    for package in inventory.packages:
        packages.setdefault(package.source, []).append(package)
    findings = []
    covered = False
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r|xz") as archive:
            for member in archive:
                total += member.size
                if total > 4 * 1024 * 1024 * 1024 or member.size > 16 * 1024 * 1024:
                    raise SourceError("Das Ubuntu-CVE-Archiv überschreitet die unterstützte Größe.")
                if not member.isfile() or not member.name.endswith(".json"):
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise SourceError("Das Ubuntu-CVE-Archiv ist unvollständig.")
                record = json.load(stream)
                if not isinstance(record, dict):
                    raise SourceError("Das Ubuntu-CVE-Archiv hat ein unbekanntes Format.")
                identifier = record.get("id", "")
                if not identifier.startswith("UBUNTU-CVE-") or record.get("withdrawn"):
                    continue
                cve = identifier.removeprefix("UBUNTU-")
                if not _CVE.fullmatch(cve):
                    continue
                level = next((item.get("score") for item in record.get("severity", [])
                              if item.get("type") == "Ubuntu"), None)
                for affected in record.get("affected", []):
                    package_info = affected.get("package", {})
                    ecosystem = package_info.get("ecosystem", "")
                    if ecosystem not in {f"Ubuntu:{inventory.release}", f"Ubuntu:{inventory.release}:LTS"}:
                        continue
                    covered = True
                    for package in packages.get(package_info.get("name"), []):
                        affected_version, fixed = _osv_affected(package.source_version, affected)
                        if affected_version:
                            findings.append(finding(package, cve, level, fixed))
    except (tarfile.TarError, EOFError, ValueError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("Das Ubuntu-CVE-Archiv ist beschädigt oder unvollständig.") from exc
    if not covered:
        raise Unsupported("Für diese Ubuntu-Version enthält das CVE-Archiv keine Daten.")
    return findings


def _osv_affected(version: str, affected: dict[str, Any]) -> tuple[bool, str | None]:
    for interval in affected.get("ranges", []):
        if interval.get("type") != "ECOSYSTEM":
            continue
        inside = False
        for event in interval.get("events", []):
            if "introduced" in event:
                lower = event["introduced"]
                inside = lower == "0" or deb_compare(version, lower) >= 0
            elif "fixed" in event:
                if inside and deb_compare(version, event["fixed"]) < 0:
                    return True, event["fixed"]
                inside = False
            elif "last_affected" in event:
                if inside and deb_compare(version, event["last_affected"]) <= 0:
                    return True, None
                inside = False
            elif "limit" in event:
                if inside and deb_compare(version, event["limit"]) < 0:
                    return True, None
                inside = False
            else:
                raise SourceError("Das Ubuntu-CVE-Archiv enthält eine unbekannte Versionsgrenze.")
        if inside:
            return True, None
    return version in affected.get("versions", []), None


def _local_name(element: Any) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: Any, name: str) -> Any:
    return next((child for child in element if _local_name(child) == name), None)


def _entity(value: str, rule: Any, *, version: bool = False) -> bool | None:
    if rule is None or rule.get("var_ref"):
        return None
    wanted = rule.text or ""
    operation = rule.get("operation", "equals")
    if operation == "pattern match":
        if len(wanted) > 1000:
            return None
        try:
            return bool(re.search(wanted, value))
        except re.error:
            return None
    order = rpm_compare(value, wanted) if version else (value > wanted) - (value < wanted)
    return {"equals": order == 0, "not equal": order != 0, "less than": order < 0,
            "less than or equal": order <= 0, "greater than": order > 0,
            "greater than or equal": order >= 0}.get(operation)


def _truth(values: list[bool | None], operator: str) -> bool | None:
    if not values:
        return None
    if operator == "AND":
        return False if False in values else None if None in values else True
    if operator == "OR":
        return True if True in values else None if None in values else False
    return None


def match_rhel_oval(inventory: Inventory, body: bytes) -> list[dict[str, Any]]:
    """Wertet OVAL-Bedingungen aus; unbekannte relevante Bedingungen bleiben Prüfprobleme."""
    try:
        decoder = bz2.BZ2Decompressor()
        xml = decoder.decompress(body, max_length=MAX_XML_BYTES + 1)
        if len(xml) > MAX_XML_BYTES or not decoder.eof:
            raise SourceError("Die RHEL-Sicherheitsdaten sind zu groß oder unvollständig.")
        root = ElementTree.fromstring(xml)
    except (ValueError, OSError, ElementTree.ParseError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("Die RHEL-Sicherheitsdaten enthalten kein gültiges OVAL-Dokument.") from exc
    if _local_name(root) != "oval_definitions":
        raise SourceError("Die RHEL-Sicherheitsdaten haben ein unbekanntes Format.")
    elements = {element.get("id"): element for element in root.iter() if element.get("id")}
    package_map: dict[str, list[Package]] = {}
    for package in inventory.packages:
        package_map.setdefault(package.name, []).append(package)
    tested: dict[str, tuple[bool | None, set[tuple[Package, str | None]]]] = {}

    def test(test_id: str) -> tuple[bool | None, set[tuple[Package, str | None]]]:
        if test_id in tested:
            return tested[test_id]
        node = elements.get(test_id)
        if node is None:
            return None, set()
        obj_ref = _child(node, "object")
        obj = elements.get(obj_ref.get("object_ref")) if obj_ref is not None else None
        if obj is None:
            return None, set()
        if _local_name(node) != "rpminfo_test":
            return None, set()
        name = _child(obj, "name")
        if name is None or name.get("var_ref"):
            return None, set()
        if name.get("operation", "equals") == "equals":
            candidates = package_map.get(name.text or "", [])
        else:
            selected = [(package, _entity(package.name, name)) for package in inventory.packages]
            if any(verdict is None for _, verdict in selected):
                return None, set()
            candidates = [package for package, verdict in selected if verdict]
        existence = node.get("check_existence", "at_least_one_exists")
        if existence == "none_exist":
            return not candidates, set()
        if not candidates:
            return False, set()
        states = [elements.get(child.get("state_ref")) for child in node if _local_name(child) == "state"]
        matches = set()
        verdicts = []
        for package in candidates:
            state_results = []
            fixes = []
            for state in states:
                if state is None:
                    state_results.append(None)
                    continue
                fields = []
                for rule in state:
                    key = _local_name(rule)
                    if key == "evr":
                        fields.append(_entity(package.version, rule, version=True))
                        if rule.get("operation") == "less than":
                            fixes.append(rule.text)
                    elif key in {"epoch", "version", "release"}:
                        epoch, evr = _epoch(package.version)
                        version, _, release = evr.partition("-")
                        value = {"epoch": str(epoch), "version": version, "release": release}[key]
                        fields.append(_entity(value, rule, version=rule.get("operation") != "pattern match"))
                    elif key == "arch":
                        fields.append(_entity(package.arch, rule))
                    elif key == "name":
                        fields.append(_entity(package.name, rule))
                    elif key == "signature_keyid":
                        signature = re.search(r"Key ID ([0-9a-fA-F]+)", package.signature)
                        fields.append(_entity(signature.group(1).lower(), rule) if signature else None)
                    else:
                        fields.append(None)
                state_results.append(_truth(fields, state.get("operator", "AND")))
            passed = _truth(state_results, node.get("state_operator", "AND")) if states else True
            verdicts.append(passed)
            if passed:
                matches.update((package, fixed) for fixed in fixes or [None])
        check = node.get("check", "all")
        verdict = _truth(verdicts, "OR" if check == "at least one" else "AND") if check in {"all", "at least one"} else None
        tested[test_id] = verdict, matches
        return verdict, matches

    def evaluate(node: Any, depth: int = 0) -> tuple[bool | None, set[tuple[Package, str | None]]]:
        if node is None or depth > 80:
            return None, set()
        kind = _local_name(node)
        if kind == "criterion":
            verdict, matches = test(node.get("test_ref", ""))
        elif kind == "extend_definition":
            definition = elements.get(node.get("definition_ref", ""))
            verdict, matches = evaluate(_child(definition, "criteria") if definition is not None else None, depth + 1)
        else:
            children = [evaluate(child, depth + 1) for child in node]
            verdict = _truth([value for value, _ in children], node.get("operator", "AND"))
            matches = set().union(*(items for value, items in children if value is True))
        if node.get("negate") in {"true", "1"}:
            verdict = not verdict if verdict is not None else None
            matches = set()
        return verdict, matches

    findings = []
    definitions = [node for node in root.iter() if _local_name(node) == "definition"]
    if not definitions:
        raise SourceError("Die RHEL-Sicherheitsdaten enthalten keine Prüfregeln.")
    for definition in definitions:
        metadata = _child(definition, "metadata")
        if metadata is None:
            continue
        cves: dict[str, str] = {}
        level = next((node.text for node in metadata.iter() if _local_name(node) == "severity"), None)
        for node in metadata.iter():
            cve = node.get("ref_id", "") if _local_name(node) == "reference" else node.text or ""
            if _CVE.fullmatch(cve):
                cves[cve] = node.get("impact") or level
        if not cves:
            continue
        verdict, matches = evaluate(_child(definition, "criteria"))
        if verdict is None:
            raise Unsupported("Die RHEL-OVAL-Regeln enthalten Bedingungen, die mit der Paketliste nicht prüfbar sind.")
        if not verdict:
            continue
        versions: dict[Package, str | None] = {}
        for package, fixed in matches:
            old_fixed = versions.get(package)
            if package not in versions or (fixed and (not old_fixed or rpm_compare(fixed, old_fixed) < 0)):
                versions[package] = fixed
        for package, fixed in versions.items():
            if package.name.startswith("redhat-release"):
                continue
            for cve, cve_level in cves.items():
                findings.append(finding(package, cve, cve_level, fixed))
    return findings


def match_fedora(inventory: Inventory, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    packages: dict[str, list[Package]] = {}
    for package in inventory.packages:
        packages.setdefault(package.source, []).append(package)
    for record in records:
        if str(record.get("release", {}).get("version")) != inventory.release:
            continue
        if record.get("status") != "stable" or record.get("type") != "security":
            continue
        cves = set(_CVE.findall(str(record.get("notes", ""))))
        for bug in record.get("bugs", []):
            cves.update(_CVE.findall(str(bug.get("title", ""))))
        level = "critical" if record.get("severity") == "urgent" else record.get("severity")
        for build in record.get("builds", []):
            if build.get("type", "rpm") != "rpm":
                continue
            parts = str(build.get("nvr", "")).rsplit("-", 2)
            if len(parts) != 3 or not re.search(rf"\.fc{re.escape(inventory.release)}(?:\.|$)", parts[2]):
                continue
            epoch = build.get("epoch") or 0
            if not isinstance(epoch, int) or epoch < 0:
                raise SourceError("Die Fedora-Sicherheitsdaten enthalten eine ungültige Epoch-Angabe.")
            fixed = f"{epoch}:{parts[1]}-{parts[2]}"
            for package in packages.get(parts[0], []):
                if rpm_compare(package.source_version, fixed) >= 0:
                    continue
                if not cves:
                    raise SourceError("Ein passendes Fedora-Sicherheitsupdate enthält keine auswertbare CVE-Kennung.")
                for cve in sorted(cves):
                    findings.append(finding(package, cve, level, fixed))
    return findings


async def fedora_data(ctx: Any, release: str) -> list[dict[str, Any]]:
    records = []
    seen = set()
    for page in range(1, 501):
        url = f"{FEDORA_URL}?releases=F{release}&type=security&status=stable&rows_per_page=100&page={page}"
        data = await source_json(ctx, url)
        if not isinstance(data, dict) or not isinstance(data.get("updates"), list) or not isinstance(data.get("pages"), int):
            raise SourceError("Die Fedora-Sicherheitsdaten haben ein unbekanntes Format.")
        for row in data["updates"]:
            if not isinstance(row, dict) or not row.get("alias") or row["alias"] in seen:
                raise SourceError("Die Fedora-Sicherheitsdaten enthalten unvollständige oder doppelte Einträge.")
            seen.add(row["alias"])
            records.append(row)
        if page >= data["pages"]:
            if not records:
                raise Unsupported("Für diese Fedora-Version enthält Bodhi keine Sicherheitsdaten.")
            return records
        if not data["updates"]:
            raise SourceError("Die Fedora-Sicherheitsdaten sind unvollständig.")
    raise SourceError("Die Fedora-Sicherheitsdaten überschreiten die unterstützte Größe.")


async def rpm_findings(ctx: Any, inventory: Inventory) -> list[dict[str, Any]]:
    if inventory.distro == "fedora":
        return match_fedora(inventory, await fedora_data(ctx, inventory.release))
    major = inventory.release.split(".")[0]
    if major in {"8", "9"}:
        body = await source_bytes(ctx, f"{RHEL_URL}/RHEL{major}/rhel-{major}-including-unpatched.oval.xml.bz2")
        return await asyncio.to_thread(match_rhel_oval, inventory, body)
    if int(major) >= 10:
        from .cve_csaf import check_rhel

        return await check_rhel(ctx, inventory)
    raise Unsupported("Für diese RHEL-Version ist keine aktuelle CVE-Datenquelle eingerichtet.")


async def check_host(ctx: Any, host_id: str) -> dict[str, Any]:
    """Liest den Server und lädt Distributionsdaten ausschließlich über das Deck."""
    result: dict[str, Any] = {"status": "error", "checked_at": time.time(), "findings": [],
                              "counts": {"high": 0, "critical": 0}, "error": None}
    try:
        host = await ctx.hosts.get(host_id)
        if host is None:
            raise SourceError("Der Server ist nicht mehr vorhanden.")
        with ctx.exec.read_access(host, timeout_s=60):
            response = await ctx.exec.run(host, inventory_command(), timeout_s=60)
        if response.exit_code != 0 or getattr(response, "truncated", False):
            raise SourceError("Die installierten Pakete konnten nicht vollständig gelesen werden.")
        inventory = parse_inventory(response.stdout or "")
        if inventory.distro == "debian":
            data = await source_json(ctx, DEBIAN_URL)
            rows = await asyncio.to_thread(match_debian, inventory, data)
        elif inventory.distro == "ubuntu":
            body = await source_bytes(ctx, UBUNTU_OSV_URL)
            rows = await asyncio.to_thread(match_ubuntu_osv, inventory, body)
        else:
            rows = await rpm_findings(ctx, inventory)
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        compare = deb_compare if inventory.distro in {"debian", "ubuntu"} else rpm_compare
        for row in rows:
            key = row["package"], row["installed_version"], row["cve"]
            previous = unique.get(key)
            if previous is None:
                unique[key] = dict(row)
                continue
            if _SEVERITY[row["severity"]] > _SEVERITY[previous["severity"]]:
                previous["severity"] = row["severity"]
            fixed, old_fixed = row["fixed_version"], previous["fixed_version"]
            if fixed and (not old_fixed or compare(fixed, old_fixed) < 0):
                previous["fixed_version"] = fixed
        findings = sorted(unique.values(), key=lambda row: (-_SEVERITY[row["severity"]], row["package"], row["cve"]))
        result.update(status="ok", findings=findings,
                      counts={level: sum(row["severity"] == level for row in findings) for level in ("high", "critical")})
    except Unsupported as exc:
        result.update(status="unsupported", error=str(exc))
    except SourceError as exc:
        result["error"] = str(exc)
    except Exception:
        result["error"] = "Die CVE-Prüfung konnte nicht abgeschlossen werden. Bitte später erneut prüfen."
    result["checked_at"] = time.time()
    return result
