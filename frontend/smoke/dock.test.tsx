import { readdirSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { URL } from "node:url";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";

import { useAuthStore } from "../src/state/auth";
import { useBrandingStore } from "../src/state/branding";
import { useEditionStore } from "../src/state/edition";
import { AboutSettings } from "../src/routes/settings/AboutSettings";
import { SettingsLayout } from "../src/routes/settings/SettingsLayout";
import { assertExcludedModuleApis } from "./dockModules.mjs";

const base = process.env.DOCK_SMOKE_URL;
const extensionsRoot = resolve(process.cwd(), "../extensions");

it.skipIf(!base)("prüft das mit EDITION=dock gebaute Image und seine Oberfläche", async () => {
  const token = process.env.DOCK_SMOKE_TOKEN;
  expect(token).toBeTruthy();
  const realFetch = globalThis.fetch;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => realFetch(new URL(String(input), base), init));
  const headers = { Authorization: `Bearer ${token}` };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const auth = useAuthStore.getState();
  const branding = useBrandingStore.getState();
  const edition = useEditionStore.getState();
  const title = document.title;
  try {
    const extensions = await fetch("/api/v1/extensions", { headers });
    expect(extensions.status).toBe(200);
    expect((await extensions.json()).map((entry: { id: string }) => entry.id)).toEqual(["docker"]);
    for (const name of readdirSync(extensionsRoot)) {
      if (name === "docker" || !existsSync(resolve(extensionsRoot, name, "extension.toml"))) continue;
      for (const path of [`/api/v1/extensions/${name}`, `/api/v1/extensions/${name}/frontend/index.js`]) {
        expect((await fetch(path, { headers })).status, path).toBe(404);
      }
    }
    await assertExcludedModuleApis((path: string) => fetch(path, { headers }));
    expect((await fetch("/api/v1/extensions/docker/frontend/index.js", { headers })).status).toBe(200);
    expect((await fetch("/api/v1/ext/docker/widgets/matrix", { headers })).status).toBe(200);

    await useEditionStore.getState().load();
    await useBrandingStore.getState().load();
    expect(useEditionStore.getState().edition.id).toBe("dock");
    expect(useBrandingStore.getState().branding?.product_name).toBe("Nodvard Dock");
    expect(document.title).toBe("Nodvard Dock");
    useAuthStore.setState({ accessToken: token!, status: "authenticated" });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/settings/about"]}>
          <Routes>
            <Route path="/settings" element={<SettingsLayout />}>
              <Route path="about" element={<AboutSettings />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByRole("heading", { name: "Über Nodvard Dock" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Einstellungen" })).getByRole("link", { name: /Über Nodvard Dock/ })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Diese Installation" })).toBeInTheDocument();
    expect(screen.getByText(/Nodvard Dock steht unter der PolyForm Noncommercial License/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  } finally {
    cleanup();
    client.clear();
    useAuthStore.setState(auth);
    useBrandingStore.setState(branding);
    useEditionStore.setState(edition);
    document.title = title;
    vi.unstubAllGlobals();
  }
}, 30_000);
