import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type HostOut } from "../../../lib/hosts";
import { useAuthStore } from "../../../state/auth";
import { DECK_EDITION, useEditionStore } from "../../../state/edition";
import { hostFixture, mockHostsApi, reply } from "../../../test/hostsMock";
import { HostForm } from "./HostForm";

function renderForm(host?: HostOut) {
  const onSaved = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><HostForm host={host} onSaved={onSaved} /></QueryClientProvider>);
  return onSaved;
}

const host = (over: Record<string, unknown> = {}) => hostFixture(over) as HostOut;
const checkbox = (name: string) => screen.getByRole("checkbox", { name });
const custom = () => screen.getByLabelText("Eigene Markierungen");

beforeEach(() => {
  useEditionStore.setState({ edition: DECK_EDITION });
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useEditionStore.setState({ edition: DECK_EDITION });
});

describe("Server-Markierungen", () => {
  it("bietet deutsche Haken mit Erklärungen und darunter eigene Markierungen an", () => {
    renderForm();
    const group = screen.getByRole("group", { name: "Markierungen" });
    expect(within(group).getAllByRole("checkbox")).toHaveLength(5);
    for (const label of ["Docker verwalten", "Docker nur bedienen", "Gameserver", "Nodvard Deck läuft hier", "NAS"]) {
      expect(checkbox(label)).not.toBeChecked();
      expect(checkbox(label)).toHaveAccessibleDescription();
    }
    expect(custom()).toHaveValue("");
  });

  it("vereinigt beim Anlegen Haken und Freitext ohne doppelte Markierungen", async () => {
    const saved = host();
    const calls = mockHostsApi({ "POST /hosts": saved });
    const onSaved = renderForm();
    fireEvent.change(screen.getByLabelText("Kurzname"), { target: { value: " PI " } });
    fireEvent.change(screen.getByLabelText("Adresse (IP oder Name)"), { target: { value: "10.0.0.5" } });
    for (const label of ["Docker verwalten", "Docker nur bedienen", "Gameserver", "Nodvard Deck läuft hier", "NAS"]) fireEvent.click(checkbox(label));
    fireEvent.change(custom(), { target: { value: " Docker, WEB web, NAS, archiv " } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(calls[0].body).toEqual({
      name: "pi", display_name: "pi", address: "10.0.0.5", os_family: "linux",
      tags: expect.arrayContaining(["docker", "docker-bedienen", "gameserver", "dashboard", "nas", "web", "archiv"]),
    });
    expect((calls[0].body as { tags: string[] }).tags).toHaveLength(7);
  });

  it("lädt vorhandene Haken und erhält eigene und ältere Markierungen beim Bearbeiten", async () => {
    const saved = host();
    const calls = mockHostsApi({ "PATCH /hosts/h1": saved });
    renderForm(host({ tags: ["nas", "docker", "lattice", "nodvard-deck", "web"] }));
    expect(checkbox("Docker verwalten")).toBeChecked();
    expect(checkbox("NAS")).toBeChecked();
    expect(custom()).toHaveValue("lattice, nodvard-deck, web");
    fireEvent.click(checkbox("Docker verwalten"));
    fireEvent.click(checkbox("Gameserver"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ tags: expect.arrayContaining(["nas", "gameserver", "lattice", "nodvard-deck", "web"]) });
    expect((calls[0].body as { tags: string[] }).tags).toHaveLength(5);
  });

  it("kann alle manuellen Markierungen entfernen", async () => {
    const calls = mockHostsApi({ "PATCH /hosts/h1": host({ tags: [] }) });
    renderForm(host({ tags: ["docker", "nas", "web"] }));
    fireEvent.click(checkbox("Docker verwalten"));
    fireEvent.click(checkbox("NAS"));
    fireEvent.change(custom(), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ tags: [] });
  });

  it("zeigt verwaltete Haken gesperrt und schickt verwaltete Markierungen auch aus Freitext nicht mit", async () => {
    const calls = mockHostsApi({ "PATCH /hosts/h1": host() });
    renderForm(host({ tags: ["proxmox", "docker", "nas"], managed_tags: ["proxmox", "docker"] }));
    expect(checkbox("Docker verwalten")).toBeChecked();
    expect(checkbox("Docker verwalten")).toBeDisabled();
    expect(custom()).toHaveValue("");
    fireEvent.change(custom(), { target: { value: "docker proxmox web" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ tags: ["nas", "web"] });
  });

  it("erkennt bekannte Markierungen im Freitext und entfernt sie beim Abwählen", async () => {
    const calls = mockHostsApi({ "PATCH /hosts/h1": host({ tags: ["web"] }) });
    renderForm(host({ tags: [] }));
    fireEvent.change(custom(), { target: { value: " WEB, Docker docker " } });
    expect(checkbox("Docker verwalten")).toBeChecked();
    fireEvent.click(checkbox("Docker verwalten"));
    expect(checkbox("Docker verwalten")).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ tags: ["web"] });
  });

  it("sendet ohne Änderung und bei anderer Reihenfolge keinen PATCH", async () => {
    const calls = mockHostsApi({});
    renderForm(host({ tags: ["web", "nas", "docker", "lattice", "nodvard-deck"] }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Nichts geändert.")).toBeInTheDocument();
    fireEvent.change(custom(), { target: { value: "nodvard-deck, WEB lattice web" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Nichts geändert.")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("zeigt Fehler zu Markierungen am Formular und erhält die Auswahl", async () => {
    mockHostsApi({ "PATCH /hosts/h1": reply(422, {
      detail: [{ type: "value_error", loc: ["body", "tags"], msg: "Ungültige Markierung 'web!'." }],
    }) });
    renderForm(host());
    fireEvent.change(custom(), { target: { value: "web!" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ungültige Markierung 'web!'.");
    expect(custom()).toHaveValue("web!");
    expect(checkbox("Docker verwalten")).toBeChecked();
  });

  it("zeigt in Dock die Docker-Auswahl und NAS ohne Hinweise auf andere Module", () => {
    useEditionStore.setState({ edition: { ...DECK_EDITION, choose_modules: false } });
    const { container } = render(<QueryClientProvider client={new QueryClient()}><HostForm onSaved={vi.fn()} /></QueryClientProvider>);
    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    for (const label of ["Docker verwalten", "Docker nur bedienen", "NAS"]) expect(checkbox(label)).toBeInTheDocument();
    expect(container).not.toHaveTextContent(/Shield|Gameserver|Nodvard Deck/);
  });

  it("erhält vorhandene Markierungen anderer Module auch in Dock", async () => {
    useEditionStore.setState({ edition: { ...DECK_EDITION, choose_modules: false } });
    const calls = mockHostsApi({ "PATCH /hosts/h1": host() });
    renderForm(host({ tags: ["gameserver", "dashboard", "nas", "legacy"] }));
    expect(custom()).toHaveValue("gameserver, dashboard, legacy");
    fireEvent.click(checkbox("NAS"));
    fireEvent.click(checkbox("Docker nur bedienen"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ tags: expect.arrayContaining(["gameserver", "dashboard", "legacy", "docker-bedienen"]) });
    expect((calls[0].body as { tags: string[] }).tags).toHaveLength(4);
  });
});
