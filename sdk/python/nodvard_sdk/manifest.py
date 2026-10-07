"""Manifest — `extension.toml`.

Wird gelesen, **ohne** den Extension-Code zu importieren. Ein Import ist Codeausführung;
eine deaktivierte oder defekte Extension darf nicht laufen, nur weil sie in der Registry
angezeigt wird. Siehe docs/01-ARCHITECTURE.md §3.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

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
        # WP-8-Blocker-Nachtrag: erlaubt ctx.http, TLS-Zertifikatspruefung fuer einen
        # einzelnen Aufruf abzuschalten (insecure_tls=True) -- getrennt von
        # net.outbound, damit ein Admin diese deutlich riskantere Faehigkeit separat
        # sieht und bestaetigt, siehe ext/context.py HttpHandle-Docstring.
        "net.outbound.insecure_tls",
        # Erlaubt `ctx.api.include_router(..., public=True)`: Routen, die OHNE Anmeldung
        # erreichbar sind (z. B. fuer Webhooks). Eigene Berechtigung, damit ein Admin sieht,
        # dass eine Erweiterung Adressen fuer jeden oeffnet; der Kern schreibt die
        # oeffentlichen Praefixe beim Einschalten ins Protokoll.
        "api.public",
        # Darf Vorschlaege mit einer Dauerfreigabe (ActionRequest.standing_approval) einreichen.
        # Eigene Berechtigung, damit im Manifest sichtbar ist, welche Erweiterung etwas ohne Klick
        # starten kann; das Gate prueft die Freigabe trotzdem selbst.
        "actions.standing_approval",
    }
)


KNOWN_ENABLE_TRIGGERS = frozenset({"host_credential"})
"""Anlaesse, bei denen der Kern eine noch unberuehrte Erweiterung von selbst einschaltet
(`enable_on` im Manifest). `host_credential`: ein Server hat einen SSH-Zugang bekommen."""


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
    `tools`, `connections`, `example`. Die Oberflaeche kennt die Namen; ein unbekannter oder
    fehlender Wert landet unter „Weitere Module“."""
    sort_order: int = 100
    """Reihenfolge innerhalb der Gruppe (kleiner = weiter oben), bei Gleichstand nach Name."""
    legacy_ids: list[str] = Field(default_factory=list)
    """Fruehere Kennungen dieser Erweiterung, z. B. `["alt-name"]` nach einer Umbenennung. Der Kern
    nimmt dann den gespeicherten Stand der alten Kennung weiter (Einstellungen, Zeitplaene, Daten)
    und erlaubt die alten Tabellen-Praefixe. Leer = nie umbenannt (der Normalfall).

    Grenze: Eine Erweiterung, die Server anlegt (`hosts.write`), kann nicht umbenannt werden -- die Server
    tragen die Kennung ihres Anbieters. Der Kern lehnt `legacy_ids` zusammen mit `hosts.write` beim
    Entdecken ab (Meldung im Protokoll)."""

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

    @property
    def table_prefix(self) -> str:
        """Zwingender Praefix fuer alle Tabellen dieser Extension."""
        return _table_prefix(self.id)

    @property
    def table_prefixes(self) -> tuple[str, ...]:
        """Alle erlaubten Tabellen-Praefixe: der eigene zuerst (Standard fuer neue Tabellen), danach
        die der alten Kennungen (`legacy_ids`) -- deren Tabellen behalten ihren Namen."""
        return (self.table_prefix, *(_table_prefix(old) for old in self.legacy_ids))

    @property
    def api_prefix(self) -> str:
        return f"/api/v1/ext/{self.id}"


def _table_prefix(ext_id: str) -> str:
    return f"ext_{ext_id.replace('-', '_')}_"


def load_manifest(path: Path) -> ExtensionManifest:
    """Liest `extension.toml`. Fuehrt keinen Extension-Code aus."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    ext = dict(data.get("extension", {}))

    settings = data.get("settings") or {}
    schema_ref = settings.get("schema")
    if schema_ref:
        schema_path = path.parent / schema_ref
        if schema_path.exists():
            ext["settings_schema"] = json.loads(schema_path.read_text(encoding="utf-8"))

    return ExtensionManifest.model_validate(ext)
