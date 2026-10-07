import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { navigateTo } from "../../../_shared/frontend/src/testShell";

import { ParamsHint, ScriptsPage, describeSchedule } from "./ScriptsPage";

const SCRIPT = {
  id: "uptime", name: "Uptime", description: "", content: "uptime\n", params_schema: {},
  target: { kind: "host", host_id: "h-pi" }, schedule: null, enabled: true, job_id: "script-uptime",
};

const RUN_BODY = { targets: 1, results: [{ host_id: "h-pi", action_id: "a1", status: "proposed" }] };

type Approvals = Record<string, { status: number; body: unknown }>;

function mockFetch(
  calls: { url: string; method: string }[], runs: unknown[] = [], runBody: unknown = RUN_BODY, approvals: Approvals = {},
  polls: Record<string, unknown> = {},
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url.endsWith("/ext/scripts/scripts")) return new Response(JSON.stringify([SCRIPT]), { status: 200 });
    if (url.includes("/jobs")) return new Response(JSON.stringify([]), { status: 200 });
    if (url.endsWith("/uptime/runs")) return new Response(JSON.stringify(runs), { status: 200 });
    if (url.endsWith("/uptime/history")) return new Response(JSON.stringify([]), { status: 200 });
    if (url.endsWith("/uptime/run") && method === "POST") {
      return new Response(JSON.stringify(runBody), { status: 200 });
    }
    const poll = /\/actions\/([^/]+)$/.exec(url);
    if (poll && method === "GET" && polls[poll[1]]) {
      const { httpStatus, ...pollBody } = polls[poll[1]] as { httpStatus?: number };
      return new Response(JSON.stringify(pollBody), { status: httpStatus ?? 200 });
    }
    const approval = /\/actions\/([^/?]+)\/approve(?:\?wait=0)?$/.exec(url);
    if (approval && approvals[approval[1]]) {
      const a = approvals[approval[1]];
      return new Response(JSON.stringify(a.body), { status: a.status });
    }
    if (/\/actions\/a1\/approve(?:\?wait=0)?$/.test(url)) return new Response(JSON.stringify({ id: "a1", status: "succeeded" }), { status: 200 });
    if (method === "PUT" && url.includes("/ext/scripts/scripts/")) return new Response(JSON.stringify(SCRIPT), { status: 200 });
    if (url.endsWith("/host-groups")) return new Response(JSON.stringify([{ id: "g-fleet", name: "Flotte" }]), { status: 200 });
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([{ id: "h-pi", name: "pi", display_name: "Raspberry Pi" }]), { status: 200 });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("ScriptsPage Ausführungen", () => {
  it("zeigt Ausgabe, Fehlerausgabe, Exit-Code und Ziel der letzten Läufe", async () => {
    vi.stubGlobal("fetch", mockFetch([], [
      { action_id: "a2", status: "failed", host_id: "h-pi", host_name: "Raspberry Pi", proposed_by: "user/u1", created_at: "2026-09-25T20:00:00Z",
        finished_at: null, exit_code: 1, output: "teil 1\n", error: "Permission denied", duration_ms: 1200 },
      { action_id: "a1", status: "succeeded", host_id: "h-pi", host_name: "Raspberry Pi", proposed_by: "user/u1", created_at: "2026-09-25T19:00:00Z",
        finished_at: null, exit_code: 0, output: " 20:00:01 up 3 days", error: "", duration_ms: 800 },
    ]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    const list = await screen.findByTestId("executions");
    expect(list.textContent).toContain("fehlgeschlagen");
    expect(list.textContent).toContain("Exit 1");
    expect(list.textContent).toContain("Permission denied");
    expect(list.textContent).toContain("up 3 days");
  });

  it("zeigt, wer den Lauf ausgelöst hat, als Name statt „user/<uuid>“", async () => {
    const run = { host_id: "h-pi", host_name: "Raspberry Pi", created_at: "2026-09-25T20:00:00Z", finished_at: null, exit_code: 0, output: "ok", error: "", duration_ms: 500 };
    vi.stubGlobal("fetch", mockFetch([], [
      { ...run, action_id: "a3", status: "succeeded", proposed_by: "user/5f3c9a1e-7b2d", proposed_by_label: "nico" },
      { ...run, action_id: "a2", status: "succeeded", proposed_by: "extension/scripts", proposed_by_label: "Skripte" },
      // älteres Backend bzw. gelöschter Nutzer: kein Name -> der rohe Wert
      { ...run, action_id: "a1", status: "succeeded", proposed_by: "user/u9" },
    ]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    const list = await screen.findByTestId("executions");

    const byName = within(list).getByText(/· nico$/);
    expect(byName).toHaveAttribute("title", "user/5f3c9a1e-7b2d");
    expect(list.textContent).not.toContain("5f3c9a1e-7b2d ·");
    expect(within(list).getByText(/· Zeitplan · Skripte$/)).toHaveAttribute("title", "extension/scripts");
    expect(within(list).getByText(/· user\/u9$/)).toBeInTheDocument();
  });

  it("'Jetzt ausführen' fragt nach, bestätigt mit Freigabe-Recht gleich mit und lädt die Läufe neu", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    await screen.findByText("1 Ziel(e) ausgeführt");
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Uptime"), expect.objectContaining({ danger: true }));
    // Ein einziges Ziel: die Freigabe wartet wie bisher, ein kurzes Skript meldet sich gleich mit Ergebnis.
    expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true);
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/uptime/runs")).length).toBe(2));
  });

  it("gibt bei mehreren Zielen ohne Wartezeit frei, damit der Knopf nicht N x 20 s hängt", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, [], {
      targets: 3,
      results: [
        { host_id: "h-pi", host_name: "pi", action_id: "a1", status: "proposed" },
        { host_id: "h-pve1", host_name: "pve1", action_id: "a2", status: "proposed" },
        { host_id: "h-docker", host_name: "docker", action_id: "a3", status: "proposed" },
      ],
    }, {
      a1: { status: 202, body: { id: "a1", status: "executing" } },
      a2: { status: 202, body: { id: "a2", status: "executing" } },
      a3: { status: 202, body: { id: "a3", status: "executing" } },
    }, {
      a1: { id: "a1", status: "executing" }, a2: { id: "a2", status: "executing" }, a3: { id: "a3", status: "executing" },
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    expect((await screen.findByRole("status")).textContent).toContain("3 laufen noch");
    const approvals = calls.filter((c) => c.method === "POST" && /\/actions\/[^/]+\/approve/.test(c.url));
    expect(approvals.map((c) => c.url.replace(/^.*\/actions\//, ""))).toEqual(["a1/approve?wait=0", "a2/approve?wait=0", "a3/approve?wait=0"]);
  });

  it("nennt übersprungene Server mit Grund", async () => {
    vi.stubGlobal("fetch", mockFetch([], [], {
      targets: 1,
      results: [
        { host_id: "h-ki", host_name: "ki-server", skipped: "keine SSH-Zugangsdaten" },
        { host_id: "h-win", host_name: "game-win", skipped: "Windows-Server" },
        { host_id: "h-pi", host_name: "pi", action_id: "a1", status: "proposed" },
      ],
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    await screen.findByText(/1 Ziel\(e\) ausgeführt · 2 übersprungen: ki-server \(keine SSH-Zugangsdaten\), game-win \(Windows-Server\)/);
  });

  it("meldet fehlgeschlagene Läufe mit Grund statt grün „ausgeführt“", async () => {
    vi.stubGlobal("fetch", mockFetch([], [], {
      targets: 3,
      results: [
        { host_id: "h-pi", host_name: "pi", action_id: "a1", status: "proposed" },
        { host_id: "h-pve1", host_name: "pve1", action_id: "a2", status: "proposed" },
        { host_id: "h-docker", host_name: "docker", action_id: "a3", status: "proposed" },
        { host_id: "h-win", host_name: "game-win", skipped: "Windows-Server" },
      ],
    }, {
      a2: { status: 200, body: { id: "a2", status: "failed", result: { success: false, exit_code: 1, error: "lynis: Permission denied\nweitere Zeile" } } },
      a3: { status: 409, body: { detail: "Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: 'expired')." } },
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("1 Ziel(e) ausgeführt");
    expect(alert.textContent).toContain("2 fehlgeschlagen: pve1 (Exit 1: lynis: Permission denied), docker (Vorschlag abgelaufen)");
    expect(alert.textContent).toContain("1 übersprungen: game-win (Windows-Server)");
    expect(alert.textContent).not.toContain("weitere Zeile");
  });

  it("zählt einen nur fehlgeschlagenen Lauf nicht als ausgeführt", async () => {
    vi.stubGlobal("fetch", mockFetch([], [], RUN_BODY, {
      a1: { status: 200, body: { id: "a1", status: "failed", result: { success: false, exit_code: 127, error: null } } },
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toContain("ausgeführt");
    expect(alert.textContent).toContain("1 fehlgeschlagen: h-pi (Exit 127)");
  });

  it("wertet auch Läufe aus, die das Gate selbst entschieden hat", async () => {
    vi.stubGlobal("fetch", mockFetch([], [], {
      targets: 3,
      results: [
        { host_id: "h-pi", host_name: "pi", action_id: "a7", status: "succeeded" },
        { host_id: "h-pve1", host_name: "pve1", action_id: "a8", status: "denied" },
        { host_id: "h-docker", host_name: "docker", action_id: "a9", status: "failed" },
      ],
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("1 Ziel(e) ausgeführt");
    expect(alert.textContent).toContain("2 fehlgeschlagen: pve1 (von einer Schutzregel blockiert), docker");
  });

  it("fragt bei einem länger laufenden Skript nach und meldet das Ergebnis, ohne die Seite zu sperren", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.stubGlobal("fetch", mockFetch([], [], RUN_BODY, {
        a1: { status: 202, body: { id: "a1", status: "executing" } },
      }, {
        a1: { id: "a1", status: "failed", result: { success: false, exit_code: 2, error: "lynis: Timeout" } },
      }));
      render(<ScriptsPage />);
      fireEvent.click(await screen.findByText("Uptime"));
      fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
      // Erst "läuft noch" -- der Knopf ist danach wieder frei.
      expect((await screen.findByRole("status")).textContent).toContain("1 laufen noch");
      await waitFor(() => expect(screen.getByRole("button", { name: "Jetzt ausführen" })).not.toBeDisabled());
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toContain("1 fehlgeschlagen: h-pi (Exit 2: lynis: Timeout)");
      expect(alert.textContent).not.toContain("laufen noch");
    } finally {
      vi.useRealTimers();
    }
  });

  it("meldet bei mehreren Zielen jedes einzeln, sobald es fertig ist", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.stubGlobal("fetch", mockFetch([], [], {
        targets: 2,
        results: [
          { host_id: "h-pi", host_name: "pi", action_id: "a1", status: "executing" },
          { host_id: "h-pve1", host_name: "pve1", action_id: "a2", status: "approved" },
        ],
      }, {}, {
        a1: { id: "a1", status: "succeeded" },
        a2: { id: "a2", status: "executing" },
      }));
      render(<ScriptsPage />);
      fireEvent.click(await screen.findByText("Uptime"));
      fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
      // "approved" (Autonomie voll) zählt als laufend, nicht als Fehlschlag.
      expect((await screen.findByRole("status")).textContent).toContain("2 laufen noch");
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      // pi ist fertig, pve1 läuft weiter -- das Ergebnis von pi wartet nicht auf pve1.
      const text = (await screen.findByRole("status")).textContent ?? "";
      expect(text).toContain("1 Ziel(e) ausgeführt");
      expect(text).toContain("1 laufen noch");
      expect(text).not.toContain("fehlgeschlagen");
    } finally {
      vi.useRealTimers();
    }
  });

  it("meldet ein Ziel, dessen Stand nicht abrufbar ist, als Fehler statt ewig als laufend", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.stubGlobal("fetch", mockFetch([], [], RUN_BODY, {
        a1: { status: 202, body: { id: "a1", status: "executing" } },
      }, {
        a1: { httpStatus: 404, detail: "Unbekannte Aktion." },
      }));
      render(<ScriptsPage />);
      fireEvent.click(await screen.findByText("Uptime"));
      fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
      expect((await screen.findByRole("status")).textContent).toContain("1 laufen noch");
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toContain("Stand nicht abrufbar: Unbekannte Aktion.");
      expect(alert.textContent).not.toContain("laufen noch");
    } finally {
      vi.useRealTimers();
    }
  });

  it("ohne Bestätigung passiert nichts", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt ausführen" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith("/uptime/run"))).toBe(false);
  });
});

describe("ScriptsPage Ziel", () => {
  it("sperrt Speichern, solange bei „Einer Gruppe“ keine Gruppe gewählt ist", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    const save = await screen.findByRole("button", { name: "Speichern" });
    expect(save).not.toBeDisabled();

    fireEvent.change(screen.getByLabelText("Ziel-Art"), { target: { value: "group" } });
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    expect(screen.getByText("Bitte zuerst eine Gruppe wählen.")).toBeInTheDocument();

    await screen.findByRole("option", { name: "Flotte" });
    fireEvent.change(screen.getByLabelText("Gruppe"), { target: { value: "g-fleet" } });
    expect(screen.getByRole("button", { name: "Speichern" })).not.toBeDisabled();
    expect(screen.queryByText("Bitte zuerst eine Gruppe wählen.")).not.toBeInTheDocument();
  });

  it("sperrt Speichern bei „Einem Server“ ohne gewählten Server", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.change(await screen.findByLabelText("Ziel-Art"), { target: { value: "host" } });
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    expect(screen.getByText("Bitte zuerst einen Server wählen.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Ziel-Art"), { target: { value: "all" } });
    expect(screen.getByRole("button", { name: "Speichern" })).not.toBeDisabled();
  });
});

describe("ScriptsPage neues Skript", () => {
  it("überschreibt kein bestehendes Skript mit gleicher Kennung", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<ScriptsPage />);
    await screen.findByText("Uptime");
    fireEvent.click(screen.getByRole("button", { name: /Neues Skript/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Uptime" } });
    expect(screen.getByLabelText("Kennung")).toHaveValue("uptime");
    fireEvent.change(screen.getByLabelText("Ziel-Art"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await screen.findByText(/Ein Skript mit der Kennung „uptime“ gibt es schon/);
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("legt ein neues Skript mit ?create=true an, Bearbeiten ohne", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<ScriptsPage />);
    await screen.findByText("Uptime");
    fireEvent.click(screen.getByRole("button", { name: /Neues Skript/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Aufräumen" } });
    fireEvent.change(screen.getByLabelText("Ziel-Art"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.url)).toEqual(["/api/v1/ext/scripts/scripts/aufraeumen?create=true"]);

    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(await screen.findByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").length).toBe(2));
    expect(calls.filter((c) => c.method === "PUT")[1].url).toBe("/api/v1/ext/scripts/scripts/uptime");
  });
});

describe("ScriptsPage Zeitplan", () => {
  it("sagt beim Zeitplan, dass geplante Läufe unter „Aktionen“ freigegeben werden müssen", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    await screen.findByRole("button", { name: "Speichern" });
    expect(screen.queryByTestId("schedule-hint")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Täglich" }));
    const hint = screen.getByTestId("schedule-hint");
    expect(hint.textContent).toContain("Geplante Läufe erscheinen als Vorschlag unter „Aktionen“");
    expect(hint.textContent).toContain("24 Stunden");
    expect(hint.textContent).toContain("„Selbstständig handeln“");
    expect(hint.textContent).toContain("„Hoch“");
  });

  it("verspricht auf der leeren Seite keine automatischen Nachtläufe", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })));
    render(<ScriptsPage />);
    await screen.findByText("Noch keine Skripte");
    expect(document.body.textContent).not.toContain("automatisch auf allen Servern");
    expect(document.body.textContent).toContain("Freigabe unter „Aktionen“");
  });
});

describe("ScriptsPage Parameter und $", () => {
  it("erklärt im Editor, wie Parameter geschrieben werden und was aus $$ wird", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    const hint = await screen.findByTestId("params-hint");
    const text = hint.textContent ?? "";
    expect(text).toContain("Parameter schreibst du als $name oder ${name}, ohne Anführungszeichen drumherum");
    expect(text).toContain("Alles andere mit $ (z. B. $HOME, \"$f\", $(date)) bleibt, wie es ist.");
    expect(text).toContain("Nur $$ wird zu einem einzelnen $: Für ein $ direkt vor einem Parameternamen schreibst du $$");
    expect(text).toContain("für die Prozessnummer $$ schreibst du $$$$.");
    // Ohne deklarierte Parameter keine leere Liste.
    expect(text).not.toContain("Parameter dieses Skripts");
  });

  it("nennt die Parameter des Skripts", () => {
    render(<ParamsHint names={["level", "ziel"]} />);
    expect(screen.getByTestId("params-hint").textContent).toContain("Parameter dieses Skripts: $level, $ziel. Parameter schreibst du");
  });
});

describe("ScriptsPage Server-Filter (?host=)", () => {
  it("zeigt nur die Skripte des Servers; derselbe Link wirkt wieder, nachdem der Filter entfernt wurde", async () => {
    window.history.replaceState({}, "", "/ext/scripts/scripts?host=h-docker&x=1");
    try {
      vi.stubGlobal("fetch", mockFetch([]));
      render(<ScriptsPage />);
      expect(await screen.findByText(/Noch kein Skript für diesen Server/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Uptime/ })).toBeNull();

      fireEvent.click(within(screen.getByTestId("host-filter")).getByRole("button", { name: "Filter entfernen" }));
      expect(await screen.findByRole("button", { name: /Uptime/ })).toBeInTheDocument();
      expect(window.location.search).toBe("?x=1"); // nur ?host= entfernt

      // Für den Router ist das dieselbe Adresse wie beim Öffnen -- die Seite muss trotzdem filtern.
      navigateTo("/ext/scripts/scripts?host=h-docker&x=1");
      expect(screen.getByTestId("host-filter")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Uptime/ })).toBeNull();

      navigateTo("/ext/scripts/scripts?host=h-pi");
      expect(screen.getByRole("button", { name: /Uptime/ })).toBeInTheDocument();
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });
});

describe("describeSchedule", () => {
  it("übersetzt gängige Cron-Ausdrücke", () => {
    expect(describeSchedule(null)).toBe("Manuell");
    expect(describeSchedule("0 * * * *")).toBe("Stündlich zur vollen Stunde");
    expect(describeSchedule("0 1 * * *")).toBe("Täglich um 01:00");
    expect(describeSchedule("30 2 * * 0")).toBe("Jeden Sonntag um 02:30");
  });
});
