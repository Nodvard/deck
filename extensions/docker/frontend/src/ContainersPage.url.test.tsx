import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContainersPage } from "./ContainersPage";

const PIHOLE = {
  id: "h1:pihole", name: "pihole", host: "pi", host_id: "h1", container: "pihole", is_self: false, state: "running",
  status: "Up 2 hours", tone: "good", url: "http://10.0.0.5:8088/admin/", url_detected: "http://10.0.0.5:8088/admin/", url_custom: false,
};
const AGENT = {
  id: "h1:portainer_agent", name: "portainer_agent", host: "pi", host_id: "h1", container: "portainer_agent", is_self: false,
  state: "running", status: "Up 2 hours", tone: "good", url: "http://pi.example:9001", url_detected: null, url_custom: true,
};

interface Call { url: string; method: string; body?: unknown }

function urlFetch(calls: Call[], putStatus = 200, putBody: unknown = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes("/ext/docker/widgets/matrix")) return new Response(JSON.stringify({ data: [PIHOLE, AGENT], meta: {} }), { status: 200 });
    if (url.includes("/ext/docker/image-updates")) return new Response(JSON.stringify({ hosts: {}, data: {} }), { status: 200 });
    if (url.includes("/ext/docker/stats")) return new Response(JSON.stringify({ data: {} }), { status: 200 });
    if (url.endsWith("/url") && method === "PUT") return new Response(JSON.stringify(putBody), { status: putStatus });
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

beforeEach(() => {
  window.history.replaceState({}, "", "/ext/docker/matrix");
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn(),
    promptDialog: vi.fn(),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("ContainersPage -- eigene Kachel-Adresse", () => {
  it("speichert eine eigene Adresse und lädt die Liste neu", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", urlFetch(calls));
    render(<ContainersPage />);
    const row = await screen.findByTestId("row-h1:pihole");
    fireEvent.click(within(row).getByRole("button", { name: "Adresse" }));
    expect(screen.getByText("Erkannt: http://10.0.0.5:8088/admin/")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Erkannte nehmen" })).toBeNull();
    // Die erkannte steht nur als Platzhalter: „Speichern“ ohne Eingabe würde sie sonst als eigene festschreiben.
    const input = screen.getByRole("textbox", { name: /Adresse, die/ });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "http://10.0.0.5:8088/admin/");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /Adresse, die/ }), { target: { value: " https://pihole.example/admin/ " } });
    const before = calls.filter((c) => c.url.includes("/widgets/matrix")).length;
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/widgets/matrix")).length).toBeGreaterThan(before));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/v1/ext/docker/containers/h1/pihole/url");
    expect(put?.body).toEqual({ url: "https://pihole.example/admin/" });
    expect(screen.queryByRole("button", { name: "Speichern" })).toBeNull();
  });

  it("nimmt die erkannte wieder und zeigt, wenn keine Weboberfläche erkannt wurde", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", urlFetch(calls));
    render(<ContainersPage />);
    const row = await screen.findByTestId("row-h1:portainer_agent");
    fireEvent.click(within(row).getByRole("button", { name: "Adresse (eigene)" }));
    expect(screen.getByText(/keine Weboberfläche erkannt/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Adresse, die/ })).toHaveValue("http://pi.example:9001");
    fireEvent.click(screen.getByRole("button", { name: "Erkannte nehmen" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ url: null }));
  });

  it("zeigt die Meldung des Servers und bleibt offen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", urlFetch(calls, 422, {
      detail: [{ type: "value_error", loc: ["body", "url"], msg: "Die Adresse darf keinen Benutzernamen und kein Passwort enthalten." }],
    }));
    render(<ContainersPage />);
    const row = await screen.findByTestId("row-h1:pihole");
    fireEvent.click(within(row).getByRole("button", { name: "Adresse" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Adresse, die/ }), { target: { value: "http://a:b@10.0.0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passwort");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeInTheDocument();
  });

  it("ohne apps.write kein Knopf", async () => {
    vi.stubGlobal("fetch", urlFetch([]));
    (window.__lattice.hasPermission as ReturnType<typeof vi.fn>).mockImplementation((p: string) => p !== "apps.write");
    render(<ContainersPage />);
    await screen.findByTestId("row-h1:pihole");
    expect(screen.queryByRole("button", { name: /^Adresse/ })).toBeNull();
  });
});
