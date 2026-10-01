"""Container-Details und Docker-Speicher (Roadmap Punkt 2: "Docker wie Portainer").

Rein lesend bis auf das Aufraeumen (`docker.prune_*`, ueber das Gate). Aus
`docker inspect` wird eine WHITELIST uebernommen -- Umgebungsvariablen nur mit ihrem
NAMEN, nie mit Wert: dort stehen Passwoerter und Tokens auch unter Namen, die nicht
danach aussehen (`DATABASE_URL=postgres://user:pw@...`). Befehlszeile/Entrypoint
bleiben aus demselben Grund weg.

Live gefunden beim Bau: der Docker-Build-Cache kann viele GB belegen, fast alles davon
freigebbar (z. B. Rueckstand alter Deploys) -- ohne diese Seite nur per Konsole sichtbar.
"""

from __future__ import annotations

import json
from typing import Any

from .capabilities import parse_size

_ZERO_TIME = "0001-01-01T00:00:00Z"
DF_LABEL = {"Images": "Images", "Containers": "Container", "Local Volumes": "Volumes", "Build Cache": "Build-Cache"}


def _time(value: Any) -> str | None:
    return value if value and value != _ZERO_TIME else None


def _ports(raw_ports: dict[str, Any] | None) -> list[dict[str, Any]]:
    """{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}, {"HostIp": "::", ...}]}
    -> je Container-Port die veroeffentlichten Host-Ports, IPv4/IPv6-Dubletten
    zusammengefasst. Nicht veroeffentlicht = host None (nur im Docker-Netz)."""
    ports: list[dict[str, Any]] = []
    for container_port, bindings in sorted((raw_ports or {}).items()):
        published = sorted({
            (b.get("HostPort") if b.get("HostIp") in (None, "", "0.0.0.0", "::") else f"{b.get('HostIp')}:{b.get('HostPort')}")
            for b in (bindings or [])
            if b.get("HostPort")
        })
        ports.append({"container": container_port, "published": published})
    return ports


def summarize_inspect(raw: dict[str, Any]) -> dict[str, Any]:
    state = raw.get("State") or {}
    config = raw.get("Config") or {}
    host_config = raw.get("HostConfig") or {}
    network = raw.get("NetworkSettings") or {}
    labels = config.get("Labels") or {}
    restart = host_config.get("RestartPolicy") or {}
    image_id = str(raw.get("Image") or "")
    project = labels.get("com.docker.compose.project")
    return {
        "name": str(raw.get("Name") or "").lstrip("/"),
        "image": config.get("Image"),
        "image_id": image_id.removeprefix("sha256:")[:12] or None,
        "created": _time(raw.get("Created")),
        "started_at": _time(state.get("StartedAt")),
        "finished_at": _time(state.get("FinishedAt")) if not state.get("Running") else None,
        "running": bool(state.get("Running")),
        "exit_code": state.get("ExitCode") if not state.get("Running") else None,
        "oom_killed": bool(state.get("OOMKilled")),
        "health": (state.get("Health") or {}).get("Status"),
        "restart_policy": restart.get("Name") or "no",
        "restart_count": int(raw.get("RestartCount") or 0),
        "privileged": bool(host_config.get("Privileged")),
        "network_mode": host_config.get("NetworkMode"),
        "memory_limit": host_config.get("Memory") or None,
        "ports": _ports(network.get("Ports")),
        "mounts": [
            {
                "type": m.get("Type"),
                "source": m.get("Name") if m.get("Type") == "volume" else m.get("Source"),
                "destination": m.get("Destination"),
                "read_only": not m.get("RW", True),
            }
            for m in raw.get("Mounts") or []
        ],
        "networks": [
            {"name": name, "ip": (net or {}).get("IPAddress") or None}
            for name, net in sorted((network.get("Networks") or {}).items())
        ],
        "env_keys": sorted({str(e).split("=", 1)[0] for e in config.get("Env") or [] if str(e)}),
        "compose": {
            "project": project,
            "service": labels.get("com.docker.compose.service"),
            "working_dir": labels.get("com.docker.compose.project.working_dir"),
        } if project else None,
    }


def _json_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def parse_system_df(text: str) -> list[dict[str, Any]]:
    """`docker system df --format '{{json .}}'` -> Belegung je Art, mit freigebbarem Anteil."""
    rows = []
    for row in _json_lines(text):
        kind = row.get("Type")
        if kind not in DF_LABEL:
            continue
        reclaimable = str(row.get("Reclaimable") or "").split(" ", 1)[0]
        rows.append({
            "type": kind,
            "label": DF_LABEL[kind],
            "total": int(row["TotalCount"]) if str(row.get("TotalCount", "")).isdigit() else None,
            "active": int(row["Active"]) if str(row.get("Active", "")).isdigit() else None,
            "size": parse_size(str(row.get("Size") or "")),
            "reclaimable": parse_size(reclaimable) if reclaimable else None,
        })
    return rows


def parse_images(text: str) -> list[dict[str, Any]]:
    """`docker images --format '{{json .}}'`. "Containers" kennt erst neueres Docker
    (29.x auf dem Pi) -- aelteres liefert "N/A", dann bleibt `in_use` offen (None)."""
    images = []
    for row in _json_lines(text):
        repo, tag = row.get("Repository") or "<none>", row.get("Tag") or "<none>"
        containers = str(row.get("Containers") or "")
        images.append({
            "id": str(row.get("ID") or "").removeprefix("sha256:")[:12],
            "name": f"{repo}:{tag}" if repo != "<none>" else None,
            "dangling": repo == "<none>" and tag == "<none>",
            "size": parse_size(str(row.get("Size") or "")),
            "created": row.get("CreatedSince"),
            "in_use": int(containers) > 0 if containers.isdigit() else None,
        })
    images.sort(key=lambda i: (i["in_use"] is not False, -(i["size"] or 0)))
    return images
