"""Image-Updates EINSPIELEN (service-matrix) -- die REINEN Teile: erkennen, ob und wie sich
ein Container aktualisieren laesst, Befehle bauen, Ausgaben lesen, Ergebnis beurteilen.
Alles, was Hosts anspricht, steht in `applier.py`.

Eingespielt wird nur bei Containern, die mit Docker Compose (v2) gestartet wurden UND
deren Compose-Dateien auf dem Host liegen: `docker compose pull <dienst>` und
`docker compose up -d --no-deps --no-build <dienst>` mit genau den Angaben, mit denen das
Projekt angelegt wurde (Projektname, Ordner, alle Dateien). Alles andere (Portainer-
Stacks, Swarm, `docker run`, Nodvard Deck selbst) bekommt statt eines Knopfes den Grund.

Sicherheit: Jeder Wert, der aus Container-Labels stammt, wandert in ein SSH-Kommando --
er wird streng geprueft (Regeln mit `\\Z`, nicht `$`: ein Zeilenumbruch am Ende ist kein
Treffer), und jeder Befehlsbauer prueft NOCHMAL selbst (`check_target`). Werte werden mit
`shlex` gequotet. Umgebungsvariablen-Werte verlassen den Host nie: `docker compose
config` wird nur ausgewertet, seine Ausgabe nie gespeichert, geloggt oder zurueckgegeben.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nodvard_sdk.types import Risk

from .capabilities import CONTAINER_NAME_RE
from .detached import busy_check_command
from .image_updates import _IMAGE_ID_RE, canonical_repo, classify_registry_error, is_valid_ref, split_ref

# --- Etiketten und strenge Pruefung -----------------------------------------------------

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_WORKDIR = "com.docker.compose.project.working_dir"
LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"
LABEL_ENV_FILE = "com.docker.compose.project.environment_file"
LABEL_ONEOFF = "com.docker.compose.oneoff"
LABEL_VERSION = "com.docker.compose.version"
LABEL_HASH = "com.docker.compose.config-hash"
LABEL_SWARM = ("com.docker.swarm.service.name", "com.docker.stack.namespace")

PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\Z")
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PATH_RE = re.compile(r"^/[A-Za-z0-9._+@=/ -]{0,1023}\Z")
"""Leerzeichen sind erlaubt (shlex quotet sie) -- aber kein Hochkomma, kein `$`, kein
Zeilenumbruch, kein `;|&*~`."""
MAX_FILES = 8
PORTAINER_PREFIX = "/data/compose/"

RC_PULL, RC_UP = 10, 20

_MISSING_VAR_RE = re.compile(r'The "(\w+)" variable is not set')
_DB_WORDS = (
    "postgres", "postgis", "pgvecto", "mariadb", "mysql", "mongo", "influxdb", "timescale", "couchdb",
    "elasticsearch", "clickhouse", "cassandra", "neo4j",
)
_DB_SERVICES = frozenset({"db", "database", "postgres", "mariadb", "mysql"})
_INFRA = (
    (("pihole", "adguard", "unbound"), "DNS im Netz ist kurz weg (Nodvard Deck nutzt Pi-hole ebenfalls)."),
    (("nginx-proxy-manager", "traefik", "caddy"), "Alle Weiterleitungen über den Proxy sind kurz weg."),
    (("wireguard", "wg-easy", "tailscale", "headscale"), "VPN-Zugang ist kurz weg – nicht aus der Ferne über dieses VPN auslösen."),
    (("portainer",), "Portainer ist kurz nicht erreichbar."),
)

BUSY_TEXT = "Für dieses Compose-Projekt läuft gerade schon ein Update."
UNCHANGED_TEXT = "Für diesen Host gibt es kein neueres Image – der Container bleibt, wie er ist."
DOWNTIME_TEXT = "Der Container wird neu erstellt und ist dabei kurz nicht erreichbar (meist unter einer Minute; das Herunterladen vorher kann dauern)."


def _valid_path(path: str) -> bool:
    if not isinstance(path, str) or PATH_RE.match(path) is None:
        return False
    if path == "/":
        return True
    return not any(part in ("", ".", "..") for part in path.split("/")[1:])


@dataclass(frozen=True)
class ComposeTarget:
    container: str
    container_id: str
    image: str
    image_id: str
    project: str
    service: str
    working_dir: str
    config_files: tuple[str, ...]
    env_files: tuple[str, ...]
    config_hash: str | None


@dataclass(frozen=True)
class NotUpdatable:
    kind: str
    why: str


def check_target(t: ComposeTarget) -> None:
    """Jeder Befehlsbauer ruft das selbst auf (egal, ob der Aufrufer schon geprueft hat)."""
    if not CONTAINER_NAME_RE.match(t.container or ""):
        raise ValueError(f"Ungültiger Containername: {t.container!r}")
    if not is_valid_ref(t.image or ""):
        raise ValueError(f"Ungültiger Image-Name: {t.image!r}")
    if not _IMAGE_ID_RE.match(t.image_id or ""):
        raise ValueError(f"Ungültige Image-ID: {t.image_id!r}")
    if not PROJECT_RE.match(t.project or ""):
        raise ValueError(f"Ungültiger Projektname: {t.project!r}")
    if not SERVICE_RE.match(t.service or ""):
        raise ValueError(f"Ungültiger Dienstname: {t.service!r}")
    if not _valid_path(t.working_dir):
        raise ValueError(f"Ungültiger Projektordner: {t.working_dir!r}")
    if not 1 <= len(t.config_files) <= MAX_FILES or not all(_valid_path(f) for f in t.config_files):
        raise ValueError("Ungültige Compose-Dateien.")
    if len(t.env_files) > MAX_FILES or not all(_valid_path(f) for f in t.env_files):
        raise ValueError("Ungültige env-Dateien.")


# --- Erkennen: laesst sich der Container so aktualisieren? ------------------------------

APPLY_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},"image_id":{{json .Image}},'
    '"state":{{json .State.Status}},"labels":{{json .Config.Labels}}}'
)
"""Ein JSON-Objekt je Zeile. Etiketten sind JSON-maskiert, es gibt also keine Zeilenumbrueche.
Umgebungsvariablen (`.Config.Env`) werden bewusst nicht gelesen."""


def cmd_apply_inspect(names: list[str]) -> str:
    for name in names:
        if not CONTAINER_NAME_RE.match(name):
            raise ValueError(f"Ungültiger Containername: {name!r}")
    return f"docker container inspect --format {shlex.quote(APPLY_FORMAT)} " + " ".join(shlex.quote(n) for n in names)


def parse_apply_inspect(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _why(kind: str, text: str) -> NotUpdatable:
    return NotUpdatable(kind, text)


def _bad_field(field: str) -> NotUpdatable:
    return NotUpdatable("labels", f"Compose-Angaben ungewöhnlich ({field}) – aus Sicherheitsgründen kein Update über Nodvard Deck.")


# Bildnamen (letzter Teil des Repositorys), an denen das Dashboard sich selbst erkennt.
SELF_IMAGE_NAMES = frozenset({"lattice", "nodvard-deck", "nodvard"})
# Registry-Images: `<registry>/nodvard/deck` (das Dashboard) und `<registry>/nodvard/deck-updater` (sein
# Update-Helfer). Den Helfer tauscht die Service-Matrix nie aus: Er hat Zugriff auf Docker, und mitten in einem
# Update des Dashboards neu angelegt, liesse er es halb fertig liegen.
OFFICIAL_IMAGE_PATH = ("nodvard", "deck")
HELPER_IMAGE_PATH = ("nodvard", "deck-updater")
SELF_IMAGE_PATHS = (OFFICIAL_IMAGE_PATH, HELPER_IMAGE_PATH)
OFFICIAL_REGISTRY = "ghcr.io"
LABEL_IMAGE_TITLE = "org.opencontainers.image.title"
HELPER_IMAGE_TITLE = "Nodvard Deck Update-Helfer"
"""Titel im Image des Update-Helfers (`deploy/updater/Dockerfile`, `release.yml`). Docker uebernimmt die Labels des
Images in die des Containers (`.Config.Labels`): So wird der Helfer auch erkannt, wenn sein Image gespiegelt unter
einem anderen Namen liegt (eigene Registry, Proxy-Cache)."""

SELF_WHY = "Das ist Nodvard Deck selbst – Update über das Deploy-Skript (scripts/deploy_pi.sh)."
SELF_WHY_OFFICIAL = "Das ist Nodvard Deck selbst – Updates findest du unter Einstellungen → System → Updates."
SELF_WHY_HELPER = (
    "Das ist der Update-Helfer von Nodvard Deck – ihn aktualisierst du auf dem Server mit "
    "„docker compose pull updater“ und danach „docker compose up -d updater“."
)


def _repo_parts(image: str) -> list[str] | None:
    if not is_valid_ref(image):
        return None
    return canonical_repo(split_ref(image)[0]).split("/")


def is_self_image(image: str) -> bool:
    """Ist `image` das Dashboard selbst (oder sein Update-Helfer)? Nur nach dem Repository-Namen, nie nach Tag oder
    Registry."""
    parts = _repo_parts(image)
    if parts is None:
        return False
    return parts[-1] in SELF_IMAGE_NAMES or tuple(parts[-2:]) in SELF_IMAGE_PATHS


def is_helper_labels(labels: dict[str, str] | None) -> bool:
    """Traegt der Container den Titel aus dem Image des Update-Helfers (`HELPER_IMAGE_TITLE`)?"""
    return bool(labels) and labels.get(LABEL_IMAGE_TITLE) == HELPER_IMAGE_TITLE


def self_why(image: str, labels: dict[str, str] | None = None) -> str:
    """Der Grund ohne Knopf fuer Nodvard Deck selbst: beim Helfer (nach Name oder Label), wie man ihn aktualisiert;
    beim offiziellen Image (`ghcr.io/nodvard/deck`) der Weg zu den Updates im Dashboard; sonst (selbst gebaut) das
    Deploy-Skript."""
    parts = _repo_parts(image) or []
    if tuple(parts[-2:]) == HELPER_IMAGE_PATH or is_helper_labels(labels):
        return SELF_WHY_HELPER
    if parts == [OFFICIAL_REGISTRY, *OFFICIAL_IMAGE_PATH]:
        return SELF_WHY_OFFICIAL
    return SELF_WHY


def classify(info: dict[str, Any], *, own: bool) -> ComposeTarget | NotUpdatable:
    """Ein `docker container inspect` (`APPLY_FORMAT`) -> Ziel oder Grund. Die Reihenfolge
    zaehlt (siehe Kommentare)."""
    raw_labels = info.get("labels")
    labels = {str(k): str(v) for k, v in raw_labels.items()} if isinstance(raw_labels, dict) else {}
    image = str(info.get("image") or "")
    name = str(info.get("name") or "").lstrip("/")

    # 1. Das Dashboard selbst: nie ueber sich selbst aktualisieren. Bildname `lattice` (alt),
    # `nodvard-deck` (neu), `nodvard` (Tippvariante), `<registry>/nodvard/deck` und sein Helfer
    # `<registry>/nodvard/deck-updater` -- der Helfer auch an seinem Label, falls sein Image anders heisst.
    if own or is_self_image(image) or is_helper_labels(labels):
        return _why("self", self_why(image, labels))
    # 2. Swarm.
    if any(labels.get(key) for key in LABEL_SWARM):
        return _why("swarm", "Swarm-Dienst – bitte mit „docker service update“ auf dem Manager aktualisieren.")
    project = labels.get(LABEL_PROJECT, "")
    # 3. Ohne Compose gestartet.
    if not project:
        return _why("plain", "Ohne Docker Compose gestartet – Nodvard Deck kennt die Startparameter nicht. Bitte von Hand neu erstellen (oder auf Compose umstellen).")
    # 4. Einmal-Container.
    if labels.get(LABEL_ONEOFF, "").lower() == "true":
        return _why("oneoff", "Einmal-Container (docker compose run) – kein Dauerdienst.")
    working_dir = labels.get(LABEL_WORKDIR, "")
    raw_files = labels.get(LABEL_CONFIG_FILES, "")
    # 5. Portainer: die Umgebungsvariablen des Stacks liegen in Portainers Datenbank, nicht in den Dateien.
    if working_dir.startswith(PORTAINER_PREFIX) or any(f.startswith(PORTAINER_PREFIX) for f in raw_files.split(",")):
        return _why("portainer", "Von Portainer verwaltet – bitte in Portainer aktualisieren (Stack → „Update the stack“ mit „Re-pull image and redeploy“).")
    service = labels.get(LABEL_SERVICE, "")
    # 6. Compose v1 nennt Container `projekt_dienst_1`; v2 wuerde sie umbenennen.
    if labels.get(LABEL_VERSION, "").startswith("1.") and re.fullmatch(rf"{re.escape(project)}_{re.escape(service)}_\d+", name):
        return _why("compose_v1", "Mit dem alten docker-compose (v1) angelegt – Compose v2 würde den Containernamen ändern. Bitte von Hand aktualisieren.")

    # 7. Strenge Pruefung jedes Wertes, der in einen Befehl wandert.
    if not CONTAINER_NAME_RE.match(name):
        return _bad_field("Containername")
    if not is_valid_ref(image):
        return _bad_field("Image")
    image_id = str(info.get("image_id") or "")
    if not _IMAGE_ID_RE.match(image_id):
        return _bad_field("Image-ID")
    container_id = str(info.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        return _bad_field("Container-ID")
    if not PROJECT_RE.match(project):
        return _bad_field("Projektname")
    if not SERVICE_RE.match(service):
        return _bad_field("Dienstname")
    if not _valid_path(working_dir):
        return _bad_field("Projektordner")
    files = _split_files(raw_files, working_dir)
    if files is None or not 1 <= len(files) <= MAX_FILES:
        return _bad_field("Compose-Dateien")
    env_raw = labels.get(LABEL_ENV_FILE, "")
    env_files: tuple[str, ...] = ()
    if env_raw:
        parsed = _split_files(env_raw, working_dir)
        if parsed is None or len(parsed) > MAX_FILES:
            return _bad_field("env-Dateien")
        env_files = parsed
    return ComposeTarget(
        container=name, container_id=container_id, image=image, image_id=image_id, project=project, service=service,
        working_dir=working_dir, config_files=files, env_files=env_files, config_hash=labels.get(LABEL_HASH) or None,
    )


def _split_files(raw: str, working_dir: str) -> tuple[str, ...] | None:
    """Komma-Liste -> absolute, gepruefte Pfade (relative Eintraege aelterer Compose-Versionen
    gehoeren zum Projektordner). `None`, sobald ein Eintrag nicht passt."""
    out: list[str] = []
    for entry in raw.split(","):
        path = entry
        if path and not path.startswith("/"):
            path = posixpath.join(working_dir, path.removeprefix("./"))
        if not _valid_path(path):
            return None
        out.append(path)
    return tuple(out)


# --- Befehle bauen ----------------------------------------------------------------------


def compose_argv(t: ComposeTarget, *args: str) -> list[str]:
    """`docker compose -p P --project-directory D -f F... [--env-file E...] <args>` -- genau
    die Angaben des urspruenglichen Projekts."""
    check_target(t)
    argv = ["docker", "compose", "--ansi", "never", "-p", t.project, "--project-directory", t.working_dir]
    for f in t.config_files:
        argv += ["-f", f]
    for e in t.env_files:
        argv += ["--env-file", e]
    return argv + list(args)


def pull_command(t: ComposeTarget) -> str:
    return shlex.join(compose_argv(t, "pull", t.service))


def up_command(t: ComposeTarget) -> str:
    # --no-deps: nur DIESER Dienst wird neu erstellt. --no-build: nie bauen. Kein --wait
    # (haengt von der Compose-Version ab) -- der Start wird selbst geprueft (`cmd_verify`).
    return shlex.join(compose_argv(t, "up", "-d", "--no-deps", "--no-build", t.service))


def display_command(t: ComposeTarget) -> str:
    """Anzeige, Sperrliste des Gates (`payload.command`) und Vergleich im Executor."""
    return f"{pull_command(t)} && {up_command(t)}"


def rollback_ref(container: str) -> str:
    """`lattice-rollback/<name>-<kurzer Hash>:previous` -- der Name des Sicherungs-Images. Der Hash
    des ECHTEN Containernamens hindert `foo_bar`, `foo-bar` und `Foo.Bar` daran, dasselbe
    Sicherungs-Image zu teilen und sich gegenseitig zu ueberschreiben."""
    slug = re.sub(r"[^a-z0-9]+", "-", container.lower()).strip("-")[:60].strip("-") or "container"
    ref = f"lattice-rollback/{slug}-{hashlib.sha256(container.encode()).hexdigest()[:8]}:previous"
    if not is_valid_ref(ref):
        raise ValueError(f"Ungültiger Sicherungs-Name: {ref!r}")
    return ref


def apply_script(t: ComposeTarget, *, old_image_id: str, rollback: str) -> str:
    """Das Skript, das entkoppelt auf dem Host laeuft (`detached.py`)."""
    if not _IMAGE_ID_RE.match(old_image_id or ""):
        raise ValueError(f"Ungültige Image-ID: {old_image_id!r}")
    if not is_valid_ref(rollback):
        raise ValueError(f"Ungültiger Sicherungs-Name: {rollback!r}")
    q = shlex.quote
    tag = shlex.join(["docker", "tag", old_image_id, rollback])
    inspect_id = f"docker image inspect --format {q('{{.Id}}')} {q(t.image)} 2>/dev/null"
    # Reihenfolge pull -> tag -> up: Ein gescheiterter oder wirkungsloser Pull (dieselbe Image-ID)
    # ueberschreibt eine aeltere Sicherung nicht; nur wenn wirklich etwas Neues da ist, wird das
    # alte Image als Sicherung markiert.
    return (
        f"echo @@step=pull; {pull_command(t)} || exit {RC_PULL}; "
        f"echo @@step=tag; new=$({inspect_id}); "
        f'if [ -n "$new" ] && [ "$new" != {q(old_image_id)} ]; then {tag} >/dev/null 2>&1 || echo @@warn=rollback-tag; fi; '
        f"echo @@step=up; {up_command(t)} || exit {RC_UP}; echo @@step=done"
    )


def rollback_commands(t: ComposeTarget, rollback: str) -> str:
    """Zurueck auf die alte Version (auf dem Host). Das alte Image bleibt unter `rollback`."""
    return "\n".join([shlex.join(["docker", "tag", rollback, t.image]), up_command(t)])


def _label_filter(key: str, value: str) -> str:
    return "--filter " + shlex.quote(f"label={key}={value}")


def _members_filters(t: ComposeTarget) -> str:
    check_target(t)
    return " ".join([
        _label_filter(LABEL_PROJECT, t.project), _label_filter(LABEL_SERVICE, t.service), _label_filter(LABEL_ONEOFF, "False"),
    ])


def cmd_probe(t: ComposeTarget) -> str:
    """Ein Aufruf: Compose v2 da? Ordner und Dateien da und lesbar? (Ohne sudo -- was der
    SSH-Benutzer nicht lesen kann, blockiert das Update mit einem Hinweis.)"""
    check_target(t)
    q = shlex.quote
    files = " ".join(q(f) for f in (*t.config_files, *t.env_files))
    script = (
        f'v=$(docker compose version --short 2>/dev/null) && echo "@@compose=$v"; '
        f"[ -d {q(t.working_dir)} ] && echo @@dir; "
        f'for f in {files}; do if [ -r "$f" ]; then echo "@@ok=$f"; elif [ -e "$f" ]; then echo "@@noread=$f"; '
        f'else echo "@@missing=$f"; fi; done'
    )
    if not t.env_files:
        env = q(f"{t.working_dir.rstrip('/')}/.env")
        script += f"; if [ -e {env} ] && [ ! -r {env} ]; then echo @@envnoread; fi"
    return script + "; " + busy_check_command(t.project)


def parse_probe(text: str, t: ComposeTarget) -> NotUpdatable | None:
    major: int | None = None
    have_dir = False
    noread: list[str] = []
    missing: list[str] = []
    env_noread = False
    busy = False
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line == "@@busy":
            busy = True
        elif line.startswith("@@compose="):
            m = re.match(r"v?(\d+)\.", line[len("@@compose="):].strip() + ".")
            major = int(m.group(1)) if m else None
        elif line == "@@dir":
            have_dir = True
        elif line.startswith("@@noread="):
            noread.append(line[len("@@noread="):])
        elif line.startswith("@@missing="):
            missing.append(line[len("@@missing="):])
        elif line == "@@envnoread":
            env_noread = True
    if busy:
        return _why("busy", BUSY_TEXT)
    if major is None or major < 2:
        return _why("compose_missing", "Docker Compose v2 („docker compose“) fehlt auf dem Host.")
    if not have_dir:
        return _why("dir", f"Projektordner {t.working_dir} fehlt auf dem Host.")
    for path in missing:
        return _why("files", f"{_file_word(t, path)} {path} fehlt auf dem Host.")
    for path in noread:
        return _why("files", f"{_file_word(t, path)} {path} ist für den SSH-Benutzer nicht lesbar.")
    if env_noread:
        return _why("files", "Die .env-Datei im Projektordner ist für den SSH-Benutzer nicht lesbar.")
    return None


def _file_word(t: ComposeTarget, path: str) -> str:
    return "Umgebungsdatei" if path in t.env_files else "Compose-Datei"


def cmd_members(t: ComposeTarget) -> str:
    return f"docker ps -a {_members_filters(t)} --format {shlex.quote('{{.Names}}|{{.State}}')}"


def parse_members(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in text.splitlines():
        name, sep, state = line.strip().partition("|")
        if sep and name:
            rows.append((name, state.strip().lower()))
    return sorted(rows)


def cmd_config(t: ComposeTarget) -> str:
    return shlex.join(compose_argv(t, "config", "--format", "json"))


def cmd_config_hash(t: ComposeTarget) -> str:
    return shlex.join(compose_argv(t, "config", "--hash", t.service))


def parse_config_hash(text: str, service: str) -> str | None:
    for line in text.splitlines():
        name, _, value = line.strip().partition(" ")
        if name == service and re.fullmatch(r"[0-9a-f]{16,128}", value.strip()):
            return value.strip()
    return None


def normalize_image(ref: str) -> str:
    """Zum Vergleichen: Docker Hub in einer Schreibweise, Tag `latest` ergaenzt."""
    repo, tag, digest = split_ref(ref)
    return f"{canonical_repo(repo)}:{tag or 'latest'}" + (f"@{digest}" if digest else "")


def _first_line(text: str, limit: int = 200) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("WARN"):
            # Fehlermeldungen von Compose zitieren gern den Wert einer Variablen ("geheim$123"):
            # Angaben in Anfuehrungszeichen kommen nie bis zur Anzeige.
            line = re.sub(r"\"[^\"]*\"|'[^']*'", "\"…\"", line)
            return line[:limit]
    return ""


def check_config(stdout: str, stderr: str, exit_code: int, t: ComposeTarget) -> NotUpdatable | None:
    """Wertet `docker compose config --format json` aus. Die Ausgabe enthaelt aufgeloeste
    Umgebungswerte (Passwoerter!) -- sie wird nur gelesen, nie zurueckgegeben oder gespeichert,
    und keine Fehlermeldung zitiert sie."""
    missing = sorted(set(_MISSING_VAR_RE.findall(stderr or "")))
    if missing:
        return _why("env_missing", f"In der Compose-Datei fehlen Variablen ({', '.join(missing[:8])}) – sie kamen beim ersten Start wohl aus der Shell. Update von Hand.")
    if exit_code != 0:
        return _why("config", f"docker compose config meldet einen Fehler: {_first_line(stderr) or f'Exit-Code {exit_code}'}")
    try:
        data = json.loads(stdout)
    except ValueError:
        return _why("config", "docker compose config lieferte eine unlesbare Antwort.")
    try:
        service = data["services"][t.service]
    except (KeyError, TypeError):
        return _why("service_missing", f"Dienst „{t.service}“ steht nicht (mehr) in der Compose-Datei.")
    if not isinstance(service, dict):
        return _why("service_missing", f"Dienst „{t.service}“ steht nicht (mehr) in der Compose-Datei.")
    image = service.get("image")
    if not isinstance(image, str) or not image:
        return _why("build_only", f"Dienst „{t.service}“ hat kein Image (wird gebaut) – Update von Hand.")
    if str(service.get("pull_policy") or "").lower() in ("never", "build"):
        return _why("pull_policy", f"Dienst „{t.service}“ ist auf „pull_policy: {service.get('pull_policy')}“ gestellt – Update von Hand.")
    if not is_valid_ref(image) or normalize_image(image) != normalize_image(t.image):
        return _why(
            "image_mismatch",
            f"Die Compose-Datei nennt ein anderes Image ({image[:120]}) als der laufende Container ({t.image}). "
            "Das wäre mehr als ein Update – bitte auf dem Host prüfen.",
        )
    return None


# --- Risiko, Warnungen, Begruendung -----------------------------------------------------


def is_database_image(image: str, service: str) -> bool:
    if service.lower() in _DB_SERVICES:
        return True
    segment = canonical_repo(split_ref(image)[0]).rsplit("/", 1)[-1].lower()
    # Oberflaechen und Messfuehler zu Datenbanken sind selbst keine (mongo-express, postgres-exporter, ...).
    helper = any(word in segment for word in ("exporter", "express", "admin")) or segment.endswith("-ui") or "-ui-" in segment
    return segment.startswith(_DB_WORDS) and not helper


def risk_for(image: str, service: str) -> Risk:
    return Risk.HIGH if is_database_image(image, service) else Risk.MEDIUM


def _utc_text(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso or "").astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except ValueError:
        return iso or "unbekannt"


def warnings_for(
    *, image: str, service: str, affected: list[str], stopped: list[str], drift: bool, stale: bool, registry_at: str | None,
) -> list[str]:
    """Die Hinweise unter der Uebersicht (die Standard-Warnung vor kurzer Nichterreichbarkeit
    zeigt die Seite immer -- `DOWNTIME_TEXT`)."""
    out: list[str] = []
    if is_database_image(image, service):
        out.append(
            "Datenbank-Container: Ein neues Image kann die Datenbank-Dateien auf eine neue Version umstellen – "
            "das lässt sich nicht einfach zurückdrehen. Vorher ein Backup machen."
        )
    repo, tag, _ = split_ref(image)
    if tag in ("", "latest"):
        out.append("Kein fester Versions-Tag („latest“) – die neue Version kann ein großer Versionssprung sein.")
    low = canonical_repo(repo).lower()
    for words, text in _INFRA:
        if any(word in low for word in words):
            out.append(text)
    if len(affected) > 1:
        out.append(f"Betrifft alle {len(affected)} Container des Dienstes „{service}“: {', '.join(affected)}.")
    if stopped:
        out.append(f"Gestoppte Container des Dienstes werden dabei wieder gestartet: {', '.join(stopped)}.")
    if drift:
        out.append("Die Compose-Datei wurde seit dem letzten Start geändert – diese Änderungen werden mit übernommen.")
    if stale:
        out.append(f"Antwort der Registry vom {_utc_text(registry_at)} (diesmal nicht erreichbar).")
    return out


def reason_text(host_name: str, container: str, image: str, project: str, service: str, risk: Risk) -> str:
    """Das Einzige, was die Aktionen-Seite zeigt -- deshalb steht hier das Wesentliche."""
    text = (
        f"Image-Update: „{container}“ auf {host_name} – {image} neu laden und Container neu erstellen "
        f"(Compose „{project}“/„{service}“). Kurz nicht erreichbar."
    )
    return text + (" Datenbank – vorher Backup!" if risk is Risk.HIGH else "")


def plan_id(command: str, old_image_id: str, remote_digest: str | None) -> str:
    return hashlib.sha256(f"{command}\n{old_image_id}\n{remote_digest or ''}".encode()).hexdigest()[:16]


def short_id(image_id: str | None) -> str:
    return (image_id or "").removeprefix("sha256:")[:12]


# --- Nach dem Lauf: Zustand pruefen -----------------------------------------------------

VERIFY_FORMAT = (
    '{"name":{{json .Name}},"image_id":{{json .Image}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},'
    '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"restarts":{{json .RestartCount}},"started":{{json .State.StartedAt}}}'
)


def cmd_verify(t: ComposeTarget, rollback: str | None = None) -> str:
    """Alle Container des Dienstes + welche Image-ID der Name heute hat (+ die des
    Sicherungs-Images `rollback`, falls angegeben)."""
    check_target(t)
    if rollback is not None and not is_valid_ref(rollback):
        raise ValueError(f"Ungültiger Sicherungs-Name: {rollback!r}")
    q = shlex.quote
    inspect_id = f"docker image inspect --format {q('{{.Id}}')}"
    script = (
        f"ids=$(docker ps -aq {_members_filters(t)}); "
        f"if [ -n \"$ids\" ]; then docker container inspect --format {q(VERIFY_FORMAT)} $ids; fi; "
        f'echo "@@image=$({inspect_id} {q(t.image)} 2>/dev/null)"'
    )
    if rollback is not None:
        script += f'; echo "@@rollback=$({inspect_id} {q(rollback)} 2>/dev/null)"'
    return script


@dataclass(frozen=True)
class Member:
    name: str
    image_id: str
    status: str
    exit_code: int | None
    health: str | None
    restarts: int | None = None
    """`RestartCount` -- steigt, wenn Docker den Container wegen eines Absturzes neu startet."""
    started: str | None = None
    """`State.StartedAt` -- aendert sich bei jedem Start."""


@dataclass(frozen=True)
class Snapshot:
    members: tuple[Member, ...]
    tag_image_id: str | None
    """Die Image-ID, auf die der Image-Name jetzt zeigt."""
    rollback_image_id: str | None = None
    """Die Image-ID des Sicherungs-Images (`lattice-rollback/...`), falls es existiert."""


def parse_verify(text: str) -> Snapshot:
    members: list[Member] = []
    tag_id: str | None = None
    rollback_id: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("@@image="):
            value = line[len("@@image="):].strip()
            tag_id = value if _IMAGE_ID_RE.match(value) else None
        elif line.startswith("@@rollback="):
            value = line[len("@@rollback="):].strip()
            rollback_id = value if _IMAGE_ID_RE.match(value) else None
        elif line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                code = d.get("exit_code")
                health = d.get("health")
                members.append(Member(
                    name=str(d.get("name") or "").lstrip("/"), image_id=str(d.get("image_id") or ""),
                    status=str(d.get("status") or "").lower(), exit_code=code if isinstance(code, int) else None,
                    health=str(health).lower() if isinstance(health, str) else None,
                    restarts=d.get("restarts") if isinstance(d.get("restarts"), int) else None,
                    started=d.get("started") if isinstance(d.get("started"), str) else None,
                ))
    return Snapshot(tuple(sorted(members, key=lambda m: m.name)), tag_id, rollback_id)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    text: str
    """Ein Satz zum Ergebnis."""
    new_image_id: str | None = None
    changed: bool = True
    """`False`: der Container laeuft mit demselben Image wie vorher."""
    health: str | None = None


CRASH_LOOP_TEXT = "Container startet immer wieder neu. Logs über den Knopf „Logs“."


def needs_stability_check(snap: Snapshot) -> bool:
    """Ohne Healthcheck sagt "running" nichts darueber, ob der Container bleibt: ein Absturz-
    Kreislauf steht zwischen zwei Neustarts kurz auf "running". Erst zwei Lesungen im Abstand
    ohne neuen Start belegen einen ruhigen Betrieb."""
    return bool(snap.members) and any(m.health is None for m in snap.members)


def restarted_since(before: Snapshot, after: Snapshot) -> bool:
    """Hat sich ein Container zwischen den Lesungen neu gestartet (hoeherer `RestartCount`,
    anderes `StartedAt`) oder ist eines seiner Mitglieder verschwunden/neu?"""
    old = {m.name: m for m in before.members}
    for m in after.members:
        prev = old.get(m.name)
        if prev is None:
            return True
        if (m.restarts or 0) > (prev.restarts or 0) or m.started != prev.started:
            return True
    return False


def describe_members(snap: Snapshot) -> str:
    if not snap.members:
        return "Kein Container des Dienstes gefunden."
    return "; ".join(
        f"{m.name}: {m.status}" + (f" ({m.health})" if m.health else "") for m in snap.members
    )


def judge_verify(snap: Snapshot, *, old_image_id: str, final: bool) -> Verdict | None:
    """`None`: noch nicht entschieden, weiter warten. `final`: die Zeit ist um -- jetzt muss
    ein Urteil her."""
    members = snap.members
    if not members:
        return Verdict(False, "Kein Container des Dienstes gefunden – er läuft nicht wieder an.") if final else None
    for m in members:
        if m.status in ("exited", "dead"):
            code = f", Exit-Code {m.exit_code}" if m.exit_code is not None else ""
            return Verdict(False, f"Container läuft nicht wieder an (Zustand: beendet{code}). Logs über den Knopf „Logs“.")
        if m.health == "unhealthy":
            return Verdict(False, "Container meldet „ungesund“. Logs über den Knopf „Logs“.")
    settled = all(m.status == "running" and m.health in (None, "healthy") for m in members)
    if not settled:
        if not final:
            return None
        restarting = [m for m in members if m.status == "restarting"]
        if restarting:
            return Verdict(False, CRASH_LOOP_TEXT)
        other = [m for m in members if m.status != "running"]
        if other:
            return Verdict(False, f"Container läuft nicht (Zustand: {other[0].status}).")
        # running, aber der Healthcheck steht noch auf "starting"
        return _judge_image(snap, old_image_id, health="starting", note="Healthcheck meldet noch „startet“ – bitte später nachsehen.")
    health = "healthy" if any(m.health == "healthy" for m in members) else None
    return _judge_image(snap, old_image_id, health=health)


def _judge_image(snap: Snapshot, old_image_id: str, *, health: str | None, note: str = "") -> Verdict:
    old = [m for m in snap.members if m.image_id == old_image_id]
    if old:
        if len(old) == len(snap.members) and snap.tag_image_id in (None, old_image_id):
            return Verdict(
                True, UNCHANGED_TEXT,
                new_image_id=old_image_id, changed=False, health=health,
            )
        return Verdict(False, "Neues Image geladen, der Container nutzt es aber nicht.", new_image_id=snap.tag_image_id)
    new_id = snap.members[0].image_id
    state = "läuft wieder" + (" (gesund)" if health == "healthy" else "")
    return Verdict(True, f"Container {state}." + (f" {note}" if note else ""), new_image_id=new_id, health=health)


# --- Ergebnis-Texte ---------------------------------------------------------------------


DETAILS_HINT = "Einzelheiten stehen in der Aktion."
"""Ersatz fuer eine Zeile vom Host in Texten, die auch ohne Server-Recht sichtbar sind."""


def describe_pull_failure(log: str, *, public: bool = False) -> str:
    """Grund, warum `docker compose pull` scheiterte -- plus die gute Nachricht.

    `public=True`: ohne die Zeile aus der Ausgabe des Hosts (die steht nur in der Ausgabe der
    Aktion), fuer Protokoll, Meldung und die Uebersicht der Seite."""
    lines = [ln.strip() for ln in (log or "").splitlines() if ln.strip() and not ln.startswith("@@")]
    recent = "\n".join(lines[-20:])
    if "no space left" in recent.lower():
        reason = "Kein Platz mehr auf dem Host – „Docker-Speicher“ aufräumen."
    else:
        problem = classify_registry_error(recent)
        if problem.kind == "error":
            if public:
                reason = f"Herunterladen fehlgeschlagen – {DETAILS_HINT}"
            else:
                errors = [ln for ln in lines if "error" in ln.lower()]
                line = (errors or lines or ["Exit-Code 10"])[-1]
                reason = f"Herunterladen fehlgeschlagen: {line[:200]}"
        else:
            reason = problem.message
    return f"{reason.rstrip('.')}. Der Container läuft unverändert weiter."


def _rollback_block(t: ComposeTarget, rollback: str | None) -> str:
    if rollback is None:
        return "Das alte Image konnte nicht als Sicherung markiert werden – ein Zurück ist nur über die Compose-Datei möglich."
    cmds = "\n".join("  " + ln for ln in rollback_commands(t, rollback).splitlines())
    return (
        f"Das alte Image bleibt als {rollback} erhalten. Zurück (auf dem Host):\n{cmds}\n"
        "Hinweis: „Ungenutzte Images entfernen“ löscht dieses Sicherungs-Image."
    )


def success_output(t: ComposeTarget, verdict: Verdict, *, old_image_id: str, rollback: str | None, log: str) -> str:
    if verdict.changed:
        first = f"„{t.container}“ aktualisiert: {t.image}"
        second = f"Image-ID alt {short_id(old_image_id)} → neu {short_id(verdict.new_image_id)} · {verdict.text}"
        block = _rollback_block(t, rollback)
    else:
        first, second, block = verdict.text, None, ""
    parts = [first] + ([second] if second else []) + ([block] if block else []) + ["--- Protokoll (Ende) ---", log.strip()[-6000:]]
    return "\n".join(parts)


def failure_headline(t: ComposeTarget, message: str) -> str:
    """Die erste Zeile der Ausgabe eines gescheiterten Updates."""
    return f"„{t.container}“: {message}"


def failure_output(
    t: ComposeTarget, message: str, *, rollback: str | None, log: str, state: str | None = None, show_rollback: bool = False,
) -> str:
    """`show_rollback`: nur wenn schon etwas veraendert wurde (nach dem Pull); `rollback=None`
    heisst dann: die Sicherung konnte nicht angelegt werden."""
    parts = [failure_headline(t, message)]
    if state:
        parts.append(f"Aktueller Zustand: {state}")
    if show_rollback:
        parts.append(_rollback_block(t, rollback))
    parts += ["--- Protokoll (Ende) ---", log.strip()[-6000:]]
    return "\n".join(parts)

