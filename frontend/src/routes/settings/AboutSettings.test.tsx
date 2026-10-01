import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SEEN_KEY, type Changelog } from "../../lib/changelog";
import { NOVNC_VERSION } from "../../lib/thirdParty";
import { useAuthStore } from "../../state/auth";
import { AboutSettings } from "./AboutSettings";
import { SettingsLayout } from "./SettingsLayout";

const DATA: Changelog = {
  current: "0.5.0",
  build: "a156da5",
  unreleased: [{ kind: "verbessert", text: "Noch ohne Nummer eingebaut.", prs: [60] }],
  versions: [
    {
      version: "0.5.0", date: "2026-10-02", title: "Neuer Stand",
      entries: [
        { kind: "behoben", text: "Ein Fehler ist weg.", prs: [51] },
        { kind: "neu", text: "Etwas ganz Neues.", prs: [50, 52] },
        { kind: "sicherheit", text: "Eine Lücke ist zu.", prs: [] },
      ],
    },
    {
      version: "0.4.0", date: "2026-09-30", title: null,
      entries: [{ kind: "neu", text: "Ältere Neuerung.", prs: [45] }],
    },
  ],
};

function serve(data: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data), { status })));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><AboutSettings /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Über Nodvard Deck", () => {
  it("zeigt Version, Build und jede Version als Karte mit Datum und Titel", async () => {
    serve(DATA);
    renderPage();
    expect(screen.getByRole("heading", { name: "Über Nodvard Deck" })).toBeInTheDocument();
    const card = (await screen.findByRole("heading", { name: "Diese Installation" })).closest("section")!;
    expect(within(card).getByText("0.5.0")).toBeInTheDocument();
    expect(within(card).getByText("a156da5")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: /Version 0\.5\.0 · 02\.10\.2026/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Version 0\.4\.0 · 30\.09\.2026/ })).toBeInTheDocument();
    expect(screen.getByText("Neuer Stand")).toBeInTheDocument();
    expect(screen.getByText("Läuft gerade")).toBeInTheDocument(); // genau eine Karte, die laufende
  });

  it("gruppiert die Einträge nach Art mit deutschen Namen, in fester Reihenfolge", async () => {
    serve(DATA);
    renderPage();
    const newest = (await screen.findByRole("heading", { name: /Version 0\.5\.0/ })).closest("details")!;
    const groups = within(newest).getAllByRole("region").map((g) => g.getAttribute("aria-label"));
    expect(groups).toEqual(["Neu", "Behoben", "Sicherheit"]); // nicht in Dateireihenfolge
    const neu = within(newest).getByRole("region", { name: "Neu" });
    expect(within(neu).getByText("Neu")).toBeInTheDocument(); // die Kennzeichnung
    expect(within(neu).getByText(/Etwas ganz Neues\./)).toBeInTheDocument();
    expect(within(within(newest).getByRole("region", { name: "Sicherheit" })).getByText(/Eine Lücke ist zu\./)).toBeInTheDocument();
    expect(within(newest).queryByRole("region", { name: "Verbessert" })).not.toBeInTheDocument();
  });

  it("nennt die Lizenz, verweist auf den Quellcode und enthält den Marken-Hinweis, auch wenn das Protokoll fehlt", async () => {
    serve({ detail: "Nicht authentifiziert" }, 403);
    renderPage();
    expect(screen.getByText(/PolyForm Noncommercial License 1\.0\.0/)).toBeInTheDocument();
    expect(screen.queryByText(/AGPL/)).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "github.com/nodvard/deck" });
    expect(link).toHaveAttribute("href", "https://github.com/nodvard/deck");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByText(/Marken ihrer jeweiligen\s+Inhaber\. Nodvard Deck steht in keiner Verbindung zu ihnen\./)).toBeInTheDocument();
    expect(screen.getByText(/Pi-hole, Nginx Proxy Manager/)).toBeInTheDocument();
    await screen.findByRole("alert");
  });

  it("verweist auf die Lizenzen der Fremdsoftware und auf den Quellcode von noVNC, auch wenn das Protokoll fehlt", async () => {
    serve({ detail: "Nicht authentifiziert" }, 403);
    renderPage();
    const card = screen.getByRole("heading", { name: "Lizenz und Marken" }).closest("section")!;
    const licenses = within(card).getByRole("link", { name: "THIRD_PARTY_LICENSES" });
    expect(licenses).toHaveAttribute("href", "https://github.com/nodvard/deck/blob/main/THIRD_PARTY_LICENSES");
    expect(licenses).toHaveAttribute("target", "_blank");
    expect(licenses).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(within(card).getByText("/app/THIRD_PARTY_LICENSES")).toBeInTheDocument();
    expect(within(card).getByText(/findest du in der Datei/)).toBeInTheDocument(); // Oberfläche duzt

    expect(card).toHaveTextContent(`Die grafische Konsole nutzt noVNC ${NOVNC_VERSION} (unverändert, Mozilla Public License 2.0).`);
    const novnc = within(card).getByRole("link", { name: "github.com/novnc/noVNC" });
    expect(novnc).toHaveAttribute("href", `https://github.com/novnc/noVNC/tree/v${NOVNC_VERSION}`);
    expect(novnc).toHaveAttribute("rel", expect.stringContaining("noopener"));
    await screen.findByRole("alert");
  });

  it("die Karte zu Lizenz und Marken steht unter der Versionsangabe", async () => {
    serve(DATA);
    renderPage();
    const installation = await screen.findByRole("heading", { name: "Diese Installation" });
    const marken = screen.getByRole("heading", { name: "Lizenz und Marken" });
    expect(installation.compareDocumentPosition(marken) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("PR-Nummern stehen klein als einfacher Text, ohne Links", async () => {
    serve(DATA);
    renderPage();
    const newest = (await screen.findByRole("heading", { name: /Version 0\.5\.0/ })).closest("details")!;
    expect(within(within(newest).getByRole("region", { name: "Neu" })).getByText("#50 #52")).toBeInTheDocument();
    expect(screen.getByText("#51")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /#\d+/ })).not.toBeInTheDocument();
    // Eintrag ohne PR bekommt keine leere Nummer.
    const security = screen.getByText(/Eine Lücke ist zu\./);
    expect(security.textContent).not.toContain("#");
  });

  it("nur die neueste Version ist aufgeklappt", async () => {
    serve(DATA);
    renderPage();
    const newest = (await screen.findByRole("heading", { name: /Version 0\.5\.0/ })).closest("details")!;
    const older = screen.getByRole("heading", { name: /Version 0\.4\.0/ }).closest("details")!;
    expect(newest.open).toBe(true);
    expect(older.open).toBe(false);
    expect(within(older).getByText("Ältere Neuerung.")).toBeInTheDocument(); // steht im Dokument, nur zugeklappt
  });

  it("zeigt „Noch ohne Versionsnummer“ nur, wenn es solche Einträge gibt", async () => {
    serve(DATA);
    const first = renderPage();
    const heading = await screen.findByRole("heading", { name: "Noch ohne Versionsnummer" });
    const card = heading.closest("section")!;
    expect(within(card).getByText("Noch ohne Nummer eingebaut.")).toBeInTheDocument();
    expect(within(card).getByText("#60")).toBeInTheDocument();
    first.unmount();

    serve({ ...DATA, unreleased: [] });
    renderPage();
    await screen.findByRole("heading", { name: /Version 0\.5\.0/ });
    expect(screen.queryByText("Noch ohne Versionsnummer")).not.toBeInTheDocument();
  });

  it("Build fehlt, wenn der Server keinen kennt", async () => {
    serve({ ...DATA, build: null });
    renderPage();
    const card = (await screen.findByRole("heading", { name: "Diese Installation" })).closest("section")!;
    expect(within(card).queryByText("Build")).not.toBeInTheDocument();
    expect(within(card).getByText("Version")).toBeInTheDocument();
  });

  it("zeigt eine Meldung, wenn das Protokoll nicht geladen werden kann", async () => {
    serve({ detail: "Nicht authentifiziert" }, 403);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Das Änderungsprotokoll konnte nicht geladen werden");
  });

  it("ein leeres Protokoll stürzt nicht ab", async () => {
    serve({ ...DATA, unreleased: [], versions: [] });
    renderPage();
    expect(await screen.findByText("Es gibt noch keine Einträge im Änderungsprotokoll.")).toBeInTheDocument();
  });

  it("merkt sich beim Öffnen den Stand (Version und Zahl unveröffentlichter Einträge)", async () => {
    serve(DATA);
    renderPage();
    await screen.findByRole("heading", { name: /Version 0\.5\.0/ });
    await waitFor(() => expect(window.localStorage.getItem(SEEN_KEY)).toBe("0.5.0|1"));
  });

  it("stürzt auch ohne Speicher nicht ab", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("gesperrt"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("gesperrt"); });
    serve(DATA);
    renderPage();
    expect(await screen.findByRole("heading", { name: /Version 0\.5\.0/ })).toBeInTheDocument();
  });
});

describe("Einstellungen: Eintrag „Über Nodvard Deck“", () => {
  it("steht für jeden Nutzer im Menü, auch ohne besondere Rechte", () => {
    useAuthStore.setState({
      accessToken: "tok", status: "authenticated", mfaToken: null,
      user: { id: "u1", username: "gast", display_name: "Gast", email: null, is_owner: false, locale: "de", permissions: [] },
    });
    serve(DATA);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/settings/about"]}>
          <Routes><Route path="/settings/*" element={<SettingsLayout />}><Route path="about" element={<AboutSettings />} /></Route></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const nav = within(screen.getByRole("navigation", { name: "Einstellungen" }));
    expect(nav.getByRole("link", { name: /Über Nodvard Deck/ })).toHaveAttribute("href", "/settings/about");
  });
});
