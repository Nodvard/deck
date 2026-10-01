/// <reference types="vite/client" />
/**
 * Design-Vorschau (nur Entwicklung, siehe preview.html): die echte AppShell + Startseite
 * mit Testdaten, die einem typischen Homelab nachempfunden sind. Ersetzt fetch/WebSocket
 * durch Attrappen -- kein Backend, kein Login.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";
import * as ReactDOMClient from "react-dom/client";
import { createMemoryRouter, RouterProvider, useParams } from "react-router-dom";

import { ActionsPage } from "../routes/ActionsPage";
import { AppShell } from "../routes/AppShell";
import { DashboardPage } from "../routes/DashboardPage";
import { FilesPage } from "../routes/FilesPage";
import { HostPage } from "../routes/HostPage";
import { LoginPage } from "../routes/LoginPage";
import { SetupPage } from "../routes/SetupPage";
import { NotificationsPage } from "../routes/NotificationsPage";
import { TerminalPage } from "../routes/TerminalPage";
import { UsersPage } from "../routes/UsersPage";
import { AccountSettings } from "../routes/settings/AccountSettings";
import { AppearanceSettings } from "../routes/settings/AppearanceSettings";
import { AuditSettings } from "../routes/settings/AuditSettings";
import { AboutSettings } from "../routes/settings/AboutSettings";
import { AutomationSettings } from "../routes/settings/AutomationSettings";
import { ExtensionConfigPage } from "../routes/settings/ExtensionConfigPage";
import { ExtensionsSettings } from "../routes/settings/ExtensionsSettings";
import { HostDetailSettings } from "../routes/settings/HostDetailSettings";
import { HostsSettings } from "../routes/settings/HostsSettings";
import { SettingsLayout } from "../routes/settings/SettingsLayout";
import { SystemSettings } from "../routes/settings/SystemSettings";
import { installDeckGlobal } from "../lib/deckGlobal";
import { migrateLegacyStorage } from "../lib/legacyStorage";
import { restartProbe, uploadTransport } from "../lib/restore";
import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";
import "../styles/index.css";
import { applyOhneProxmox, previewState, respond, RESTORE_ID } from "./fixtures";

class SilentWebSocket extends EventTarget {
  readyState = 0;
  constructor() {
    super();
  }
  send() {}
  close() {}
}
(window as unknown as { WebSocket: unknown }).WebSocket = SilentWebSocket;

const realFetch = window.fetch.bind(window);
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const path = new URL(url, window.location.origin).pathname + new URL(url, window.location.origin).search;
  if (!path.startsWith("/api/")) return realFetch(input, init);
  const body = respond(path, init?.method ?? "GET", typeof init?.body === "string" ? JSON.parse(init.body) : undefined);
  await new Promise((r) => setTimeout(r, 120));
  return new Response(JSON.stringify(body ?? {}), { status: body === undefined ? 404 : 200, headers: { "Content-Type": "application/json" } });
};

// Wiederherstellen in der Vorschau: Hochladen mit Fortschritt vorspielen, Neustart "laeuft" (die Seite bleibt am Wartebildschirm).
uploadTransport.send = ({ onProgress, file }) =>
  new Promise((resolve) => {
    let loaded = 0;
    const total = Math.max(file.size, 1);
    const timer = setInterval(() => {
      loaded = Math.min(total, loaded + total / 8);
      onProgress(loaded, total);
      if (loaded >= total) {
        clearInterval(timer);
        resolve({ status: 201, text: JSON.stringify({ id: RESTORE_ID, state: "uploaded", size: file.size, header: { mode: "passwort", created_at: "2026-09-30T00:30:41Z", app_version: "0.4.2" }, summary: null, expires_in: 3600 }) });
      }
    }, 150);
  });
let healthCalls = 0;
restartProbe.health = async () => (healthCalls++ === 0 ? { status: "ok", uptime_s: 900 } : null);

// Wie die echte Shell (main.tsx): Browser-Speicher uebernehmen, globales Objekt unter beiden Namen.
migrateLegacyStorage();
installDeckGlobal({
  React,
  ReactDOM: ReactDOMClient as never,
  ReactJsxRuntime: undefined as never,
  getAccessToken: () => "preview",
  confirmDialog: async () => true,
  promptDialog: async () => null,
  hasPermission: () => true,
});

void useBrandingStore.getState().load();

const previewPath = new URLSearchParams(window.location.search).get("path") ?? "/";
// „Verbindung pruefen“ in der Vorschau: ?scenario=new|changed|error|empty|start (sonst alles in Ordnung).
const scenario = new URLSearchParams(window.location.search).get("scenario");
if (scenario === "new" || scenario === "changed" || scenario === "error") previewState.scenario = scenario;
if (scenario === "empty") previewState.noHosts = true;
// ?scenario=demo: frische Installation, auf der die Beispieldaten schon angelegt sind.
if (scenario === "demo") { previewState.noHosts = true; previewState.demo = true; }
// ?scenario=ohne-proxmox (System, Service-Matrix & Co. an) bzw. ohne-proxmox-min (nur Terminal/Dateien):
// von Hand angelegte Server mit SSH-Zugang, Proxmox und Backups aus.
if (scenario === "ohne-proxmox" || scenario === "ohne-proxmox-min" || scenario === "ohne-proxmox-viele") {
  applyOhneProxmox(scenario === "ohne-proxmox-min", scenario === "ohne-proxmox-viele");
}
// ?scenario=keine-apps: Server da, aber weder erkannte Dienste noch eigene Apps (Leerzustand von „Apps“).
if (scenario === "keine-apps") previewState.noApps = true;
// ?scenario=start: Server angelegt, aber noch ohne Zugang; Module brauchen Einrichtung ("Erste Schritte").
if (scenario === "start") previewState.start = true;
// ?scenario=restore-uploaded|restore-ready|restore-pending|restore-result|restore-replaced: Zustand der Karte Wiederherstellen.
if (scenario?.startsWith("restore-")) previewState.restore = scenario.slice("restore-".length) as typeof previewState.restore;
// ?path=/setup: Einrichtungsassistent mit frischer Installation.
if (previewPath === "/setup") previewState.setup = true;

// Die Anmeldeseite in der Vorschau: abgemeldet starten (sonst leitet sie sofort weiter).
if (previewPath !== "/login" && previewPath !== "/setup") useAuthStore.setState({
  accessToken: "preview",
  user: { id: "u1", username: "admin", display_name: "Admin", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  status: "authenticated",
  mfaToken: null,
});

/** Extension-Seiten direkt aus ihren Quellen (vite.preview.config.ts erlaubt den Zugriff). */
const EXTENSION_PAGES = import.meta.glob("../../../extensions/*/frontend/src/*Page.tsx");
const PAGE_BY_EXT: Record<string, string> = {
  gameserver: "GameServerPage", system: "SystemPage", proxmox: "ProxmoxNodePage", backups: "BackupsPage", "service-matrix": "ServiceMatrixPage",
  "nexus-soc": "SocPage", scripts: "ScriptsPage", documents: "DocumentsPage", inventory: "InventoryPage",
};

function PreviewExtensionPage() {
  const { extId = "" } = useParams();
  const [Page, setPage] = React.useState<React.ComponentType | null>(null);
  React.useEffect(() => {
    const name = PAGE_BY_EXT[extId];
    const key = Object.keys(EXTENSION_PAGES).find((k) => k.includes(`/extensions/${extId}/`) && k.endsWith(`/${name}.tsx`));
    if (!key) return;
    void EXTENSION_PAGES[key]().then((mod) => setPage(() => (mod as Record<string, React.ComponentType>)[name]));
  }, [extId]);
  return Page ? <Page /> : <p className="p-6 text-sm opacity-60">Lade Extension-Seite …</p>;
}

const router = createMemoryRouter(
  [
    { path: "/login", element: <LoginPage /> },
    { path: "/setup", element: <SetupPage /> },
    {
      element: <AppShell />,
      children: [
        { path: "/", element: <DashboardPage /> },
        { path: "/notifications", element: <NotificationsPage /> },
        { path: "/actions", element: <ActionsPage /> },
        { path: "/files/*", element: <FilesPage /> },
        { path: "/terminal", element: <TerminalPage /> },
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
        { path: "/hosts/:hostId", element: <HostPage /> },
        { path: "/ext/:extId/*", element: <PreviewExtensionPage /> },
        { path: "*", element: <p className="p-6 text-sm opacity-60">In der Vorschau nicht verfügbar.</p> },
      ],
    },
  ],
  { initialEntries: [previewPath] },
);

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });

ReactDOMClient.createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <RouterProvider router={router} />
  </QueryClientProvider>,
);
