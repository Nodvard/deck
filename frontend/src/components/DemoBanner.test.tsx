import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { removeQuestion, type DemoStatus } from "../lib/demo";
import { useAuthStore } from "../state/auth";
import { DemoBanner } from "./DemoBanner";

const confirmDialog = vi.hoisted(() => vi.fn(async (..._args: unknown[]) => true));
vi.mock("../state/dialogs", () => ({ confirmDialog, promptDialog: vi.fn(async () => null) }));

const ACTIVE: DemoStatus = { active: true, hosts: 5, notifications: 4, layout_replaced: false, hosts_with_access: [] };
let status: DemoStatus;
let calls: string[];
let deleteFails: boolean;

function installFetch() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url}`);
    if (url === "/demo" && method === "GET") return new Response(JSON.stringify(status));
    if (url === "/demo" && method === "DELETE") {
      if (deleteFails) return new Response(JSON.stringify({ detail: "Auf einem Beispiel-Server läuft gerade eine Aktion." }), { status: 409 });
      status = { ...status, active: false, hosts: 0, notifications: 0 };
      return new Response(JSON.stringify({ removed_hosts: 5, kept_hosts: 0, removed_notifications: 4, layout_restored: false }));
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
}

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

function renderBanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><DemoBanner /></MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  status = { ...ACTIVE };
  deleteFails = false;
  confirmDialog.mockClear();
  confirmDialog.mockImplementation(async () => true);
  login(["*"]);
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("DemoBanner", () => {
  it("sagt, dass es von den eingeschalteten Modulen abhängt, wie viel zu sehen ist, und führt zu den Modulen", async () => {
    renderBanner();
    const hint = await screen.findByTestId("demo-modules-hint");
    expect(hint.textContent).toContain("hängt von den eingeschalteten Modulen ab");
    expect(within(hint).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
  });

  it("ohne Recht für Module nur der Satz, kein Link", async () => {
    login(["hosts.read"]);
    renderBanner();
    const hint = await screen.findByTestId("demo-modules-hint");
    expect(hint.textContent).toContain("hängt von den eingeschalteten Modulen ab");
    expect(within(hint).queryByRole("link")).toBeNull();
  });

  it("zeigt oben „Du siehst Beispieldaten“ mit Knopf zum Löschen, solange Beispieldaten da sind", async () => {
    renderBanner();
    const banner = await screen.findByTestId("demo-banner");
    expect(banner.textContent).toContain("Du siehst Beispieldaten.");
    expect(screen.getByRole("button", { name: "Beispieldaten löschen" })).toBeInTheDocument();
  });

  it("ohne Beispieldaten: kein Band", async () => {
    status = { ...ACTIVE, active: false, hosts: 0, notifications: 0 };
    renderBanner();
    await waitFor(() => expect(calls).toContain("GET /demo"));
    await act(async () => {});
    expect(screen.queryByTestId("demo-banner")).toBeNull();
  });

  it("löscht erst nach Bestätigung und blendet das Band danach aus", async () => {
    renderBanner();
    fireEvent.click(await screen.findByRole("button", { name: "Beispieldaten löschen" }));
    await waitFor(() => expect(screen.queryByTestId("demo-banner")).toBeNull());
    expect(calls).toContain("DELETE /demo");
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    const [message, options] = confirmDialog.mock.calls[0] as [string, { danger?: boolean; confirmLabel?: string }];
    expect(message).toContain("5 Beispiel-Server");
    expect(message).toContain("Deine eigenen Daten bleiben unberührt");
    expect(options).toMatchObject({ danger: true, confirmLabel: "Beispieldaten löschen" });
  });

  it("Abbrechen in der Bestätigung löscht nichts", async () => {
    confirmDialog.mockImplementation(async () => false);
    renderBanner();
    fireEvent.click(await screen.findByRole("button", { name: "Beispieldaten löschen" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    await act(async () => {});
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
    expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
  });

  it("zeigt den Grund, wenn das Löschen scheitert, und lässt das Band stehen", async () => {
    deleteFails = true;
    renderBanner();
    fireEvent.click(await screen.findByRole("button", { name: "Beispieldaten löschen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Auf einem Beispiel-Server läuft gerade eine Aktion.");
    expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
  });

  it("ohne Recht zum Löschen (nur Server sehen): Band ja, Knopf nein", async () => {
    login(["hosts.read"]);
    renderBanner();
    const banner = await screen.findByTestId("demo-banner");
    expect(banner.textContent).toContain("ein Administrator kann sie löschen");
    expect(screen.queryByRole("button", { name: "Beispieldaten löschen" })).toBeNull();
  });

  it("nur hosts.write ohne settings.write reicht nicht für den Knopf", async () => {
    login(["hosts.read", "hosts.write"]);
    renderBanner();
    await screen.findByTestId("demo-banner");
    expect(screen.queryByRole("button", { name: "Beispieldaten löschen" })).toBeNull();
  });

  it("ohne hosts.read wird der Status gar nicht erst abgefragt", async () => {
    login(["notifications.read"]);
    renderBanner();
    await act(async () => {});
    expect(calls).toEqual([]);
    expect(screen.queryByTestId("demo-banner")).toBeNull();
  });
});

describe("removeQuestion", () => {
  it("nennt Zahlen, Layout und Server mit Zugang", () => {
    const text = removeQuestion({ active: true, hosts: 5, notifications: 4, layout_replaced: true, hosts_with_access: ["Beispiel-Raspberry-Pi"] });
    expect(text).toContain("5 Beispiel-Server, 4 Beispiel-Meldungen");
    expect(text).toContain("Dashboard-Layout wird auf den Stand von vorher zurückgesetzt");
    expect(text).toContain("Bei „Beispiel-Raspberry-Pi“ hast du inzwischen einen Zugang hinterlegt. Er wird mit gelöscht.");
  });

  it("nennt die Beispiel-Apps, wenn es welche gibt (und keine, wenn die Antwort das Feld nicht kennt)", () => {
    expect(removeQuestion({ ...ACTIVE, apps: 3 })).toContain("5 Beispiel-Server, 4 Beispiel-Meldungen, 3 Beispiel-Apps)");
    expect(removeQuestion({ ...ACTIVE, apps: 0 })).toContain("4 Beispiel-Meldungen)");
    expect(removeQuestion(ACTIVE)).not.toContain("Apps");
  });

  it("Mehrzahl bei mehreren Servern mit Zugang, sonst keine Warnung", () => {
    const many = removeQuestion({ ...ACTIVE, hosts_with_access: ["A", "B"] });
    expect(many).toContain("Bei A, B hast du inzwischen Zugänge hinterlegt. Sie werden mit gelöscht.");
    expect(removeQuestion(ACTIVE)).not.toContain("Achtung");
    expect(removeQuestion(ACTIVE)).not.toContain("Layout");
  });
});
