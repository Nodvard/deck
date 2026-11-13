import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { mockHostsApi, reply, type Call } from "../../test/hostsMock";
import { TelemetrySettings } from "./TelemetrySettings";

const OFF = { basis: false, nutzung: false, fehler: false };
const ID = "81f813f9-7e6c-4276-905d-b2bdf8d678cc";

function backend(blocked: string | null = null) {
  let choices = { ...OFF };
  let history: unknown[] = [];
  let id = ID;
  function state(selected = choices) {
    return { choices, blocked, preview: { schema: 1, id,
      ...(selected.basis ? { basis: { version: "0.12.1", produkt: "deck", installation: "docker", arch: "arm64", sprache: "de" } } : {}),
      ...(selected.nutzung ? { nutzung: { module: ["pihole"], fremd: "0", server: "2-5", benutzer: "1", zwei_faktor: false, sicherungen_aktiv: false } } : {}),
      ...(selected.fehler ? { fehler: { arten: { "ssh.timeout": 2 } } } : {}),
    }, history, next_send_at: null, pending_deletions: 0 };
  }
  return mockHostsApi({
    "GET /telemetry": () => state(),
    "POST /telemetry/preview": (call: Call) => state(call.body as typeof choices),
    "PUT /telemetry": (call: Call) => { choices = call.body as typeof choices; return state(); },
    "POST /telemetry/send": () => { history = [{ at: "2026-11-04T12:00:00Z", operation: "report", outcome: "sent", report: state().preview }]; return { result: "sent" }; },
    "DELETE /telemetry": () => { choices = { ...OFF }; id = "1d210cad-0eb0-4488-b155-30cc23b9b99d"; return { result: "deleted" }; },
  });
}

beforeEach(() => useAuthStore.setState({ accessToken: "test", status: "authenticated", user: { id: "u", username: "owner", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] } }));
afterEach(() => vi.unstubAllGlobals());

it("zeigt die echte JSON-Antwort und sendet bei ausgeschalteten Schaltern nichts", async () => {
  const calls = backend();
  render(<TelemetrySettings />);
  expect(JSON.parse((await screen.findByLabelText("JSON-Vorschau")).textContent!)).toEqual({ schema: 1, id: ID });
  expect(screen.getAllByRole("switch")).toHaveLength(3);
  for (const toggle of screen.getAllByRole("switch")) expect(toggle).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Jetzt senden" })).toBeDisabled();
  expect(calls.map((call) => call.method)).toEqual(["GET"]);
});

it("zeigt jede Stufe vor dem Speichern und speichert die ausdrückliche Auswahl", async () => {
  const calls = backend();
  render(<TelemetrySettings />);
  fireEvent.click(await screen.findByRole("switch", { name: "Stufe 2: Nutzung" }));
  await waitFor(() => expect(screen.getByLabelText("JSON-Vorschau")).toHaveTextContent('"pihole"'));
  const preview = JSON.parse(screen.getByLabelText("JSON-Vorschau").textContent!);
  expect(Object.keys(preview).sort()).toEqual(["id", "nutzung", "schema"]);
  expect(screen.getByRole("button", { name: "Jetzt senden" })).toBeDisabled();
  expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Auswahl speichern" }));
  expect(await screen.findByText("Auswahl gespeichert.")).toBeInTheDocument();
  expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ basis: false, nutzung: true, fehler: false });
  fireEvent.click(screen.getByRole("button", { name: "Jetzt senden" }));
  expect(await screen.findByText("Bericht gesendet.")).toBeInTheDocument();
  expect(await screen.findByText(/Gesendet$/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("switch", { name: "Stufe 2: Nutzung" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Auswahl speichern" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Auswahl speichern" }));
  await waitFor(() => expect(calls.filter((call) => call.method === "PUT")).toHaveLength(2));
  expect(calls.filter((call) => call.method === "PUT")[1].body).toEqual(OFF);
});

it.each(["Stufe 1: Basis", "Stufe 3: Fehler"])("zeigt den Block für %s", async (label) => {
  backend();
  render(<TelemetrySettings />);
  fireEvent.click(await screen.findByRole("switch", { name: label }));
  await waitFor(() => expect(Object.keys(JSON.parse(screen.getByLabelText("JSON-Vorschau").textContent!))).toContain(label.includes("Basis") ? "basis" : "fehler"));
});

it.each(["admin", "privacy", "endpoint"])("sperrt Opt-in und Versand bei %s", async (reason) => {
  const calls = backend(reason);
  render(<TelemetrySettings />);
  for (const toggle of await screen.findAllByRole("switch")) expect(toggle).toBeDisabled();
  expect(screen.getByRole("button", { name: "Jetzt senden" })).toBeDisabled();
  expect(calls).toHaveLength(1);
});

it("löscht, zeigt eine neue Kennung und schaltet alle Stufen aus", async () => {
  const calls = backend();
  render(<TelemetrySettings />);
  await screen.findByLabelText("JSON-Vorschau");
  fireEvent.click(screen.getByRole("button", { name: "Meine Daten löschen" }));
  expect(await screen.findByText(/Löschwunsch abgeschlossen/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("JSON-Vorschau")).not.toHaveTextContent(ID));
  expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
  for (const toggle of screen.getAllByRole("switch")) expect(toggle).not.toBeChecked();
});

it("beachtet die 24-Stunden-Sperre auch beim manuellen Versand", async () => {
  const calls = mockHostsApi({ "GET /telemetry": { choices: { ...OFF, basis: true }, blocked: null, preview: { schema: 1, id: ID }, history: [], next_send_at: "2099-01-01T00:00:00Z", pending_deletions: 0 } });
  render(<TelemetrySettings />);
  expect(await screen.findByRole("button", { name: "Jetzt senden" })).toBeDisabled();
  expect(calls).toHaveLength(1);
});

it("zeigt Ladefehler und bietet einen erneuten Versuch", async () => {
  mockHostsApi({ "GET /telemetry": reply(503, { detail: "Datenbank nicht erreichbar" }) });
  render(<TelemetrySettings />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Datenbank nicht erreichbar");
  expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
});

it("verwirft eine alte Vorschau, wenn die neue Auswahl nicht geladen werden kann", async () => {
  backend();
  const online = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    String(input).endsWith("/telemetry/preview")
      ? Promise.resolve(new Response(JSON.stringify({ detail: "Vorschau nicht erreichbar" }), { status: 503 }))
      : online(input, init)));
  render(<TelemetrySettings />);
  await screen.findByLabelText("JSON-Vorschau");
  fireEvent.click(screen.getByRole("switch", { name: "Stufe 1: Basis" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Vorschau nicht erreichbar");
  expect(screen.queryByLabelText("JSON-Vorschau")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Auswahl speichern" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Jetzt senden" })).toBeDisabled();
});

it("nennt offene Löschwünsche und behauptet keinen erfolgreichen Empfang", async () => {
  mockHostsApi({
    "GET /telemetry": { choices: OFF, blocked: null, preview: { schema: 1, id: ID }, history: [], next_send_at: null, pending_deletions: 1 },
    "DELETE /telemetry": { result: "delete_pending" },
  });
  render(<TelemetrySettings />);
  fireEvent.click(await screen.findByRole("button", { name: "Meine Daten löschen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Mindestens ein Löschwunsch ist noch offen");
  expect(await screen.findByText("Offene Löschwünsche: 1")).toBeInTheDocument();
});

it("versteckt den Bereich ohne Inhaberrechte und ruft die API nicht auf", () => {
  useAuthStore.setState({ user: { ...useAuthStore.getState().user!, is_owner: false } });
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  render(<TelemetrySettings />);
  expect(screen.getByText(/Nur der Inhaber/)).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it("bietet beim Einrichten gleich große Knöpfe und geht ohne Zustimmung weiter", async () => {
  const calls = backend();
  const next = vi.fn();
  render(<TelemetrySettings onNext={next} />);
  const skip = await screen.findByRole("button", { name: "Weiter ohne Teilen" });
  const share = screen.getByRole("button", { name: "Teilen" });
  expect(skip.className).toBe(share.className);
  expect(share).toBeDisabled();
  fireEvent.click(skip);
  await waitFor(() => expect(next).toHaveBeenCalledOnce());
  expect(calls.find((call) => call.method === "PUT")?.body).toEqual(OFF);
  expect(calls.some((call) => call.path === "/telemetry/send")).toBe(false);
});
