"""Docker-Befehle aus festen Wörtern und geprüften Werten, zusammengesetzt mit `shlex.join`."""

from __future__ import annotations

import re
import shlex

CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")
"""Dockers Namensregel (`[a-zA-Z0-9][a-zA-Z0-9_.-]+`). `\\Z` statt `$`: ein Zeilenumbruch am Ende ist kein Treffer."""
COMPOSE_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}\Z")
"""Composes Regel für Projektnamen."""
DOCKER_ID_RE = re.compile(r"^[0-9a-f]{12,64}\Z")
"""Kurze (`docker ps`) oder volle Container-ID."""
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
NETWORK_ID_RE = re.compile(r"^[0-9a-f]{64}\Z")
"""Nur volle IDs: ein Netz, das inzwischen unter demselben Namen neu angelegt wurde, bleibt unberührt."""
VOLUME_NAME_RE = CONTAINER_NAME_RE
"""Docker prüft Volume-Namen mit derselben Regel wie Containernamen; anonyme Volumes heißen 64 Hex-Zeichen."""
NETWORK_IN_USE = 3
"""Exit-Code von `network_remove`, wenn noch ein Container am Netz hängt; seine Namen stehen dann in stdout."""

PS_FORMAT = (
    '{{.ID}}|{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}'
    '|{{.Label "com.docker.compose.project.working_dir"}}'
)
"""`.ID` erkennt den Container von Nodvard Deck selbst (Docker setzt dessen Hostnamen auf die kurze ID). Der Projektordner
steht ganz hinten, nach den Ports: so verschiebt ein `|` im Ordnernamen nichts davor."""
STATS_FORMAT = "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}"
PORTS_FORMAT = "{{.Id}}|{{json .HostConfig.PortBindings}}"
MAX_LOG_TAIL = 5000
LOG_SINCE_RE = re.compile(r"^[1-9][0-9]{0,3}[mh]\Z")
"""Zeitraum für `docker logs --since` in Minuten oder Stunden, etwa `10m` oder `168h`."""

VERBS = frozenset({"start", "stop", "restart", "rm"})
PRUNE = {
    "docker.prune_images": ("image", "prune", "-f"),
    "docker.prune_unused_images": ("image", "prune", "-a", "-f"),
    "docker.prune_build_cache": ("builder", "prune", "-f"),
}
PICK_SHELL = "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi"
"""bash, wenn es sie gibt, sonst sh; hat das Image keine Shell, zeigt das Terminal Dockers eigene Meldung."""


def _docker(*args: str) -> str:
    return shlex.join(["docker", *args])


def _name(value: str) -> str:
    if not CONTAINER_NAME_RE.match(value):
        raise ValueError(f"Ungültiger Containername: {value!r}")
    return value


def _ids(values: list[str]) -> list[str]:
    if not values or not all(DOCKER_ID_RE.match(v) for v in values):
        raise ValueError(f"Ungültige Container-IDs: {values!r}")
    return values


def ps_all() -> str:
    return _docker("ps", "-a", "--format", PS_FORMAT)


def stats() -> str:
    return _docker("stats", "--no-stream", "--format", STATS_FORMAT)


def port_bindings(ids: list[str]) -> str:
    """Die Port-Zuordnung gestoppter Container, die `docker ps` nicht zeigt."""
    return _docker("inspect", "--type", "container", "--format", PORTS_FORMAT, *_ids(ids))


def system_df() -> str:
    return _docker("system", "df", "--format", "{{json .}}")


def system_df_verbose() -> str:
    """Images, Container, Volumes und Build-Cache mit Größe und Nutzung, als ein JSON-Objekt."""
    return _docker("system", "df", "-v", "--format", "{{json .}}")


def networks() -> str:
    return _docker("network", "ls", "--no-trunc", "--format", "{{json .}}")


def image_remove(image_id: str) -> str:
    """Ohne `-f`: ein Image, das ein Container nutzt (auch ein gestoppter), lehnt Docker selbst ab."""
    if not IMAGE_ID_RE.match(image_id):
        raise ValueError(f"Ungültige Image-ID: {image_id!r}")
    return _docker("image", "rm", image_id)


def volume_remove(name: str) -> str:
    """Ohne `-f`: ein Volume, das ein Container nutzt, lehnt Docker selbst ab."""
    if not VOLUME_NAME_RE.match(name):
        raise ValueError(f"Ungültiger Volume-Name: {name!r}")
    return _docker("volume", "rm", name)


def network_remove(network_id: str) -> str:
    """Löscht das Netz nur, wenn kein Container daran hängt; `docker network rm` sieht nur laufende, und ein nie gestarteter
    findet sich nur unter dem Namen oder der ID, mit der er angelegt wurde."""
    if not NETWORK_ID_RE.match(network_id):
        raise ValueError(f"Ungültige Netz-ID: {network_id!r}")
    name = _docker("network", "inspect", "--format", "{{.Name}}", network_id)
    # Die CLI legt Container immer mit dem Namen an; über die API mit einer ID, die nicht 12 oder 64 Zeichen lang ist,
    # bliebe einer unsichtbar.
    users = _docker(
        "ps", "-a", "--format", "{{.Names}}", "--filter", f"network={network_id}", "--filter", f"network={network_id[:12]}",
    )
    script = (
        f'name=$({name}) || exit 1; used=$({users} --filter "network=$name") || exit 1; '
        f'[ -z "$used" ] || {{ echo "$used"; exit {NETWORK_IN_USE}; }}; '
        f'{_docker("network", "rm", network_id)} >/dev/null && printf \'%s\\n\' "$name"'
    )
    return shlex.join(["sh", "-c", script])


def images() -> str:
    return _docker("images", "--format", "{{json .}}")


def inspect_container(name: str) -> str:
    return _docker("inspect", "--type", "container", _name(name))


def container_id(name: str) -> str:
    return _docker("inspect", "-f", "{{.Id}}", _name(name))


def container_full_id(name: str) -> str:
    return _docker("inspect", "--type", "container", "-f", "{{.Id}}", _name(name))


def container_id_state(name: str) -> str:
    return _docker("inspect", "--type", "container", "-f", "{{.Id}}|{{.State.Status}}", _name(name))


def container_verb(verb: str, target: str) -> str:
    """`docker start|stop|restart|rm <Name oder ID>`, ohne `-f` und ohne `-v`."""
    if verb not in VERBS:
        raise ValueError(f"Unbekanntes Verb: {verb!r}")
    return _docker(verb, _name(target))


def logs(name: str, tail: int, *, follow: bool, since: str | None = None) -> str:
    if isinstance(tail, bool) or not isinstance(tail, int) or not 0 <= tail <= MAX_LOG_TAIL:
        raise ValueError(f"Ungültige Zeilenzahl: {tail!r}")
    if since is not None and not (isinstance(since, str) and LOG_SINCE_RE.match(since)):
        raise ValueError(f"Ungültiger Zeitraum: {since!r}")
    return _docker(
        "logs", "--tail", str(tail), "--timestamps", *(["--since", since] if since else []),
        *(["--follow"] if follow else []), _name(name),
    )


def restart_counts() -> str:
    """Wie oft Docker jeden Container nach seinem Ende von selbst wieder gestartet hat (`RestartCount`), in einem Aufruf."""
    inspect = _docker("inspect", "--type", "container", "--format", "{{.Name}}|{{.RestartCount}}")
    return shlex.join(["sh", "-c", f'ids=$({_docker("ps", "-aq")}) && [ -n "$ids" ] && {inspect} $ids'])


def info() -> str:
    """Was der Docker-Dienst sieht, unter anderem CPU-Zahl und Arbeitsspeicher; im LXC-Container dessen Grenzen."""
    return _docker("info", "--format", "{{json .}}")


def virtualization() -> str:
    """`lxc`, `kvm`, `none` usw.; ohne systemd fehlt das Programm, dann bleibt die Umgebung unbekannt."""
    return shlex.join(["systemd-detect-virt"])


def compose_restart(project: str) -> str:
    """Ohne Compose-Datei: Compose v2 findet die Container über ihre Labels."""
    if not COMPOSE_PROJECT_RE.match(project):
        raise ValueError(f"Ungültiger Projektname: {project!r}")
    return _docker("compose", "-p", project, "restart")


def prune(action_type: str) -> str | None:
    args = PRUNE.get(action_type)
    return _docker(*args) if args else None


def shell(name: str) -> str:
    """`docker exec -it <name> sh -c '<bash, sonst sh>'`."""
    return _docker("exec", "-it", _name(name), "sh", "-c", PICK_SHELL)
