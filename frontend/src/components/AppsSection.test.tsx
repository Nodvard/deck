import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppTileOut, HostOut } from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { AppsSection } from "./AppsSection";

const confirmDialog = vi.hoisted(() => vi.fn(async (..._args: unknown[]) => true));
vi.mock("../state/dialogs", () => ({ confirmDialog, promptDialog: vi.fn(async () => null) }));

interface Call { method: string; url: string; body: Record<string, unknown> | null }
let calls: Call[];
let fail: Response | null;

function installFetch() {
  calls = [];
  fail = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      method: init?.method ?? "GET",
      url: String(input).replace("/api/v1", ""),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    });
    return fail ?? new Response(JSON.stringify([]), { status: 200 });
  }));
}

const custom = (id: string, name: string, over: Partial<AppTileOut> = {}): AppTileOut => ({
  id, source: "custom", name, url: `http://192.168.2.${id.length + 10}`, host: null, host_id: null, state: null, tone: null, image: null,
  icon: null, color: null, group: null, open_in_new_tab: true, sort_order: 0, ...over,
});
const detected = (id: string, name: string, over: Partial<AppTileOut> = {}): AppTileOut => ({
  id, source: "detected", name, url: "http://10.0.0.5:3000", host: "docker", host_id: "h1", state: "running", tone: "good", image: "x/y",
  icon: null, color: null, group: null, open_in_new_tab: true, sort_order: null, ...over,
});

const APPS: AppTileOut[] = [
  custom("a1", "FRITZ!Box", { icon: "router", color: "#ef4444", group: "Netzwerk", url: "http://192.168.2.1:8080/admin" }),
  custom("a2", "Pi-hole", { icon: "shield-check", group: "Netzwerk", host: "Raspberry Pi", host_id: "h-pi", open_in_new_tab: false }),
  custom("a3", "Synology", { icon: "🏠", group: "Speicher" }),
  custom("a4", "Drucker"),
  detected("d1", "grafana"),
  detected("d2", "redis", { url: null, state: "exited", tone: "danger" }),
];
const HOSTS = [{ id: "h-pi", name: "pi", display_name: "Raspberry Pi" }] as HostOut[];

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

function renderSection(props: Partial<React.ComponentProps<typeof AppsSection>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AppsSection apps={APPS} hosts={HOSTS} canWrite {...props} />
    </QueryClientProvider>,
  );
}

/** Die Kacheln im Raster, in der Reihenfolge der Anzeige (Kennung der Kachel, ohne das Symbol). */
const tileIds = () => Array.from(screen.getByTestId("apps").children).map((el) => (el.getAttribute("data-testid") ?? "").replace(/^app-(custom-)?/, ""));
const openMenu = (name: string) => fireEvent.click(screen.getByRole("button", { name: `Menü für ${name}` }));

beforeEach(() => {
  login(["apps.write", "hosts.read"]);
  installFetch();
  confirmDialog.mockClear();
  confirmDialog.mockImplementation(async () => true);
});
afterEach(() => vi.unstubAllGlobals());

describe("AppsSection: Kacheln", () => {
  it("zeigt eigene Apps zuerst und die erkannten Dienste danach; erkannte ohne Web-Oberfläche nur auf Wunsch", () => {
    renderSection();
    expect(tileIds()).toEqual(["a1", "a2", "a3", "a4", "d1"]);
    expect(screen.queryByTestId("app-d2")).toBeNull();
    fireEvent.click(screen.getByLabelText("auch ohne Web-Oberfläche"));
    expect(screen.getByTestId("app-d2")).toBeInTheDocument();
  });

  it("eigene Links öffnen sicher: rel noopener noreferrer, neuer Tab nach Wahl, Untertitel Server oder Adresse", () => {
    renderSection();
    const fritz = within(screen.getByTestId("app-custom-a1")).getByRole("link");
    expect(fritz).toHaveAttribute("href", "http://192.168.2.1:8080/admin");
    expect(fritz).toHaveAttribute("target", "_blank");
    expect(fritz).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByTestId("app-custom-a1").textContent).toContain("192.168.2.1:8080");

    const pihole = within(screen.getByTestId("app-custom-a2")).getByRole("link");
    expect(pihole).not.toHaveAttribute("target");
    expect(pihole).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByTestId("app-custom-a2").textContent).toContain("Raspberry Pi");

    // Auch der Link eines erkannten Dienstes: wie bisher.
    expect(screen.getByTestId("app-d1")).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("macht aus einer unzulässigen Adresse nie einen Link", () => {
    renderSection({ apps: [custom("x1", "Böse", { url: "javascript:alert(1)" }), custom("x2", "Leer", { url: null })] });
    for (const id of ["x1", "x2"]) {
      const tile = screen.getByTestId(`app-custom-${id}`);
      expect(within(tile).queryByRole("link")).toBeNull();
      expect(tile.querySelector("[href]")).toBeNull();
    }
  });

  it("zeigt Symbol, Emoji oder Anfangsbuchstaben", () => {
    renderSection();
    expect(within(screen.getByTestId("app-custom-a1")).getByTestId("app-avatar").querySelector("svg")).not.toBeNull();
    expect(within(screen.getByTestId("app-custom-a3")).getByTestId("app-avatar").textContent).toBe("🏠");
    expect(within(screen.getByTestId("app-custom-a4")).getByTestId("app-avatar").textContent).toBe("Dr");
  });

  it("nimmt nur eine echte Farbe in den Stil (nie freien Text)", () => {
    renderSection({ apps: [custom("c1", "Rot", { color: "#ef4444" }), custom("c2", "Böse", { color: "red;background:url(http://tracker.example/x)" })] });
    expect((within(screen.getByTestId("app-custom-c1")).getByTestId("app-avatar") as HTMLElement).getAttribute("style")).toContain("rgb(239, 68, 68)");
    const bad = (within(screen.getByTestId("app-custom-c2")).getByTestId("app-avatar") as HTMLElement).getAttribute("style") ?? "";
    expect(bad).not.toContain("tracker");
    expect(bad).not.toContain("color-mix");  // die Ersatzfarbe nach dem Namen, nicht die verlangte Farbe
  });
});

describe("AppsSection: Suche und Gruppen", () => {
  it("filtert nach Suchtext", () => {
    renderSection();
    fireEvent.change(screen.getByLabelText("Apps filtern"), { target: { value: "pi" } });
    expect(tileIds()).toEqual(["a2"]);
    fireEvent.change(screen.getByLabelText("Apps filtern"), { target: { value: "gibt-es-nicht" } });
    expect(screen.getByText("Keine passenden Apps.")).toBeInTheDocument();
  });

  it("zeigt Gruppen-Knöpfe mit Anzahl und filtert damit", () => {
    renderSection();
    const chips = within(screen.getByTestId("app-groups"));
    expect(chips.getAllByRole("button").map((b) => b.textContent)).toEqual(["Alle 5", "Netzwerk 2", "Speicher 1", "Ohne Gruppe 1", "Erkannt 1"]);
    expect(chips.getByRole("button", { name: /^Alle/ })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(chips.getByRole("button", { name: /^Netzwerk/ }));
    expect(chips.getByRole("button", { name: /^Netzwerk/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("app-custom-a1")).toBeInTheDocument();
    expect(screen.getByTestId("app-custom-a2")).toBeInTheDocument();
    expect(screen.queryByTestId("app-custom-a3")).toBeNull();
    expect(screen.queryByTestId("app-d1")).toBeNull();

    fireEvent.click(chips.getByRole("button", { name: /^Erkannt/ }));
    expect(screen.getByTestId("app-d1")).toBeInTheDocument();
    expect(screen.queryByTestId("app-custom-a1")).toBeNull();

    fireEvent.click(chips.getByRole("button", { name: /^Ohne Gruppe/ }));
    expect(screen.getByTestId("app-custom-a4")).toBeInTheDocument();
    expect(screen.queryByTestId("app-custom-a1")).toBeNull();
  });

  it("„Erkannt“ bleibt gewählt, wenn die Suche keinen erkannten Dienst findet (statt still auf „Alle“ zu springen)", () => {
    renderSection();
    const chips = within(screen.getByTestId("app-groups"));
    fireEvent.click(chips.getByRole("button", { name: /^Erkannt/ }));
    fireEvent.change(screen.getByLabelText("Apps filtern"), { target: { value: "fritz" } });
    expect(chips.getByRole("button", { name: /^Erkannt/ })).toHaveAttribute("aria-pressed", "true");
    expect(chips.getByRole("button", { name: /^Alle/ })).toHaveAttribute("aria-pressed", "false");
    expect(chips.getByRole("button", { name: /^Erkannt/ }).textContent).toContain("0");
    expect(screen.queryByTestId("apps")?.children.length ?? 0).toBe(0);
    expect(screen.getByText("Keine passenden Apps.")).toBeInTheDocument();
    expect(screen.queryByTestId("app-custom-a1")).toBeNull();
  });

  it("ohne eigene Apps gibt es keine Gruppen-Knöpfe", () => {
    renderSection({ apps: [detected("d1", "grafana")] });
    expect(screen.queryByTestId("app-groups")).toBeNull();
  });

  it("ein Filter, den es nicht mehr gibt, fällt auf „Alle“ zurück", () => {
    const view = renderSection();
    fireEvent.click(within(screen.getByTestId("app-groups")).getByRole("button", { name: /^Speicher/ }));
    expect(tileIds()).toEqual(["a3"]);
    const client = new QueryClient();
    view.rerender(
      <QueryClientProvider client={client}>
        <AppsSection apps={APPS.filter((a) => a.group !== "Speicher")} hosts={HOSTS} canWrite />
      </QueryClientProvider>,
    );
    expect(within(screen.getByTestId("app-groups")).getByRole("button", { name: /^Alle/ })).toHaveAttribute("aria-pressed", "true");
    expect(tileIds()).toEqual(["a1", "a2", "a4", "d1"]);
  });
});

describe("AppsSection: mit Schreibrecht", () => {
  it("zeigt „App hinzufügen“ und öffnet den Dialog", async () => {
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: /App hinzufügen/ }));
    expect(await screen.findByRole("dialog", { name: "App hinzufügen" })).toBeInTheDocument();
  });

  it("Bearbeiten öffnet den Dialog mit den Werten der App", async () => {
    renderSection();
    openMenu("Pi-hole");
    fireEvent.click(screen.getByRole("menuitem", { name: /Bearbeiten/ }));
    const dialog = await screen.findByRole("dialog", { name: "App bearbeiten" });
    expect((within(dialog).getByLabelText("Name") as HTMLInputElement).value).toBe("Pi-hole");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Löschen fragt nach und löscht dann", async () => {
    renderSection();
    openMenu("Drucker");
    fireEvent.click(screen.getByRole("menuitem", { name: /Löschen/ }));
    await waitFor(() => expect(calls).toEqual(expect.arrayContaining([expect.objectContaining({ method: "DELETE", url: "/apps/a4" })])));
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    const [message, options] = confirmDialog.mock.calls[0] as [string, { danger?: boolean; confirmLabel?: string }];
    expect(message).toContain("„Drucker“");
    expect(message).toContain("für alle Benutzer");
    expect(options).toMatchObject({ danger: true, confirmLabel: "Löschen" });
  });

  it("Abbrechen bei der Rückfrage löscht nichts", async () => {
    confirmDialog.mockImplementation(async () => false);
    renderSection();
    openMenu("Drucker");
    fireEvent.click(screen.getByRole("menuitem", { name: /Löschen/ }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(calls.filter((c) => c.method === "DELETE")).toEqual([]);
  });

  it("zeigt eine Fehlermeldung, wenn Löschen scheitert", async () => {
    fail = new Response(JSON.stringify({ detail: "Diese App gibt es nicht (mehr)." }), { status: 404 });
    renderSection();
    openMenu("Drucker");
    fireEvent.click(screen.getByRole("menuitem", { name: /Löschen/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Diese App gibt es nicht (mehr).");
  });

  it("Nach hinten / Nach vorn tauschen mit dem Nachbarn und schicken die ganze Reihenfolge", async () => {
    renderSection();
    openMenu("FRITZ!Box");
    expect(screen.getByRole("menuitem", { name: /Nach vorn/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("menuitem", { name: /Nach hinten/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")).toMatchObject({ url: "/apps/order", body: { ids: ["a2", "a1", "a3", "a4"] } });

    calls.length = 0;
    openMenu("Drucker");
    expect(screen.getByRole("menuitem", { name: /Nach hinten/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("menuitem", { name: /Nach vorn/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ ids: ["a1", "a2", "a4", "a3"] });
  });

  it("bei aktiver Gruppe wird mit der nächsten sichtbaren App getauscht", async () => {
    renderSection();
    fireEvent.click(within(screen.getByTestId("app-groups")).getByRole("button", { name: /^Netzwerk/ }));
    openMenu("Pi-hole");
    fireEvent.click(screen.getByRole("menuitem", { name: /Nach vorn/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ ids: ["a2", "a1", "a3", "a4"] });
  });

  it("das Menü schließt mit Escape und per Klick daneben", () => {
    renderSection();
    openMenu("Drucker");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    openMenu("Drucker");
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Menü für Drucker" })).toHaveAttribute("aria-expanded", "false");
  });

  it("im Menü liegt der Fokus zuerst auf „Bearbeiten“, Pfeiltasten gehen durch die Einträge (gesperrte werden übersprungen)", () => {
    renderSection();
    openMenu("FRITZ!Box"); // erste App: „Nach vorn“ ist gesperrt
    const items = () => screen.getAllByRole("menuitem");
    expect(items()[0]).toHaveFocus();
    fireEvent.keyDown(items()[0], { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: /Nach hinten/ })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: /Löschen/ })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(items()[0]).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    expect(screen.getByRole("menuitem", { name: /Löschen/ })).toHaveFocus();
  });

  it("die Kachel mit offenem Menü liegt über ihren Nachbarn (deren Knöpfe ragen sonst ins Menü)", () => {
    renderSection();
    const tile = screen.getByTestId("app-custom-a2");
    expect(tile.className).not.toContain("z-30");
    openMenu("Pi-hole");
    expect(tile.className).toContain("z-30");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(tile.className).not.toContain("z-30");
  });

  it("nach „Nach hinten“ steht der Fokus wieder auf dem Menü-Knopf der Kachel (nicht auf <body>)", async () => {
    renderSection();
    openMenu("FRITZ!Box");
    fireEvent.click(screen.getByRole("menuitem", { name: /Nach hinten/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Menü für FRITZ!Box" })).toHaveFocus();
  });

  it("nach Bearbeiten und Abbrechen steht der Fokus wieder auf dem Menü-Knopf der Kachel", async () => {
    renderSection();
    openMenu("Pi-hole");
    fireEvent.click(screen.getByRole("menuitem", { name: /Bearbeiten/ }));
    const dialog = await screen.findByRole("dialog", { name: "App bearbeiten" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(screen.getByRole("button", { name: "Menü für Pi-hole" })).toHaveFocus());
  });

  it("nach „App hinzufügen“ und Abbrechen steht der Fokus wieder auf dem Knopf", async () => {
    renderSection();
    const add = screen.getByRole("button", { name: /App hinzufügen/ });
    add.focus();
    fireEvent.click(add);
    const dialog = await screen.findByRole("dialog", { name: "App hinzufügen" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(add).toHaveFocus());
  });

  /** Wie im Cockpit: die Liste kommt von oben, und die Antwort des Servers ändert sie sofort. */
  function renderLive(onCall: (call: Call, setApps: (apps: AppTileOut[]) => void) => void) {
    let setApps: (apps: AppTileOut[]) => void = () => {};
    function Live() {
      const [apps, set] = useState(APPS);
      setApps = set;
      return <AppsSection apps={apps} hosts={HOSTS} canWrite />;
    }
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const call = { method: init?.method ?? "GET", url: String(input).replace("/api/v1", ""), body: typeof init?.body === "string" ? JSON.parse(init.body) : null };
      calls.push(call);
      // Die Liste aendert sich mit der Antwort (wie im Cockpit, wo der Zwischenspeicher sofort angepasst wird).
      return new Promise<Response>((resolve) => {
        setTimeout(() => {
          onCall(call, (apps) => setApps(apps));
          resolve(new Response(JSON.stringify([]), { status: 200 }));
        }, 0);
      });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Live /></QueryClientProvider>);
  }

  it("nach dem Löschen (Kachel ist weg) geht der Fokus nicht verloren, sondern auf „App hinzufügen“", async () => {
    renderLive((call, setApps) => { if (call.method === "DELETE") setApps(APPS.filter((a) => a.id !== "a4")); });
    openMenu("Drucker");
    fireEvent.click(screen.getByRole("menuitem", { name: /Löschen/ }));
    await waitFor(() => expect(screen.queryByTestId("app-custom-a4")).toBeNull());
    await waitFor(() => expect(screen.getByRole("button", { name: /App hinzufügen/ })).toHaveFocus());
  });

  it("nach dem Verschieben (die Kachel wandert im Raster) bleibt der Fokus auf ihrem Menü-Knopf", async () => {
    renderLive((call, setApps) => {
      if (call.method === "PUT") setApps([APPS[1], APPS[0], ...APPS.slice(2)]);
    });
    openMenu("FRITZ!Box");
    fireEvent.click(screen.getByRole("menuitem", { name: /Nach hinten/ }));
    await waitFor(() => expect(tileIds().slice(0, 2)).toEqual(["a2", "a1"]));
    await waitFor(() => expect(screen.getByRole("button", { name: "Menü für FRITZ!Box" })).toHaveFocus());
  });

  it("an erkannten Diensten gibt es kein Menü", () => {
    renderSection();
    expect(within(screen.getByTestId("app-d1")).queryByRole("button")).toBeNull();
    expect(screen.getAllByRole("button", { name: /^Menü für / })).toHaveLength(4);
  });
});

describe("AppsSection: ohne Schreibrecht", () => {
  beforeEach(() => login(["hosts.read"]));

  it("zeigt die Kacheln, aber weder „App hinzufügen“ noch ein Menü noch einen Dialog", () => {
    renderSection({ canWrite: false });
    expect(screen.getByTestId("app-custom-a1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /App hinzufügen/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Menü für / })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("AppsSection: leer", () => {
  it("mit Schreibrecht: Leerzustand mit Knopf, der den Dialog öffnet", async () => {
    renderSection({ apps: [] });
    const empty = screen.getByTestId("apps-empty");
    expect(empty.textContent).toContain("Noch keine Apps");
    expect(screen.queryByTestId("apps")).toBeNull();
    expect(screen.getAllByRole("button", { name: /App hinzufügen/ })).toHaveLength(1);
    fireEvent.click(within(empty).getByRole("button", { name: /App hinzufügen/ }));
    expect(await screen.findByRole("dialog", { name: "App hinzufügen" })).toBeInTheDocument();
  });

  it("ohne Schreibrecht: der Leerzustand hat keinen Knopf", () => {
    renderSection({ apps: [], canWrite: false });
    expect(within(screen.getByTestId("apps-empty")).queryByRole("button")).toBeNull();
  });
});
