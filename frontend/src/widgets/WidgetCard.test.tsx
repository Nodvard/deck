import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import type { WidgetOut } from "./types";
import { WidgetCard } from "./WidgetCard";

/**
 * Höhe nach Inhalt (src/widgets/fit.ts). jsdom kennt kein Layout; die Höhen kommen hier aus festen Werten: Kopf und
 * Rahmen 47 px, jede Listenzeile 48 px. So lässt sich prüfen, welche Stufe die Karte meldet und wo sie kürzt.
 */
const CHROME = 47;
const ROW = 48;

const WIDGET: WidgetOut = {
  id: "certificates", ext_id: "npm", title: "Zertifikate", icon: null, description: null,
  size: { w: 4, h: 3, min_w: 1, min_h: 1 },
  refresh: { interval_s: null, ws_channel: null },
  data_endpoint: "widgets/certificates",
  view: {
    kind: "list", empty_text: "Keine Zertifikate", max_items: null,
    item: { title: "{{ domain }}", subtitle: null, icon: null, badge: null, actions: [] },
  },
  permissions: [], component: null, default_enabled: true,
} as unknown as WidgetOut;

function rowsOf(content: Element): number {
  return content.querySelectorAll("[data-fit-row]").length;
}

const descriptors: [object, string, PropertyDescriptor | undefined][] = [];
function stub(proto: object, name: string, get: (this: HTMLElement) => unknown) {
  descriptors.push([proto, name, Object.getOwnPropertyDescriptor(proto, name)]);
  Object.defineProperty(proto, name, { configurable: true, get });
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
  stub(HTMLElement.prototype, "offsetHeight", function () {
    if (this.classList.contains("panel")) return 300;
    if (this.parentElement?.classList.contains("widget-body")) return 20 + rowsOf(this) * ROW + (this.textContent?.includes("weitere") ? 28 : 0);
    return 0;
  });
  stub(HTMLElement.prototype, "clientHeight", function () {
    return this.classList.contains("widget-body") ? 300 - CHROME : 0;
  });
  descriptors.push([Element.prototype, "getBoundingClientRect", Object.getOwnPropertyDescriptor(Element.prototype, "getBoundingClientRect")]);
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const rows = this.parentElement ? [...this.parentElement.children].filter((el) => el.hasAttribute("data-fit-row")) : [];
    const index = rows.indexOf(this);
    const bottom = index >= 0 ? 10 + (index + 1) * ROW : 0;
    return { top: 0, bottom, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
});

afterEach(() => {
  for (const [proto, name, descriptor] of descriptors.splice(0).reverse()) {
    if (descriptor) Object.defineProperty(proto, name, descriptor);
  }
  vi.unstubAllGlobals();
});

function renderCard(
  items: unknown[],
  onRows: (rows: number) => void,
  { widget = WIDGET, draggable = false, fixedRows = null, edit }: { widget?: WidgetOut; draggable?: boolean; fixedRows?: number | null; edit?: ReactNode } = {},
) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/v1/pages")) {
      return new Response(JSON.stringify([
        { ext_id: "npm", path: "/proxy", title: "NPM", icon: null, nav_section: "x", nav_order: 1, show_in_nav: true, permissions: [], component: "X" },
      ]), { status: 200 });
    }
    if (url.endsWith(`/api/v1/ext/npm/${widget.data_endpoint}`)) return new Response(JSON.stringify({ data: items, meta: {} }), { status: 200 });
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (props: { fixedRows: number | null; edit?: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WidgetCard widget={widget} onRows={onRows} draggable={draggable} fixedRows={props.fixedRows} edit={props.edit} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(tree({ fixedRows, edit }));
  return { rerenderWith: (next: { fixedRows: number | null; edit?: ReactNode }) => view.rerender(tree(next)) };
}

const domains = (n: number) => Array.from({ length: n }, (_, i) => ({ domain: `dienst-${i + 1}.example` }));

describe("WidgetCard: Höhe nach Inhalt", () => {
  it("leere Liste: kleinste Stufe (XS), nur der graue Satz", async () => {
    const onRows = vi.fn();
    renderCard([], onRows);
    expect(await screen.findByText("Keine Zertifikate")).toBeInTheDocument();
    await waitFor(() => expect(onRows).toHaveBeenLastCalledWith(1));
  });

  it("drei Zeilen: die nächste Stufe, die passt (M)", async () => {
    const onRows = vi.fn();
    renderCard(domains(3), onRows);
    expect(await screen.findByText("dienst-3.example")).toBeInTheDocument();
    // 47 + 20 + 3 * 48 = 211 > S (192), also M
    await waitFor(() => expect(onRows).toHaveBeenLastCalledWith(3));
  });

  it("zu lang für L: endet an der letzten ganzen Zeile, darunter „+ x weitere“ als Link zur Modulseite", async () => {
    const onRows = vi.fn();
    renderCard(domains(14), onRows);
    const more = await screen.findByTestId("widget-more");
    // Platz: 498 - 47 - 28 = 423; Unterkanten 58, 106, …, 394 (8 Zeilen), die neunte (442) passt nicht mehr.
    expect(more).toHaveTextContent("+ 6 weitere");
    expect(more.closest("a")).toHaveAttribute("href", "/ext/npm/proxy");
    expect(screen.getByText("dienst-8.example")).toBeInTheDocument();
    expect(screen.queryByText("dienst-9.example")).not.toBeInTheDocument();
    expect(onRows).toHaveBeenLastCalledWith(5);
  });
});

describe("WidgetCard: gewählte Größe", () => {
  it("eine lange Liste endet an der letzten ganzen Zeile der Stufe, darunter „+ x weitere“", async () => {
    const onRows = vi.fn();
    renderCard(domains(6), onRows, { fixedRows: 2 });
    const more = await screen.findByTestId("widget-more");
    // Platz in S: 192 - 47 - 28 = 117; Unterkanten 58, 106 passen, 154 nicht mehr.
    expect(more).toHaveTextContent("+ 4 weitere");
    expect(screen.getByText("dienst-2.example")).toBeInTheDocument();
    expect(screen.queryByText("dienst-3.example")).not.toBeInTheDocument();
    expect(onRows).toHaveBeenLastCalledWith(2);

    // Dieselbe Liste ohne gewählte Größe passt in L und wird nicht gekürzt.
    cleanup();
    renderCard(domains(6), onRows);
    expect(await screen.findByText("dienst-6.example")).toBeInTheDocument();
    expect(screen.queryByTestId("widget-more")).not.toBeInTheDocument();
  });

  it("ein kurzer Inhalt bekommt trotzdem genau die gewählte Höhe", async () => {
    const onRows = vi.fn();
    renderCard(domains(1), onRows, { fixedRows: 5 });
    expect(await screen.findByText("dienst-1.example")).toBeInTheDocument();
    await waitFor(() => expect(onRows).toHaveBeenLastCalledWith(5));
    expect(screen.queryByTestId("widget-more")).not.toBeInTheDocument();
  });

  it("eine andere Größe misst den Inhalt neu: aus der gekürzten Liste wird wieder die ganze", async () => {
    const onRows = vi.fn();
    const view = renderCard(domains(6), onRows, { fixedRows: 2 });
    await screen.findByTestId("widget-more");
    view.rerenderWith({ fixedRows: 5 });
    expect(await screen.findByText("dienst-6.example")).toBeInTheDocument();
    expect(screen.queryByTestId("widget-more")).not.toBeInTheDocument();
    await waitFor(() => expect(onRows).toHaveBeenLastCalledWith(5));
  });
});

describe("WidgetCard: Bearbeiten", () => {
  it("die Steuerung steht unter dem Kopf, der Kopf ist kein Link und „+ x weitere“ auch nicht", async () => {
    renderCard(domains(14), vi.fn(), { edit: <div data-testid="bar">Leiste</div> });
    const more = await screen.findByTestId("widget-more");
    expect(more.closest("a")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("Zertifikate")).toBeInTheDocument();
    // Kopf, Leiste, Inhalt
    expect(screen.getByTestId("widget-npm-certificates").children[1]).toBe(screen.getByTestId("bar"));
  });

  it("der Inhalt nimmt keine Klicks an, damit beim Anordnen keine Aktion ausgelöst wird", async () => {
    renderCard(domains(1), vi.fn(), { edit: <div /> });
    const row = await screen.findByText("dienst-1.example");
    expect(row.closest(".widget-body")).toHaveClass("pointer-events-none");
    cleanup();
    renderCard(domains(1), vi.fn());
    expect((await screen.findByText("dienst-1.example")).closest(".widget-body")).not.toHaveClass("pointer-events-none");
  });

  it("der Griff ist immer sichtbar, nicht erst beim Darüberfahren", async () => {
    renderCard(domains(1), vi.fn(), { draggable: true, edit: <div /> });
    await screen.findByText("dienst-1.example");
    const handle = screen.getByTitle("Zum Verschieben ziehen");
    expect(handle).not.toHaveClass("opacity-0");
    // Ohne Bedienung per Tastatur bleibt der Griff für Screenreader stumm.
    expect(handle).toHaveAttribute("aria-hidden", "true");
  });
});

describe("WidgetCard: Kopf und Griff", () => {
  it("der ganze Kopf ist der Link zur Modulseite, ohne Raster gibt es keinen Griff", async () => {
    renderCard(domains(1), vi.fn());
    const head = await screen.findByRole("link", { name: "Zertifikate öffnen" });
    expect(head).toHaveAttribute("href", "/ext/npm/proxy");
    expect(head).toHaveTextContent("Zertifikate");
    expect(document.querySelector(".widget-drag-handle")).toBeNull();
  });

  it("im Raster: eigener Griff zum Verschieben, nicht im Link (Links brechen das Ziehen ab)", async () => {
    renderCard(domains(1), vi.fn(), { draggable: true });
    const head = await screen.findByRole("link", { name: "Zertifikate öffnen" });
    const handle = document.querySelector(".widget-drag-handle");
    expect(handle).not.toBeNull();
    expect(head.contains(handle)).toBe(false);
    expect(handle).toHaveAttribute("title", "Zum Verschieben ziehen");
  });

  it("Zahl und Tacho stehen mittig, Listen beginnen oben", async () => {
    const stat = {
      ...WIDGET, id: "count", data_endpoint: "widgets/count",
      view: { kind: "stat", value: "{{ n }}", label: null, delta: null, tone: "neutral" },
    } as unknown as WidgetOut;
    renderCard({ n: 7 } as unknown as unknown[], vi.fn(), { widget: stat });
    const value = await screen.findByText("7");
    expect(value.closest(".widget-body > div")).toHaveClass("my-auto");
    cleanup();
    renderCard(domains(1), vi.fn());
    const row = await screen.findByText("dienst-1.example");
    expect(row.closest(".widget-body > div")).not.toHaveClass("my-auto");
  });
});
