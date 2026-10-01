import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SEEN_KEY,
  changelogFingerprint,
  formatDate,
  frontendBuildDate,
  markChangelogSeen,
  useChangelogNotice,
  type Changelog,
} from "./changelog";

const entry = (text: string) => ({ kind: "neu" as const, text, prs: [] });

function changelog(current: string, unreleased = 0): Changelog {
  return {
    current,
    build: null,
    unreleased: Array.from({ length: unreleased }, (_, i) => entry(`Neu ${i}`)),
    versions: [{ version: current, date: "2026-09-30", title: null, entries: [entry("Etwas")] }],
  };
}

function serve(data: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })));
}

function Probe() {
  const { version, isNew } = useChangelogNotice();
  return <div data-testid="probe">{version ? `v${version}` : "keine Version"}{isNew ? " NEU" : ""}</div>;
}

function renderProbe() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Hilfsfunktionen", () => {
  it("Datum wird deutsch geschrieben, ohne Zeitzonen-Verschiebung", () => {
    expect(formatDate("2026-09-30")).toBe("30.09.2026");
    expect(formatDate("2026-01-01")).toBe("01.01.2026");
    expect(formatDate("kein Datum")).toBe("kein Datum");
  });

  it("Fingerabdruck ändert sich mit der Version und der Zahl unveröffentlichter Einträge", () => {
    expect(changelogFingerprint(changelog("0.4.0", 0))).not.toBe(changelogFingerprint(changelog("0.5.0", 0)));
    expect(changelogFingerprint(changelog("0.4.0", 0))).not.toBe(changelogFingerprint(changelog("0.4.0", 2)));
    expect(changelogFingerprint(changelog("0.4.0", 2))).toBe(changelogFingerprint(changelog("0.4.0", 2)));
  });

  it("Bauzeitpunkt der Oberfläche wird aus der Build-Kennung gelesen, Unsinn ergibt nichts", () => {
    expect(frontendBuildDate()).toBeNull(); // in Tests nicht gesetzt
    const moment = Date.UTC(2026, 8, 30, 12, 30);
    vi.stubGlobal("__BUILD_ID__", moment.toString(36));
    expect(frontendBuildDate()?.getTime()).toBe(moment);
    vi.stubGlobal("__BUILD_ID__", "dev");
    expect(frontendBuildDate()).toBeNull();
    vi.stubGlobal("__BUILD_ID__", "");
    expect(frontendBuildDate()).toBeNull();
  });
});

describe("Neu-Markierung (useChangelogNotice)", () => {
  it("beim allerersten Besuch: Version sichtbar, keine Markierung, Stand wird still gemerkt", async () => {
    serve(changelog("0.4.0", 1));
    renderProbe();
    await screen.findByText("v0.4.0");
    await waitFor(() => expect(window.localStorage.getItem(SEEN_KEY)).toBe("0.4.0|1"));
    expect(screen.getByTestId("probe")).not.toHaveTextContent("NEU");
  });

  it("gemerkt wird unter nodvard-deck.changelogSeen, der alte Schluessel lattice.changelogSeen bleibt unberuehrt", async () => {
    expect(SEEN_KEY).toBe("nodvard-deck.changelogSeen");
    window.localStorage.setItem("lattice.changelogSeen", "0.3.0|0"); // noch nicht uebernommen (main.tsx macht das beim Start)
    serve(changelog("0.4.0", 1));
    renderProbe();
    await screen.findByText("v0.4.0");
    // Die Seite liest nur den neuen Schluessel: ohne ihn gilt es als erster Besuch (keine Markierung)...
    await waitFor(() => expect(window.localStorage.getItem("nodvard-deck.changelogSeen")).toBe("0.4.0|1"));
    expect(screen.getByTestId("probe")).not.toHaveTextContent("NEU");
    // ... und den alten fasst sie nicht an.
    expect(window.localStorage.getItem("lattice.changelogSeen")).toBe("0.3.0|0");
  });

  it("gleicher Stand wie beim letzten Besuch: keine Markierung", async () => {
    window.localStorage.setItem(SEEN_KEY, "0.4.0|1");
    serve(changelog("0.4.0", 1));
    renderProbe();
    await screen.findByText("v0.4.0");
    expect(screen.getByTestId("probe")).not.toHaveTextContent("NEU");
  });

  it("neue Version: Markierung", async () => {
    window.localStorage.setItem(SEEN_KEY, "0.4.0|0");
    serve(changelog("0.5.0", 0));
    renderProbe();
    await screen.findByText("v0.5.0 NEU");
    // Nur ansehen reicht nicht: gemerkt wird erst, wenn die Seite "Über Nodvard Deck" geöffnet wird.
    expect(window.localStorage.getItem(SEEN_KEY)).toBe("0.4.0|0");
  });

  it("neue unveröffentlichte Einträge bei gleicher Version: Markierung", async () => {
    window.localStorage.setItem(SEEN_KEY, "0.4.0|1");
    serve(changelog("0.4.0", 3));
    renderProbe();
    await screen.findByText("v0.4.0 NEU");
  });

  it("die Markierung verschwindet, sobald der Stand gemerkt wird", async () => {
    window.localStorage.setItem(SEEN_KEY, "0.4.0|0");
    serve(changelog("0.5.0", 0));
    renderProbe();
    await screen.findByText("v0.5.0 NEU");
    act(() => markChangelogSeen("0.5.0|0"));
    await screen.findByText("v0.5.0");
    expect(screen.getByTestId("probe")).not.toHaveTextContent("NEU");
    expect(window.localStorage.getItem(SEEN_KEY)).toBe("0.5.0|0");
  });

  it("ein anderer Tab, der den Stand merkt, räumt die Markierung auch hier ab", async () => {
    window.localStorage.setItem(SEEN_KEY, "0.4.0|0");
    serve(changelog("0.5.0", 0));
    renderProbe();
    await screen.findByText("v0.5.0 NEU");
    window.localStorage.setItem(SEEN_KEY, "0.5.0|0");
    act(() => { window.dispatchEvent(new StorageEvent("storage", { key: SEEN_KEY })); });
    await screen.findByText("v0.5.0");
  });

  it("ohne Speicher (wirft beim Lesen und Schreiben): kein Absturz, keine Markierung", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("gesperrt"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("gesperrt"); });
    serve(changelog("0.5.0", 2));
    renderProbe();
    await screen.findByText("v0.5.0");
    expect(screen.getByTestId("probe")).not.toHaveTextContent("NEU");
    expect(() => markChangelogSeen("irgendwas")).not.toThrow();
  });

  it("solange nichts geladen ist (oder der Server fehlschlägt): weder Version noch Markierung", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 })));
    renderProbe();
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(screen.getByTestId("probe")).toHaveTextContent("keine Version");
    expect(window.localStorage.getItem(SEEN_KEY)).toBeNull();
  });
});
