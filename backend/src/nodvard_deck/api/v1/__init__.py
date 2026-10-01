"""Aggregation aller /api/v1-Router.

WP-0 lieferte `public`, WP-1 ergaenzt `auth` + `me`, WP-2 ergaenzt `secrets`
(Metadaten, nie Werte) + `audit` (Filter + NDJSON-Export), WP-3 ergaenzt `extensions`
(Registry) + `catalog` (`/pages`, `/widgets`), WP-4 ergaenzt `hosts`
(Hosts/Gruppen/Zugangsdaten) + `terminal` (Session-Ticket + WS-Bruecke), WP-5 ergaenzt
`actions` (Gate-Journal: Liste/Details/Entscheiden -- der host-gebundene Katalog+
Ausloeser liegt weiterhin in `hosts.router`) + `settings` (globale Autonomie-/
Sperrlisten-Einstellungen, Gap-Fill siehe dortiger Modul-Docstring), WP-6 ergaenzt
`jobs` (Zeitplan + Laeufe), `notifications` (Notification-Center) + `ws` (der
generische WS-Multiplex-Hub aus docs/04 §4). WP-7 ergaenzt `dashboard`
(Layout-Persistenz pro Nutzer) + ein `GET /extensions/{id}/frontend/index.js` direkt
in `extensions.router` (ESM-Bundle-Auslieferung, docs/02 §5). WP-11 ergaenzt `files`
(Dateimanager: Quellen-Fan-out ueber `FileSourceProvider`, Transfer zwischen Quellen).
WP-13 ergaenzt `branding` (`PUT /branding` -- der Erstinbetriebnahme-Assistent im
Web-Frontend braucht einen Schreibweg, `GET /branding` in `public.router` bleibt
unveraendert). Eine seit WP-1 offene Luecke schliesst `users`
(Nutzerverwaltung: Anlegen/Aendern/Entfernen weiterer Accounts, Rollenzuweisung --
`POST /auth/bootstrap` deckte bis dahin nur den allerersten Nutzer ab). Die
Konsole ergaenzt `console` (Bildschirm einer VM/eines Containers ueber
die `ConsoleTarget`-Capability, Einmal-Ticket + WS-Bruecke wie beim Terminal).
Die eigenen App-Kacheln ergaenzen `apps` (`/apps`, "+ App hinzufuegen" im Cockpit; die
Zusammenfuehrung mit den erkannten Diensten steht in `overview`).
Das Aenderungsprotokoll ergaenzt `changelog` (`GET /app/changelog`, fuer die Seite "Ueber
Nodvard Deck"). Die Sicherungen ergaenzen `system` (Infos, Sicherungen; Kritisches nur fuer den Owner).
Extension-eigene Router (`/api/v1/ext/<id>/...`) werden NICHT hier, sondern dynamisch
von `ext.runtime.ExtensionRuntime.mount_router()` an- und abgemontiert.
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
