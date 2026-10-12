import { render, screen } from "@testing-library/react";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BETA_HINT } from "../components/BetaBadge";
import { ExtensionPage } from "./ExtensionPage";

/** Die Seite eines Moduls mit `stage: "beta"` bekommt oben eine schmale Zeile mit dem Kennzeichen; Seiten fertiger Module nicht. */
function DemoPage() {
  return <h2 data-testid="demo-page">Seite des Moduls</h2>;
}

const state = vi.hoisted(() => ({ pages: [] as Record<string, unknown>[] }));
vi.mock("../lib/catalog", () => ({
  usePages: () => ({ data: state.pages, isLoading: false }),
}));
vi.mock("/api/v1/extensions/demo/frontend/index.js?v=dev", () => ({ DemoPage }));
vi.mock("/api/v1/extensions/ruhig/frontend/index.js?v=dev", () => ({ DemoPage }));
vi.mock("/api/v1/extensions/alt/frontend/index.js?v=dev", () => ({ DemoPage }));

const page = (ext_id: string, extra: Record<string, unknown> = {}) => ({ ext_id, path: "/page", component: "DemoPage", ...extra });

function renderAt(path: string) {
  window.history.replaceState(null, "", path);
  render(<RouterProvider router={createBrowserRouter([{ path: "/ext/:extId/*", element: <ExtensionPage /> }])} />);
}

beforeEach(() => {
  state.pages = [page("demo", { stage: "beta" }), page("ruhig", { stage: "stable" }), page("alt")];
});

describe("ExtensionPage: Beta-Kennzeichen im Seitenkopf", () => {
  it("zeigt bei einem Beta-Modul die Zeile mit Abzeichen und Erklärung über der Seite", async () => {
    renderAt("/ext/demo/page");
    const notice = await screen.findByTestId("beta-page-notice");
    expect(notice).toHaveTextContent("Beta");
    expect(notice).toHaveTextContent(BETA_HINT);
    const body = await screen.findByTestId("demo-page");
    // Die Zeile steht über der Seite, die Seite selbst bleibt unverändert.
    expect(notice.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("zeigt bei einem fertigen Modul keine Zeile", async () => {
    renderAt("/ext/ruhig/page");
    await screen.findByTestId("demo-page");
    expect(screen.queryByTestId("beta-page-notice")).toBeNull();
    expect(screen.queryByTestId("beta-badge")).toBeNull();
  });

  it("zeigt auch ohne Angabe (älteres Backend) keine Zeile", async () => {
    renderAt("/ext/alt/page");
    await screen.findByTestId("demo-page");
    expect(screen.queryByTestId("beta-page-notice")).toBeNull();
  });
});
