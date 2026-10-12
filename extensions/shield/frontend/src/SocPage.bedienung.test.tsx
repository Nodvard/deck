import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { SocPage, findingsLast30Days } from "./SocPage";

/** Shield-Bedienung: Knöpfe oben nur, wo sie hingehören, eine Zahl je Kachel. */

const OVERVIEW = {
  hosts: [
    { host_id: "h1", host_name: "pi", host_status: "up", reachable: true, clamav_installed: true, lynis_installed: true, last_scan: null, last_audit: null, scanning: false, auditing: false },
  ],
  summary: { hosts: 1, protected: 1, open_threats: 0, quarantined: 2, neutralized_total: 5, findings_30d: 3, avg_hardening: null, score: 80 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) return json(OVERVIEW);
    if (url.endsWith("/defender/updates")) {
      return json({
        hosts: [], runs: [], summary: { hosts: 1, checked: 1, up_to_date: 1, packages: 0, security: 0, reboot: 0 },
        config: { check_enabled: true, check_cron: "0 6 * * *", auto_enabled: false, auto_mode: "security", auto_cron: "30 3 * * *", auto_reboot: false, auto_tag: null },
      });
    }
    if (url.endsWith("/defender/guard")) return json({ hosts: [], events: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 1, hosts: 1 } });
    if ((url.includes("/defender/findings") || url.includes("/defender/scans") || url.includes("/defender/audits") || url.includes("/defender/events")) && method === "GET") return json([]);
    if (url.includes("/defender/scans") && method === "POST") return json({ scans: ["s1"] });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(false);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

function openTab(tab: string) {
  window.history.replaceState(null, "", `/ext/shield/soc?tab=${tab}`);
}

describe("Knöpfe oben", () => {
  it.each(["overview", "scans"])("auf „%s“ stehen die vier Knöpfe direkt da", async (tab) => {
    openTab(tab);
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    expect(await screen.findByRole("button", { name: /Schnellscan starten/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tiefenscan/ })).toBeInTheDocument();
    expect(screen.queryByTestId("shield-more")).toBeNull();
  });

  it.each(["updates", "guard", "quarantine", "hardening", "containers"])("auf „%s“ liegen sie unter „Mehr“ und funktionieren dort", async (tab) => {
    openTab(tab);
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    const more = await screen.findByTestId("shield-more");
    expect(more).not.toHaveAttribute("open");
    // Nicht mehr als eigene Knöpfe in der Kopfzeile: nur im Menü.
    expect(within(more).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual([
      "Briefing senden", "Härtungs-Audit", "Tiefenscan", "Schnellscan starten",
    ]);
    fireEvent.click(within(more).getByText("Mehr"));
    expect(more).toHaveAttribute("open");
    fireEvent.click(within(more).getByRole("button", { name: /Tiefenscan/ }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Tiefenscan"), expect.anything()));
    expect(more).not.toHaveAttribute("open");
    expect(document.activeElement?.textContent).toContain("Mehr"); // Fokus zurück auf „Mehr“, nicht auf die Seite
  });

  it("„Mehr“ schließt mit Escape und bei einem Klick daneben", async () => {
    openTab("updates");
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    const more = await screen.findByTestId("shield-more");
    // Das Ereignis „toggle“ kommt (wie im Browser) erst im nächsten Takt; erst dann hört das Menü auf Klicks daneben.
    const reopen = async () => {
      fireEvent.click(within(more).getByText("Mehr"));
      expect(more).toHaveAttribute("open");
      await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
    };
    await reopen();
    fireEvent.keyDown(within(more).getByRole("button", { name: /Tiefenscan/ }), { key: "Escape" });
    expect(more).not.toHaveAttribute("open");

    await reopen();
    fireEvent.pointerDown(document.body); // Antippen am Handy (iOS schickt dabei keine Mausereignisse)
    expect(more).not.toHaveAttribute("open");

    await reopen();
    fireEvent.keyDown(document.body, { key: "Escape" }); // Escape auch ohne Fokus im Menü
    expect(more).not.toHaveAttribute("open");
    expect(document.activeElement?.tagName).toBe("SUMMARY");
  });

  it("ohne Verwaltungsrecht weder Knöpfe noch „Mehr“", async () => {
    window.__lattice.hasPermission = vi.fn().mockReturnValue(false);
    openTab("updates");
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    await screen.findByRole("tab", { name: /Updates/ });
    expect(screen.queryByTestId("shield-more")).toBeNull();
    expect(screen.queryByRole("button", { name: /Schnellscan starten/ })).toBeNull();
  });
});

describe("Kachel „In Quarantäne“", () => {
  it("zeigt eine Zahl statt einer Summe aus zwei verschiedenen Dingen", async () => {
    openTab("overview");
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    const tile = (await screen.findByText("In Quarantäne")).closest("div.panel") as HTMLElement;
    // Genau diese Kachel: Wert 2 (in Quarantäne), nicht 5 (die frühere Summe), darunter die Funde der 30 Tage.
    expect(tile).toHaveTextContent(/^In Quarantäne23 Funde in 30 Tagen$/);
    expect(screen.queryByText("Neutralisiert")).toBeNull();
  });

  it("Einzahl und keine Funde", () => {
    expect(findingsLast30Days(1)).toBe("1 Fund in 30 Tagen");
    expect(findingsLast30Days(0)).toBe("keine Funde in 30 Tagen");
  });
});
