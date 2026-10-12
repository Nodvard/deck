"""Wichtige Orte eines Docker-Hosts fuer den Dateimanager: die Projektordner seiner Compose-Projekte.

Der Ordner steht als Etikett an jedem Compose-Container (`com.docker.compose.project.working_dir`). Die Orte lesen
ihn aus dem Stand, den der Dienstkatalog beim letzten `docker ps` gemerkt hat (`DockerServiceCatalog.compose_dirs()`),
und fragen den Host hoechstens einmal selbst, wenn es noch keinen oder nur einen alten Stand gibt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nodvard_sdk import FilePlace

from .capabilities import MAX_COMPOSE_DIRS, DockerServiceCatalog

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


class DockerFilePlaces:
    """Erfuellt `nodvard_sdk.capabilities.FilePlaceProvider`: je Compose-Projekt auf einem Docker-Host sein Ordner."""

    def __init__(self, ctx: "ExtensionContext", catalog: DockerServiceCatalog) -> None:
        self._ctx = ctx
        self._catalog = catalog

    async def places_for_host(self, host_id: str) -> list[FilePlace]:
        host = await self._ctx.hosts.get(host_id)
        tag = (await self._ctx.settings.get()).get("docker_host_tag") or "docker"
        if host is None or tag not in host.tags:
            return []
        dirs = await self._catalog.compose_dirs(host)
        return [
            FilePlace(label=f"Docker: {project}", path=dirs[project], icon="boxes", hint=f"Ordner des Compose-Projekts „{project}“")
            for project in sorted(dirs)[:MAX_COMPOSE_DIRS]
        ]
