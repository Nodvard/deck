"""Begrenzt die Kernoberfläche auf die Funktionen der laufenden Ausgabe."""

from fastapi import HTTPException
from starlette.requests import HTTPConnection
from starlette.types import Scope

from ..edition import DOCK, of
from ..ext.runtime import get_extension_runtime
from .deps import SettingsDep

DOCK_ROUTES = {
    "/api/v1/actions": {"GET"},
    "/api/v1/actions/{action_id}": {"GET"},
    "/api/v1/actions/{action_id}/approve": {"POST"},
    "/api/v1/actions/{action_id}/dismiss": {"POST"},
    "/api/v1/actions/{action_id}/reject": {"POST"},
    "/api/v1/app/changelog": {"GET"},
    "/api/v1/app/icon-{name}.png": {"GET"},
    "/api/v1/app/manifest.webmanifest": {"GET"},
    "/api/v1/audit": {"GET"},
    "/api/v1/audit/export": {"GET"},
    "/api/v1/auth/bootstrap": {"GET", "POST"},
    "/api/v1/auth/bootstrap/restart": {"POST"},
    "/api/v1/auth/bootstrap/restore/{restore_id}/inspect": {"POST"},
    "/api/v1/auth/bootstrap/restore/{restore_id}/schedule": {"POST"},
    "/api/v1/auth/bootstrap/restore/pending": {"DELETE"},
    "/api/v1/auth/bootstrap/restore/upload": {"PUT"},
    "/api/v1/auth/login": {"POST"},
    "/api/v1/auth/logout": {"POST"},
    "/api/v1/auth/mfa": {"POST"},
    "/api/v1/auth/refresh": {"POST"},
    "/api/v1/branding": {"GET", "PUT"},
    "/api/v1/branding/custom-css": {"GET", "PUT"},
    "/api/v1/branding/custom.css": {"GET"},
    "/api/v1/branding/favicon": {"GET", "POST"},
    "/api/v1/branding/logo": {"GET", "POST"},
    "/api/v1/capabilities": {"GET"},
    "/api/v1/edition": {"GET"},
    "/api/v1/extensions": {"GET"},
    "/api/v1/extensions/{ext_id}": {"GET"},
    "/api/v1/extensions/{ext_id}/enable": {"POST"},
    "/api/v1/extensions/{ext_id}/frontend/index.js": {"GET"},
    "/api/v1/extensions/{ext_id}/secrets": {"DELETE", "PUT"},
    "/api/v1/extensions/{ext_id}/settings": {"GET", "PUT"},
    "/api/v1/extensions/{ext_id}/test": {"POST"},
    "/api/v1/health": {"GET"},
    "/api/v1/hosts": {"GET", "POST"},
    "/api/v1/hosts/{host_id}": {"DELETE", "GET", "PATCH"},
    "/api/v1/hosts/{host_id}/actions/{action_type}": {"POST"},
    "/api/v1/hosts/{host_id}/check": {"POST"},
    "/api/v1/hosts/{host_id}/credentials": {"GET", "POST"},
    "/api/v1/hosts/{host_id}/credentials/{credential_id}": {"DELETE"},
    "/api/v1/hosts/{host_id}/credentials/{credential_id}/make-default": {"POST"},
    "/api/v1/hosts/{host_id}/credentials/{credential_id}/setup": {"GET"},
    "/api/v1/hosts/{host_id}/credentials/{credential_id}/setup-with-password": {"POST"},
    "/api/v1/hosts/{host_id}/credentials/generate-key": {"POST"},
    "/api/v1/hosts/{host_id}/known-hosts": {"GET", "POST"},
    "/api/v1/hosts/{host_id}/known-hosts/{key_type}": {"DELETE"},
    "/api/v1/hosts/{host_id}/requirements": {"GET"},
    "/api/v1/hosts/{host_id}/ssh-retry": {"GET", "POST"},
    "/api/v1/me": {"GET", "PATCH"},
    "/api/v1/me/password": {"POST"},
    "/api/v1/me/preferences": {"GET", "PATCH"},
    "/api/v1/me/recovery-codes": {"POST"},
    "/api/v1/me/totp": {"DELETE"},
    "/api/v1/me/totp/confirm": {"POST"},
    "/api/v1/me/totp/setup": {"POST"},
    "/api/v1/notifications": {"GET"},
    "/api/v1/notifications/{notification_id}": {"DELETE", "GET"},
    "/api/v1/notifications/delete-read": {"POST"},
    "/api/v1/notifications/read": {"POST"},
    "/api/v1/notifications/read-all": {"POST"},
    "/api/v1/notifications/unread-count": {"GET"},
    "/api/v1/pages": {"GET"},
    "/api/v1/roles": {"GET"},
    "/api/v1/secrets": {"GET", "POST"},
    "/api/v1/secrets/{secret_id}": {"DELETE", "PATCH"},
    "/api/v1/secrets/{secret_id}/value": {"PUT"},
    "/api/v1/settings": {"GET"},
    "/api/v1/settings/{key}": {"PUT"},
    "/api/v1/settings/autonomy": {"PUT"},
    "/api/v1/settings/notification-types": {"GET"},
    "/api/v1/system/backups": {"GET"},
    "/api/v1/system/backups/{name}": {"DELETE"},
    "/api/v1/system/backups/{name}/ticket": {"POST"},
    "/api/v1/system/backups/{name}/verify": {"POST"},
    "/api/v1/system/backups/config": {"PUT"},
    "/api/v1/system/backups/download": {"POST"},
    "/api/v1/system/backups/download-jobs/{job_id}": {"GET"},
    "/api/v1/system/backups/download/{ticket}": {"GET"},
    "/api/v1/system/backups/key": {"PUT"},
    "/api/v1/system/backups/run": {"POST"},
    "/api/v1/system/connection": {"GET"},
    "/api/v1/system/info": {"GET"},
    "/api/v1/system/restart": {"POST"},
    "/api/v1/system/restore/{restore_id}/inspect": {"POST"},
    "/api/v1/system/restore/{restore_id}/schedule": {"POST"},
    "/api/v1/system/restore/pending": {"DELETE"},
    "/api/v1/system/restore/replaced": {"DELETE"},
    "/api/v1/system/restore/status": {"GET"},
    "/api/v1/system/restore/upload": {"PUT"},
    "/api/v1/system/updates": {"GET"},
    "/api/v1/system/updates/check": {"POST"},
    "/api/v1/telemetry": {"DELETE", "GET", "PUT"},
    "/api/v1/telemetry/preview": {"POST"},
    "/api/v1/telemetry/send": {"POST"},
    "/api/v1/terminal/sessions": {"POST"},
    "/api/v1/users": {"GET", "POST"},
    "/api/v1/users/{user_id}": {"DELETE", "GET", "PATCH"},
    "/api/v1/users/{user_id}/reset-2fa": {"POST"},
    "/api/v1/ws": {"WEBSOCKET"},
    "/api/v1/ws/terminal/{session_id}": {"WEBSOCKET"},
}


def edition_module(ext_id: str, edition_id: str) -> bool:
    """Gehört das Modul fest zur Ausgabe? Das sagt sein Manifest (`editions`), der Kern kennt kein Modul beim Namen."""
    runtime = get_extension_runtime()
    manifest = getattr(runtime.discovered.get(runtime.canonical(ext_id)), "manifest", None)
    return edition_id in (getattr(manifest, "editions", None) or ())


def dock_route_allowed(path: str, method: str) -> bool:
    parts = path.split("/")
    if len(parts) > 4 and parts[:4] == ["", "api", "v1", "ext"]:
        return edition_module(parts[4], DOCK.id)
    allowed = DOCK_ROUTES.get(path, set())
    # Starlette hängt HEAD an jede GET-Route, im Scope bleibt die Methode HEAD.
    return method in allowed or (method == "HEAD" and "GET" in allowed)


def route_path(scope: Scope) -> str:
    """Pfadvorlage der getroffenen Route samt aller Router-Präfixe.

    FastAPI bindet Unter-Router ein, ohne ihre Routen umzuschreiben: `scope["route"]` trägt bei HTTP nur den Pfad im
    eigenen Router, den vollen Pfad kennt der Routenkontext. Websocket-Routen tragen ihn selbst."""
    context = (scope.get("fastapi") or {}).get("effective_route_context")
    return getattr(context, "path", "") or scope["route"].path


async def require_edition_route(request: HTTPConnection, settings: SettingsDep) -> None:
    if of(settings).choose_modules:
        return
    method = request.scope.get("method", "WEBSOCKET")
    if not dock_route_allowed(route_path(request.scope), method):
        raise HTTPException(404, "Diese Funktion ist in dieser Ausgabe nicht verfügbar.")
    ext_id = request.path_params.get("ext_id")
    if ext_id is not None and not edition_module(ext_id, of(settings).id):
        raise HTTPException(404, "Dieses Modul ist in dieser Ausgabe nicht verfügbar.")
