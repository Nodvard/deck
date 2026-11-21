"""Orte für den Dateimanager: je Compose-Projekt eines Docker-Hosts sein Projektordner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nodvard_sdk import FilePlace

from .capabilities import MAX_COMPOSE_DIRS, DockerServiceCatalog

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

log = logging.getLogger("nodvard_deck.ext.docker")


class DockerFilePlaces:
    """Dateimanager-Orte (`FilePlaceProvider`): je Compose-Projekt auf einem Docker-Host sein Ordner."""

    def __init__(self, ctx: "ExtensionContext", catalog: DockerServiceCatalog) -> None:
        self._ctx = ctx
        self._catalog = catalog

    async def places_for_host(self, host_id: str) -> list[FilePlace]:
        host = await self._ctx.hosts.get(host_id)
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        if host is None or tag not in host.tags:
            return []
        # Der Ordner steht als Etikett `com.docker.compose.project.working_dir` an jedem Compose-Container; der Katalog
        # merkt ihn sich beim `docker ps`.
        try:
            dirs = await self._catalog.compose_dirs(host)
        except Exception as exc:  # noqa: BLE001 - Orte sind optional, der Dateimanager bleibt erreichbar
            log.warning("file_places_failed host=%s error=%s", host_id, type(exc).__name__)
            return []
        return [
            FilePlace(label=f"Docker: {project}", path=dirs[project], icon="boxes", hint=f"Ordner des Compose-Projekts „{project}“")
            for project in sorted(dirs)[:MAX_COMPOSE_DIRS]
        ]
