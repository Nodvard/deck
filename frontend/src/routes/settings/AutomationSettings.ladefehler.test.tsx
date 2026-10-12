import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { AutomationSettings } from "./AutomationSettings";

/** Seiten-Durchgang: Scheitert die Server-Liste, steht im Wartungsfenster der Grund statt „Keine Server bekannt.“. */

const SETTINGS = [
  { key: "autonomy.mode", value: "propose" }, { key: "autonomy.max_risk", value: "low" },
  { key: "security.deny_patterns", value: [] },
  { key: "maintenance.windows", value: [{ cron: "0 2 * * 0", duration_minutes: 60, host_ids: [] }] },
];

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});

describe("AutomationSettings bei einem Ladefehler", () => {
  it("Server-Liste nicht ladbar: Grund statt „Keine Server bekannt.“, erneut versuchen", async () => {
    let broken = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = (typeof input === "string" ? input : input.toString()).replace(/^\/api\/v1/, "").split("?")[0];
      if (path === "/settings") return new Response(JSON.stringify(SETTINGS), { status: 200 });
      if (path === "/hosts") {
        return broken
          ? new Response(JSON.stringify({ detail: "Datenbank gerade gesperrt." }), { status: 500 })
          : new Response(JSON.stringify([{ id: "h1", name: "pi", display_name: "Raspberry Pi" }]), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch: ${path}`);
    }));
    render(<AutomationSettings />);

    const win = await screen.findByTestId("maintenance-window");
    const alert = await within(win).findByTestId("load-error");
    expect(alert).toHaveTextContent("Die Server-Liste ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    expect(within(win).queryByText("Keine Server bekannt.")).toBeNull();

    broken = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await within(win).findByLabelText("Raspberry Pi")).toBeInTheDocument();
    expect(within(win).queryByTestId("load-error")).toBeNull();
  });
});
