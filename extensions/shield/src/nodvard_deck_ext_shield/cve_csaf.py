"""RHEL-Pakete lokal mit den veröffentlichten CSAF-Sicherheitsupdates vergleichen."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote

from .cve import Inventory, Package, SourceError, Unsupported, finding, rpm_compare, source_json

API_URL = "https://access.redhat.com/hydra/rest/securitydata"
PAGE_SIZE = 1000
MAX_PAGES = 100
MAX_ADVISORIES = 2000
_ADVISORY = re.compile(r"RH(?:SA|BA|EA)-[0-9]{4}:[0-9]+")
_CVE = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")


@dataclass(frozen=True)
class Product:
    name: str
    version: str
    arch: str
    scope: str


def _mapping(value: Any) -> dict:
    if not isinstance(value, dict):
        raise SourceError("Die RHEL-CSAF-Daten enthalten einen ungültigen Eintrag.")
    return value


def _list(value: Any) -> list:
    if not isinstance(value, list):
        raise SourceError("Die RHEL-CSAF-Daten enthalten eine ungültige Liste.")
    return value


async def advisory_ids(ctx: Any, major: str) -> list[str]:
    selected: set[str] = set()
    seen: set[str] = set()
    release = re.compile(rf"\.el{re.escape(major)}(?:[_.+@]|$)")
    for page in range(1, MAX_PAGES + 1):
        url = f"{API_URL}/csaf.json?per_page={PAGE_SIZE}&page={page}&isCompressed=false"
        rows = _list(await source_json(ctx, url))
        if len(rows) > PAGE_SIZE:
            raise SourceError("Der RHEL-CSAF-Index überschreitet die angeforderte Seitengröße.")
        for value in rows:
            row = _mapping(value)
            advisory = row.get("RHSA")
            packages = _list(row.get("released_packages"))
            if not isinstance(advisory, str) or not _ADVISORY.fullmatch(advisory) or advisory in seen:
                raise SourceError("Der RHEL-CSAF-Index enthält ungültige oder doppelte Kennungen.")
            if not all(isinstance(package, str) for package in packages):
                raise SourceError("Der RHEL-CSAF-Index enthält ungültige Paketnamen.")
            seen.add(advisory)
            if any(release.search(package) for package in packages):
                selected.add(advisory)
                if len(selected) > MAX_ADVISORIES:
                    raise SourceError("Für diese RHEL-Version überschreitet der CSAF-Abgleich die unterstützte Größe.")
        if len(rows) < PAGE_SIZE:
            if not selected:
                raise Unsupported("Für diese RHEL-Version enthält der CSAF-Index keine Sicherheitsupdates.")
            return sorted(selected)
    raise SourceError("Der RHEL-CSAF-Index konnte nicht vollständig gelesen werden.")


def _platform(cpe: Any, release: str) -> str:
    if not isinstance(cpe, str):
        raise SourceError("Einer RHEL-CSAF-Plattform fehlt die CPE-Kennung.")
    fields = cpe.split(":")
    if cpe.startswith("cpe:/") and len(fields) >= 5:
        kind, vendor, product, version = fields[1][1:], fields[2], fields[3], fields[4]
    elif cpe.startswith("cpe:2.3:") and len(fields) >= 6:
        kind, vendor, product, version = fields[2:6]
    else:
        raise SourceError("Die RHEL-CSAF-Daten enthalten eine unbekannte CPE-Kennung.")
    if kind != "o" or vendor != "redhat" or not product.startswith("enterprise_linux"):
        return "excluded"
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise SourceError("Die RHEL-CSAF-Daten nennen keine eindeutige Betriebssystemversion.")
    if version.split(".")[0] != release.split(".")[0]:
        return "excluded"
    if "." in version and "." not in release:
        return "unsupported"
    if "." in version and version != release:
        return "excluded"
    return "matched" if product == "enterprise_linux" else "unsupported"


def _rpm(purl: Any, scope: str) -> Product:
    if not isinstance(purl, str) or not purl.startswith("pkg:rpm/redhat/") or "#" in purl:
        raise SourceError("Die RHEL-CSAF-Daten enthalten eine unbekannte RPM-Kennung.")
    path, separator, query = purl[len("pkg:rpm/redhat/"):].partition("?")
    name, at, version = path.rpartition("@")
    qualifiers = parse_qs(query, keep_blank_values=True)
    name, version = unquote(name), unquote(version)
    arch = qualifiers.get("arch", [""])
    epoch = qualifiers.get("epoch", ["0"])
    if (
        not at or not separator or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name)
        or not re.fullmatch(r"[0-9][A-Za-z0-9+.:~^_-]*", version)
        or len(arch) != 1 or not re.fullmatch(r"[A-Za-z0-9_]+", arch[0])
        or len(epoch) != 1 or not re.fullmatch(r"[0-9]+", epoch[0])
    ):
        raise SourceError("Die RHEL-CSAF-Daten enthalten eine unvollständige RPM-Version.")
    if ":" in version:
        inline_epoch, version = version.split(":", 1)
        if not inline_epoch.isdigit() or ("epoch" in qualifiers and int(inline_epoch) != int(epoch[0])):
            raise SourceError("Eine RHEL-CSAF-Version enthält widersprüchliche Epoch-Angaben.")
        epoch = [inline_epoch]
    return Product(name, f"{int(epoch[0])}:{version}", arch[0], scope)


def _products(tree: dict, release: str) -> tuple[dict[str, Product], bool]:
    products: dict[str, dict] = {}

    def add(product: Any) -> None:
        row = _mapping(product)
        identifier = row.get("product_id")
        if not isinstance(identifier, str) or not identifier or identifier in products:
            raise SourceError("Die RHEL-CSAF-Daten enthalten ungültige oder doppelte Produktkennungen.")
        products[identifier] = row

    def walk(branches: Any, depth: int = 0) -> None:
        if depth > 30:
            raise SourceError("Der RHEL-CSAF-Produktbaum ist zu tief verschachtelt.")
        for value in _list(branches):
            row = _mapping(value)
            if "product" in row:
                add(row["product"])
            if "branches" in row:
                walk(row["branches"], depth + 1)

    walk(tree.get("branches", []))
    for row in _list(tree.get("full_product_names", [])):
        add(row)
    mapped: dict[str, Product] = {}
    covered = False
    for value in _list(tree.get("relationships")):
        row = _mapping(value)
        identifier = _mapping(row.get("full_product_name")).get("product_id")
        component = products.get(row.get("product_reference"))
        platform = products.get(row.get("relates_to_product_reference"))
        if row.get("category") != "default_component_of" or component is None or platform is None:
            raise SourceError("Eine RHEL-CSAF-Produktbeziehung kann nicht zugeordnet werden.")
        if not isinstance(identifier, str) or not identifier or identifier in mapped:
            raise SourceError("Die RHEL-CSAF-Daten enthalten doppelte Produktbeziehungen.")
        platform_helper = _mapping(platform.get("product_identification_helper"))
        scope = _platform(platform_helper.get("cpe"), release)
        if scope == "excluded":
            mapped[identifier] = Product("", "0", "", scope)
            continue
        component_helper = _mapping(component.get("product_identification_helper"))
        mapped[identifier] = _rpm(component_helper.get("purl"), scope)
        covered = covered or scope == "matched"
    if not mapped:
        raise SourceError("Die RHEL-CSAF-Daten enthalten keine auswertbaren Paketbeziehungen.")
    return mapped, covered


def _installed(product: Product, packages: tuple[Package, ...]) -> list[tuple[Package, str]]:
    if product.arch == "src":
        return [(package, package.source_version) for package in packages if package.source == product.name]
    return [(package, package.version) for package in packages if package.name == product.name and package.arch == product.arch]


def _severity(vulnerability: dict, identifier: str, fallback: Any) -> Any:
    general = fallback
    scoped = None
    for value in _list(vulnerability.get("threats", [])):
        threat = _mapping(value)
        if threat.get("category") != "impact":
            continue
        if "product_ids" not in threat:
            general = threat.get("details")
        elif identifier in _list(threat["product_ids"]):
            scoped = threat.get("details")
    return scoped if scoped is not None else general


def match_advisory(inventory: Inventory, data: Any) -> tuple[list[dict], bool]:
    data = _mapping(data)
    document = _mapping(data.get("document"))
    products, covered = _products(_mapping(data.get("product_tree")), inventory.release)
    fallback = _mapping(document.get("aggregate_severity", {})).get("text")
    vulnerabilities = _list(data.get("vulnerabilities"))
    if not vulnerabilities:
        raise SourceError("Das RHEL-CSAF-Sicherheitsupdate enthält keine Schwachstellenangaben.")
    findings = []
    for value in vulnerabilities:
        vulnerability = _mapping(value)
        cve = vulnerability.get("cve")
        if not isinstance(cve, str) or not _CVE.fullmatch(cve):
            raise SourceError("Das RHEL-CSAF-Sicherheitsupdate enthält eine ungültige CVE-Kennung.")
        status = _mapping(vulnerability.get("product_status"))
        fixed = _list(status.get("fixed", []))
        if not fixed:
            raise SourceError("Das RHEL-CSAF-Sicherheitsupdate enthält keine zugeordneten korrigierten Pakete.")
        for identifier in fixed:
            if not isinstance(identifier, str) or identifier not in products:
                raise SourceError("Eine CVE aus den RHEL-CSAF-Daten verweist auf ein unbekanntes Produkt.")
            product = products[identifier]
            if product.scope == "excluded":
                continue
            for package, installed in _installed(product, inventory.packages):
                if rpm_compare(installed, product.version) >= 0:
                    continue
                if product.scope == "unsupported":
                    raise Unsupported("Für dieses RHEL-Paket ist der EUS- oder Versionskanal aus der Paketliste nicht eindeutig prüfbar.")
                findings.append(finding(package, cve, _severity(vulnerability, identifier, fallback), product.version))
    return findings, covered


async def check_rhel(ctx: Any, inventory: Inventory) -> list[dict]:
    major = inventory.release.split(".")[0]
    if inventory.distro != "rhel" or not major.isdigit() or int(major) < 10:
        raise Unsupported("Der RHEL-CSAF-Abgleich unterstützt RHEL ab Version 10.")
    identifiers = await advisory_ids(ctx, major)
    findings = []
    covered = False
    failure: SourceError | None = None

    async def check(advisory: str) -> tuple[list[dict], bool, SourceError | None]:
        try:
            data = await source_json(ctx, f"{API_URL}/csaf/{advisory}.json")
            document = _mapping(_mapping(data).get("document"))
            if _mapping(document.get("tracking")).get("id") != advisory:
                raise SourceError("Das RHEL-CSAF-Dokument gehört nicht zur angeforderten Sicherheitsmeldung.")
            rows, supported = await asyncio.to_thread(match_advisory, inventory, data)
            return rows, supported, None
        except SourceError as exc:
            return [], False, exc

    # Immer dieselben Distributionsdaten laden, unabhängig von installierten Paketen.
    for offset in range(0, len(identifiers), 2):
        results = await asyncio.gather(*(check(advisory) for advisory in identifiers[offset:offset + 2]))
        for rows, supported, error in results:
            findings.extend(rows)
            covered = covered or supported
            failure = failure or error
    if failure is not None:
        raise failure
    if not covered:
        raise Unsupported("Die CSAF-Daten enthalten keine eindeutig passende RHEL-Version für diesen Server.")
    return findings
