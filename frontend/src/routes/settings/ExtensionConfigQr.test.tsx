import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { useAuthStore } from "../../state/auth";
import { ExtensionConfigPage } from "./ExtensionConfigPage";

type Call = { method: string; url: string; body: unknown };
type Handler = (body: unknown, url: string) => unknown;

/** Antwortet nach "METHODE /pfad" (ohne Query), alles andere ist ein Fehler im Test. */
function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = (typeof input === "string" ? input : input.toString()).replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body });
    const handler = routes[`${method} ${url.split("?")[0]}`];
    if (!handler) throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    const result = await handler(body, url);
    if (result instanceof Response) return result;
    return result === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(result), { status: 200 });
  }));
  return calls;
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});

afterEach(() => vi.unstubAllGlobals());

const QR_PATH = "/ext/demo/subscribe-link";
const LINK = "ntfy://ntfy.example.org/nodvard-abc?display=Nodvard%20Deck";

const schema = (extra: object = { "x-qr-url": QR_PATH }) => ({
  type: "object",
  properties: { topic: { type: "string", title: "Thema" } },
  ...extra,
});

function settings(values: object = { topic: "nodvard-abc" }, extra?: object) {
  return { schema: schema(extra), values, secrets: [] };
}

const ext = (state = "enabled") => ({ id: "demo", name: "Demo", description: null, icon: "server", state, version: "1" });

function renderConfig() {
  return render(
    <MemoryRouter initialEntries={["/settings/extensions/demo"]}>
      <Routes><Route path="/settings/extensions/:extId" element={<><ExtensionConfigPage /><GlobalDialogs /></>} /></Routes>
    </MemoryRouter>,
  );
}

const linkCalls = (calls: Call[]) => calls.filter((c) => c.url.startsWith(QR_PATH));

describe("QR-Code (x-qr-url)", () => {
  it("zeigt unter dem Formular eine Karte mit QR-Code, Erklärung und dem Link als Text", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings(),
      [`GET ${QR_PATH}`]: () => ({ url: LINK, text: "Halte die Kamera auf den Code.", error: null }),
    });
    renderConfig();
    expect(await screen.findByTestId("qr-code")).toBeInTheDocument();
    expect(screen.getByTestId("qr-link")).toHaveTextContent(LINK);
    expect(screen.getByText("Halte die Kamera auf den Code.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "QR-Code" })).toBeInTheDocument();
    expect(screen.getByText(/Nach dem Speichern aktualisiert/)).toBeInTheDocument();
    expect(linkCalls(calls)).toHaveLength(1);
    // Die Karte steht nach den Einstellungen und vor der Verbindung.
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings.indexOf("Einstellungen")).toBeLessThan(headings.indexOf("QR-Code"));
    expect(headings.indexOf("QR-Code")).toBeLessThan(headings.indexOf("Verbindung"));
  });

  it("zeigt bei `error` den Satz statt eines Codes", async () => {
    mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({}),
      [`GET ${QR_PATH}`]: () => ({ url: null, text: null, error: "Es fehlt noch das Thema. Trage es ein und speichere." }),
    });
    renderConfig();
    expect(await screen.findByText("Es fehlt noch das Thema. Trage es ein und speichere.")).toBeInTheDocument();
    expect(screen.queryByTestId("qr-code")).toBeNull();
    expect(screen.queryByTestId("qr-link")).toBeNull();
  });

  it("lädt nach dem Speichern neu und zeigt dann den neuen Link", async () => {
    let link = LINK;
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings(),
      "PUT /extensions/demo/settings": (body) => {
        link = "ntfy://ntfy.example.org/nodvard-neu?display=Nodvard%20Deck";
        return settings((body as { values: object }).values);
      },
      [`GET ${QR_PATH}`]: () => ({ url: link, text: "Text", error: null }),
    });
    renderConfig();
    await waitFor(() => expect(screen.getByTestId("qr-link")).toHaveTextContent("nodvard-abc"));
    fireEvent.change(screen.getByLabelText("Thema"), { target: { value: "nodvard-neu" } });
    expect(await screen.findByText(/noch nicht gespeichert sind/)).toBeInTheDocument();
    expect(screen.getByTestId("qr-link")).toHaveTextContent("nodvard-abc"); // bis zum Speichern der gespeicherte Stand
    fireEvent.click(screen.getAllByRole("button", { name: "Speichern" })[0]);
    await screen.findByText("Einstellungen gespeichert.");
    await waitFor(() => expect(screen.getByTestId("qr-link")).toHaveTextContent("nodvard-neu"));
    expect(linkCalls(calls)).toHaveLength(2);
    expect(screen.queryByText(/noch nicht gespeichert sind/)).toBeNull();
  });

  it("ohne x-qr-url gibt es keine Karte und keine Anfrage", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({}, {}),
    });
    renderConfig();
    await screen.findByLabelText("Thema");
    expect(screen.queryByRole("heading", { name: "QR-Code" })).toBeNull();
    expect(calls.filter((c) => c.url.startsWith("/ext/"))).toEqual([]);
  });

  it("bei einer ausgeschalteten Erweiterung steht ein Hinweis, gefragt wird nicht", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext("disabled"),
      "GET /extensions/demo/settings": () => settings(),
    });
    renderConfig();
    expect(await screen.findByText("Der QR-Code erscheint, sobald die Erweiterung eingeschaltet ist.")).toBeInTheDocument();
    expect(screen.queryByTestId("qr-code")).toBeNull();
    expect(linkCalls(calls)).toEqual([]);
  });

  it("sagt es, wenn die Antwort nicht kommt, und lässt den Rest der Seite benutzbar", async () => {
    mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings(),
      [`GET ${QR_PATH}`]: () => new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 }),
    });
    renderConfig();
    const card = (await screen.findByRole("heading", { name: "QR-Code" })).closest("section")!;
    expect(await within(card).findByText("Der QR-Code konnte nicht geladen werden.")).toBeInTheDocument();
    expect(screen.getByLabelText("Thema")).toBeInTheDocument();
  });
});
