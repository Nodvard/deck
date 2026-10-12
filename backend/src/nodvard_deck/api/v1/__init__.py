"""Fasst alle `/api/v1`-Router zusammen.

Router der Erweiterungen (`/api/v1/ext/<id>/...`) hängt `ExtensionRuntime.mount_router()` dynamisch ein und aus.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import actions, apps, audit, auth, branding, catalog, changelog, console, dashboard, demo, extension_settings, extensions, files, host_access, hosts, jobs, me, notifications, overview, public, secrets, settings, system, terminal, users, ws

router = APIRouter(prefix="/api/v1")
router.include_router(public.router)
router.include_router(auth.router)
router.include_router(branding.router)
router.include_router(me.router)
router.include_router(secrets.router)
router.include_router(audit.router)
router.include_router(extensions.router)
router.include_router(extension_settings.router)
router.include_router(catalog.router)
router.include_router(changelog.router)
router.include_router(hosts.router)
router.include_router(host_access.router)
router.include_router(terminal.router)
router.include_router(console.router)
router.include_router(actions.router)
router.include_router(settings.router)
router.include_router(jobs.router)
router.include_router(notifications.router)
router.include_router(overview.router)
router.include_router(apps.router)
router.include_router(ws.router)
router.include_router(dashboard.router)
router.include_router(demo.router)
router.include_router(files.router)
router.include_router(users.router)
router.include_router(system.router)

__all__ = ["router"]
