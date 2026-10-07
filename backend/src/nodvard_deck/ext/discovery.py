"""Entdeckung von Extensions: Verzeichnis-Scan + Entry-Points, ohne Code-Import
(docs/02-EXTENSION-API.md §1).

`nodvard_sdk.load_manifest()` erledigt das Parsen von `extension.toml` bereits (reines
TOML-Lesen, kein Import) -- dieses Modul erledigt nur das FINDEN der Manifeste, an
zwei Stellen:

1. Verzeichnis-Scan: `<extensions_dir>/<name>/extension.toml` -- fuer mitgeliefertes/
   lokales Material (`source="bundled"`), das Manifest liegt neben dem Code.
2. Entry-Points: fuer `pip install`-te Extensions (`source="pip"`). Ein Entry-Point
   selbst ist reine, statische Distributions-Metadaten (kein Import); das Manifest
   wird zusaetzlich als Paketdatei ueber `Distribution.locate_file()` gelesen --
   ebenfalls ohne die Extension zu importieren. Gelesen werden **zwei Gruppen**: `nodvard_deck.extensions`
   (neu) und `lattice.extensions` (der Name vor der Umbenennung -- Fremd-Extensions tragen ihn noch ein).
   Steht dieselbe Distribution mit demselben Manifest in beiden (Extension fuer alte und neue Kerne),
   zaehlt sie einmal.

**Fehler-Isolation gilt schon hier:** ein Manifest, das nicht parst, darf die
Entdeckung der ANDEREN Extensions nicht verhindern -- deshalb sammelt `scan_*` Fehler
pro Kandidat statt beim ersten Fehler abzubrechen.
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from nodvard_sdk import ExtensionManifest, load_manifest

DEFAULT_ENTRY_POINT_GROUPS = ("nodvard_deck.extensions", "lattice.extensions")
"""Neue Gruppe zuerst, dann der alte Name. Beide bleiben, bis eine angekuendigte Major-Version den alten entfernt."""


@dataclass
class DiscoveredExtension:
    """Ein Fund -- entweder ein gueltiges Manifest oder ein Fehler, nie beides."""

    id: str
    source: str  # "bundled" | "pip"
    source_path: Path
    """Bei `bundled`: das Extension-Verzeichnis (Basis fuer `sys.path`-Eintrag und
    `src/`-Layout). Bei `pip`: der Distributions-Root (informativ, `sys.path` ist
    bereits korrekt, weil die Extension installiert ist)."""
    manifest: ExtensionManifest | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.manifest is not None and self.error is None


def scan_directory(extensions_dir: Path) -> list[DiscoveredExtension]:
    """Ein Unterverzeichnis pro Extension, `extension.toml` direkt darin."""
    if not extensions_dir.is_dir():
        return []

    found: list[DiscoveredExtension] = []
    for child in sorted(extensions_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "extension.toml"
        if not manifest_path.exists():
            continue

        try:
            manifest = load_manifest(manifest_path)
        except Exception as exc:  # noqa: BLE001 - ein kaputtes Manifest darf die anderen nicht stoppen
            found.append(
                DiscoveredExtension(
                    id=child.name, source="bundled", source_path=child, manifest=None, error=str(exc)
                )
            )
            continue

        found.append(
            DiscoveredExtension(id=manifest.id, source="bundled", source_path=child, manifest=manifest)
        )
    return found


def _normalize_dist_name(name: str) -> str:
    """PEP 503: `Pip_Ext.Dist` und `pip-ext-dist` sind dieselbe Distribution."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _scan_group(group: str) -> list[tuple[str, DiscoveredExtension]]:
    """Eine Entry-Point-Gruppe -> (normalisierter Distributionsname, Fund)."""
    try:
        entry_points = importlib.metadata.entry_points(group=group)
    except Exception as exc:  # noqa: BLE001 - defekte Metadaten duerfen den Boot nicht stoppen
        return [("?", DiscoveredExtension(id="?", source="pip", source_path=Path("."), manifest=None, error=str(exc)))]

    found: list[tuple[str, DiscoveredExtension]] = []
    for ep in entry_points:
        dist = ep.dist
        dist_name = dist.name if dist is not None else ep.name
        try:
            if dist is None:
                raise LookupError(f"Entry-Point '{ep.name}' hat keine zugehörige Distribution")
            manifest_path = Path(str(dist.locate_file("extension.toml")))
            if not manifest_path.exists():
                raise FileNotFoundError(
                    f"Distribution '{dist_name}' liefert kein extension.toml im Root"
                )
            manifest = load_manifest(manifest_path)
        except Exception as exc:  # noqa: BLE001 - ein kaputter Kandidat darf die anderen nicht stoppen
            found.append((
                _normalize_dist_name(dist_name),
                DiscoveredExtension(id=dist_name, source="pip", source_path=Path("."), manifest=None, error=str(exc)),
            ))
            continue

        found.append((
            _normalize_dist_name(dist_name),
            DiscoveredExtension(
                id=manifest.id,
                source="pip",
                source_path=manifest_path.parent,
                manifest=manifest,
            ),
        ))
    return found


def scan_entry_points(group: str | Iterable[str] = DEFAULT_ENTRY_POINT_GROUPS) -> list[DiscoveredExtension]:
    """Pip-installierte Extensions. Erwartet je Distribution ein `extension.toml` im
    Distributions-Root neben dem `entry_point`-Eintrag -- gelesen ueber
    `Distribution.locate_file()`, damit auch dieser Pfad ohne Import auskommt.

    `group`: eine Gruppe oder mehrere (Standard: die neue und die alte). Funde mit gleicher Distribution UND
    gleicher ID (aus beiden Gruppen oder doppelt eingetragen) werden zusammengefasst, der erste gewinnt."""
    groups = (group,) if isinstance(group, str) else tuple(group)
    found: list[DiscoveredExtension] = []
    seen: set[tuple[str, str]] = set()
    for one in groups:
        for dist_name, item in _scan_group(one):
            key = (dist_name, item.id)
            if key in seen:
                continue
            seen.add(key)
            found.append(item)
    return found


def discover_all(
    extensions_dir: Path, *, entry_point_group: str | Iterable[str] = DEFAULT_ENTRY_POINT_GROUPS
) -> list[DiscoveredExtension]:
    return [*scan_directory(extensions_dir), *scan_entry_points(entry_point_group)]


LEGACY_BLOCKING_PERMISSIONS: dict[str, str] = {"hosts.write": "Server anlegen"}
"""Berechtigungen, die sich mit `legacy_ids` nicht vertragen, jeweils mit dem Satzteil fuer die Meldung.

Wer sie hat, speichert Daten dauerhaft unter seiner Kennung und sucht sie darueber wieder: `hosts.write`
(`ctx.hosts.upsert_discovered`) schreibt `Host.provider_ext_id` und `HostTag.managed_by_ext_id` und findet
seine Server ueber `provider_ext_id` wieder. Nach einer Umbenennung gaebe es doppelte Server, die alten
verloeren ihren Anbieter, und ein Rueckweg aufs alte Image faende die neuen nicht. Die Pruefung sitzt im Kern
und nicht im Manifest-Pruefer des SDK: Es ist eine Grenze der Kern-Speicherung (heute), keine Eigenschaft
des Manifest-Formats, und faellt weg, sobald der Kern die alten Kennungen auch dort mitliest.

Nicht dabei, weil nichts dauerhaft unter der Kennung gesucht wird: `secrets.*` (`Secret.owner_ext_id` ist
nur die Herkunftsangabe), `notify.send` (`Notification.source_ext_id`) und `audit.write` (Akteur im
Protokoll) -- alte Eintraege behalten die alte Kennung, nichts sucht danach. `ctx.connectors` braucht keine
Berechtigung und liest ueber alle eigenen Kennungen (`ConnectorsHandle`)."""


@dataclass
class LegacyResolution:
    """Ergebnis von `resolve_legacy()`."""

    found: list[DiscoveredExtension]
    """Alle Funde in derselben Reihenfolge; abgelehnte stehen darin als Fund mit Fehler."""
    owner: dict[str, str]
    """Alte Kennung -> Kennung der Erweiterung, die sie (ohne Konflikt) in `legacy_ids` nennt."""
    refused: list[DiscoveredExtension]
    """Die wegen eines Konflikts abgelehnten Funde (mit Fehlertext) -- fuers Protokoll."""
    refused_old: dict[str, str] = field(default_factory=dict)
    """Alte Kennung -> Grund, wenn die Erweiterungen, die sie in `legacy_ids` nennen, abgelehnt wurden und es
    sie selbst nicht als Erweiterung gibt. Bei einem Update steht die Registry-Zeile noch unter dieser Kennung;
    sie nennt dann diesen Grund statt "nicht gefunden". Jede alte Kennung, die es nicht selbst gibt, steht
    entweder hier oder in `owner`."""


def resolve_legacy(found: list[DiscoveredExtension]) -> LegacyResolution:
    """Prueft die alten Kennungen (`legacy_ids`) aller gueltigen Funde.

    - Ist eine alte Kennung selbst noch als Erweiterung da, wird die NEUE abgelehnt; die alte bleibt, wie
      sie ist. So laeuft nichts doppelt, und keine Erweiterung uebernimmt den Stand einer anderen. Als "da"
      zaehlt ein Ordner mit diesem Namen (auch mit kaputtem Manifest, dann laedt keine der beiden) oder ein
      Paket mit lesbarem Manifest. Ein Paket mit kaputtem Manifest traegt als Fund den Distributionsnamen
      und blockiert nichts; laden kann es ohnehin nicht.
    - Nennen zwei Erweiterungen dieselbe alte Kennung, werden beide abgelehnt.
    - Hat eine Erweiterung `legacy_ids` UND eine Berechtigung aus `LEGACY_BLOCKING_PERMISSIONS`
      (heute `hosts.write`), wird sie abgelehnt.

    Ohne `legacy_ids` aendert sich nichts: `found` kommt unveraendert zurueck, `owner` ist leer."""
    present: dict[str, DiscoveredExtension] = {}
    for d in found:
        present.setdefault(d.id, d)
    claims: dict[str, set[str]] = {}
    for d in found:
        if d.ok:
            for old in d.manifest.legacy_ids:  # type: ignore[union-attr]
                claims.setdefault(old, set()).add(d.id)

    problems: dict[str, list[str]] = {}
    for d in found:
        if not d.ok or not d.manifest.legacy_ids:  # type: ignore[union-attr]
            continue
        for permission in d.manifest.permissions:  # type: ignore[union-attr]
            base = permission.split(":", 1)[0]
            what = LEGACY_BLOCKING_PERMISSIONS.get(base)
            if what is None:
                continue
            message = f"Erweiterungen, die {what}, können noch nicht umbenannt werden (legacy_ids zusammen mit {base})."
            if message not in problems.setdefault(d.id, []):
                problems[d.id].append(message)
    for old, claimants in sorted(claims.items()):
        if old in present:
            other = present[old]
            where = f"Ordner „{other.source_path.name}“" if other.source == "bundled" else "als Paket installiert"
            for ext_id in claimants:
                problems.setdefault(ext_id, []).append(
                    f"Die alte Kennung „{old}“ gehört noch zu einer installierten Erweiterung ({where}). "
                    "Bitte die alte Erweiterung entfernen."
                )
        elif len(claimants) > 1:
            others = ", ".join(f"„{c}“" for c in sorted(claimants))
            for ext_id in claimants:
                problems.setdefault(ext_id, []).append(
                    f"Die alte Kennung „{old}“ wird von mehreren Erweiterungen beansprucht ({others})."
                )

    resolved: list[DiscoveredExtension] = []
    refused: list[DiscoveredExtension] = []
    for d in found:
        if d.ok and d.id in problems:
            d = DiscoveredExtension(
                id=d.id, source=d.source, source_path=d.source_path, manifest=None,
                error=" ".join(problems[d.id]),
            )
            refused.append(d)
        resolved.append(d)
    owner = {old: next(iter(c)) for old, c in claims.items() if not c & problems.keys()}
    refused_old: dict[str, str] = {}
    for old, claimants in sorted(claims.items()):
        names = sorted(claimants & problems.keys())
        if old in present or not names:
            continue
        reasons = " ".join(dict.fromkeys(p for name in names for p in problems[name]))
        # Mehrere: der gemeinsame Grund nennt sie schon alle.
        refused_old[old] = f"Die neue Version heißt „{names[0]}“. {reasons}" if len(names) == 1 else reasons
    return LegacyResolution(found=resolved, owner=owner, refused=refused, refused_old=refused_old)
