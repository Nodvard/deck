import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SEEN_KEY, type Changelog } from "../../lib/changelog";
import { NOVNC_VERSION } from "../../lib/thirdParty";
import { useAuthStore } from "../../state/auth";
import { useBrandingStore } from "../../state/branding";
import { DECK_EDITION, useEditionStore } from "../../state/edition";
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
  it("zeigt Dokumentation als eigene Gruppe im Änderungsprotokoll", async () => {
    serve({ ...DATA, unreleased: [{ kind: "dokumentation", text: "Dock-Anleitungen ergänzt.", prs: [] }] });
    renderPage();
    const group = await screen.findByRole("region", { name: "Dokumentation" });
    expect(fetch).toHaveBeenCalledWith("/api/v1/app/changelog?include_documentation=true", expect.any(Object));
    expect(within(group).getByText("Dock-Anleitungen ergänzt.")).toBeInTheDocument();
    expect(within(group).getByText("Dokumentation")).toHaveClass("text-white");
  });

  it("zeigt Version, Build und jede Version als Karte mit Datum und Titel", async () => {
    serve(DATA);
    renderPage();
    expect(screen.getByRole("heading", { name: "Über Nodvard Deck" })).toBeInTheDocument();
    const card = (await screen.findByRole("heading", { name: "Diese Installation" })).closest("section")!;
    expect(within(card).getByText("0.5.0")).toBeInTheDocument();
    expect(within(card).getByText("a156da5")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: /Version 0\.5\.0 · 02\.10\.2026/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Alle Versionen" }));
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

  it("nennt Discord, GitHub, den Entwickler und die Lizenzen der Bibliotheken, auch wenn das Protokoll fehlt", async () => {
    serve({ detail: "Nicht authentifiziert" }, 403);
    renderPage();
    const card = screen.getByRole("heading", { name: "Hilfe und Austausch" }).closest("section")!;
    const links: [string, string][] = [
      ["Discord von Nodvard", "https://discord.gg/gTTs7qMabt"],
      ["Diskussionen", "https://github.com/nodvard/deck/discussions"],
      ["GitHub", "https://github.com/nodvard/deck"],
      ["Lizenzen der verwendeten Bibliotheken", "https://github.com/nodvard/deck/blob/main/THIRD_PARTY_LICENSES"],
    ];
    for (const [name, href] of links) {
      const link = within(card).getByRole("link", { name });
      expect(link).toHaveAttribute("href", href);
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    }
    expect(within(card).getByText("Entwickelt von Nico Benks.")).toBeInTheDocument();
    expect(within(card).getByText("Discord-Community und Bot: yosuke.ae")).toBeInTheDocument();
    expect(within(card).getByText(/findest du auf/)).toBeInTheDocument(); // Oberfläche duzt
    await screen.findByRole("alert");
  });

  it("Hilfe und Lizenz stehen direkt nach der Installation vor dem Änderungsprotokoll", async () => {
    serve(DATA);
    renderPage();
    const installation = (await screen.findByRole("heading", { name: "Diese Installation" })).closest("section")!;
    const help = screen.getByRole("heading", { name: "Hilfe und Austausch" }).closest("section")!;
    const license = screen.getByRole("heading", { name: "Lizenz und Marken" }).closest("section")!;
    const changelog = screen.getByRole("region", { name: "Änderungsprotokoll" });
    fireEvent.click(within(changelog).getByRole("button", { name: "Alle Versionen" }));
    const unreleased = screen.getByRole("heading", { name: "Noch ohne Versionsnummer" }).closest("details")!;
    const newest = screen.getByRole("heading", { name: /Version 0\.5\.0/ });
    const oldest = screen.getByRole("heading", { name: /Version 0\.4\.0/ });
    const row = installation.nextElementSibling!;
    expect(Array.from(row.children)).toEqual([help, license]);
    expect(row.nextElementSibling).toBe(changelog);
    expect(changelog).toContainElement(unreleased);
    for (const heading of [unreleased, newest, oldest]) {
      expect(help.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(license.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("ordnet Hilfe und Lizenz am Handy in einer Spalte und auf breiten Bildschirmen in zwei Spalten an", async () => {
    serve(DATA);
    renderPage();
    await screen.findByRole("heading", { name: "Diese Installation" });
    const help = screen.getByRole("heading", { name: "Hilfe und Austausch" }).closest("section")!;
    const license = screen.getByRole("heading", { name: "Lizenz und Marken" }).closest("section")!;
    expect(help.parentElement).toBe(license.parentElement);
    expect(help.parentElement).toHaveClass("grid", "grid-cols-1", "xl:grid-cols-2");
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

  it("zeigt zunächst nur die laufende Version und blendet ältere Versionen per Knopf ein und aus", async () => {
    serve(DATA);
    renderPage();
    const newest = (await screen.findByRole("heading", { name: /Version 0\.5\.0/ })).closest("details")!;
    expect(newest.open).toBe(true);
    expect(screen.queryByRole("heading", { name: /Version 0\.4\.0/ })).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Alle Versionen" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const history = document.getElementById(toggle.getAttribute("aria-controls")!)!;
    expect(history).toHaveAttribute("hidden");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAccessibleName("Weniger Versionen");
    const older = screen.getByRole("heading", { name: /Version 0\.4\.0/ }).closest("details")!;
    expect(history).toContainElement(older);
    expect(older.open).toBe(false);
    expect(within(older).getByText("Ältere Neuerung.")).toBeInTheDocument(); // steht im Dokument, nur zugeklappt
    fireEvent.click(toggle);
    expect(history).toHaveAttribute("hidden");
    expect(screen.queryByRole("heading", { name: /Version 0\.4\.0/ })).not.toBeInTheDocument();
    expect(newest.open).toBe(true);
  });

  it("öffnet die laufende Version auch dann, wenn eine neuere Version im Protokoll steht", async () => {
    serve({ ...DATA, current: "0.4.0" });
    renderPage();
    const current = (await screen.findByRole("heading", { name: /Version 0\.4\.0/ })).closest("details")!;
    expect(current.open).toBe(true);
    expect(within(current).getByText("Läuft gerade")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Version 0\.5\.0/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Alle Versionen" }));
    expect(screen.getByRole("heading", { name: /Version 0\.5\.0/ }).closest("details")!.open).toBe(false);
  });

  it("hält alle Versionen eingeklappt, wenn die laufende Version noch keinen Eintrag hat", async () => {
    serve({ ...DATA, current: "0.6.0" });
    renderPage();
    await screen.findByRole("heading", { name: "Diese Installation" });
    expect(screen.queryByRole("heading", { name: /Version 0\./ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Alle Versionen" }));
    for (const heading of screen.getAllByRole("heading", { name: /Version 0\./ })) {
      expect(heading.closest("details")!.open).toBe(false);
    }
  });

  it("zeigt bei nur einer laufenden Version keinen Knopf für weitere Versionen", async () => {
    serve({ ...DATA, versions: [DATA.versions[0]] });
    renderPage();
    await screen.findByRole("heading", { name: /Version 0\.5\.0/ });
    expect(screen.queryByRole("button", { name: "Alle Versionen" })).not.toBeInTheDocument();
  });

  it("zeigt „Noch ohne Versionsnummer“ nur, wenn es solche Einträge gibt", async () => {
    serve(DATA);
    const first = renderPage();
    const heading = await screen.findByRole("heading", { name: "Noch ohne Versionsnummer" });
    const card = heading.closest("details")!;
    expect(card.open).toBe(false);
    expect(within(card).getByText("Noch ohne Nummer eingebaut.")).toBeInTheDocument();
    expect(within(card).getByText("#60")).toBeInTheDocument();
    fireEvent.click(heading.closest("summary")!);
    expect(card.open).toBe(true);
    expect(within(card).getByRole("region", { name: "Verbessert" })).toBeVisible();
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
    expect(screen.queryByRole("button", { name: "Alle Versionen" })).not.toBeInTheDocument();
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

  it("lädt Dock aus der API und nennt es im Titel, Menü und auf der Über-Seite", async () => {
    const edition = useEditionStore.getState();
    const branding = useBrandingStore.getState();
    const title = document.title;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      const data = path === "/api/v1/edition"
        ? { ...DECK_EDITION, id: "dock", product_name: "Nodvard Dock", choose_modules: false, repo_tools: false }
        : path === "/api/v1/branding"
          ? { product_name: "Nodvard Dock", colors: { accent: "#0ea5e9" } }
          : DATA;
      return new Response(JSON.stringify(data));
    }));
    try {
      await useEditionStore.getState().load();
      await useBrandingStore.getState().load();
      expect(document.title).toBe("Nodvard Dock");
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={["/settings/about"]}>
            <Routes><Route path="/settings" element={<SettingsLayout />}><Route path="about" element={<AboutSettings />} /></Route></Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      expect(screen.getByRole("heading", { name: "Über Nodvard Dock" })).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Über Nodvard Dock/ })).toHaveAttribute("href", "/settings/about");
      expect(screen.getByText(/Nodvard Dock baut auf vielen freien Bibliotheken auf/)).toBeInTheDocument();
      expect(screen.getByText(/Nodvard Dock steht unter der PolyForm Noncommercial License/)).toBeInTheDocument();
      expect(screen.getByText(/Nodvard Dock steht in keiner Verbindung zu ihnen/)).toBeInTheDocument();
      await screen.findByRole("heading", { name: "Diese Installation" });
    } finally {
      cleanup();
      useEditionStore.setState(edition);
      useBrandingStore.setState(branding);
      document.title = title;
    }
  });
});
