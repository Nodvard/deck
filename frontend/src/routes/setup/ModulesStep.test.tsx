import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BETA_HINT } from "../../components/BetaBadge";
import { ModulesStep, forEveryone, groupModules, shortDescription } from "./ModulesStep";

describe("shortDescription", () => {
  it("nimmt den ersten Satz", () => {
    expect(shortDescription("Findet Container. Zeigt sie als Kacheln.")).toBe("Findet Container.");
  });

  it("trennt nicht bei „z. B.“", () => {
    expect(shortDescription("Gameserver starten (z. B. Valheim). Server werden über eine Markierung erkannt.")).toBe(
      "Gameserver starten (z. B. Valheim).",
    );
  });

  it("kürzt bei einem Doppelpunkt ab, wenn davor schon ein sinnvoller Satz steht", () => {
    expect(shortDescription("Virenschutz für alle Server: ClamAV-Scans, Quarantäne und mehr.")).toBe("Virenschutz für alle Server.");
    // Zu kurzer Anfang: nicht kürzen.
    expect(shortDescription("Hinweis: wichtig und lang genug für einen Satz.")).toBe("Hinweis: wichtig und lang genug für einen Satz.");
  });

  it("lässt einen Text ohne Satzende ganz stehen und kommt mit leeren Werten klar", () => {
    expect(shortDescription("Web-Terminal per SSH")).toBe("Web-Terminal per SSH");
    expect(shortDescription(null)).toBe("");
    expect(shortDescription("")).toBe("");
  });
});

describe("groupModules", () => {
  const row = (id: string, category: string | null | undefined, sort_order?: number, name = id) => ({
    id, name, state: "disabled", description: null, icon: null, last_error: null, category, sort_order,
  });

  it("ordnet nach Gruppe, dann Reihenfolge, dann Name; Unbekanntes kommt ans Ende", () => {
    const groups = groupModules([
      row("c", "tools", 5), row("b", "servers", 20), row("a", "servers", 20, "A zuerst"), row("x", "irgendwas"), row("y", undefined),
      row("d", "servers", 10),
    ]);
    expect(groups.map((g) => g.id)).toEqual(["servers", "tools", "other"]);
    expect(groups[0].rows.map((r) => r.id)).toEqual(["d", "a", "b"]);
    expect(groups[2].rows.map((r) => r.id)).toEqual(["x", "y"]);
  });
});

describe("Schritt „Was willst du nutzen?“ für Neulinge", () => {
  const row = (id: string, category: string, description: string) => ({
    id, name: id, state: "disabled", description, icon: null, last_error: null, category, sort_order: 10,
  });
  afterEach(() => vi.unstubAllGlobals());

  it("Beispiel-Module für Entwickler stehen nicht in der Liste", () => {
    const rows = [row("system", "servers", "Zustand."), row("hello-world", "example", "Beispiel für Entwickler.")];
    expect(forEveryone(rows).map((r) => r.id)).toEqual(["system"]);
  });

  it("zeigt „Hello World“ nicht und kürzt die Beschreibungen am Handy nicht mit „…“ ab", async () => {
    const longText = "Nur für Proxmox: zeigt alle Backups deiner Proxmox-Server auf einen Blick und warnt bei fehlenden oder fehlgeschlagenen Sicherungen.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      row("backups", "servers", longText),
      row("hello-world", "example", "Beispiel-Erweiterung für Nodvard Deck, gedacht für Entwickler."),
    ]))));
    render(<ModulesStep onBack={() => {}} onNext={() => {}} />);
    const card = await screen.findByTestId("module-backups");
    expect(card.querySelector("[class*='line-clamp']")).toBeNull();
    expect(screen.queryByTestId("module-hello-world")).toBeNull();
    expect(screen.queryByText("Zum Ausprobieren")).toBeNull();
    expect(screen.queryByText("Für Entwickler")).toBeNull();
  });
});

describe("Schritt „Was willst du nutzen?“: Beta-Kennzeichen", () => {
  const module = (id: string, extra: Record<string, unknown> = {}) => ({
    id, name: id, state: "disabled", description: "Beschreibung.", icon: null, last_error: null, category: "servers", sort_order: 10, ...extra,
  });
  afterEach(() => vi.unstubAllGlobals());

  it("kennzeichnet nur Module mit stage beta und erklärt es einmal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      module("system", { stage: "stable" }),
      module("gameserver", { stage: "beta", sort_order: 60 }),
      module("network", { stage: "beta", sort_order: 70 }),
      module("alt"),
    ]))));
    render(<ModulesStep onBack={() => {}} onNext={() => {}} />);
    await screen.findByTestId("module-system");
    expect(within(screen.getByTestId("module-gameserver")).getByTestId("beta-badge")).toBeInTheDocument();
    expect(within(screen.getByTestId("module-network")).getByTestId("beta-badge")).toBeInTheDocument();
    expect(within(screen.getByTestId("module-system")).queryByTestId("beta-badge")).toBeNull();
    expect(within(screen.getByTestId("module-alt")).queryByTestId("beta-badge")).toBeNull();
    expect(screen.getAllByTestId("beta-explainer")).toHaveLength(1);
    expect(screen.getByTestId("beta-explainer")).toHaveTextContent(BETA_HINT);
  });

  it("ohne Beta-Modul gibt es weder Abzeichen noch Erklärung", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([module("system", { stage: "stable" }), module("alt")]))));
    render(<ModulesStep onBack={() => {}} onNext={() => {}} />);
    await screen.findByTestId("module-system");
    expect(screen.queryByTestId("beta-badge")).toBeNull();
    expect(screen.queryByTestId("beta-explainer")).toBeNull();
  });
});
