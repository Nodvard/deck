import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VaultwardenPage, type VaultwardenStatus } from "./VaultwardenPage";

const OK: VaultwardenStatus = {
  state: "ok", message: null, url: "https://vault.example.org", https: true, version: "1.32.0",
  api_version: "2024.6.2", server_name: "Vaultwarden", signups_open: false, domain: "https://vault.example.org",
  domain_matches: true,
};

function mockFetch(data: VaultwardenStatus | { detail: string }, status = 200) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/ext/vaultwarden/summary")) return new Response(JSON.stringify(data), { status });
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

let permissions: Set<string>;

beforeEach(() => {
  permissions = new Set(["hosts.read", "extensions.manage"]);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(),
    hasPermission: (p: string) => permissions.has(p),
  };
});

describe("Vaultwarden-Seite", () => {
  it("zeigt Zustand, Version, HTTPS, Registrierung und wie sich die Apps verbinden", async () => {
    vi.stubGlobal("fetch", mockFetch(OK));
    render(<VaultwardenPage />);
    const stats = within(await screen.findByTestId("stats"));
    expect(stats.getByText("1.32.0")).toBeInTheDocument();
    expect(stats.getByText("HTTPS")).toBeInTheDocument();
    expect(stats.getByText("zu")).toBeInTheDocument();
    expect(screen.queryByTestId("hints")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("apps-howto")).getByText("https://vault.example.org")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Web-Tresor öffnen" })).toHaveAttribute("href", "https://vault.example.org/");
  });

  it("warnt bei HTTP, offener Registrierung und abweichender DOMAIN", async () => {
    vi.stubGlobal("fetch", mockFetch({
      ...OK, url: "http://192.0.2.20:8222", https: false, signups_open: true, domain: "https://vault.example.org",
      domain_matches: false,
    }));
    render(<VaultwardenPage />);
    const hints = within(await screen.findByTestId("hints"));
    expect(hints.getByText("ohne HTTPS")).toBeInTheDocument();
    expect(hints.getByText("Registrierung offen")).toBeInTheDocument();
    expect(hints.getByText(/https:\/\/vault.example.org als Adresse eingetragen/)).toBeInTheDocument();
  });

  it("zeigt ohne Einstellung einen Hinweis, für Konten ohne Verwaltungsrecht ohne Link", async () => {
    const data: VaultwardenStatus = { state: "not_configured", message: "Vaultwarden ist noch nicht eingerichtet." };
    vi.stubGlobal("fetch", mockFetch(data));
    const { unmount } = render(<VaultwardenPage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/vaultwarden");
    unmount();

    permissions = new Set(["hosts.read"]);
    render(<VaultwardenPage />);
    const plain = within(await screen.findByTestId("not-configured"));
    expect(plain.queryByRole("link")).not.toBeInTheDocument();
    expect(plain.getByText(/Ein Administrator kann das/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Einstellungen" })).not.toBeInTheDocument();
  });

  it("zeigt eine nicht erreichbare Instanz mit dem Satz vom Server", async () => {
    vi.stubGlobal("fetch", mockFetch({ state: "unreachable", message: "Vaultwarden antwortet nicht (Zeitüberschreitung).", url: "http://vw.test", https: false }));
    render(<VaultwardenPage />);
    const box = within(await screen.findByTestId("problem"));
    expect(box.getByText("Vaultwarden antwortet nicht (Zeitüberschreitung).")).toBeInTheDocument();
    expect(box.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
  });

  it("zeigt einen Ladefehler", async () => {
    vi.stubGlobal("fetch", mockFetch({ detail: "Keine Berechtigung." }, 403));
    render(<VaultwardenPage />);
    expect(await screen.findByText("Keine Berechtigung.")).toBeInTheDocument();
  });
});
