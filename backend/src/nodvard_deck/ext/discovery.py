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
from dataclasses import dataclass
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
