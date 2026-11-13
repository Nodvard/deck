import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Preferences } from "../lib/firstSteps";
import { useAuthStore } from "../state/auth";
import { TotpReminder } from "./TotpReminder";

let preferences: Preferences;
let enabled: boolean | undefined;
let patches: unknown[];
let patchFails: boolean;
let getFails: string | null;

const key = (id = "u1") => ["me", "totp-reminder", id];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function login(id = "u1") {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id, username: id, display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function Location() {
  return <p data-testid="location">{useLocation().pathname}</p>;
}

function renderReminder(queryClient = client()) {
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><TotpReminder /><Location /></MemoryRouter>
      </QueryClientProvider>,
    ),
    queryClient,
  };
}

async function loaded(queryClient: QueryClient, id = "u1") {
  await waitFor(() => expect(queryClient.getQueryState(key(id))?.fetchStatus).toBe("idle"));
}

beforeEach(() => {
  preferences = { first_steps_dismissed: false, totp_setup_skipped: true, totp_reminder_dismissed: false };
  enabled = false;
  patches = [];
  patchFails = false;
  getFails = null;
  login();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (method === "GET" && path === getFails) return json({ detail: "Nicht erreichbar" }, 500);
    if (path === "/me/preferences" && method === "GET") return json(preferences);
    if (path === "/me/preferences" && method === "PATCH") {
      const body = JSON.parse(String(init?.body));
      patches.push(body);
      if (patchFails) return json({ detail: "Speichern fehlgeschlagen" }, 500);
      preferences = { ...preferences, ...body };
      return json(preferences);
    }
    if (path === "/me" && method === "GET") return json({ id: useAuthStore.getState().user?.id, totp_enabled: enabled });
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TotpReminder", () => {
  it("zeigt nach dem Überspringen die Erinnerung und den direkten Link, ohne sie beim Folgen zu erledigen", async () => {
    renderReminder();
    const reminder = await screen.findByTestId("totp-reminder");
    expect(reminder).toHaveTextContent("Du hast die Einrichtung übersprungen.");
    expect(reminder).toHaveTextContent("bis Zwei-Faktor aktiv ist oder du sie bewusst ausblendest");
    const link = screen.getByRole("link", { name: "Einstellungen → Mein Konto" });
    expect(link).toHaveAttribute("href", "/settings/account");
    fireEvent.click(link);
    expect(screen.getByTestId("location")).toHaveTextContent("/settings/account");
    expect(patches).toEqual([]);
  });

  it("bleibt ohne Entscheidung nach erneutem Laden erhalten", async () => {
    const first = renderReminder();
    await screen.findByTestId("totp-reminder");
    first.unmount();
    renderReminder();
    expect(await screen.findByTestId("totp-reminder")).toBeInTheDocument();
    expect(patches).toEqual([]);
  });

  it("speichert das bewusste Ausblenden und bleibt auch mit neuem Zwischenspeicher weg", async () => {
    const first = renderReminder();
    fireEvent.click(await screen.findByRole("button", { name: "Erinnerung ausblenden" }));
    await waitFor(() => expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument());
    expect(patches).toEqual([{ totp_reminder_dismissed: true }]);
    expect(first.queryClient.getQueryData<Preferences>(["me", "preferences"])?.totp_reminder_dismissed).toBe(true);
    first.unmount();
    const second = renderReminder();
    await loaded(second.queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
  });

  it.each([
    { first_steps_dismissed: false, totp_setup_skipped: false, totp_reminder_dismissed: false },
    { first_steps_dismissed: false, totp_setup_skipped: true, totp_reminder_dismissed: true },
    { first_steps_dismissed: false },
  ])("zeigt ohne offenen Aufschub keine Erinnerung: %j", async (value) => {
    preferences = value;
    const rendered = renderReminder();
    await loaded(rendered.queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).not.toContain("/api/v1/me");
  });

  it.each([true, undefined])("zeigt bei aktivem oder unbekanntem Zwei-Faktor-Status keine Erinnerung: %s", async (value) => {
    enabled = value;
    const rendered = renderReminder();
    await loaded(rendered.queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
    expect(patches).toEqual([]);
  });

  it.each(["/me", "/me/preferences"])("behauptet bei fehlgeschlagener Abfrage von %s keinen offenen Schritt", async (path) => {
    getFails = path;
    const rendered = renderReminder();
    await loaded(rendered.queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
    expect(patches).toEqual([]);
  });

  it("wartet auch mit alten Daten im Zwischenspeicher auf eine aktuelle Antwort", async () => {
    const queryClient = client();
    queryClient.setQueryData(key(), { preferences, enabled: false });
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    renderReminder(queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
    await act(async () => finish(json({ ...preferences, totp_reminder_dismissed: true })));
    await loaded(queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
  });

  it("behält bei einem Speicherfehler die Erinnerung und erlaubt einen erneuten Versuch", async () => {
    patchFails = true;
    renderReminder();
    fireEvent.click(await screen.findByRole("button", { name: "Erinnerung ausblenden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Die Erinnerung konnte nicht ausgeblendet werden.");
    expect(screen.getByTestId("totp-reminder")).toBeInTheDocument();
    patchFails = false;
    fireEvent.click(screen.getByRole("button", { name: "Erinnerung ausblenden" }));
    await waitFor(() => expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument());
    expect(patches).toHaveLength(2);
  });

  it("zeigt nach einem Kontowechsel keine Erinnerung aus dem vorigen Konto", async () => {
    const rendered = renderReminder();
    await screen.findByTestId("totp-reminder");
    preferences = { ...preferences, totp_setup_skipped: false };
    act(() => login("u2"));
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
    await loaded(rendered.queryClient, "u2");
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
  });

  it("zeigt die Erinnerung nach Aktivierung beim nächsten Öffnen der Übersicht nicht mehr", async () => {
    const first = renderReminder();
    await screen.findByTestId("totp-reminder");
    first.unmount();
    enabled = true;
    renderReminder(first.queryClient);
    await loaded(first.queryClient);
    expect(screen.queryByTestId("totp-reminder")).not.toBeInTheDocument();
    expect(patches).toEqual([]);
  });
});
