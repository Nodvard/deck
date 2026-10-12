import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SystemPage } from "./SystemPage";

/** Seiten-Durchgang: Ein Ladefehler ist keine leere Liste ("Noch kein Linux-Server"). */

beforeEach(() => {
  window.history.replaceState({}, "", "/ext/system/system");
  window.localStorage.clear();
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("SystemPage bei einem Ladefehler", () => {
  it("nennt den Grund statt „Noch kein Linux-Server“ und lädt auf Wunsch neu", async () => {
    let broken = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/v1/hosts")) {
        return broken
          ? new Response(JSON.stringify({ detail: "Datenbank gerade gesperrt." }), { status: 500 })
          : new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.endsWith("/ext/system/widgets/health")) return new Response(JSON.stringify({ data: [] }), { status: 200 });
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
    render(<SystemPage />);

    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Die Server-Liste ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    expect(screen.queryByText("Noch kein Linux-Server")).toBeNull();

    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Noch kein Linux-Server")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });
});
