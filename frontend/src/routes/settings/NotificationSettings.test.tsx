import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { isValidElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { mockHostsApi, reply, type Call } from "../../test/hostsMock";
import { routes } from "../router";
import { NotificationSettings } from "./NotificationSettings";
import { SettingsLayout } from "./SettingsLayout";

const KEY = "notifications.push_types";
const types = [
  { id: "host.down", label: "Server nicht erreichbar" },
  { id: "shield.security_updates", label: "Sicherheitsupdates verfügbar" },
  { id: "backups.failed", label: "Backup fehlgeschlagen" },
];
const stored = {
  "host.down": { enabled: false, priority: "urgent" },
  "shield.security_updates": { enabled: true, priority: null },
};

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["settings.write"] },
  });
});
afterEach(() => vi.unstubAllGlobals());

function mockSettings(extra: Record<string, unknown> = {}) {
  return mockHostsApi({
    "GET /settings/notification-types": types,
    "GET /settings": [{ key: KEY, value: stored }],
    "PUT /settings/notifications.push_types": ({ body }: Call) => ({ key: KEY, ...(body as object) }),
    ...extra,
  });
}

describe("Benachrichtigungen je Art", () => {
  it("zeigt alle gelieferten Arten mit gespeichertem Schalter und Priorität", async () => {
    mockSettings();
    render(<NotificationSettings />);
    const table = await screen.findByRole("table", { name: "Meldungsarten" });
    expect(within(table).getAllByRole("row")).toHaveLength(types.length + 1);
    for (const { label } of types) expect(within(table).getByRole("rowheader", { name: label })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Server nicht erreichbar: Push" })).not.toBeChecked();
    expect(screen.getByLabelText("Server nicht erreichbar: Priorität")).toHaveValue("urgent");
    expect(screen.getByRole("switch", { name: "Backup fehlgeschlagen: Push" })).toBeChecked();
    expect(screen.getByLabelText("Backup fehlgeschlagen: Priorität")).toHaveValue("");
    expect(within(screen.getByLabelText("Backup fehlgeschlagen: Priorität")).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Wie bisher", "min", "low", "default", "high", "urgent"]);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("speichert Schalter und Priorität gemeinsam und übernimmt die Serverantwort", async () => {
    const calls = mockSettings();
    render(<NotificationSettings />);
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("switch", { name: "Server nicht erreichbar: Push" }));
    fireEvent.change(screen.getByLabelText("Backup fehlgeschlagen: Priorität"), { target: { value: "low" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Benachrichtigungen gespeichert.")).toBeInTheDocument();
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ value: {
      ...stored,
      "host.down": { enabled: true, priority: "urgent" },
      "backups.failed": { enabled: true, priority: "low" },
    } });
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("kann eine Priorität wieder auf das bisherige Verhalten zurücksetzen", async () => {
    const calls = mockSettings();
    render(<NotificationSettings />);
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText("Server nicht erreichbar: Priorität"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Benachrichtigungen gespeichert.");
    expect(calls.find((call) => call.method === "PUT")?.body).toMatchObject({ value: { "host.down": { priority: null } } });
  });

  it("behält Änderungen bei einem Speicherfehler für einen neuen Versuch", async () => {
    mockSettings({ "PUT /settings/notifications.push_types": reply(500, { detail: "Speichern fehlgeschlagen." }) });
    render(<NotificationSettings />);
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("switch", { name: "Backup fehlgeschlagen: Push" }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Speichern fehlgeschlagen.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled());
    expect(screen.getByRole("switch", { name: "Backup fehlgeschlagen: Push" })).not.toBeChecked();
  });

  it("zeigt Ladefehler und lässt keine unvollständige Tabelle speichern", async () => {
    mockSettings({ "GET /settings/notification-types": reply(500, { detail: "Meldungsarten nicht geladen." }) });
    render(<NotificationSettings />);
    expect(await screen.findByText("Meldungsarten nicht geladen.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Speichern" })).not.toBeInTheDocument();
  });

  it("verlinkt den eingerichteten Bereich für Nutzer mit Einstellungsrecht", () => {
    render(<MemoryRouter initialEntries={["/settings/account"]}><SettingsLayout /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /Benachrichtigungen/ })).toHaveAttribute("href", "/settings/notifications");
    function findRoute(nodes: typeof routes): boolean {
      return nodes.some((route) => (route.path === "notifications" && isValidElement(route.element) && route.element.type === NotificationSettings) || findRoute(route.children ?? []));
    }
    expect(findRoute(routes)).toBe(true);
  });
});
