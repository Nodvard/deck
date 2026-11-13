import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { API, proposeAndApprove } from "./api";
import { Fail2banSettings } from "./Fail2banSettings";

vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), proposeAndApprove: vi.fn() }));

const settings = {
  bantime: 600, findtime: 900, maxretry: 5,
  ignoreip: ["127.0.0.0/8", "203.0.113.9"], deck_address: "203.0.113.9",
  jails: ["sshd"], available_jails: ["sshd", "nginx-http-auth"],
};

beforeEach(() => {
  vi.mocked(proposeAndApprove).mockReset().mockResolvedValue({ kind: "ok", text: "Erledigt." });
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify(settings), { status: 200 })));
});

async function open(canManage = true, hostId = "h1", onSaved = vi.fn()) {
  render(<Fail2banSettings hostId={hostId} canManage={canManage} onSaved={onSaved} />);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Fail2ban-Einstellungen" }));
  await screen.findByLabelText("Versuche");
  return onSaved;
}

it("lädt je Server nur lesend und zeigt Pflicht-Jail und feste SSH-Ausnahme", async () => {
  await open(true, "server-2");
  expect(fetch).toHaveBeenCalledWith(`/api/v1${API}/hosts/server-2/fail2ban`, expect.objectContaining({ headers: expect.any(Object) }));
  expect(screen.getByLabelText(/Deck-Verbindung/)).toHaveValue("203.0.113.9");
  expect(screen.getByLabelText(/Deck-Verbindung/)).toHaveAttribute("readonly");
  expect(screen.getByRole("checkbox", { name: "sshd (Pflicht)" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "sshd (Pflicht)" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "nginx-http-auth" })).not.toBeChecked();
  expect(screen.getByLabelText(/Weitere Ausnahmen/)).toHaveValue("127.0.0.0/8");
  expect(proposeAndApprove).not.toHaveBeenCalled();
});

it("übergibt Werte, zusätzliche Ausnahmen und Jail-Auswahl ans Aktions-Gate", async () => {
  const onSaved = await open();
  fireEvent.change(screen.getByLabelText("Sperrdauer (Sekunden)"), { target: { value: "3600" } });
  fireEvent.change(screen.getByLabelText("Fundzeit (Sekunden)"), { target: { value: "300" } });
  fireEvent.change(screen.getByLabelText("Versuche"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText(/Weitere Ausnahmen/), { target: { value: "192.0.2.0/24\n2001:db8::/64" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "nginx-http-auth" }));
  fireEvent.click(screen.getByRole("button", { name: "Einstellungen übernehmen" }));
  await waitFor(() => expect(proposeAndApprove).toHaveBeenCalledWith(`${API}/hosts/h1/fail2ban`, {
    bantime: 3600, findtime: 300, maxretry: 3,
    ignoreip: ["192.0.2.0/24", "2001:db8::/64"], jails: ["sshd", "nginx-http-auth"],
  }, "medium", expect.any(AbortSignal)));
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
});

it("nimmt einen zusätzlichen Jail aus der Auswahl und behält sshd", async () => {
  await open();
  const jail = screen.getByRole("checkbox", { name: "nginx-http-auth" });
  fireEvent.click(jail);
  fireEvent.click(jail);
  fireEvent.click(screen.getByRole("button", { name: "Einstellungen übernehmen" }));
  await waitFor(() => expect(proposeAndApprove).toHaveBeenCalledWith(expect.any(String),
    expect.objectContaining({ jails: ["sshd"] }), "medium", expect.any(AbortSignal)));
});

it("speichert 256 weitere Ausnahmen auch nach erneutem Laden", async () => {
  const onSaved = await open();
  const exceptions = Array.from({ length: 256 }, (_, i) => `192.0.2.${i}`);
  vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify({
    ...settings, ignoreip: [...exceptions, settings.deck_address],
  }), { status: 200 }));
  fireEvent.change(screen.getByLabelText(/Weitere Ausnahmen/), { target: { value: exceptions.join("\n") } });

  for (let count = 1; count <= 2; count++) {
    fireEvent.click(screen.getByRole("button", { name: "Einstellungen übernehmen" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(count));
    expect(screen.getByLabelText(/Weitere Ausnahmen/)).toHaveValue(exceptions.join("\n"));
  }

  expect(proposeAndApprove).toHaveBeenCalledTimes(2);
  for (const [, body] of vi.mocked(proposeAndApprove).mock.calls) {
    expect(body).toMatchObject({ ignoreip: exceptions });
  }
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("zeigt Lesefehler mit erneutem Laden und bietet ohne Daten kein Übernehmen an", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Hilfsskript veraltet" }), { status: 409 }));
  render(<Fail2banSettings hostId="h1" canManage onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Fail2ban-Einstellungen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Hilfsskript veraltet");
  expect(screen.queryByRole("button", { name: "Einstellungen übernehmen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  expect(await screen.findByLabelText("Versuche")).toHaveValue(5);
});

it("zeigt den Konfigurationsfehler und behält die Eingaben für eine Korrektur", async () => {
  vi.mocked(proposeAndApprove).mockResolvedValue({ kind: "error", text: "Konfigurationstest fehlgeschlagen" });
  const onSaved = await open();
  fireEvent.change(screen.getByLabelText(/Weitere Ausnahmen/), { target: { value: "ungültig" } });
  fireEvent.click(screen.getByRole("button", { name: "Einstellungen übernehmen" }));
  expect(await screen.findByText(/Konfigurationstest fehlgeschlagen/)).toBeInTheDocument();
  expect(screen.getByLabelText(/Weitere Ausnahmen/)).toHaveValue("ungültig");
  expect(onSaved).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("lässt ohne Verwaltungsrecht nur das Lesen zu", async () => {
  await open(false);
  expect(screen.getByLabelText("Versuche")).toBeDisabled();
  expect(screen.getByLabelText(/Weitere Ausnahmen/)).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "nginx-http-auth" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Einstellungen übernehmen" })).not.toBeInTheDocument();
});

it("kennzeichnet gespeicherte Werte bei nicht erreichbarem SSH-Jail", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ ...settings, runtime: false }), { status: 200 }));
  await open();
  expect(screen.getByRole("status")).toHaveTextContent("gespeicherte Einstellungen, sonst Standardwerte");
  expect(proposeAndApprove).not.toHaveBeenCalled();
});

it("wartet auf Freigabe, ohne einen übernommenen Stand vorzutäuschen", async () => {
  vi.mocked(proposeAndApprove).mockResolvedValue({ kind: "warn", text: "Wartet auf Freigabe unter Aktionen." });
  const onSaved = await open();
  fireEvent.click(screen.getByRole("button", { name: "Einstellungen übernehmen" }));
  expect(await screen.findByText(/Wartet auf Freigabe/)).toBeInTheDocument();
  expect(onSaved).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(1);
});
