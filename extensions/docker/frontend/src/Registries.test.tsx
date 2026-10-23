import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { RegistryList } from "./Registries";

const STATE = {
  registries: [
    { name: "ghcr", address: "ghcr.io", username: "nico", logged_in: false, secret_set: true },
    { name: "hub", address: "docker.io", username: "nico", logged_in: true, secret_set: true },
    { name: "lab", address: "registry.lab:5000", username: "deck", logged_in: false, secret_set: false },
  ],
  creds_store: null,
  error: null,
};
const requests: string[] = [];

function serve(over: Record<string, (init?: RequestInit) => Response> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url}`);
    for (const [part, answer] of Object.entries(over)) {
      if (url.includes(part)) return answer(init);
    }
    if (url.endsWith("/registries")) return Response.json(STATE);
    if (url.endsWith("/login") || url.endsWith("/logout")) {
      return Response.json({ action_id: "r1", status: "proposed", risk: "medium", detail: null }, { status: 202 });
    }
    throw new Error(url);
  }));
}

const confirm = () => window.__lattice.confirmDialog as Mock;

beforeEach(() => {
  requests.length = 0;
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("Registries", () => {
  it("zeigt je Registry den Stand und nur die passenden Knöpfe", async () => {
    serve();
    render(<RegistryList hostId="h1" />);
    const ghcr = await screen.findByTestId("registry-ghcr");
    expect(ghcr.textContent).toContain("nicht angemeldet");
    expect(screen.getByRole("button", { name: "Bei ghcr anmelden" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Bei ghcr abmelden" })).toBeNull();
    expect(screen.getByTestId("registry-hub").textContent).toContain("angemeldet");
    expect(screen.queryByRole("button", { name: "Bei hub anmelden" })).toBeNull();
    expect(screen.getByTestId("registry-lab").textContent).toContain("Kein Passwort im Tresor");
    expect(screen.getByRole("button", { name: "Bei lab anmelden" })).toBeDisabled();
    expect(screen.getByTestId("registries").textContent).toContain("nur umkodiert, nicht verschlüsselt");
  });

  it("meldet über die Freigabe an und zeigt das Ergebnis", async () => {
    serve({
      "/actions/r1/approve": () => Response.json({ id: "r1", status: "succeeded", result: { output: "Docker auf diesem Server ist bei ghcr.io angemeldet." } }),
    });
    render(<RegistryList hostId="h1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Bei ghcr anmelden" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Docker auf diesem Server ist bei ghcr.io angemeldet."));
    expect(String(confirm().mock.calls[0][0])).toMatch(/^Docker auf diesem Server bei ghcr\.io als nico anmelden\? /);
    expect(requests).toContain("POST /api/v1/ext/docker/hosts/h1/registries/ghcr/login");
  });

  it("ohne sofortige Freigabe sagt es, dass sie fehlt", async () => {
    serve();
    window.__lattice.hasPermission = vi.fn((permission: string) => !permission.startsWith("actions.approve"));
    render(<RegistryList hostId="h1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Bei hub abmelden" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Zum Abmelden vorgeschlagen"));
  });

  it("ein gescheitertes Anmelden kommt als Warnung", async () => {
    serve({
      "/actions/r1/approve": () => Response.json({ id: "r1", status: "failed", result: { error: "Anmelden bei ghcr.io ist gescheitert: unauthorized" } }),
    });
    render(<RegistryList hostId="h1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Bei ghcr anmelden" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Anmelden bei ghcr.io ist gescheitert: unauthorized"));
    expect(screen.getByRole("status").className).toContain("text-red-400");
  });

  it("ohne Einträge zeigt es den Weg in die Einstellungen", async () => {
    serve({ "/registries": () => Response.json({ registries: [], creds_store: null, error: null }) });
    render(<RegistryList hostId="h1" />);
    expect((await screen.findByTestId("registries")).textContent).toContain("Noch keine private Registry eingetragen");
    expect(screen.getByRole("link", { name: "Moduleinstellungen" }).getAttribute("href")).toBe("/settings/extensions/docker");
  });

  it("doppelte Namen sind ein Hinweis, kein „noch keine Registry“", async () => {
    const warning = "Der Name „ghcr“ steht mehrmals in den Einstellungen. Deck nutzt keinen dieser Einträge, bis jeder Name nur einmal vorkommt.";
    serve({ "/registries": () => Response.json({ registries: [], creds_store: null, error: null, warnings: [warning] }) });
    const { unmount } = render(<RegistryList hostId="h1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(warning);
    expect(screen.getByTestId("registries").textContent).not.toContain("Noch keine private Registry eingetragen");
    unmount();
    serve({ "/registries": () => Response.json({ ...STATE, warnings: [warning] }) });
    render(<RegistryList hostId="h1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(warning);
    expect(screen.getByTestId("registry-hub")).toBeInTheDocument();
  });

  it("ein Lesefehler ist kein „nicht angemeldet“", async () => {
    serve({
      "/registries": () => Response.json({ ...STATE, error: "Server nicht erreichbar: timeout",
        registries: STATE.registries.map((r) => ({ ...r, logged_in: null })) }),
    });
    render(<RegistryList hostId="h1" />);
    const ghcr = await screen.findByTestId("registry-ghcr");
    expect(ghcr.textContent).toContain("unbekannt");
    expect(ghcr.textContent).not.toContain("nicht angemeldet");
    expect(screen.getByTestId("registries").textContent).toContain("Server nicht erreichbar: timeout");
  });

  it("ein Ladefehler bietet einen neuen Versuch", async () => {
    serve({ "/registries": () => new Response("weg", { status: 502 }) });
    render(<RegistryList hostId="h1" />);
    expect(await screen.findByRole("button", { name: /Erneut versuchen/ })).toBeInTheDocument();
  });
});
