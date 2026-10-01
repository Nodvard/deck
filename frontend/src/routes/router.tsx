import { createBrowserRouter } from "react-router-dom";

import { ActionsPage } from "./ActionsPage";
import { AppShell } from "./AppShell";
import { ConsolePage } from "./ConsolePage";
import { DashboardPage } from "./DashboardPage";
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

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  // Erstinbetriebnahme-Assistent -- oeffentlich wie /login, entscheidet selbst
  // (GET /auth/bootstrap) ob er sich zeigt oder auf /login umleitet.
  { path: "/setup", element: <SetupPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { path: "/", element: <DashboardPage /> },
          // Kern-Seite wie Dashboard -- KEINE PageSpec, der Dateimanager
          // kennt keine einzelne Extension namentlich (docs/04 Paragraph 3).
          { path: "/files/*", element: <FilesPage /> },
          { path: "/actions", element: <ActionsPage /> },
          // Konsole: Bildschirm einer VM/eines Containers -- Kern-Seite ohne
          // PageSpec (kennt keinen Hypervisor, nur die ConsoleTarget-Capability).
          { path: "/console/:hostId", element: <ConsolePage /> },
          // Server-Seite (Plesk-Stil): alles zu EINEM Host -- Werkzeug-Kacheln kommen
          // aus GET /hosts/{id}/tools, der Kern kennt keine Extension namentlich.
          { path: "/hosts/:hostId", element: <HostPage /> },
          // Terminal-Seite: Host waehlen, echte Sitzung, mehrere Tabs.
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
              { path: "audit", element: <AuditSettings /> },
              { path: "about", element: <AboutSettings /> },
            ],
          },
          // PageSpec.path (docs/02-EXTENSION-API.md §5) wird zu /ext/<ext_id><path>.
          { path: "/ext/:extId/*", element: <ExtensionPage /> },
        ],
      },
    ],
  },
]);
