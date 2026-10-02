"""Konfiguration aus Umgebung/.env.

Siehe docs/00-DECISIONS.md D-02 (Datenbank), D-06 (Vault), D-11 (Beobachtbarkeit).
Umgebungsvariablen heissen `NODVARD_DECK_<FELD>`; die alten `LATTICE_<FELD>` gelten als
Rueckfall weiter (siehe `Settings.settings_customise_sources`).
Branding steht bewusst NICHT hier — das ist ein Laufzeitwert in der Tabelle `settings`,
siehe branding.py und docs/01-ARCHITECTURE.md §6.
"""

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
"""Neues Praefix der Umgebungsvariablen (technischer Name des Dashboards: nodvard_deck)."""

LEGACY_ENV_PREFIX = "LATTICE_"
"""Alter Name, gilt als Rueckfall weiter: Bestehende `.env`-Dateien, Compose-Dateien und
Container mit `LATTICE_*` laufen unveraendert. Ein neuer Name gewinnt immer."""

_warned_legacy: set[str] = set()
"""Alte Namen, ueber die schon gewarnt wurde (einmal je Prozess und Name)."""

LOCAL_TIMEZONE = "Europe/Berlin"
"""Rueckfall fuer die Ortszeit, wenn weder Einstellung noch Umgebung eine nennen. Die
EINGESTELLTE Zone (`system.timezone`, `core.timezone.get_timezone`) gilt fuer alles, was der
Nutzer als Uhrzeit einstellt: Jobs (`Job.timezone`) und Wartungsfenster
(`core.maintenance`). EINE Quelle, damit ein "03:00" im Zeitplan-Waehler ueberall dieselbe
Uhrzeit meint. Die Spaltenvorgabe von `Job.timezone` bleibt diese Konstante
(Bestands-Jobs tragen sie)."""


@functools.lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    """Alle Zonennamen, die `zoneinfo` kennt (Systemdaten oder das Paket `tzdata`)."""
    return frozenset(zoneinfo.available_timezones())


def _clean_zone(raw: str | None) -> str | None:
    """Gueltiger Zonenname oder `None`. `TZ` darf mit ":" beginnen (POSIX-Schreibweise)."""
    if not raw:
        return None
    name = raw.strip().removeprefix(":")
    return name if name in known_timezones() else None


class _LegacyNameCollector:
    """Sammelt die NAMEN (nie Werte) alter Variablen aus beiden Rueckfall-Quellen."""

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


_EMPTY_MEANS_UNSET = ("build", "image", "ssh_confirm_new_host_keys")
"""Felder, bei denen eine leere Variable als nicht gesetzt gilt: Dann greift der naechste Name (z. B. `LATTICE_BUILD`
hinter einem leeren `NODVARD_DECK_BUILD`) und danach die Datei im Image (`image_info`). Bei
`ssh_confirm_new_host_keys` heisst das: keine Uebersteuerung, es gilt die gespeicherte Einstellung."""


class _SkipEmpty(PydanticBaseSettingsSource):
    """Huelle um eine Quelle: laesst leere Werte der Felder aus `_EMPTY_MEANS_UNSET` weg."""

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
    """Steht als letzte Quelle und liefert nichts; meldet nur einmalig die alten Namen.

    Die Meldung enthaelt ausschliesslich Variablennamen. Werte (z. B. `JWT_SECRET`)
    duerfen nie im Log landen."""

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
        """Reihenfolge (vorne gewinnt): init, Umgebung neu, Umgebung alt, `.env` neu,
        `.env` alt, Secrets. `env_settings`/`dotenv_settings` sind die Quellen fuer das neue
        Praefix (`env_prefix`). Die alten `LATTICE_*`-Namen haengen sich je Quelle dahinter,
        damit die Prozess-Umgebung immer vor der `.env` gewinnt und innerhalb jeder Quelle
        der neue Name vor dem alten. Bei `build`/`image` zaehlt ein leerer Wert als nicht gesetzt
        (`_SkipEmpty`); was dann noch fehlt, kommt aus der Datei im Image (`_image_info_fallback`)."""
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
    """Alles Zustandsbehaftete lebt hier: DB, Master-Key, Extension-Daten, Run-Logs."""

    database_url: str = "sqlite+aiosqlite:///./data/lattice.db"
    """SQLite ist der Default (D-02). postgresql+asyncpg://... funktioniert ohne
    Codeaenderung, solange die Disziplin-Regeln aus D-02 eingehalten werden."""

    master_key_path: Path = Path("./data/master.key")
    """Fernet-Schluessel fuer den Secrets-Vault (D-06). Wird bei Erstinbetriebnahme
    erzeugt (0600), niemals in der Datenbank abgelegt."""

    vault_keyring_path: Path = Path("./data/vault_keyring.json")
    """Versionierte Schluessel fuer Rotation ohne Big-Bang (D-06).
    Version 1 uebernimmt beim allerersten Zugriff unveraendert den Wert aus
    `master_key_path` -- vor WP-2 verschluesselte Secrets (z. B. TOTP aus WP-1) bleiben
    dadurch ohne Re-Encryption lesbar. Siehe core/vault.py `load_keyring()`."""

    audit_retention_days: int = 90
    """Rueckfall fuer den reservierten Schluessel `audit.retention_days`
    (docs/03-DATA-MODEL.md §9). Gilt nur, solange unter Einstellungen, System keine
    Aufbewahrungsdauer gesetzt ist (`services.audit.effective_retention_days`)."""

    timezone: str | None = None
    """NODVARD_DECK_TIMEZONE: Vorgabe fuer die Zeitzone aller Zeitplaene und
    Wartungsfenster (z. B. `Europe/Berlin`). Leer = `TZ` der Umgebung, sonst Europe/Berlin
    (`default_timezone`). Die Einstellung `system.timezone` gewinnt ueber diese Vorgabe."""

    jwt_secret: str | None = None
    """Ueberschreibt jwt_secret_path, wenn gesetzt -- fuer Docker/K8s-Secret-Mounts
    (NODVARD_DECK_JWT_SECRET als Env-Var). Im Normalfall leer; dann greift die Datei."""

    jwt_secret_path: Path = Path("./data/jwt_secret.key")
    """Wie master_key_path: wird beim ersten Start erzeugt, wenn weder diese Datei
    noch `jwt_secret` existiert. Bewusst eine ANDERE Datei/ein anderer Schluessel als
    der Vault-Master-Key (D-06) -- JWT-Signatur und Secret-Verschluesselung sind zwei
    unabhaengige Zwecke, ein kompromittierter Schluessel soll nicht automatisch den
    anderen mitkompromittieren."""

    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_days: int = 30
    mfa_token_ttl_seconds: int = 5 * 60
    """Kurzlebiges Zwischentoken zwischen Passwort-Login und TOTP-Bestaetigung
    (POST /auth/login -> 202 mfa_required -> POST /auth/mfa)."""

    cookie_secure: bool | None = None
    """`Secure`-Flag am Refresh-Cookie (D-07). `None` (Standard) = automatisch: gesetzt,
    wenn die Anfrage ueber HTTPS kam, sonst nicht -- ein Browser verwirft ein
    Secure-Cookie ueber Klartext-HTTP still (z. B. env=prod unter
    http://...:8080 im Heimnetz). NODVARD_DECK_COOKIE_SECURE=1/0 erzwingt es, z. B. hinter einem
    TLS-Proxy, dessen X-Forwarded-Proto uvicorn nicht vertraut."""

    setup_code: str | None = None
    """LATTICE_SETUP_CODE: Einrichtungscode fuer die Erstinbetriebnahme vorgeben (mindestens
    8 Zeichen). Leer = Nodvard Deck wuerfelt einen und schreibt ihn bei jedem Start ins Protokoll,
    bis das erste Konto angelegt ist (core/setup_code.py)."""

    demo_mode: bool = False
    """NODVARD_DECK_DEMO_MODE=1 speist Fake-Daten statt echter Connectoren (D-10)."""

    api_docs: bool | None = None
    """NODVARD_DECK_API_DOCS: ob die API-Doku (`/docs`, `/redoc`, `/openapi.json`) OHNE Anmeldung
    ausgeliefert wird. Leer (Standard) = nur im Entwicklungsmodus (`env=dev`), in einer Installation
    mit `env=prod` (Docker-Image, Compose-Datei) also nicht; `1`/`0` erzwingt es. Unabhaengig davon
    liefert `GET /api/v1/system/openapi.json` das API-Dokument an angemeldete Admins (Recht
    `system.read`). Gelesen wird der Wert einmal beim Erzeugen der App (`main.create_app`)."""

    build: str | None = None
    """NODVARD_DECK_BUILD: Kennung des laufenden Builds. Ohne (nicht leere) Variable gilt die Version aus der
    Datei im Image (`image_info`, beim Release-Image z. B. `0.6.0` oder `0.6.0-rc1`), sonst `None`.
    Angezeigt unter "Über Nodvard Deck" (`GET /app/changelog`, ohne Wert zeigt die Oberflaeche ihren
    eigenen Bauzeitpunkt), in `GET /system/info` und im Manifest einer Sicherung. "Nach Updates suchen" und die
    Kopien vor Updates nehmen beim offiziellen Image die Version aus der Datei im Image, nur ohne sie diese Angabe
    (jeweils nur, wenn es eine gueltige Version ist; `core.updates.running_version`)."""

    image: str | None = None
    """NODVARD_DECK_IMAGE: aus welchem Image der Container stammt, ohne Tag. Ohne (nicht leere) Variable gilt
    die Angabe aus der Datei im Image (`image_info`; das Release-Image traegt `ghcr.io/nodvard/deck`), sonst
    `None` = selbst gebaut. Nur damit unterscheidet "Nach Updates suchen" (`core.updates.is_official_image`)
    das offizielle Image von einem selbst gebauten."""

    image_info_path: Path = image_info.PATH
    """Datei mit Herkunft und Version des Images (`image_info`). Nur fuer Tests aendern."""

    @field_validator("build", "image", mode="before")
    @classmethod
    def _strip_or_none(cls, value: Any) -> Any:
        """Leerraum am Rand weg, leer = nicht gesetzt (nie `""`)."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _image_info_fallback(self) -> Settings:
        """`image`/`build` ohne Variable: aus der Datei im Image (fehlt sie oder ist sie kaputt: `None`)."""
        if self.image is None or self.build is None:
            info = image_info.read(self.image_info_path)
            if self.image is None:
                self.image = info.get("image")
            if self.build is None:
                self.build = info.get("version")
        return self

    extensions_dir: Path = Path("./extensions")
    """Verzeichnis-Scan-Wurzel fuer mitgelieferte/lokale Extensions (docs/02 §1) --
    Konvention: alle Befehle laufen aus dem Repository-Root, dieser Pfad also relativ
    dazu. Pip-installierte Extensions werden zusaetzlich ueber Entry-Points entdeckt,
    siehe ext/discovery.py."""

    skip_pre_migrate_backup: bool = False
    """NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1: Notausgang. Ohne Wert (Standard) legt `nodvard_deck.boot`
    vor JEDER Migration eine Kopie der Datenbank an (`<Datenordner>/backups/vor-update/`) und
    migriert nie ohne sie. Mit diesem Schalter entfaellt die Kopie, z. B. wenn der Platz fuer
    sie nie reicht. Dann gibt es bei einer gescheiterten Migration keinen Rueckweg."""

    max_body_bytes: int = 1024**2
    """NODVARD_DECK_MAX_BODY_BYTES: groesster Anfragekoerper einer normalen Anfrage (Standard 1 MiB).
    Darueber antwortet der Server sofort mit 413, ohne den Koerper zu lesen. Gilt auch ohne Anmeldung.
    Routen mit bewusst grossen Uploads (Sicherung wiederherstellen, Dateien hochladen, Logo, Dokumente,
    Bilder) haben eigene, hoehere Grenzen."""

    files_max_upload_bytes: int = 0
    """NODVARD_DECK_FILES_MAX_UPLOAD_BYTES: groesste Datei, die im Dateibrowser hochgeladen werden darf.
    0 (Standard): keine Grenze, wie bisher -- der Upload geht als Strom direkt an die Quelle (SFTP, WebDAV)
    und landet nie ganz im Speicher; ISO-Abbilder und VM-Images sind oft groesser als 4 GB. Ein Wert ueber 0
    wird schon am Kopf der Anfrage geprueft, bevor die Quelle das Ziel oeffnet. Bei Uploads ohne Laenge
    (chunked) zaehlt nur die Middleware mit, und zwar mindestens bis `max_body_bytes`; dort hat die Quelle das
    Ziel beim Abbruch schon geoeffnet (SFTP schreibt in eine Temp-Datei und laesst das Ziel dann unberuehrt)."""

    restore_max_upload_bytes: int = 4 * 1024**3
    """NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES: groesste Sicherungsdatei, die beim Wiederherstellen
    hochgeladen werden darf (Standard 4 GiB). Wird schon am Kopf der Anfrage geprueft, nicht erst
    nach dem Hochladen."""

    restore_max_unpacked_bytes: int = 16 * 1024**3
    """NODVARD_DECK_RESTORE_MAX_UNPACKED_BYTES: wie gross eine Sicherung entpackt werden darf
    (Standard 16 GiB; zusaetzlich nie mehr als der freie Platz). Schutz vor Bomben."""

    restore_max_entries: int = 200_000
    """NODVARD_DECK_RESTORE_MAX_ENTRIES: wie viele Dateien und Ordner eine Sicherung hoechstens enthalten darf."""

    ext_data_dir: Path = Path("./data/ext")
    """Wurzel fuer `ExtensionContext.data_dir` -- je Extension ein Unterverzeichnis
    `<ext_data_dir>/<ext_id>/` (docs/02-EXTENSION-API.md §2)."""

    metrics_interval_s: int = 30
    """Takt des Verlaufs-Sammlers (core/metrics_history.py) fuer Hosts ohne eigene
    Historie beim Anbieter (z. B. den Pi per SSH). 0 schaltet ihn ab."""

    metrics_raw_retention_h: int = 48
    metrics_rollup_retention_d: int = 35

    ssh_connect_timeout_s: float = 10.0
    """D-05: gemeinsamer asyncssh-Layer fuer Terminal, Skripte und KI-Remediation."""

    ssh_confirm_new_host_keys: bool | None = None
    """NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS: Uebersteuerung fuer ALLE Server.
    `true`: neue Server-Schluessel werden nicht still gemerkt (TOFU, core/ssh.py), wenn
    Hintergrundjobs, Terminal oder Status zum ersten Mal mit einem Server sprechen -- das
    scheitert mit `HostKeyUnknown`, bis jemand den Fingerabdruck unter Einstellungen -> Server &
    Zugaenge ("Verbindung pruefen") bestaetigt hat. `false`: wie frueher, still merken.
    Nicht gesetzt (`None`, Standard): es gilt die gespeicherte Einstellung `ssh.confirm_new_host_keys`
    (neue Installationen: an, bestehende: aus; siehe `services.hosts.host_key_confirmation_required`).
    Bereits gemerkte Schluessel sind nie betroffen; ein "Schluessel vergessen" verlangt immer eine
    Bestaetigung, egal was hier steht."""

    terminal_session_ttl_s: float = 30.0
    """Wie lange ein per `POST /terminal/sessions` gemintetes, noch nicht per WS
    eingeloestes Session-Ticket gueltig bleibt (core/terminal_sessions.py)."""

    terminal_idle_timeout_s: float = 30 * 60
    """Eine Terminal-Sitzung ohne jede Eingabe UND Ausgabe wird nach so vielen Sekunden beendet
    (`api/v1/terminal.py`). Solange ein Befehl noch Text liefert, laeuft sie weiter. 0 = aus."""

    terminal_recheck_interval_s: float = 15.0
    """Wie oft eine offene Terminal-Sitzung pruefen laesst, ob Konto, Berechtigung und Anmeldung
    noch gelten (`services/session_guard.py`). Wer deaktiviert, abgemeldet oder ausgesperrt wird,
    verliert die Shell spaetestens nach so vielen Sekunden."""

    @property
    def api_docs_public(self) -> bool:
        """Soll die API-Doku ohne Anmeldung erreichbar sein? Ausdruecklicher Wert gewinnt, sonst `env == "dev"`."""
        if self.api_docs is not None:
            return self.api_docs
        return self.env.strip().lower() == "dev"

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_or_create_jwt_secret(self) -> str:
        """`jwt_secret` (Env-Var) gewinnt, wenn gesetzt; sonst Datei laden/erzeugen --
        gleiches Muster wie `core.vault.load_or_create_master_key`, absichtlich nicht
        dieselbe Funktion (unterschiedlicher Wertetyp: Text-Secret vs. Fernet-Key)."""
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
    """Vorgabe der Zeitzone, solange `system.timezone` nicht gesetzt ist:
    `NODVARD_DECK_TIMEZONE` (alt `LATTICE_TIMEZONE`), sonst `TZ`, sonst `LOCAL_TIMEZONE`.
    Ein ungueltiger Name wird uebersprungen (mit Warnung), nie zum Absturz."""
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
    """Gecachte Singleton-Instanz. In Tests: `get_settings.cache_clear()`-Ersatz ist,
    das Modul-Attribut `_settings` direkt zurueckzusetzen (siehe tests/conftest.py)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
