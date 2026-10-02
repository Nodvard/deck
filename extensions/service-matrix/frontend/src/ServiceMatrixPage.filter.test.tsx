/** Server-Filter (`?host=`): Zusammenfassung, "Prüfe Images …" und Knopf gelten nur für diesen Host. */
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ServiceMatrixPage, scopeImages } from "./ServiceMatrixPage";

const SERVICES = [
  { id: "h1:nginx", name: "nginx", host: "raspberrypi", host_id: "h1", container: "nginx", is_self: false, state: "running", status: "Up", tone: "good", url: null },
  { id: "h2:db", name: "db", host: "docker", host_id: "h2", container: "db", is_self: false, state: "running", status: "Up", tone: "good", url: null },
];

const digest = (c: string) => "sha256:" + c.repeat(64);
const host = (over: Record<string, unknown> = {}) => ({ checking: false, checked_at: "2026-09-30T10:00:00+00:00", error: null, ...over });

function images(h2: Record<string, unknown> = {}) {
  return {
    data: {
      "h1:nginx": { image: "nginx:1.27", status: "current", reason: null, remote_digest: digest("a"), registry_at: null },
      "h2:db": { image: "postgres:16", status: "update", reason: null, remote_digest: digest("b"), registry_at: null },
    },
    hosts: { h1: host(), h2: host(h2) },
  };
}

function setup(body: unknown) {
  window.history.replaceState({}, "", "/ext/service-matrix/matrix?host=h1");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }));
    if (url.includes("/image-updates")) return new Response(JSON.stringify(body));
    if (url.includes("/stats")) return new Response(JSON.stringify({ data: {} }));
    throw new Error(url);
  }));
}

beforeEach(() => vi.unstubAllGlobals());

describe("Image-Zusammenfassung mit Server-Filter", () => {
  it("zählt nur den gefilterten Host (Update auf einem anderen Host zählt nicht mit)", async () => {
    setup(images());
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect(screen.queryByText("db")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("image-summary")).toBeInTheDocument());
    const text = screen.getByTestId("image-summary").textContent ?? "";
    expect(text).not.toContain("mit Update");
    expect(text).toContain("alles aktuell");
  });

  it("ein anderer Host, der nicht erreichbar ist, stört die Zeile nicht", async () => {
    setup(images({ error: "Keine Verbindung" }));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-summary")).toBeInTheDocument());
    expect(screen.getByTestId("image-summary").textContent).not.toContain("nicht erreichbar");
  });

  it("prüft gerade nur ein anderer Host: kein Hinweis und Knopf bleibt bedienbar", async () => {
    setup(images({ checking: true }));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    const button = await screen.findByRole("button", { name: "Image-Updates prüfen" });
    expect(button).not.toBeDisabled();
    expect(screen.getByTestId("image-summary").textContent).not.toContain("Image-Quellen werden gefragt");
  });

  it("prüft der gefilterte Host selbst, steht der Hinweis da", async () => {
    const body = images();
    body.hosts.h1.checking = true;
    setup(body);
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect(await screen.findByRole("button", { name: "Prüfe Images …" })).toBeDisabled();
  });
});

describe("scopeImages", () => {
  it("ohne Filter bleibt alles", () => {
    const all = images();
    expect(scopeImages(all as never, null)).toBe(all);
  });
  it("mit Filter nur Host und Container mit dem Präfix", () => {
    const scoped = scopeImages({ ...images(), applying: { "h1:a": {}, "h2:b": {} } } as never, "h1")!;
    expect(Object.keys(scoped.data)).toEqual(["h1:nginx"]);
    expect(Object.keys(scoped.hosts)).toEqual(["h1"]);
    expect(Object.keys(scoped.applying ?? {})).toEqual(["h1:a"]);
  });
});
