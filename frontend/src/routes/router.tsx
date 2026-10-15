import { createBrowserRouter, type RouteObject } from "react-router-dom";

import { ActionsPage } from "./ActionsPage";
import { AppShell } from "./AppShell";
import { AppsPage } from "./AppsPage";
import { ConsolePage } from "./ConsolePage";
import { DashboardPage } from "./DashboardPage";
import { NotFoundPage, RouteErrorPage } from "./ErrorPages";
import { ExtensionPage } from "./ExtensionPage";
import { FilesPage } from "./FilesPage";
import { HostPage } from "./HostPage";
import { LoginPage } from "./LoginPage";
import { NotificationsPage } from "./NotificationsPage";
import { RequireAuth } from "./RequireAuth";
import { TerminalPage } from "./TerminalPage";
import { SetupPage } from "./SetupPage";
import { UsersPage } from "./UsersPage";
import { AboutSettings } from "./settings/AboutSettings";
import { AccountSettings } from "./settings/AccountSettings";
import { AppearanceSettings } from "./settings/AppearanceSettings";
import { AuditSettings } from "./settings/AuditSettings";
import { AutomationSettings } from "./settings/AutomationSettings";
import { ExtensionConfigPage } from "./settings/ExtensionConfigPage";
import { ExtensionsSettings } from "./settings/ExtensionsSettings";
import { HostDetailSettings } from "./settings/HostDetailSettings";
import { HostsSettings } from "./settings/HostsSettings";
import { SettingsLayout } from "./settings/SettingsLayout";
import { SystemSettings } from "./settings/SystemSettings";
import { VaultSettings } from "./settings/VaultSettings";

/**
 * Die Routen als eigener Wert, damit Tests sie mit einem Memory-Router laufen lassen können. Ganz außen fängt
 * `RouteErrorPage fullScreen` Fehler der Oberfläche selbst auf, innerhalb von `AppShell` bleibt bei einem Seitenfehler
 * das Menü stehen. Die `*`-Route liegt hinter `RequireAuth`: Erst anmelden, dann „Seite nicht gefunden“.
 */
export const routes: RouteObject[] = [
  {
    errorElement: <RouteErrorPage fullScreen />,
    children: [
      { path: "/login", element: <LoginPage /> },
      // Erstinbetriebnahme-Assistent: öffentlich wie /login, entscheidet selbst (GET /auth/bootstrap), ob er sich
      // zeigt oder auf /login umleitet.
      { path: "/setup", element: <SetupPage /> },
      {
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              {
                errorElement: <RouteErrorPage />,
                children: [
                  { path: "/", element: <DashboardPage /> },
                  // Kern-Seite wie Dashboard, keine PageSpec: Der Dateimanager kennt keine
                  // einzelne Extension namentlich.
                  { path: "/files/*", element: <FilesPage /> },
                  { path: "/apps", element: <AppsPage /> },
                  { path: "/actions", element: <ActionsPage /> },
                  // Konsole: Bildschirm einer VM/eines Containers, Kern-Seite ohne PageSpec (kennt keinen Hypervisor,
                  // nur die ConsoleTarget-Capability).
                  { path: "/console/:hostId", element: <ConsolePage /> },
                  // Server-Seite (Plesk-Stil): alles zu einem Host; Werkzeug-Kacheln kommen aus GET /hosts/{id}/tools,
                  // der Kern kennt keine Extension namentlich.
                  { path: "/hosts/:hostId", element: <HostPage /> },
                  // Terminal-Seite: Host wählen, echte Sitzung, mehrere Tabs.
                  { path: "/terminal", element: <TerminalPage /> },
                  // Benachrichtigungs-Center: Meldungen aus api/v1/notifications.py.
                  { path: "/notifications", element: <NotificationsPage /> },
                  {
                    path: "/settings",
                    element: <SettingsLayout />,
                    children: [
                      { path: "account", element: <AccountSettings /> },
                      { path: "appearance", element: <AppearanceSettings /> },
                      { path: "users", element: <UsersPage /> },
                      { path: "hosts", element: <HostsSettings /> },
                      { path: "hosts/:hostId", element: <HostDetailSettings /> },
                      { path: "automation", element: <AutomationSettings /> },
                      { path: "system", element: <SystemSettings /> },
                      { path: "extensions", element: <ExtensionsSettings /> },
                      { path: "extensions/:extId", element: <ExtensionConfigPage /> },
                      { path: "vault", element: <VaultSettings /> },
                      { path: "audit", element: <AuditSettings /> },
                      { path: "about", element: <AboutSettings /> },
                    ],
                  },
                  // PageSpec.path (docs/02-EXTENSION-API.md) wird zu /ext/<ext_id><path>.
                  { path: "/ext/:extId/*", element: <ExtensionPage /> },
                  // Alles andere: freundliche Seite statt einer englischen Fehlermeldung.
                  { path: "*", element: <NotFoundPage /> },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
