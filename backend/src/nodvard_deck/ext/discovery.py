"""Findet Extensions per Verzeichnis-Scan und Entry-Points, ohne Code zu importieren (docs/02-EXTENSION-API.md §1).

Ein Manifest, das nicht parst, darf die anderen Extensions nicht aufhalten: Fehler werden je Kandidat gesammelt.
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from nodvard_sdk import ExtensionManifest, load_manifest

from .old_names import OLD_ENTRY_POINT_GROUP, OLD_GROUP_ERROR

DEFAULT_ENTRY_POINT_GROUPS = ("nodvard_deck.extensions",)


@dataclass
class DiscoveredExtension:
    """Ein Fund: entweder ein gültiges Manifest oder ein Fehler, nie beides."""

    id: str
    source: str  # "bundled" | "pip"
    source_path: Path
    """Bei `bundled` das Extension-Verzeichnis, bei `pip` der Distributions-Root."""
    manifest: ExtensionManifest | None
    error: str | None = None
    refused: bool = False
    """Gefunden, aber nie geladen; `error` ist der Grund für die Zeile der Erweiterung."""

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
    except Exception as exc:  # noqa: BLE001 - defekte Metadaten dürfen den Start nicht stoppen
        return [("?", DiscoveredExtension(id="?", source="pip", source_path=Path("."), manifest=None, error=str(exc)))]

    found: list[tuple[str, DiscoveredExtension]] = []
    for ep in entry_points:
        dist = ep.dist
        dist_name = dist.name if dist is not None else ep.name
        try:
            if dist is None:
                raise LookupError(f"Entry-Point '{ep.name}' hat keine zugehörige Distribution")
            # Das Manifest liegt als Paketdatei neben den Metadaten; so bleibt auch dieser Weg ohne Import.
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
    """Pip-installierte Extensions mit `extension.toml` im Distributions-Root.

    Funde mit gleicher Distribution und ID aus mehreren Gruppen zählen einmal, der erste gewinnt."""
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


def scan_old_entry_points(skip: Collection[str] = ()) -> list[DiscoveredExtension]:
    """Pakete, die sich nur unter der alten Gruppe anmelden, als abgelehnte Funde (außer den Kennungen in `skip`)."""
    return [
        DiscoveredExtension(
            id=item.id, source="pip", source_path=item.source_path, manifest=None, error=OLD_GROUP_ERROR, refused=True
        )
        for _, item in _scan_group(OLD_ENTRY_POINT_GROUP)
        if item.id not in skip
    ]


def discover_all(
    extensions_dir: Path, *, entry_point_group: str | Iterable[str] = DEFAULT_ENTRY_POINT_GROUPS
) -> list[DiscoveredExtension]:
    pip = scan_entry_points(entry_point_group)
    return [*scan_directory(extensions_dir), *pip, *scan_old_entry_points({d.id for d in pip})]


# Berechtigungen, die Daten unter der Kennung speichern und sie darüber wiederfinden (`hosts.write`:
# `Host.provider_ext_id`, `HostTag.managed_by_ext_id`): Nach einer Umbenennung gäbe es doppelte Server, und ein Rückweg
# aufs alte Image fände die neuen nicht. Die Grenze fällt weg, sobald der Kern die alten Kennungen dort mitliest.
# `secrets.*`, `notify.send`, `audit.write` halten die Kennung nur als Herkunft, nichts sucht danach.
LEGACY_BLOCKING_PERMISSIONS: dict[str, str] = {"hosts.write": "Server anlegen"}


@dataclass
class LegacyResolution:
    """Ergebnis von `resolve_legacy()`."""

    found: list[DiscoveredExtension]
    """Alle Funde in derselben Reihenfolge; abgelehnte stehen als Fund mit Fehler darin."""
    owner: dict[str, str]
    """Alte Kennung -> Kennung der Erweiterung, die sie ohne Konflikt in `legacy_ids` nennt."""
    refused: list[DiscoveredExtension]
    """Die wegen eines Konflikts abgelehnten Funde mit Fehlertext, fürs Protokoll."""
    refused_old: dict[str, str] = field(default_factory=dict)
    """Alte Kennung -> Grund, wenn alle Erweiterungen, die sie nennen, abgelehnt wurden und es sie selbst nicht gibt.

    Bei einem Update zeigt die Zeile der alten Kennung diesen Grund statt "nicht gefunden"."""
    awaiting_consent: set[tuple[str, str]] = field(default_factory=set)
    """Paare (Kennung, frühere Kennung), deren Übernahme erst ein Admin erlauben muss; die Erweiterung ist abgelehnt."""


def resolve_legacy(
    found: list[DiscoveredExtension], awaiting_consent: Collection[tuple[str, str]] = ()
) -> LegacyResolution:
    """Prüft die alten Kennungen (`legacy_ids`) aller gültigen Funde und lehnt Konflikte ab; Funde ohne sie bleiben.

    Ein Paar aus `awaiting_consent` (Kennung, frühere Kennung) zählt nicht als Anspruch, die Erweiterung wartet."""
    # Wartet ein Fund auf die Daten unter seiner eigenen Kennung, zählt er nicht als da: Ein Paket mit der früheren
    # Kennung eines mitgelieferten Moduls verdrängt dieses sonst.
    same = {ext_id for ext_id, old in awaiting_consent if ext_id == old}
    # Als "da" zählt ein Ordner mit dem Namen (auch mit kaputtem Manifest) oder ein Paket mit lesbarem Manifest;
    # ein Paket mit kaputtem Manifest trägt den Distributionsnamen und blockiert nichts.
    present: dict[str, DiscoveredExtension] = {}
    for d in found:
        if not (d.ok and d.id in same):
            present.setdefault(d.id, d)
    # Ist die alte noch installiert, gilt der Grund dafür; die Erlaubnis änderte daran nichts. Bei derselben Kennung
    # ist der Fund dort höchstens ein kaputtes Paket gleichen Namens, und das ersetzt keine Erlaubnis.
    waiting = {(ext_id, old) for ext_id, old in awaiting_consent if ext_id == old or old not in present}
    claims: dict[str, set[str]] = {}
    for d in found:
        if d.ok:
            for old in d.manifest.legacy_ids:  # type: ignore[union-attr]
                if (d.id, old) not in waiting:
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
        # Gibt es die alte Erweiterung noch, wird die neue abgelehnt, damit nichts doppelt läuft.
        if old in present:
            other = present[old]
            where = f"Ordner „{other.source_path.name}“" if other.source == "bundled" else "als Paket installiert"
            for ext_id in claimants:
                problems.setdefault(ext_id, []).append(
                    f"Die alte Kennung „{old}“ gehört noch zu einer installierten Erweiterung ({where}). "
                    "Bitte die alte Erweiterung entfernen."
                )
        elif len(claimants) > 1:  # derselbe alte Name von zwei Erweiterungen: beide ablehnen
            others = ", ".join(f"„{c}“" for c in sorted(claimants))
            for ext_id in claimants:
                problems.setdefault(ext_id, []).append(
                    f"Die alte Kennung „{old}“ wird von mehreren Erweiterungen beansprucht ({others})."
                )
    for ext_id, old in sorted(waiting):
        problems.setdefault(ext_id, []).append(
            f"Unter „{old}“ liegen noch gespeicherte Daten einer anderen Erweiterung. Die neue übernimmt sie erst, "
            "wenn ein Admin das unter Einstellungen → Erweiterungen erlaubt."
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
    for ext_id, old in sorted(waiting):
        if ext_id != old and old not in owner and old not in refused_old:
            refused_old[old] = (
                f"„{ext_id}“ soll diese Erweiterung ersetzen und ihre gespeicherten Daten übernehmen. Das muss erst "
                "ein Admin unter Einstellungen → Erweiterungen erlauben; bis dahin läuft keine der beiden."
            )
    return LegacyResolution(
        found=resolved, owner=owner, refused=refused, refused_old=refused_old, awaiting_consent=waiting,
    )
