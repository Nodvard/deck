import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { checkFixture, mockHostsApi, reply } from "../../../test/hostsMock";
import { ConnectionCheck } from "./ConnectionCheck";

function show(routes: Record<string, unknown> = {}) {
  const calls = mockHostsApi({
    "GET /hosts/h1/ssh-retry": { failures: 3, retry_at: Date.now() / 1000 + 1800 },
    "GET /hosts": [],
    "POST /hosts/h1/check": checkFixture(),
    ...routes,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ConnectionCheck hostId="h1" /></QueryClientProvider>);
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

it("zeigt Ursachen und Pause, während Verbindung prüfen weiter funktioniert", async () => {
  const calls = show();
  await screen.findByText("Zugang abgelehnt");
  for (const text of [/Schlüssel fehlt/, /2FA/, /fail2ban/, /Duo/, /pausieren bis/]) {
    expect(screen.getByText(text)).toBeInTheDocument();
  }
  const check = screen.getByRole("button", { name: "Verbindung prüfen" });
  expect(check).toBeEnabled();
  fireEvent.click(check);
  await screen.findByText("Anmeldung als nodvard");
  expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/hosts/h1/check"]);
});

it("setzt zuerst zurück, prüft sofort und lädt den neuen Stand", async () => {
  let cleared = false;
  const calls = show({
    "GET /hosts/h1/ssh-retry": () => ({ failures: cleared ? 0 : 3, retry_at: Date.now() / 1000 + 1800 }),
    "POST /hosts/h1/ssh-retry": () => { cleared = true; },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Jetzt erneut prüfen" }));
  await screen.findByText("Anmeldung als nodvard");
  await waitFor(() => expect(screen.queryByText("Zugang abgelehnt")).not.toBeInTheDocument());
  expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
    "/hosts/h1/ssh-retry", "/hosts/h1/check",
  ]);
});

it("zeigt einen Fehler beim Zurücksetzen und startet dann keine Anmeldung", async () => {
  const calls = show({ "POST /hosts/h1/ssh-retry": reply(503, { detail: "Zurücksetzen fehlgeschlagen." }) });
  fireEvent.click(await screen.findByRole("button", { name: "Jetzt erneut prüfen" }));
  await screen.findByText("Zurücksetzen fehlgeschlagen.");
  expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/check"))).toBe(false);
  expect(screen.getByRole("button", { name: "Jetzt erneut prüfen" })).toBeEnabled();
});

it("zeigt abgelaufene Pausen nicht als laufend an", async () => {
  show({ "GET /hosts/h1/ssh-retry": { failures: 3, retry_at: 1 } });
  await screen.findByText("Zugang abgelehnt");
  expect(screen.queryByText(/pausieren bis/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Jetzt erneut prüfen" })).toBeEnabled();
});

it("blendet den Hinweis nach erfolgreicher Verbindungsprüfung aus", async () => {
  let checked = false;
  const calls = show({
    "GET /hosts/h1/ssh-retry": () => ({ failures: checked ? 0 : 3, retry_at: checked ? 0 : Date.now() / 1000 + 1800 }),
    "POST /hosts/h1/check": () => { checked = true; return checkFixture(); },
  });
  await screen.findByText("Zugang abgelehnt");
  fireEvent.click(screen.getByRole("button", { name: "Verbindung prüfen" }));
  await screen.findByText("Anmeldung als nodvard");
  await waitFor(() => expect(screen.queryByText("Zugang abgelehnt")).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Jetzt erneut prüfen" })).not.toBeInTheDocument();
  expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/hosts/h1/check"]);
});
