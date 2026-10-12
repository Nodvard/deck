"""Konfiguration aus Umgebung und `.env`: Die Variablen heißen `NODVARD_DECK_<FELD>`, die alten `LATTICE_<FELD>` gelten als
Rückfall weiter (siehe `Settings.settings_customise_sources`)."""

from __future__ import annotations

import functools
import logging
import os
import zoneinfo
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from . import image_info

_log = logging.getLogger("nodvard_deck.config")

ENV_PREFIX = "NODVARD_DECK_"
"""Präfix der Umgebungsvariablen (technischer Name des Dashboards: nodvard_deck)."""

LEGACY_ENV_PREFIX = "LATTICE_"
"""Alter Name, gilt als Rückfall weiter: Bestehende `.env`-Dateien, Compose-Dateien und Container mit `LATTICE_*` laufen
unverändert. Ein neuer Name gewinnt immer."""

_warned_legacy: set[str] = set()
"""Alte Namen, über die schon gewarnt wurde (einmal je Prozess und Name)."""

LOCAL_TIMEZONE = "Europe/Berlin"
"""Rückfall für die Ortszeit, wenn weder Einstellung noch Umgebung eine nennen. Die eingestellte Zone
(`core.timezone.get_timezone`) gilt für alles, was der Nutzer als Uhrzeit einstellt (Jobs, Wartungsfenster), damit "03:00"
im Zeitplan-Wähler überall dieselbe Uhrzeit meint. Auch die Spaltenvorgabe von `Job.timezone` (Bestands-Jobs tragen sie)."""


@functools.lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    """Alle Zonennamen, die `zoneinfo` kennt (Systemdaten oder das Paket `tzdata`)."""
    return frozenset(zoneinfo.available_timezones())


def _clean_zone(raw: str | None) -> str | None:
    """Gültiger Zonenname oder `None`; `TZ` darf mit ":" beginnen (POSIX-Schreibweise)."""
    if not raw:
        return None
    name = raw.strip().removeprefix(":")
    return name if name in known_timezones() else None


class _LegacyNameCollector:
    """Sammelt die Namen (nie Werte) alter Variablen aus beiden Rückfall-Quellen."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def add(self, names: list[str]) -> None:
        self.names.extend(n for n in names if n not in self.names)


class _LegacyEnvSource(EnvSettingsSource):
    """Liest `LATTICE_*` aus der Prozess-Umgebung und merkt sich, welche Namen vorkamen."""

    def __init__(self, settings_cls: type[BaseSettings], collector: _LegacyNameCollector) -> None:
        super().__init__(settings_cls, env_prefix=LEGACY_ENV_PREFIX)
        self._collector = collector

    def __call__(self) -> dict[str, Any]:
        self._collector.add(_present_names(self))
        return super().__call__()


class _LegacyDotEnvSource(DotEnvSettingsSource):
    """Wie `_LegacyEnvSource`, aber aus der `.env`-Datei."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        collector: _LegacyNameCollector,
        like: DotEnvSettingsSource,
    ) -> None:
        # Dieselbe Datei wie die Neu-Quelle (auch wenn sie per `Settings(_env_file=...)` kommt).
        super().__init__(
            settings_cls,
            env_file=like.env_file,
            env_file_encoding=like.env_file_encoding,
            env_prefix=LEGACY_ENV_PREFIX,
        )
        self._collector = collector

    def __call__(self) -> dict[str, Any]:
        self._collector.add(_present_names(self))
        return super().__call__()


def _present_names(source: EnvSettingsSource) -> list[str]:
    """Namen (in der Schreibweise `LATTICE_<FELD>`) der Felder, die in der Quelle stehen."""
    return [
        f"{LEGACY_ENV_PREFIX}{field}".upper()
        for field in Settings.model_fields
        if f"{LEGACY_ENV_PREFIX}{field}".lower() in source.env_vars
    ]


_EMPTY_MEANS_UNSET = ("build", "image", "ssh_confirm_new_host_keys", "trusted_proxies")
"""Felder, bei denen eine leere Variable als nicht gesetzt gilt: Dann greift der nächste Name (z. B. `LATTICE_BUILD` hinter
einem leeren `NODVARD_DECK_BUILD`) und danach die Datei im Image (`image_info`). Bei `ssh_confirm_new_host_keys` und
`trusted_proxies` heißt das: keine Übersteuerung, es gilt die gespeicherte Einstellung."""


class _SkipEmpty(PydanticBaseSettingsSource):
    """Hülle um eine Quelle: lässt leere Werte der Felder aus `_EMPTY_MEANS_UNSET` weg."""

    def __init__(self, inner: PydanticBaseSettingsSource) -> None:
        super().__init__(inner.settings_cls)
        self._inner = inner

    def _set_current_state(self, state: dict[str, Any]) -> None:
        super()._set_current_state(state)
        self._inner._set_current_state(state)

    def _set_settings_sources_data(self, states: dict[str, dict[str, Any]]) -> None:
        super()._set_settings_sources_data(states)
        self._inner._set_settings_sources_data(states)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # pragma: no cover
        return self._inner.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        data = self._inner()
        for name in _EMPTY_MEANS_UNSET:
            value = data.get(name)
            if isinstance(value, str) and not value.strip():
                del data[name]
        return data


class _LegacyNameReporter(PydanticBaseSettingsSource):
    """Steht als letzte Quelle und liefert nichts; meldet nur einmalig die alten Namen. Die Meldung enthält ausschließlich
    Variablennamen, Werte (z. B. `JWT_SECRET`) dürfen nie im Log landen."""

    def __init__(self, settings_cls: type[BaseSettings], collector: _LegacyNameCollector) -> None:
        super().__init__(settings_cls)
        self._collector = collector

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # pragma: no cover
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        new = [n for n in self._collector.names if n not in _warned_legacy]
        if new:
            _warned_legacy.update(new)
            _log.warning(
                "Alte Umgebungsvariablen gefunden (gelten als Rueckfall weiter, bitte auf %s<NAME> "
                "umbenennen): %s",
                ENV_PREFIX,
                ", ".join(sorted(new)),
            )
        return {}


class Settings(BaseSettings):
    """Einstellungen aus der Umgebung. Das Branding steht bewusst nicht hier, sondern als Laufzeitwert in der Tabelle `settings`."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Reihenfolge (vorne gewinnt): init, Umgebung neu, Umgebung alt, `.env` neu, `.env` alt, Secrets. Die alten Namen
        hängen sich je Quelle dahinter, damit die Prozess-Umgebung immer vor der `.env` gewinnt und in jeder Quelle der neue
        Name vor dem alten."""
        assert isinstance(dotenv_settings, DotEnvSettingsSource)
        collector = _LegacyNameCollector()
        return (
            init_settings,
            _SkipEmpty(env_settings),
            _SkipEmpty(_LegacyEnvSource(settings_cls, collector)),
            _SkipEmpty(dotenv_settings),
            _SkipEmpty(_LegacyDotEnvSource(settings_cls, collector, dotenv_settings)),
            file_secret_settings,
            _LegacyNameReporter(settings_cls, collector),
        )

    env: str = "dev"
    log_json: bool = False

    data_dir: Path = Path("./data")
    """Alles Zustandsbehaftete lebt hier: Datenbank, Master-Key, Extension-Daten, Protokolle der Läufe."""

    updater_dir: Path = Path("/app/updater")
    """Kanal zum Update-Helfer (`core.updater_client`): ein eigenes Volume, das der Helfer einrichtet und das root gehört.
    Bewusst ein fester Pfad ohne Eintrag im Image: Fehlt der Ordner (Entwicklung, Tests, Installation ohne Helfer), gibt es
    keinen Helfer. Nur für Tests ändern."""

    database_url: str = "sqlite+aiosqlite:///./data/lattice.db"
    """SQLite ist die Vorgabe; `postgresql+asyncpg://...` geht ohne Codeänderung, solange die Regeln aus
    docs/00-DECISIONS.md (Datenbank) eingehalten werden."""

    master_key_path: Path = Path("./data/master.key")
    """Fernet-Schlüssel für den Secrets-Vault. Wird bei der Erstinbetriebnahme erzeugt (0600), nie in der Datenbank abgelegt."""

    vault_keyring_path: Path = Path("./data/vault_keyring.json")
    """Versionierte Schlüssel für Rotation ohne Big-Bang. Version 1 übernimmt beim ersten Zugriff unverändert den Wert aus
    `master_key_path`, vorhandene verschlüsselte Secrets bleiben so ohne Neuverschlüsselung lesbar (`load_keyring()` in
    core/vault.py)."""

    audit_retention_days: int = 90
    """Rückfall für den reservierten Schlüssel `audit.retention_days` (docs/03-DATA-MODEL.md §9): gilt nur, solange unter
    Einstellungen, System keine Aufbewahrungsdauer gesetzt ist (`services.audit.effective_retention_days`)."""

    timezone: str | None = None
    """NODVARD_DECK_TIMEZONE: Vorgabe für die Zeitzone aller Zeitpläne und Wartungsfenster (z. B. `Europe/Berlin`). Leer =
    `TZ` der Umgebung, sonst Europe/Berlin (`default_timezone`). Die Einstellung `system.timezone` gewinnt über diese Vorgabe."""

    jwt_secret: str | None = None
    """Überschreibt `jwt_secret_path`, wenn gesetzt, für Docker/K8s-Secret-Mounts (NODVARD_DECK_JWT_SECRET). Im Normalfall
    leer, dann greift die Datei."""

    jwt_secret_path: Path = Path("./data/jwt_secret.key")
    """Wie `master_key_path`: wird beim ersten Start erzeugt, wenn weder diese Datei noch `jwt_secret` existiert. Bewusst
    ein anderer Schlüssel als der Vault-Master-Key: JWT-Signatur und Secret-Verschlüsselung sind zwei unabhängige Zwecke,
    ein kompromittierter Schlüssel soll nicht den anderen mitkompromittieren."""

    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_days: int = 30
    mfa_token_ttl_seconds: int = 5 * 60
    """Kurzlebiges Zwischentoken zwischen Passwort-Login und TOTP-Bestätigung (POST /auth/login -> 202 mfa_required ->
    POST /auth/mfa)."""

    cookie_secure: bool | None = None
    """`Secure`-Flag am Refresh-Cookie. `None` (Standard) = automatisch: nur bei HTTPS, denn ein Browser verwirft ein
    Secure-Cookie über Klartext-HTTP still (z. B. env=prod unter http://...:8080 im Heimnetz). Hinter einem TLS-Proxy zählt
    dessen X-Forwarded-Proto, sobald er unter `system.trusted_proxies` steht; NODVARD_DECK_COOKIE_SECURE=1/0 erzwingt es."""

    setup_code: str | None = None
    """LATTICE_SETUP_CODE: Einrichtungscode für die Erstinbetriebnahme vorgeben (mindestens 8 Zeichen). Leer = das
    Dashboard würfelt einen und schreibt ihn bei jedem Start ins Protokoll, bis das erste Konto angelegt ist
    (core/setup_code.py)."""

    demo_mode: bool = False
    """NODVARD_DECK_DEMO_MODE=1 speist Fake-Daten statt echter Connectoren."""

    api_docs: bool | None = None
    """NODVARD_DECK_API_DOCS: ob die API-Doku (`/docs`, `/redoc`, `/openapi.json`) ohne Anmeldung ausgeliefert wird. Leer =
    nur mit `env=dev`, `1`/`0` erzwingt es. Angemeldete Admins (`system.read`) bekommen sie immer über
    `GET /api/v1/system/openapi.json`. Der Wert wird einmal beim Erzeugen der App gelesen (`main.create_app`)."""

    build: str | None = None
    """NODVARD_DECK_BUILD: Kennung des laufenden Builds. Ohne (nicht leere) Variable gilt die Version aus der Datei im Image
    (`image_info`, z. B. `0.6.0` oder `0.6.0-rc1`), sonst `None`. Angezeigt in `GET /app/changelog`, `GET /system/info` und im
    Manifest einer Sicherung. Update-Suche und Kopien vor Updates nehmen beim offiziellen Image die Version aus der Datei,
    nur ohne sie diese Angabe, und nur wenn sie gültig ist (`core.updates.running_version`)."""

    image: str | None = None
    """NODVARD_DECK_IMAGE: aus welchem Image der Container stammt, ohne Tag. Ohne (nicht leere) Variable gilt die Angabe aus
    der Datei im Image (`image_info`), sonst `None` = selbst gebaut. Nur daran erkennt die Update-Suche
    (`core.updates.is_official_image`) das offizielle Image."""

    image_info_path: Path = image_info.PATH
    """Datei mit Herkunft und Version des Images (`image_info`). Nur für Tests ändern."""

    @field_validator("build", "image", mode="before")
    @classmethod
    def _strip_or_none(cls, value: Any) -> Any:
        """Leerraum am Rand weg, leer = nicht gesetzt (nie `""`)."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _image_info_fallback(self) -> Settings:
        """`image` und `build` ohne Variable aus der Datei im Image (fehlt sie oder ist sie kaputt: `None`)."""
        if self.image is None or self.build is None:
            info = image_info.read(self.image_info_path)
            if self.image is None:
                self.image = info.get("image")
            if self.build is None:
                self.build = info.get("version")
        return self

    extensions_dir: Path = Path("./extensions")
    """Wurzel für den Scan nach mitgelieferten und lokalen Extensions (docs/02 §1); alle Befehle laufen aus dem Repository-Root,
    der Pfad ist relativ dazu. Pip-installierte Extensions werden zusätzlich über Entry-Points entdeckt (ext/discovery.py)."""

    skip_pre_migrate_backup: bool = False
    """NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1: Notausgang. Ohne Wert legt `nodvard_deck.boot` vor jeder Migration eine Kopie
    der Datenbank an (`<Datenordner>/backups/vor-update/`) und migriert nie ohne sie. Mit dem Schalter entfällt die Kopie,
    z. B. wenn der Platz nie reicht; eine gescheiterte Migration hat dann keinen Rückweg."""

    max_body_bytes: int = 1024**2
    """NODVARD_DECK_MAX_BODY_BYTES: größter Anfragekörper einer normalen Anfrage (Standard 1 MiB). Darüber antwortet der Server
    sofort mit 413, ohne den Körper zu lesen, auch ohne Anmeldung. Routen mit bewusst großen Uploads (Sicherung, Dateien,
    Logo, Dokumente, Bilder) haben eigene, höhere Grenzen."""

    files_max_upload_bytes: int = 0
    """NODVARD_DECK_FILES_MAX_UPLOAD_BYTES: größte Datei im Dateibrowser. 0 (Standard) = keine Grenze: Der Upload geht als
    Strom direkt an die Quelle (SFTP, WebDAV) und landet nie ganz im Speicher; ISO- und VM-Images sind oft größer als 4 GB.
    Ein Wert über 0 wird am Kopf der Anfrage geprüft, bevor die Quelle das Ziel öffnet."""
    # Bei Uploads ohne Länge (chunked) zählt nur die Middleware mit, mindestens bis `max_body_bytes`; die Quelle hat das Ziel
    # beim Abbruch dann schon geöffnet (SFTP schreibt in eine Temp-Datei und lässt das Ziel unberührt).

    restore_max_upload_bytes: int = 4 * 1024**3
    """NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES: größte Sicherungsdatei beim Wiederherstellen (Standard 4 GiB), schon am Kopf
    der Anfrage geprüft, nicht erst nach dem Hochladen."""

    restore_max_unpacked_bytes: int = 16 * 1024**3
    """NODVARD_DECK_RESTORE_MAX_UNPACKED_BYTES: wie groß eine Sicherung entpackt werden darf (Standard 16 GiB, nie mehr als
    der freie Platz). Schutz vor Bomben."""

    restore_max_entries: int = 200_000
    """NODVARD_DECK_RESTORE_MAX_ENTRIES: wie viele Dateien und Ordner eine Sicherung höchstens enthalten darf."""

    ext_data_dir: Path = Path("./data/ext")
    """Wurzel für `ExtensionContext.data_dir`: je Extension ein Unterverzeichnis `<ext_data_dir>/<ext_id>/`
    (docs/02-EXTENSION-API.md §2)."""

    metrics_interval_s: int = 30
    """Takt des Verlaufs-Sammlers (core/metrics_history.py) für Hosts ohne eigene Historie beim Anbieter (z. B. den Pi per
    SSH). 0 schaltet ihn ab."""

    metrics_raw_retention_h: int = 48
    metrics_rollup_retention_d: int = 35

    ssh_connect_timeout_s: float = 10.0
    """Zeitlimit für den Verbindungsaufbau des gemeinsamen asyncssh-Layers (Terminal, Skripte, KI-Remediation)."""

    ssh_confirm_new_host_keys: bool | None = None
    """NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS: Übersteuerung für alle Server. `true`: Neue Server-Schlüssel werden nicht still
    gemerkt (TOFU, core/ssh.py), die Verbindung scheitert mit `HostKeyUnknown`, bis jemand den Fingerabdruck bestätigt hat;
    `false`: still merken. Nicht gesetzt gilt die gespeicherte Einstellung `ssh.confirm_new_host_keys` (neue Installationen
    an, bestehende aus; `services.hosts.host_key_confirmation_required`)."""
    # Bereits gemerkte Schlüssel sind nie betroffen; "Schlüssel vergessen" verlangt immer eine Bestätigung, egal was hier steht.

    trusted_proxies: str | None = None
    """NODVARD_DECK_TRUSTED_PROXIES: Adressen oder Netze der Reverse-Proxys, deren `X-Forwarded-For` und `X-Forwarded-Proto`
    gelten (kommagetrennt, z. B. `192.0.2.10, 198.51.100.0/24`). Gesetzt gewinnt sie über die Einstellung
    `system.trusted_proxies` (dann nicht änderbar); ungültige Einträge werden mit Warnung übersprungen (`core.trusted_proxies`)."""

    terminal_session_ttl_s: float = 30.0
    """Wie lange ein per `POST /terminal/sessions` erzeugtes, noch nicht per WebSocket eingelöstes Session-Ticket gültig
    bleibt (core/terminal_sessions.py)."""

    terminal_idle_timeout_s: float = 30 * 60
    """Eine Terminal-Sitzung ohne jede Eingabe und Ausgabe wird nach so vielen Sekunden beendet (`api/v1/terminal.py`).
    Solange ein Befehl noch Text liefert, läuft sie weiter. 0 = aus."""

    terminal_recheck_interval_s: float = 15.0
    """Wie oft eine offene Terminal-Sitzung prüfen lässt, ob Konto, Berechtigung und Anmeldung noch gelten
    (`services/session_guard.py`). Wer deaktiviert, abgemeldet oder ausgesperrt wird, verliert die Shell spätestens nach so
    vielen Sekunden."""

    @property
    def api_docs_public(self) -> bool:
        """Soll die API-Doku ohne Anmeldung erreichbar sein? Ein ausdrücklicher Wert gewinnt, sonst `env == "dev"`."""
        if self.api_docs is not None:
            return self.api_docs
        return self.env.strip().lower() == "dev"

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create_jwt_secret(self) -> str:
        """`jwt_secret` (Env-Var) gewinnt, wenn gesetzt; sonst Datei laden oder erzeugen. Wie
        `core.vault.load_or_create_master_key`, aber absichtlich nicht dieselbe Funktion (Text-Secret statt Fernet-Key)."""
        if self.jwt_secret:
            return self.jwt_secret

        self.jwt_secret_path.parent.mkdir(parents=True, exist_ok=True)
        if self.jwt_secret_path.exists():
            return self.jwt_secret_path.read_text(encoding="utf-8").strip()

        import secrets as _secrets

        value = _secrets.token_urlsafe(48)
        self.jwt_secret_path.write_text(value, encoding="utf-8")
        try:
            import os as _os

            _os.chmod(self.jwt_secret_path, 0o600)
        except OSError:
            pass
        return value


_settings: Settings | None = None


def default_timezone(settings: Settings | None = None) -> str:
    """Vorgabe der Zeitzone, solange `system.timezone` nicht gesetzt ist: `NODVARD_DECK_TIMEZONE` (alt `LATTICE_TIMEZONE`),
    sonst `TZ`, sonst `LOCAL_TIMEZONE`. Ein ungültiger Name wird mit Warnung übersprungen, nie zum Absturz."""
    settings = settings or get_settings()
    for source, raw in (("NODVARD_DECK_TIMEZONE", settings.timezone), ("TZ", os.environ.get("TZ"))):
        if not raw:
            continue
        name = _clean_zone(raw)
        if name:
            return name
        _log.warning("%s=%r ist keine bekannte Zeitzone und wird ignoriert.", source, raw)
    return LOCAL_TIMEZONE


def get_settings() -> Settings:
    """Gecachte Singleton-Instanz; Tests setzen dafür das Modul-Attribut `_settings` direkt zurück (siehe tests/conftest.py)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
