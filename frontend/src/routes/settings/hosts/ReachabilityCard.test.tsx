import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockHostsApi, reply } from "../../../test/hostsMock";
import { useAuthStore } from "../../../state/auth";
import { ReachabilityCard, intervalLabel } from "./ReachabilityCard";

function setUser(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
  });
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ReachabilityCard /></QueryClientProvider>);
}

const SETTINGS = [
  { key: "autonomy.mode", value: "propose" },
  { key: "hosts.reachability.enabled", value: true },
  { key: "hosts.reachability.interval_minutes", value: 2 },
];

beforeEach(() => setUser(["*"]));
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Karte „Erreichbarkeit prüfen“", () => {
  it("zeigt Schalter und Abstand aus den Einstellungen", async () => {
    mockHostsApi({ "GET /settings": SETTINGS });
    renderCard();
    expect(await screen.findByText("Erreichbarkeit prüfen")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Erreichbarkeit regelmäßig prüfen" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("combobox", { name: "Wie oft prüfen" })).toHaveValue("2");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("ohne gespeicherte Werte gelten die Vorgaben: an, alle 2 Minuten", async () => {
    mockHostsApi({ "GET /settings": [] });
    renderCard();
    expect(await screen.findByRole("switch", { name: "Erreichbarkeit regelmäßig prüfen" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("combobox", { name: "Wie oft prüfen" })).toHaveValue("2");
  });

  it("speichert nur, was geändert wurde", async () => {
    const calls = mockHostsApi({
      "GET /settings": SETTINGS,
      "PUT /settings/hosts.reachability.interval_minutes": (c: { body: unknown }) => ({ key: "hosts.reachability.interval_minutes", value: (c.body as { value: number }).value }),
    });
    renderCard();
    fireEvent.change(await screen.findByRole("combobox", { name: "Wie oft prüfen" }), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].path).toBe("/settings/hosts.reachability.interval_minutes");
    expect(puts[0].body).toEqual({ value: 10 });
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("Ausschalten schickt nur den Schalter und sperrt die Auswahl", async () => {
    const calls = mockHostsApi({
      "GET /settings": SETTINGS,
      "PUT /settings/hosts.reachability.enabled": { key: "hosts.reachability.enabled", value: false },
    });
    renderCard();
    fireEvent.click(await screen.findByRole("switch", { name: "Erreichbarkeit regelmäßig prüfen" }));
    expect(screen.getByRole("combobox", { name: "Wie oft prüfen" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");
    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.path, c.body])).toEqual([
      ["/settings/hosts.reachability.enabled", { value: false }],
    ]);
  });

  it("zeigt einen Fehler des Servers und lässt den Knopf stehen", async () => {
    mockHostsApi({
      "GET /settings": SETTINGS,
      "PUT /settings/hosts.reachability.interval_minutes": reply(422, { detail: "hosts.reachability.interval_minutes muss zwischen 1 und 60 Minuten liegen." }),
    });
    renderCard();
    fireEvent.change(await screen.findByRole("combobox", { name: "Wie oft prüfen" }), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("muss zwischen 1 und 60 Minuten liegen");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
  });

  it("zeigt einen ungewöhnlichen gespeicherten Abstand trotzdem an", async () => {
    mockHostsApi({ "GET /settings": [{ key: "hosts.reachability.interval_minutes", value: 45 }] });
    renderCard();
    expect(await screen.findByRole("combobox", { name: "Wie oft prüfen" })).toHaveValue("45");
    expect(screen.getByRole("option", { name: "Alle 45 Minuten" })).toBeInTheDocument();
  });

  it("gibt es ohne das Recht settings.write gar nicht und fragt nichts ab", async () => {
    setUser(["hosts.read", "hosts.write"]);
    const calls = mockHostsApi({});
    renderCard();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText("Erreichbarkeit prüfen")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("meldet, wenn die Einstellung nicht geladen werden kann", async () => {
    mockHostsApi({ "GET /settings": reply(500, { detail: "kaputt" }) });
    renderCard();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Die Einstellung konnte nicht geladen werden."));
  });
});

describe("intervalLabel", () => {
  it("nennt den Abstand in einfachem Deutsch", () => {
    expect(intervalLabel(1)).toBe("Jede Minute");
    expect(intervalLabel(2)).toBe("Alle 2 Minuten");
    expect(intervalLabel(60)).toBe("Jede Stunde");
  });
});
