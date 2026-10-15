"""Das Manifest `extension.toml`, gelesen ohne den Extension-Code zu importieren (ein Import wäre Codeausführung).

Eine deaktivierte oder defekte Extension darf nicht laufen, nur weil die Registry sie anzeigt.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

KNOWN_PERMISSIONS = frozenset(
    {
        "hosts.read",
        "hosts.write",
        "hosts.execute",
        "secrets.read",
        "secrets.write",
        "audit.read",
        "audit.write",
        "notify.send",
        "schedule.register",
        "files.provide",
        "settings.read",
        "net.outbound",
        # Erlaubt ctx.http, die TLS-Zertifikatsprüfung für einen einzelnen Aufruf abzuschalten
        # (insecure_tls=True) oder ein Zertifikat zu pinnen (pinned_sha256) -- getrennt von
        # net.outbound, damit ein Admin diese deutlich riskantere Fähigkeit separat
        # sieht und bestätigt (siehe HttpHandle).
        "net.outbound.insecure_tls",
        # Erlaubt `ctx.api.include_router(..., public=True)`: Routen, die OHNE Anmeldung
        # erreichbar sind (z. B. für Webhooks). Eigene Berechtigung, damit ein Admin sieht,
        # dass eine Erweiterung Adressen für jeden öffnet; der Kern schreibt die
        # öffentlichen Präfixe beim Einschalten ins Protokoll.
        "api.public",
        # Darf Vorschläge mit einer Dauerfreigabe (ActionRequest.standing_approval) einreichen.
        # Eigene Berechtigung, damit im Manifest sichtbar ist, welche Erweiterung etwas ohne Klick
        # starten kann; das Gate prüft die Freigabe trotzdem selbst.
        "actions.standing_approval",
    }
)


KNOWN_STAGES = ("stable", "beta")
"""Reifegrad einer Erweiterung (`stage` im Manifest): `stable` (Vorgabe) oder `beta` (funktioniert, ist aber
noch nicht so gründlich erprobt wie der Rest; die Oberfläche kennzeichnet sie)."""

ROOT_HELPER_DIR = "/usr/local/lib/nodvard-deck"
"""Ordner der Hilfsskripte auf den Servern. Jedes Modul mit root-Bedarf bringt höchstens ein eigenes Skript mit
(`root_helper` im Manifest), das unter `root_helper_path(ext_id)` liegt. Name und Ort bestimmt das SDK, nie das Modul:
Sonst könnte ein Modul root-eigenen Code an beliebiger Stelle ablegen oder das Skript eines anderen ersetzen."""

_HELPER_FILE_PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
ROOT_HELPER_MAX_VERSION = 9999


def root_helper_path(ext_id: str) -> str:
    """Ort des Hilfsskripts eines Moduls auf dem Server, z. B. `/usr/local/lib/nodvard-deck/shield-root`."""
    # fullmatch: `$` in `ID_RE` passt auch vor einem Zeilenumbruch am Ende, der Pfad landet aber in einer sudoers-Zeile.
    if not ID_RE.fullmatch(ext_id):
        raise ValueError(f"Ungueltige Extension-ID {ext_id!r}.")
    return f"{ROOT_HELPER_DIR}/{ext_id}-root"


class RootHelperSpec(BaseModel):
    """Das Hilfsskript eines Moduls für root-Befehle (`root_helper = { file = "...", version = 1 }` in `[extension]`).

    Ein festes POSIX-sh-Skript mit einem Unterbefehl je Aufgabe, das jedes Argument selbst prüft. Die Einrichtung legt
    es root-eigen unter `root_helper_path(id)` ab und erlaubt dem Server-Benutzer per sudo genau dieses Skript, statt
    `ALL`. `version` ist eine ganze Zahl und muss mit der Zeile `HELPER_VERSION=<n>` im Skript übereinstimmen, die
    `<skript> version` ausgibt; jede Änderung am Skript braucht eine höhere Zahl. Der Kern liest die Datei selbst (ohne
    den Code des Moduls zu importieren) und prüft sie; ist etwas nicht in Ordnung, gibt es für dieses Modul kein
    Hilfsskript und es gilt die bisherige Regel."""

    # Unbekannte Schlüssel sind ein Fehler: Als Abschnitt `[extension.root_helper]` geschrieben, landeten die folgenden
    # Zeilen (etwa `permissions`) sonst still hier statt im Manifest.
    model_config = ConfigDict(extra="forbid")

    file: str
    """Pfad relativ zum Ordner mit `extension.toml`, z. B. `root-helper/shield-root.sh`."""
    version: int = Field(ge=1, le=ROOT_HELPER_MAX_VERSION, strict=True)

    @field_validator("file")
    @classmethod
    def _relative_sh_file(cls, v: str) -> str:
        parts = v.split("/")
        if (
            not v.endswith(".sh")
            or "\\" in v
            or v.startswith("/")
            or any(part in ("", ".", "..") or not _HELPER_FILE_PART_RE.fullmatch(part) for part in parts)
        ):
            raise ValueError(
                f"Ungueltiger Pfad {v!r} fuer root_helper.file: relativ zum Ordner von extension.toml, "
                "ohne '..', nur A-Z, a-z, 0-9, '_', '.', '-', Endung .sh."
            )
        return v


KNOWN_ENABLE_TRIGGERS = frozenset({"host_credential"})
"""Anlässe, bei denen der Kern eine noch unberührte Erweiterung von selbst einschaltet
(`enable_on` im Manifest). `host_credential`: ein Server hat einen SSH-Zugang bekommen."""


_SETTINGS_SECTION_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_WIDGET_ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
# Eine Adresse unterhalb von /api/v1/ext/<id>: Abschnitte aus Buchstaben, Ziffern, `_`, `.`, `-` oder ein Platzhalter.
# `.` und `..` allein sind kein Abschnitt (siehe `_DOT_SEGMENTS`): Sie zeigten auf eine andere Adresse als die genannte.
_ROUTE_RE = re.compile(r"(?:/(?:[A-Za-z0-9_.-]+|\{[a-z_][a-z0-9_]*\}))+")
_DOT_SEGMENTS = frozenset({".", ".."})
_ROUTE_PARAM_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_SECRET_LABEL_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")


class AdoptSpec(BaseModel):
    """Ein Teil, den das Modul `from` an dieses abgibt (`[[extension.adopts]]`); der Kern übernimmt ihn beim ersten
    Entdecken dieses Moduls samt Ein/Aus, die Zeile von `from` bleibt für einen Rückweg unverändert."""

    # Unbekannte Schlüssel sind ein Fehler, sonst gingen Tippfehler still verloren.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    """Kennung des Moduls, das den Teil bisher hatte."""
    settings: str | None = None
    """Abschnitt der Einstellungen von `from`, der zu den eigenen Einstellungen wird."""
    widgets: dict[str, str] = Field(default_factory=dict)
    """Kacheln im Cockpit aller Nutzer: alte Kennung -> eigene."""
    routes: dict[str, str] = Field(default_factory=dict)
    """Alte Adresse -> eigene, je unterhalb von `/api/v1/ext/<id>`; die alte bleibt als veraltet erreichbar."""
    secrets: list[str] = Field(default_factory=list)
    """Tresor-Einträge von `from`, die zu eigenen werden (lesen muss das Modul sie über `secrets.read:` dürfen)."""

    @field_validator("from_")
    @classmethod
    def _valid_from(cls, v: str) -> str:
        if not ID_RE.fullmatch(v):
            raise ValueError(f"Ungueltige Kennung {v!r} in adopts.from.")
        return v

    @field_validator("settings")
    @classmethod
    def _valid_section(cls, v: str | None) -> str | None:
        if v is not None and not _SETTINGS_SECTION_RE.fullmatch(v):
            raise ValueError(f"Ungueltiger Abschnitt {v!r} in adopts.settings.")
        return v

    @field_validator("widgets")
    @classmethod
    def _valid_widgets(cls, v: dict[str, str]) -> dict[str, str]:
        for old, new in v.items():
            if not (_WIDGET_ID_RE.fullmatch(old) and _WIDGET_ID_RE.fullmatch(new)):
                raise ValueError(f"Ungueltige Kachel-Kennung in adopts.widgets: {old!r} -> {new!r}.")
        return v

    @field_validator("routes")
    @classmethod
    def _valid_routes(cls, v: dict[str, str]) -> dict[str, str]:
        for old, new in v.items():
            if not (_ROUTE_RE.fullmatch(old) and _ROUTE_RE.fullmatch(new)) or _DOT_SEGMENTS & {
                *old.split("/"), *new.split("/"),
            }:
                raise ValueError(f"Ungueltige Adresse in adopts.routes: {old!r} -> {new!r}.")
            if sorted(_ROUTE_PARAM_RE.findall(old)) != sorted(_ROUTE_PARAM_RE.findall(new)):
                raise ValueError(f"adopts.routes: {old!r} und {new!r} brauchen dieselben Platzhalter.")
        return v

    @field_validator("secrets")
    @classmethod
    def _valid_secrets(cls, v: list[str]) -> list[str]:
        for label in v:
            if not _SECRET_LABEL_RE.fullmatch(label):
                raise ValueError(f"Ungueltiger Tresor-Eintrag {label!r} in adopts.secrets.")
        if len(set(v)) != len(v):
            raise ValueError("adopts.secrets nennt einen Eintrag doppelt.")
        return v


class ExtensionManifest(BaseModel):
    id: str
    name: str
    version: str
    api_version: str
    entrypoint: str
    author: str | None = None
    description: str | None = None
    icon: str | None = None
    frontend: str | None = None
    requires: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    settings_schema: dict[str, Any] | None = None
    source: str = "local"
    enable_on: list[str] = Field(default_factory=list)
    """Wann der Kern die Erweiterung von selbst einschaltet (siehe `KNOWN_ENABLE_TRIGGERS`) --
    nur, solange niemand sie je bewusst ein- oder ausgeschaltet hat. Leer = nie automatisch."""
    category: str | None = None
    """Gruppe in der Modul-Auswahl des Einrichtungsassistenten, z. B. `servers`, `security`,
    `tools`, `connections`, `example`. Die Oberfläche kennt die Namen; ein unbekannter oder
    fehlender Wert landet unter „Weitere Module“."""
    sort_order: int = 100
    """Reihenfolge innerhalb der Gruppe (kleiner = weiter oben), bei Gleichstand nach Name."""
    stage: str = "stable"
    """Reifegrad: `stable` (Vorgabe, wenn das Feld fehlt) oder `beta`. Die Oberfläche zeigt bei `beta` ein
    ruhiges Abzeichen „Beta“ (Modul-Liste, Assistent, Menü, Seitenkopf). Ein unbekannter Wert gilt als `beta`."""
    # Grenze: Eine Erweiterung, die Server anlegt (`hosts.write`), kann nicht umbenannt werden, weil die Server die
    # Kennung ihres Anbieters tragen. Der Kern lehnt `legacy_ids` zusammen mit `hosts.write` beim Entdecken ab (Meldung
    # im Protokoll).
    legacy_ids: list[str] = Field(default_factory=list)
    """Frühere Kennungen dieser Erweiterung, z. B. `["alt-name"]` nach einer Umbenennung. Der Kern
    nimmt dann den gespeicherten Stand der alten Kennung weiter (Einstellungen, Zeitpläne, Daten)
    und erlaubt die alten Tabellen-Präfixe. Leer = nie umbenannt (der Normalfall)."""
    root_helper: RootHelperSpec | None = None
    """Hilfsskript für root-Befehle auf den Servern (siehe `RootHelperSpec`). Im Manifest als Inline-Tabelle in
    `[extension]` angeben, nicht als eigener Abschnitt `[extension.root_helper]`: Alles nach einem solchen Abschnitt
    gehörte sonst zu ihm. Fehlt das Feld, braucht das Modul keines."""

    adopts: list[AdoptSpec] = Field(default_factory=list)
    """Teile, die andere Module an dieses abgeben (siehe `AdoptSpec`). Leer = nichts übernommen (der Normalfall)."""

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(
                f"Ungueltige Extension-ID {v!r}: nur a-z, 0-9 und '-', Beginn mit "
                f"Buchstabe, 2-64 Zeichen."
            )
        return v

    @field_validator("permissions")
    @classmethod
    def _known_permissions(cls, v: list[str]) -> list[str]:
        for perm in v:
            base = perm.split(":", 1)[0]
            if base not in KNOWN_PERMISSIONS:
                raise ValueError(
                    f"Unbekannte Permission {perm!r}. Bekannt: "
                    f"{', '.join(sorted(KNOWN_PERMISSIONS))}"
                )
        return v

    @field_validator("enable_on")
    @classmethod
    def _known_enable_triggers(cls, v: list[str]) -> list[str]:
        for trigger in v:
            if trigger not in KNOWN_ENABLE_TRIGGERS:
                raise ValueError(
                    f"Unbekannter Anlass {trigger!r} in enable_on. Bekannt: "
                    f"{', '.join(sorted(KNOWN_ENABLE_TRIGGERS))}"
                )
        return v

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, v: str) -> str:
        """Ein unbekannter Wert ist kein Ladefehler, sondern gilt als `beta`: Eine Erweiterung für einen neueren Kern
        darf einen Reifegrad nennen, den dieser Kern noch nicht kennt, und soll trotzdem laden (das Feld steuert nur
        die Anzeige). `beta` statt `stable`, damit ein Tippfehler nicht still als erprobt gilt."""
        return v if v in KNOWN_STAGES else "beta"

    @field_validator("legacy_ids")
    @classmethod
    def _valid_legacy_ids(cls, v: list[str]) -> list[str]:
        for old in v:
            if not ID_RE.match(old):
                raise ValueError(
                    f"Ungueltige alte Kennung {old!r} in legacy_ids: nur a-z, 0-9 und '-', Beginn mit "
                    f"Buchstabe, 2-64 Zeichen."
                )
        if len(set(v)) != len(v):
            raise ValueError("legacy_ids nennt eine Kennung mehrfach.")
        return v

    @model_validator(mode="after")
    def _legacy_ids_differ_from_id(self) -> "ExtensionManifest":
        if self.id in self.legacy_ids:
            raise ValueError(f"legacy_ids darf die eigene Kennung {self.id!r} nicht enthalten.")
        return self

    @model_validator(mode="after")
    def _adopts_from_others(self) -> ExtensionManifest:
        sources = [a.from_ for a in self.adopts]
        for source in sources:
            if source == self.id or source in self.legacy_ids:
                raise ValueError(f"adopts.from darf nicht die eigene oder eine alte Kennung sein ({source!r}).")
        if len(set(sources)) != len(sources):
            raise ValueError("adopts nennt ein Modul mehrfach.")
        return self

    @property
    def table_prefix(self) -> str:
        """Zwingender Präfix für alle Tabellen dieser Extension."""
        return _table_prefix(self.id)

    @property
    def table_prefixes(self) -> tuple[str, ...]:
        """Alle erlaubten Tabellen-Präfixe: der eigene zuerst (Standard für neue Tabellen), danach
        die der alten Kennungen (`legacy_ids`) -- deren Tabellen behalten ihren Namen."""
        return (self.table_prefix, *(_table_prefix(old) for old in self.legacy_ids))

    @property
    def api_prefix(self) -> str:
        return f"/api/v1/ext/{self.id}"


def _table_prefix(ext_id: str) -> str:
    return f"ext_{ext_id.replace('-', '_')}_"


def load_manifest(path: Path) -> ExtensionManifest:
    """Liest `extension.toml`. Führt keinen Extension-Code aus."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    ext = dict(data.get("extension", {}))

    settings = data.get("settings") or {}
    schema_ref = settings.get("schema")
    if schema_ref:
        schema_path = path.parent / schema_ref
        if schema_path.exists():
            ext["settings_schema"] = json.loads(schema_path.read_text(encoding="utf-8"))

    return ExtensionManifest.model_validate(ext)
