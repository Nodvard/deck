import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { CommandPalette, matches, useCommandPaletteHotkey } from "./CommandPalette";

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function Harness() {
  const [open, setOpen] = useState(false);
  useCommandPaletteHotkey(setOpen);
  return (
    <>
      <Where />
      <CommandPalette open={open} onOpenChange={setOpen} />
    </>
  );
}

function renderHarness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Routes>
          <Route path="*" element={<Harness />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
  }));
});

describe("matches", () => {
  it("alle Suchwörter müssen vorkommen, Reihenfolge egal", () => {
    const item = { title: "game-win: Konsole", subtitle: "192.168.1.92", keywords: "vm bildschirm" };
    expect(matches(item, "kons game")).toBe(true);
    expect(matches(item, "game terminal")).toBe(false);
    expect(matches(item, "1.92")).toBe(true);
  });
});

describe("CommandPalette", () => {
  it("Strg+K öffnet, Suche findet die Konsole eines Gasts, Enter springt hin", async () => {
    renderHarness();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText("Suchen");
    fireEvent.change(input, { target: { value: "game kons" } });
    await screen.findByRole("option", { name: /game-win: Konsole/ });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/console/g-valheim"));
    expect(screen.queryByLabelText("Suchen")).toBeNull();
  });

  it("bietet das Terminal eines Hosts an (Liste von IDs)", async () => {
    renderHarness();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText("Suchen");
    fireEvent.change(input, { target: { value: "game ssh" } });
    fireEvent.click(await screen.findByRole("option", { name: /game-win: Terminal/ }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/terminal?host=g-valheim"));
  });

  it("findet Extension-Seiten und springt mit Pfeiltasten", async () => {
    renderHarness();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const input = await screen.findByLabelText("Suchen");
    fireEvent.change(input, { target: { value: "backup" } });
    const option = await screen.findByRole("option", { name: /^Backups/ });
    expect(option).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/ext/backups/backups"));
  });

  it("„Benutzer“ steht nur einmal in der Liste, keine doppelten Einträge", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    renderHarness();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText("Suchen");
    const names = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(new Set(names).size).toBe(names.length);
    fireEvent.change(input, { target: { value: "benutzer" } });
    expect(await screen.findAllByRole("option")).toHaveLength(1);
    fireEvent.change(input, { target: { value: "accounts" } });
    expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual(["Einstellungen: Benutzer"]);
    expect(errors.mock.calls.some((c) => String(c[0]).includes("same key"))).toBe(false);
    errors.mockRestore();
  });

  it("Betrachter sehen weder Terminal noch Einstellungen ohne Berechtigung", async () => {
    useAuthStore.setState({
      user: {
        id: "u2", username: "gast", display_name: null, email: null, is_owner: false, locale: "de",
        permissions: ["hosts.read", "jobs.read", "audit.read", "files.read", "notifications.read"],
      },
    });
    renderHarness();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText("Suchen");
    const names = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(names).not.toContain("Terminal");
    expect(names.filter((n) => n?.startsWith("Einstellungen:"))).toEqual([
      "Einstellungen: Mein Konto", "Einstellungen: Protokoll",
      "Einstellungen: Über Nodvard Deck", // ohne Berechtigung für jeden da
    ]);
    expect(names).toContain("Meldungen");
    fireEvent.change(input, { target: { value: "ssh" } });
    expect(await screen.findByText("Nichts gefunden.")).toBeInTheDocument();
  });

  it("Apps öffnen ihre Adresse in einem neuen Tab", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderHarness();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText("Suchen");
    fireEvent.change(input, { target: { value: "grafana" } });
    fireEvent.click(await screen.findByRole("option", { name: /grafana/ }));
    expect(open).toHaveBeenCalledWith("http://192.168.2.21:3000", "_blank", "noopener,noreferrer");
  });
});
