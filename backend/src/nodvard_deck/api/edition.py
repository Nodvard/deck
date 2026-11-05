"""Begrenzt die Kernoberfläche auf die Funktionen der laufenden Ausgabe."""

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from ..edition import of
from ..ext.runtime import get_extension_runtime
from .deps import SettingsDep


def unavailable_route(path: str) -> tuple[int, str] | None:
    parts = path.removeprefix("/api/v1/").strip("/").split("/")
    if parts[0] in {"apps", "overview", "files", "console", "host-groups"} or parts[:2] == ["demo", "seed"]:
        return 404, "Diese Funktion ist in dieser Ausgabe nicht verfügbar."
    if parts[:2] in [["ws", "console"], ["terminal", "hosts"]] or parts[:3] in [
        ["system", "updates", "helper"], ["system", "updates", "apply"], ["system", "updates", "rollback"],
    ]:
        return 404, "Diese Funktion ist in dieser Ausgabe nicht verfügbar."
    if (parts[0] == "hosts" and len(parts) > 2 and parts[2] in {"metrics", "tools"}) or parts[:3] == ["hosts", "metrics", "latest"]:
        return 404, "Diese Funktion ist in dieser Ausgabe nicht verfügbar."
    if parts[0] == "extensions" and len(parts) == 3 and parts[2] in {"disable", "takeover"}:
        return 409, "Die Module stehen in dieser Ausgabe fest."
    return None


async def require_edition_route(request: HTTPConnection, settings: SettingsDep) -> None:
    if not of(settings).choose_modules:
        refusal = unavailable_route(request.url.path)
        if refusal:
            raise HTTPException(*refusal)
        parts = request.url.path.removeprefix("/api/v1/").strip("/").split("/")
        runtime = get_extension_runtime()
        if parts[0] == "extensions" and len(parts) > 1 and runtime.canonical(parts[1]) not in runtime.discovered:
            raise HTTPException(404, "Dieses Modul ist in dieser Ausgabe nicht verfügbar.")
