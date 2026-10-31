import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { BackupsPanel } from "./Backups";

const LIST = {
  folder: "/home/deck/nodvard-sicherungen/shop/",
  error: null,
  backups: [
    { stamp: "20261016-041500", at: "2026-10-16T04:15:00+00:00", folder: "/home/deck/nodvard-sicherungen/shop/20261016-041500", volumes: [
      { name: "shop_data", size_bytes: 2048, services: ["web"], restorable: true },
      { name: "alt_weg", size_bytes: 10, services: [], restorable: false },
    ] },
  ],
};
const bodies: unknown[] = [];
const requests: string[] = [];

function serve(over: Record<string, (init?: RequestInit) => Response> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url}`);
    if (typeof init?.body === "string") bodies.push(JSON.parse(init.body));
    for (const [part, answer] of Object.entries(over)) {
      if (url.includes(part)) return answer(init);
    }
    if (url.endsWith("/backups")) return Response.json(LIST);
    if (url.endsWith("/restore") || url.endsWith("/delete")) {
      return Response.json({ action_id: "b1", status: "proposed", risk: url.endsWith("/restore") ? "high" : "medium", detail: null }, { status: 202 });
    }
    throw new Error(url);
  }));
}

const confirm = () => window.__nodvardDeck.confirmDialog as Mock;

beforeEach(() => {
  bodies.length = 0;
  requests.length = 0;
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("Sicherungen", () => {
  it("zeigt die Sicherung, wählt nur Volumes des Stacks und spielt über die Freigabe zurück", async () => {
    const onDone = vi.fn();
    serve({ "/actions/b1/approve": () => Response.json({ id: "b1", status: "succeeded", result: { output: "Stack „shop“: shop_data auf den Stand vom 16.10.2026, 06:15 zurückgespielt." } }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={onDone} />);
    const entry = await screen.findByTestId("backup-20261016-041500");
    expect(entry.textContent).toContain("nutzt der Stack nicht mehr");
    expect(screen.getByRole("checkbox", { name: "shop_data zurückspielen" })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "alt_weg zurückspielen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("Stack „shop“: shop_data auf den Stand vom 16.10.2026, 06:15 zurückgespielt."));
    const question = String(confirm().mock.calls[0][0]);
    expect(question).toMatch(/^shop_data auf den Stand vom .* zurückspielen\? Was seitdem in diesem Volume dazukam oder sich änderte, ist danach weg\. web stoppt dafür, bis das Zurückspielen fertig ist; bei großen Volumes kann das dauern\.$/);
    expect(confirm().mock.calls[0][1]).toEqual({ confirmLabel: "Zurückspielen", danger: true });
    expect(requests).toContain("POST /api/v1/ext/docker/hosts/h1/stacks/shop/backups/20261016-041500/restore");
    expect(bodies.at(-1)).toEqual({ volumes: ["shop_data"] });
  });

  it("ohne gewähltes Volume gibt es kein Zurückspielen, abgebrochen geht nichts raus", async () => {
    serve();
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    const box = await screen.findByRole("checkbox", { name: "shop_data zurückspielen" });
    fireEvent.click(box);
    expect(screen.getByRole("button", { name: "Zurückspielen" })).toBeDisabled();
    fireEvent.click(box);
    confirm().mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalled());
    expect(requests.filter((r) => r.startsWith("POST"))).toEqual([]);
  });

  it("löschen wartet auf die Freigabe und sagt es", async () => {
    const onDone = vi.fn();
    serve();
    window.__nodvardDeck.hasPermission = vi.fn((permission: string) => !permission.startsWith("actions.approve"));
    render(<BackupsPanel hostId="h1" project="shop" onDone={onDone} />);
    fireEvent.click(await screen.findByRole("button", { name: "Sicherung löschen" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("Vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“."));
    expect(requests).toContain("POST /api/v1/ext/docker/hosts/h1/stacks/shop/backups/20261016-041500/delete");
  });

  it("ein gescheitertes Zurückspielen kommt als Warnung", async () => {
    const onDone = vi.fn();
    serve({ "/actions/b1/approve": () => Response.json({ id: "b1", status: "failed", result: {
      error: "Das Archiv von shop_data ist beschädigt oder unvollständig (gzip: unexpected end of file). An shop_data hat sich nichts geändert." } }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={onDone} />);
    fireEvent.click(await screen.findByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(expect.stringContaining("An shop_data hat sich nichts geändert."), true));
  });

  it("ohne Container des Stacks nennt es den Grund", async () => {
    serve({ "/backups": () => Response.json({ ...LIST, note: "Der Stack hat gerade keine Container, deshalb lässt sich kein Volume zurückspielen." }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Der Stack hat gerade keine Container");
  });

  it("nennt halb gepackte Archive", async () => {
    serve({ "/backups": () => Response.json({ ...LIST, backups: [{ ...LIST.backups[0], unfinished: ["shop_cache"] }] }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByText(/^Nicht enthalten: shop_cache\./)).toHaveTextContent(
      "Von shop_cache liegt noch ein halbes Archiv im Ordner; „Sicherung löschen“ nimmt die Reste mit.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalled());
    expect(String(confirm().mock.calls[0][0])).toContain("shop_cache ist nicht in dieser Sicherung und bleibt auf dem heutigen Stand");
  });

  it("gestoppte Dienste bleiben gestoppt und stehen so in der Liste und im Dialog", async () => {
    const volume = { name: "shop_data", size_bytes: 2048, services: ["web", "worker"], stopped_services: ["worker"], restorable: true };
    serve({ "/backups": () => Response.json({ ...LIST, backups: [{ ...LIST.backups[0], volumes: [volume] }] }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByText("web, worker (gestoppt)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalled());
    expect(String(confirm().mock.calls[0][0])).toMatch(
      /ist danach weg\. web stoppt dafür, bis das Zurückspielen fertig ist; bei großen Volumes kann das dauern\. worker ist gestoppt\. Deck startet ihn nicht; Compose kann ihn aber mitstarten, wenn ein laufender Dienst ihn braucht, etwa als Einmal-Dienst für Migrationen\.$/,
    );
  });

  it("ist der ganze Stack gestoppt, stoppt im Dialog nichts", async () => {
    const volume = { name: "shop_data", size_bytes: 2048, services: ["web"], stopped_services: ["web"], restorable: true };
    serve({ "/backups": () => Response.json({ ...LIST, backups: [{ ...LIST.backups[0], volumes: [volume] }] }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalled());
    const question = String(confirm().mock.calls[0][0]);
    expect(question).toMatch(/ist danach weg\. Bei großen Volumes kann das dauern\. web ist gestoppt und bleibt es\.$/);
    expect(question).not.toContain("stoppt dafür");
  });

  it("nennt, was in einer Sicherung fehlt, auch im Dialog", async () => {
    serve({ "/backups": () => Response.json({ ...LIST, backups: [{ ...LIST.backups[0], missing: ["shop_more"] }] }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByText(/^Nicht enthalten: shop_more\./)).toHaveClass("text-amber-300");
    expect(screen.getByText(/^Nicht enthalten/)).toHaveTextContent(
      /^Nicht enthalten: shop_more\. Die Sicherung ist unvollständig, etwa weil das Packen abbrach; spielst du nur einen Teil zurück, passen die Daten der Dienste vielleicht nicht zusammen\.$/,
    );
    fireEvent.click(screen.getByRole("button", { name: "Zurückspielen" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalled());
    expect(String(confirm().mock.calls[0][0])).toContain(
      "shop_more ist nicht in dieser Sicherung und bleibt auf dem heutigen Stand; die Daten der Dienste passen danach vielleicht nicht zusammen.",
    );
  });

  it("eine unlesbare Inhaltsliste gilt nicht als vollständig", async () => {
    const unclear = "Die Inhaltsliste dieser Sicherung ist beschädigt; ob die Sicherung vollständig ist, lässt sich nicht sagen.";
    serve({ "/backups": () => Response.json({ ...LIST, backups: [{ ...LIST.backups[0], contents_error: unclear }] }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByText(unclear)).toHaveClass("text-amber-300");
  });

  it("während am Stack etwas läuft, ist ein halbes Archiv nicht abgebrochen und die Knöpfe warten", async () => {
    let busy: string | null = "Update";
    serve({ "/backups": () => Response.json({ ...LIST, busy, backups: [{ ...LIST.backups[0], unfinished: ["shop_cache"], missing: ["shop_cache", "shop_more"] }] }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByText(/Noch nicht fertig: shop_cache, shop_more\. Vielleicht packt Deck gerade daran\./)).toBeInTheDocument();
    expect(screen.queryByText(/Nicht enthalten/)).toBeNull();
    expect(screen.getByText(/Am Stack läuft gerade etwas \(Update\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zurückspielen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sicherung löschen" })).toBeDisabled();
    busy = null;
    fireEvent.click(screen.getByRole("button", { name: "Neu laden" }));
    const line = await screen.findByText(/^Nicht enthalten: shop_cache, shop_more\./);
    expect(line).toHaveTextContent("Von shop_cache liegt noch ein halbes Archiv im Ordner;");
    expect(screen.getByTestId("backup-20261016-041500").querySelectorAll(".text-amber-300")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Zurückspielen" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Sicherung löschen" })).toBeEnabled();
  });

  it("ohne Sicherung sagt es das, ein Lesefehler ist keine leere Liste", async () => {
    serve({ "/backups": () => Response.json({ ...LIST, backups: [] }) });
    const { unmount } = render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect((await screen.findByTestId("backups-shop")).textContent).toContain("Für diesen Stack gibt es noch keine Sicherung.");
    unmount();
    vi.unstubAllGlobals();
    serve({ "/backups": () => Response.json({ ...LIST, backups: [], error: "Die Sicherungen ließen sich nicht lesen: Permission denied" }) });
    render(<BackupsPanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByText(/Permission denied/)).toBeInTheDocument();
    expect(screen.queryByText("Für diesen Stack gibt es noch keine Sicherung.")).toBeNull();
  });
});
