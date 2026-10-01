import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../components/GlobalDialogs";
import { useAuthStore } from "../state/auth";
import { useDialogsStore } from "../state/dialogs";
import { ActionsPage } from "./ActionsPage";

const HOSTS = [{ id: "h1", display_name: "Proxmox-Knoten pve2" }];

const PROPOSED_ACTION = {
  id: "a1", ext_id: "proxmox", action_type: "vm.stop", host_id: "h1",
  payload: {}, risk: "high", status: "proposed",
  proposed_by_type: "user", proposed_by_id: "u1", proposed_by_label: "nico", reason: "Testabschaltung",
  created_at: "2026-09-19T00:00:00Z",
};

function mockFetch(onCall?: (path: string, method: string) => void, actions: Record<string, unknown>[] = [PROPOSED_ACTION]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    onCall?.(url, method);
    if (url.includes("/api/v1/hosts") && !url.includes("/actions")) {
      return new Response(JSON.stringify(HOSTS), { status: 200 });
    }
    if (url.includes("/api/v1/actions") && method === "GET") {
      return new Response(JSON.stringify(actions), { status: 200 });
    }
    if (url.endsWith("/api/v1/actions/a1/approve") && method === "POST") {
      return new Response(JSON.stringify({ ...PROPOSED_ACTION, status: "succeeded" }), { status: 200 });
    }
    if (url.endsWith("/api/v1/actions/a1/reject") && method === "POST") {
      return new Response(JSON.stringify({ ...PROPOSED_ACTION, status: "denied" }), { status: 200 });
    }
    if (url.endsWith("/api/v1/actions/a1/dismiss") && method === "POST") {
      return new Response(JSON.stringify({ ...PROPOSED_ACTION, status: "dismissed" }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

beforeEach(() => {
  useDialogsStore.setState({ confirmPending: null, promptPending: null });
});

afterEach(() => {
  vi.useRealTimers();
});

function loginAsAdmin() {
  useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
}

/** approve antwortet mit 202 'executing', GET /actions/a1 liefert der
 * Reihe nach `pollStatuses` (der letzte bleibt stehen). */
function mockBackgroundFetch(pollStatuses: Record<string, unknown>[], counters: { polls: number; lists: number }) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
    if (url.endsWith("/api/v1/actions/a1") && method === "GET") {
      const body = pollStatuses[Math.min(counters.polls, pollStatuses.length - 1)];
      counters.polls += 1;
      return new Response(JSON.stringify({ ...PROPOSED_ACTION, ...body }), { status: 200 });
    }
    if (url.includes("/api/v1/actions") && method === "GET") {
      counters.lists += 1;
      return new Response(JSON.stringify([PROPOSED_ACTION]), { status: 200 });
    }
    if (url.endsWith("/api/v1/actions/a1/approve") && method === "POST") {
      return new Response(JSON.stringify({ ...PROPOSED_ACTION, status: "executing" }), { status: 202 });
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

describe("ActionsPage", () => {
  it("zeigt wartende Aktionen mit aufgeloestem Host-Namen", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    vi.stubGlobal("fetch", mockFetch());
    render(<ActionsPage />);

    await screen.findByText("Proxmox-Knoten pve2");
    expect(screen.getByText("proxmox/vm.stop")).toBeInTheDocument();
    expect(screen.getByText("Testabschaltung")).toBeInTheDocument();
  });

  it("zeigt bei „Vorgeschlagen von“ den Namen und die rohe Kennung nur als Tooltip", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    const byExtension = { ...PROPOSED_ACTION, id: "a2", proposed_by_type: "extension", proposed_by_id: "nexus-soc", proposed_by_label: "Nodvard Shield" };
    vi.stubGlobal("fetch", mockFetch(undefined, [PROPOSED_ACTION, byExtension]));
    render(<ActionsPage />);

    const name = await screen.findByText("nico");
    expect(name).toHaveAttribute("title", "user/u1");
    expect(screen.getByText("Nodvard Shield")).toHaveAttribute("title", "extension/nexus-soc");
    expect(screen.queryByText("user/u1")).not.toBeInTheDocument();
    expect(screen.queryByText("extension/nexus-soc")).not.toBeInTheDocument();
  });

  it("zeigt ohne aufgelösten Namen (älteres Backend, gelöschter Nutzer) den rohen Wert", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    const noLabel = { ...PROPOSED_ACTION, proposed_by_label: undefined };
    const emptyLabel = { ...PROPOSED_ACTION, id: "a2", proposed_by_id: "u9", proposed_by_label: "" };
    vi.stubGlobal("fetch", mockFetch(undefined, [noLabel, emptyLabel]));
    render(<ActionsPage />);

    const raw = await screen.findByText("user/u1");
    expect(raw).toHaveAttribute("title", "user/u1");
    expect(screen.getByText("user/u9")).toBeInTheDocument();
  });

  describe("Entscheider (approved_by_label)", () => {
    const DECIDED = {
      ...PROPOSED_ACTION, status: "succeeded", approved_by_user_id: "u2", approved_by_label: "anna",
      gate_decision: { rule: "autonomy:propose" },
    };

    function showFor(actions: Record<string, unknown>[]) {
      loginAsAdmin();
      vi.stubGlobal("fetch", mockFetch(undefined, actions));
      render(<ActionsPage />);
    }

    it("zeigt bei einer bestätigten Aktion „Bestätigt von <Name>“ mit der Kennung als Tooltip", async () => {
      showFor([DECIDED]);
      const line = await screen.findByText("Bestätigt von anna");
      expect(line).toHaveAttribute("title", "user/u2");
    });

    it("zeigt bei abgelehnten und verworfenen Aktionen das passende Verb", async () => {
      showFor([
        { ...DECIDED, id: "d1", status: "denied", approved_by_label: "berta" },
        { ...DECIDED, id: "d2", status: "dismissed", approved_by_label: "carl" },
      ]);
      expect(await screen.findByText("Abgelehnt von berta")).toBeInTheDocument();
      expect(screen.getByText("Verworfen von carl")).toBeInTheDocument();
      expect(screen.queryByText(/Bestätigt von/)).not.toBeInTheDocument();
    });

    it("zeigt bei einer wartenden Aktion nichts", async () => {
      showFor([{ ...PROPOSED_ACTION, approved_by_user_id: null, approved_by_label: null }]);
      await screen.findByText("nico");
      expect(screen.queryByText(/^(Bestätigt|Abgelehnt|Verworfen) von|^Automatisch freigegeben/)).not.toBeInTheDocument();
    });

    it("zeigt bei „Selbstständig handeln“ „Automatisch freigegeben“ statt eines Namens", async () => {
      showFor([{
        ...PROPOSED_ACTION, status: "succeeded", approved_by_user_id: null, approved_by_label: null,
        gate_decision: { rule: "autonomy:full" },
      }]);
      expect(await screen.findByText("Automatisch freigegeben")).toBeInTheDocument();
      expect(screen.queryByText(/Bestätigt von/)).not.toBeInTheDocument();
    });

    it("zeigt bei einer von der Sicherheitsregel gesperrten Aktion keinen Entscheider", async () => {
      showFor([{
        ...PROPOSED_ACTION, status: "denied", approved_by_user_id: null, approved_by_label: null,
        gate_decision: { rule: "deny_pattern:x" },
      }]);
      await screen.findByText("nico");
      expect(screen.queryByText(/^(Bestätigt|Abgelehnt|Verworfen) von|^Automatisch freigegeben/)).not.toBeInTheDocument();
    });

    it("zeigt dem Betrachter die rohe Kennung, wenn das Backend keinen Namen auflöst", async () => {
      showFor([{ ...DECIDED, approved_by_user_id: "3f2a-uuid", approved_by_label: "3f2a-uuid" }]);
      const line = await screen.findByText("Bestätigt von 3f2a-uuid");
      expect(line).toHaveAttribute("title", "user/3f2a-uuid");
    });

    it("kommt mit einem älteren Backend ohne die Felder klar", async () => {
      showFor([{ ...PROPOSED_ACTION, status: "succeeded" }]);
      await screen.findByText("nico");
      expect(screen.queryByText(/Bestätigt von/)).not.toBeInTheDocument();
    });
  });

  it("bestätigt eine Aktion, wenn der Nutzer die Berechtigung hat", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    let approveCalled = false;
    vi.stubGlobal("fetch", mockFetch((url, method) => {
      if (url.endsWith("/approve") && method === "POST") approveCalled = true;
    }));
    render(<ActionsPage />);

    const approveBtn = await screen.findByRole("button", { name: "Bestätigen" });
    expect(approveBtn).not.toBeDisabled();
    fireEvent.click(approveBtn);

    await waitFor(() => expect(approveCalled).toBe(true));
  });

  it("deaktiviert Bestätigen/Ablehnen/Verwerfen ohne die noetige Berechtigung", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u2", username: "operator", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["hosts.read"] } });
    vi.stubGlobal("fetch", mockFetch());
    render(<ActionsPage />);

    const approveBtn = await screen.findByRole("button", { name: "Bestätigen" });
    expect(approveBtn).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ablehnen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Verwerfen" })).toBeDisabled();
  });

  it("lehnt eine Aktion mit eingegebener Begründung ab", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    let rejectBody: unknown = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/api/v1/hosts") && !url.includes("/actions")) return new Response(JSON.stringify(HOSTS), { status: 200 });
      if (url.includes("/api/v1/actions") && method === "GET") return new Response(JSON.stringify([PROPOSED_ACTION]), { status: 200 });
      if (url.endsWith("/reject") && method === "POST") {
        rejectBody = JSON.parse(init!.body as string);
        return new Response(JSON.stringify({ ...PROPOSED_ACTION, status: "denied" }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    }));
    render(
      <>
        <ActionsPage />
        <GlobalDialogs />
      </>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Ablehnen" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(dialog.querySelector("input")!, { target: { value: "Nicht jetzt" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(rejectBody).toEqual({ reason: "Nicht jetzt" }));
  });

  it("meldet eine fehlgeschlagene Ausführung mit Grund statt „Bestätigt“", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
      if (url.includes("/api/v1/actions") && method === "GET") return new Response(JSON.stringify([PROPOSED_ACTION]), { status: 200 });
      if (url.endsWith("/api/v1/actions/a1/approve") && method === "POST") {
        return new Response(JSON.stringify({
          ...PROPOSED_ACTION, status: "failed", gate_decision: { rule: "autonomy:propose" },
          result: { success: false, error: "SSH: Verbindung abgelehnt" }, finished_at: "2026-09-19T00:00:05Z",
        }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    }));
    render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByText("vm.stop: Fehlgeschlagen: SSH: Verbindung abgelehnt")).toBeInTheDocument();
    expect(screen.queryByText(/Bestätigt/)).toBeNull();
  });

  it("meldet „Ausgeführt“, wenn die Ausführung geklappt hat", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    vi.stubGlobal("fetch", mockFetch());
    render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByText("vm.stop: Ausgeführt")).toBeInTheDocument();
  });

  it("zeigt in der Tabelle den Fehlergrund und die Sperr-Begründung", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    const rows = [
      { ...PROPOSED_ACTION, id: "a2", status: "failed", gate_decision: { rule: "autonomy:propose" }, result: { success: false, error: "Host hat keine Standard-Zugangsdaten." } },
      { ...PROPOSED_ACTION, id: "a3", status: "denied", gate_decision: { rule: "flap_limit", detail: "Bereits mehrfach in kurzer Zeit versucht." }, result: {} },
      { ...PROPOSED_ACTION, id: "a4", status: "executing", gate_decision: { rule: "autonomy:propose" }, result: {} },
    ];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
      if (url.includes("/api/v1/actions")) return new Response(JSON.stringify(rows), { status: 200 });
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
    render(<ActionsPage />);

    expect(await screen.findByText("Host hat keine Standard-Zugangsdaten.")).toBeInTheDocument();
    expect(screen.getByText("Bereits mehrfach in kurzer Zeit versucht.")).toBeInTheDocument();
    expect(screen.getByText("Läuft …", { selector: "span" }).className).toContain("sky");
  });

  it("lädt die Liste auch nach einem Fehler neu (z. B. 409, Vorschlag abgelaufen)", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    let listCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
      if (url.includes("/api/v1/actions") && method === "GET") {
        listCalls += 1;
        return new Response(JSON.stringify(listCalls === 1 ? [PROPOSED_ACTION] : []), { status: 200 });
      }
      if (url.endsWith("/approve") && method === "POST") {
        return new Response(JSON.stringify({ detail: "Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: 'expired')." }), { status: 409 });
      }
      throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    }));
    render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByText(/nicht mehr im Zustand 'proposed'/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Bestätigen" })).toBeNull());
    expect(screen.getByText("Keine Aktionen in diesem Filter.")).toBeInTheDocument();
  });

  it("bietet „Läuft“ als Statusfilter an", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    vi.stubGlobal("fetch", mockFetch());
    render(<ActionsPage />);

    await screen.findByText("proxmox/vm.stop");
    expect(screen.getByRole("option", { name: "Läuft" })).toHaveAttribute("value", "executing");
  });

  it("Handy: Tabelle scrollt in sich, Knöpfe dürfen umbrechen", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    vi.stubGlobal("fetch", mockFetch());
    render(<ActionsPage />);

    const approveBtn = await screen.findByRole("button", { name: "Bestätigen" });
    expect(screen.getByRole("table").parentElement).toHaveClass("overflow-x-auto");
    expect(approveBtn.parentElement).toHaveClass("flex-wrap");
  });

  it("wechselt den Statusfilter und fragt erneut ab", async () => {
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u1", username: "admin", display_name: null, email: null, is_owner: true, locale: "de", permissions: [] } });
    const calledUrls: string[] = [];
    vi.stubGlobal("fetch", mockFetch((url, method) => {
      if (method === "GET" && url.includes("/api/v1/actions")) calledUrls.push(url);
    }));
    render(<ActionsPage />);

    await screen.findByText("proxmox/vm.stop");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "failed" } });

    await waitFor(() => expect(calledUrls.some((u) => u.includes("status_=failed"))).toBe(true));
  });

  it("zeigt bei 202 „läuft im Hintergrund“ und fragt nach, bis das Ergebnis da ist", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loginAsAdmin();
    const counters = { polls: 0, lists: 0 };
    vi.stubGlobal("fetch", mockBackgroundFetch([
      { status: "executing" },
      { status: "failed", result: { success: false, error: "Paketquelle nicht erreichbar" } },
    ], counters));
    render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByText(/vm\.stop: Läuft im Hintergrund/)).toBeInTheDocument();
    // Der Knopf ist schon wieder frei, die Aktion laeuft weiter.
    await waitFor(() => expect(screen.getByRole("button", { name: "Bestätigen" })).not.toBeDisabled());
    expect(counters.polls).toBe(0);

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(counters.polls).toBe(1);
    expect(screen.getByText(/Läuft im Hintergrund/)).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await screen.findByText("vm.stop: Fehlgeschlagen: Paketquelle nicht erreichbar")).toBeInTheDocument();
    const listsAfterDone = counters.lists;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(counters.polls).toBe(2);
    expect(counters.lists).toBe(listsAfterDone);
  });

  it("zeigt bei 409 „läuft schon“ keinen Fehler, sondern fragt nach", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loginAsAdmin();
    const counters = { polls: 0, lists: 0 };
    const background = mockBackgroundFetch([{ status: "succeeded", result: { success: true, output: "ok" } }], counters);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/approve") && init?.method === "POST") {
        return new Response(JSON.stringify({ detail: "Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: 'executing')." }), { status: 409 });
      }
      return background(input, init);
    }));
    render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByText(/vm\.stop: Wurde schon bestätigt\. Läuft im Hintergrund/)).toBeInTheDocument();
    expect(screen.queryByText(/nicht mehr im Zustand/)).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(counters.polls).toBe(1);
    expect(await screen.findByText(/^vm\.stop: Ausgeführt/)).toBeInTheDocument();
  });

  it("hört beim Verlassen der Seite auf nachzufragen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loginAsAdmin();
    const counters = { polls: 0, lists: 0 };
    vi.stubGlobal("fetch", mockBackgroundFetch([{ status: "executing" }], counters));
    const { unmount } = render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));
    await screen.findByText(/Läuft im Hintergrund/);
    unmount();

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(counters.polls).toBe(0);
  });

  it("fragt nicht nach, wenn die Seite schon vor der Antwort der Freigabe verlassen wurde", async () => {
    // Kein behobener Fehler: hier entsteht der Abbruch-Controller schon beim Einhaengen.
    // Der Test haelt nur fest, dass das so bleibt (Schutz gegen Rueckschritte).
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loginAsAdmin();
    const counters = { polls: 0, lists: 0 };
    const background = mockBackgroundFetch([{ status: "executing" }], counters);
    let release = (_res: Response) => {};
    let approveCalled = false;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/approve") && init?.method === "POST") {
        approveCalled = true;
        return new Promise<Response>((resolve) => { release = resolve; });
      }
      return background(input, init);
    }));
    const { unmount } = render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));
    await waitFor(() => expect(approveCalled).toBe(true));
    unmount();
    await act(async () => {
      release(new Response(JSON.stringify({ ...PROPOSED_ACTION, status: "executing" }), { status: 202 }));
    });

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(counters.polls).toBe(0);
  });

  it("gibt nach einer Stunde mit einem Hinweis auf", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loginAsAdmin();
    const counters = { polls: 0, lists: 0 };
    vi.stubGlobal("fetch", mockBackgroundFetch([{ status: "executing" }], counters));
    render(<ActionsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));
    await screen.findByText(/Läuft im Hintergrund/);

    await act(() => vi.advanceTimersByTimeAsync(60 * 60 * 1000 + 3000));
    expect(await screen.findByText(/vm\.stop: Läuft seit über einer Stunde/)).toBeInTheDocument();
    const polls = counters.polls;
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(counters.polls).toBe(polls);
  });

  it("lädt die Liste neu, solange eine Zeile noch läuft", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    loginAsAdmin();
    let listCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
      if (url.includes("/api/v1/actions")) {
        listCalls += 1;
        const status = listCalls < 3 ? "executing" : "succeeded";
        return new Response(JSON.stringify([{ ...PROPOSED_ACTION, status, gate_decision: {}, result: {} }]), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
    render(<ActionsPage />);

    expect(await screen.findByText("Läuft …", { selector: "span" })).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await screen.findByText("Erfolgreich", { selector: "span" })).toBeInTheDocument();
    const calls = listCalls;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(listCalls).toBe(calls);
  });

  describe("mehrere Freigaben gleichzeitig", () => {
    const A = { ...PROPOSED_ACTION, id: "a1", ext_id: "nexus-soc", action_type: "updates.apply", reason: "Updates", gate_decision: {}, result: {} };
    const B = { ...PROPOSED_ACTION, id: "a2", action_type: "container.restart", reason: "Neustart", gate_decision: {}, result: {} };

    /** Freigabe von A haengt, bis der Test sie mit `releaseA` beantwortet; B scheitert sofort. */
    function mockTwoFetch() {
      const state = { releaseA: (_res: Response) => {} };
      const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
        if (url.endsWith("/api/v1/actions/a1/approve") && method === "POST") {
          return new Promise<Response>((resolve) => { state.releaseA = resolve; });
        }
        if (url.endsWith("/api/v1/actions/a2/approve") && method === "POST") {
          return new Response(JSON.stringify({ ...B, status: "failed", result: { success: false, error: "Container nicht gefunden" } }), { status: 200 });
        }
        if (url.includes("/api/v1/actions/a1") && method === "GET") return new Response(JSON.stringify({ ...A, status: "executing" }), { status: 200 });
        if (url.includes("/api/v1/actions") && method === "GET") return new Response(JSON.stringify([A, B]), { status: 200 });
        throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
      });
      return { fetchMock, state };
    }

    it("überschreibt die Meldung einer neueren Freigabe nicht mit der späten Antwort einer älteren", async () => {
      loginAsAdmin();
      const { fetchMock, state } = mockTwoFetch();
      vi.stubGlobal("fetch", fetchMock);
      render(<ActionsPage />);

      await screen.findByText("Updates");
      const approveButtons = () => screen.getAllByRole("button", { name: "Bestätigen" });
      fireEvent.click(approveButtons()[0]!); // A: die Antwort steht noch aus
      fireEvent.click(approveButtons()[1]!); // B: scheitert sofort
      expect(await screen.findByText("container.restart: Fehlgeschlagen: Container nicht gefunden")).toBeInTheDocument();

      // Die Zeile von A ist weiter gesperrt, obwohl B fertig ist -- und mit ihr die
      // Auswahl und die Sammel-Bedienung, solange irgendeine Freigabe noch wartet.
      expect(approveButtons()[0]).toBeDisabled();
      expect(screen.getByRole("checkbox", { name: "Alle auswählen" })).toBeDisabled();
      for (const box of screen.getAllByRole("checkbox", { name: /^Auswählen:/ })) expect(box).toBeDisabled();

      await act(async () => {
        state.releaseA(new Response(JSON.stringify({ ...A, status: "executing" }), { status: 202 }));
      });
      await waitFor(() => expect(approveButtons()[0]).not.toBeDisabled());
      expect(screen.getByRole("checkbox", { name: "Alle auswählen" })).not.toBeDisabled();

      expect(screen.getByText("container.restart: Fehlgeschlagen: Container nicht gefunden")).toBeInTheDocument();
      expect(screen.queryByText(/updates\.apply: Läuft im Hintergrund/)).toBeNull();
    });

    it("die Meldung der zuletzt gestarteten Freigabe bleibt stehen -- der späte Fehler der älteren kommt dazu, statt zu verschwinden", async () => {
      loginAsAdmin();
      const { fetchMock, state } = mockTwoFetch();
      vi.stubGlobal("fetch", fetchMock);
      render(<ActionsPage />);

      await screen.findByText("Updates");
      fireEvent.click(screen.getAllByRole("button", { name: "Bestätigen" })[0]!);
      fireEvent.click(screen.getAllByRole("button", { name: "Bestätigen" })[1]!);
      await screen.findByText("container.restart: Fehlgeschlagen: Container nicht gefunden");

      await act(async () => {
        state.releaseA(new Response(JSON.stringify({ detail: "Zeitüberschreitung" }), { status: 500 }));
      });
      await waitFor(() => expect(screen.getAllByRole("button", { name: "Bestätigen" })[0]).not.toBeDisabled());
      expect(screen.getByText("container.restart: Fehlgeschlagen: Container nicht gefunden")).toBeInTheDocument();
      expect(screen.getByText("updates.apply: Fehler: Zeitüberschreitung")).toBeInTheDocument();
    });

    it("scheitert die ältere Freigabe erst nach einer neueren erfolgreichen, steht ihr Grund da, auch wenn ihre Zeile aus dem Filter fällt", async () => {
      loginAsAdmin();
      let listCalls = 0;
      const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
        if (url.endsWith("/api/v1/actions/a1/approve") && method === "POST") {
          return new Promise<Response>((resolve) => { releaseA = resolve; });
        }
        if (url.endsWith("/api/v1/actions/a2/approve") && method === "POST") {
          return new Response(JSON.stringify({ ...B, status: "succeeded", result: { success: true } }), { status: 200 });
        }
        if (url.includes("/api/v1/actions") && method === "GET") {
          // Nach den Freigaben ist im Standardfilter „Wartet auf Bestätigung“ nichts mehr offen.
          listCalls += 1;
          return new Response(JSON.stringify(listCalls === 1 ? [A, B] : []), { status: 200 });
        }
        throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
      });
      let releaseA: (res: Response) => void = () => {};
      vi.stubGlobal("fetch", fetchMock);
      render(<ActionsPage />);

      await screen.findByText("Updates");
      fireEvent.click(screen.getAllByRole("button", { name: "Bestätigen" })[0]!);
      fireEvent.click(screen.getAllByRole("button", { name: "Bestätigen" })[1]!);
      await screen.findByText("container.restart: Ausgeführt");

      await act(async () => {
        releaseA(new Response(JSON.stringify({ ...A, status: "failed", result: { success: false, error: "Server nicht erreichbar" } }), { status: 200 }));
      });

      expect(await screen.findByText(/updates\.apply: Fehlgeschlagen: Server nicht erreichbar/)).toBeInTheDocument();
      expect(screen.getByText("container.restart: Ausgeführt")).toBeInTheDocument();
      expect(await screen.findByText("Keine Aktionen in diesem Filter.")).toBeInTheDocument();
    });

    it("ein später Erfolg oder Zwischenstand der älteren Freigabe bleibt still verworfen", async () => {
      loginAsAdmin();
      const { fetchMock, state } = mockTwoFetch();
      vi.stubGlobal("fetch", fetchMock);
      render(<ActionsPage />);

      await screen.findByText("Updates");
      fireEvent.click(screen.getAllByRole("button", { name: "Bestätigen" })[0]!);
      fireEvent.click(screen.getAllByRole("button", { name: "Bestätigen" })[1]!);
      await screen.findByText("container.restart: Fehlgeschlagen: Container nicht gefunden");

      await act(async () => {
        state.releaseA(new Response(JSON.stringify({ ...A, status: "succeeded", result: { success: true } }), { status: 200 }));
      });
      await waitFor(() => expect(screen.getAllByRole("button", { name: "Bestätigen" })[0]).not.toBeDisabled());
      expect(screen.queryByText(/^updates\.apply: /)).toBeNull();
    });
  });

  describe("Sammel-Freigabe", () => {
    const ROWS = [
      { ...PROPOSED_ACTION, id: "b1", reason: "Wartung ki-server", gate_decision: {}, result: {} },
      { ...PROPOSED_ACTION, id: "b2", reason: "Wartung docker", gate_decision: {}, result: {} },
      { ...PROPOSED_ACTION, id: "b3", reason: "Wartung pve1", gate_decision: {}, result: {} },
      { ...PROPOSED_ACTION, id: "b4", reason: "Wartung berry", gate_decision: {}, result: {} },
    ];

    /** `approve`: Antwort je Aktions-ID; `calls` sammelt alle POST-Aufrufe. */
    function mockBulkFetch(
      approve: Record<string, () => Response>,
      calls: string[],
      polls: Record<string, Record<string, unknown>[]> = {},
    ) {
      const pollCount: Record<string, number> = {};
      return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        const method = init?.method ?? "GET";
        if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
        const single = /\/api\/v1\/actions\/(b\d)$/.exec(url);
        if (single && method === "GET") {
          const seq = polls[single[1]!] ?? [{ status: "succeeded" }];
          const n = pollCount[single[1]!] ?? 0;
          pollCount[single[1]!] = n + 1;
          const row = ROWS.find((r) => r.id === single[1])!;
          return new Response(JSON.stringify({ ...row, ...seq[Math.min(n, seq.length - 1)] }), { status: 200 });
        }
        if (url.includes("/api/v1/actions") && method === "GET") return new Response(JSON.stringify(ROWS), { status: 200 });
        if (method === "POST") {
          calls.push(url.replace("/api/v1", ""));
          const m = /\/actions\/(b\d)\/approve\?wait=0$/.exec(url);
          if (m) return approve[m[1]!]!();
          const d = /\/actions\/(b\d)\/dismiss$/.exec(url);
          if (d) return new Response(JSON.stringify({ ...ROWS.find((r) => r.id === d[1]), status: "dismissed" }), { status: 200 });
        }
        throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
      });
    }

    const running = (id: string) => () =>
      new Response(JSON.stringify({ ...ROWS.find((r) => r.id === id), status: "executing" }), { status: 202 });

    it("gibt ausgewählte Aktionen mit wait=0 frei und fasst gemischte Ergebnisse zusammen", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      loginAsAdmin();
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockBulkFetch({
        b1: running("b1"),
        b2: running("b2"),
        b3: () => new Response(JSON.stringify({
          ...ROWS[2], status: "denied", gate_decision: { rule: "blocklist", detail: "Server ist gesperrt." },
        }), { status: 200 }),
        b4: () => new Response(JSON.stringify({ detail: "Vorschlag ist nicht mehr im Zustand 'proposed' (jetzt: 'expired')." }), { status: 409 }),
      }, calls, { b1: [{ status: "executing" }, { status: "succeeded" }], b2: [{ status: "failed", result: { success: false, error: "SSH weg" } }] }));
      render(
        <>
          <ActionsPage />
          <GlobalDialogs />
        </>,
      );

      await screen.findByText("Wartung ki-server");
      fireEvent.click(screen.getByLabelText("Alle auswählen"));
      const approveAll = screen.getByRole("button", { name: "Ausgewählte freigeben (4)" });
      fireEvent.click(approveAll);
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent("4 ausgewählte Aktionen bestätigen");
      expect(dialog).toHaveTextContent("proxmox/vm.stop ×4");
      fireEvent.click(screen.getByRole("button", { name: "Freigeben" }));

      expect(await screen.findByText(/2 gestartet, 1 gesperrt: Server ist gesperrt\., 1 nicht mehr offen\. Läuft im Hintergrund/)).toBeInTheDocument();
      expect(calls).toEqual([
        "/actions/b1/approve?wait=0",
        "/actions/b2/approve?wait=0",
        "/actions/b3/approve?wait=0",
        "/actions/b4/approve?wait=0",
      ]);
      // Die Auswahl ist danach leer, die Knöpfe sind wieder frei.
      expect(screen.getByRole("button", { name: "Ausgewählte freigeben (0)" })).toBeDisabled();

      // Wie bei Einzelfreigaben: nachfragen, bis alle gestarteten fertig sind.
      await act(() => vi.advanceTimersByTimeAsync(3000));
      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(await screen.findByText(/Ergebnis: 1 ausgeführt, 1 fehlgeschlagen: SSH weg/)).toBeInTheDocument();
    });

    it("zeigt eine 403 (Berechtigung fehlt) als gesperrt mit Grund und macht bei den anderen weiter", async () => {
      loginAsAdmin();
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockBulkFetch({
        b1: () => new Response(JSON.stringify({ detail: "Berechtigung 'actions.approve:high' fehlt." }), { status: 403 }),
        b2: () => new Response(JSON.stringify({ ...ROWS[1], status: "succeeded" }), { status: 200 }),
        b3: running("b3"),
        b4: running("b4"),
      }, calls));
      render(
        <>
          <ActionsPage />
          <GlobalDialogs />
        </>,
      );

      await screen.findByText("Wartung ki-server");
      fireEvent.click(screen.getByLabelText("Alle auswählen"));
      fireEvent.click(screen.getByRole("button", { name: "Ausgewählte freigeben (4)" }));
      fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

      expect(await screen.findByText(/3 gestartet, 1 gesperrt: proxmox\/vm\.stop: Berechtigung 'actions\.approve:high' fehlt\./)).toBeInTheDocument();
      expect(calls).toHaveLength(4);
    });

    it("überschreibt eine neuere Meldung nicht mit dem späten Ergebnis einer älteren Sammel-Freigabe", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      loginAsAdmin();
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockBulkFetch({
        b1: running("b1"),
      }, calls, { b1: [{ status: "executing" }, { status: "executing" }, { status: "succeeded" }] }));
      render(
        <>
          <ActionsPage />
          <GlobalDialogs />
        </>,
      );

      await screen.findByText("Wartung ki-server");
      const boxes = screen.getAllByLabelText(/Auswählen: proxmox\/vm\.stop auf Proxmox-Knoten pve2/);
      fireEvent.click(boxes[0]!);
      fireEvent.click(screen.getByRole("button", { name: "Ausgewählte freigeben (1)" }));
      fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));
      expect(await screen.findByText(/1 gestartet\. Läuft im Hintergrund/)).toBeInTheDocument();

      // Währenddessen eine neue Sammel-Aktion: ihre Meldung soll stehen bleiben.
      fireEvent.click(screen.getAllByLabelText(/Auswählen: proxmox\/vm\.stop auf Proxmox-Knoten pve2/)[1]!);
      fireEvent.click(screen.getByRole("button", { name: "Ausgewählte verwerfen (1)" }));
      fireEvent.click(await screen.findByRole("button", { name: "Bestätigen" }));
      expect(await screen.findByText("1 verworfen")).toBeInTheDocument();

      for (let i = 0; i < 4; i += 1) await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(screen.getByText("1 verworfen")).toBeInTheDocument();
      expect(screen.queryByText(/Ergebnis:/)).toBeNull();
    });

    it("ist nur mit der nötigen Berechtigung zu sehen und wählt nur entscheidbare Zeilen aus", async () => {
      useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u2", username: "operator", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["actions.approve:low"] } });
      const rows = [
        { ...PROPOSED_ACTION, id: "b1", risk: "low", reason: "Klein", gate_decision: {}, result: {} },
        { ...PROPOSED_ACTION, id: "b2", risk: "high", reason: "Groß", gate_decision: {}, result: {} },
      ];
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/api/v1/hosts")) return new Response(JSON.stringify(HOSTS), { status: 200 });
        return new Response(JSON.stringify(rows), { status: 200 });
      }));
      render(<ActionsPage />);

      await screen.findByText("Groß");
      expect(screen.getAllByRole("checkbox")).toHaveLength(2); // Alle auswählen + die eine low-Zeile
      fireEvent.click(screen.getByLabelText("Alle auswählen"));
      expect(screen.getByRole("button", { name: "Ausgewählte freigeben (1)" })).toBeEnabled();
    });

    it("zeigt ohne jede Berechtigung weder Kästchen noch Sammel-Knöpfe", async () => {
      useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: { id: "u3", username: "gast", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["hosts.read"] } });
      vi.stubGlobal("fetch", mockFetch());
      render(<ActionsPage />);

      await screen.findByText("proxmox/vm.stop");
      expect(screen.queryByRole("checkbox")).toBeNull();
      expect(screen.queryByRole("button", { name: /Ausgewählte/ })).toBeNull();
    });

    it("ist gegen Doppelklick gesichert: jede Aktion wird nur einmal freigegeben", async () => {
      loginAsAdmin();
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockBulkFetch({
        b1: () => new Response(JSON.stringify({ ...ROWS[0], status: "succeeded" }), { status: 200 }),
        b2: () => new Response(JSON.stringify({ ...ROWS[1], status: "succeeded" }), { status: 200 }),
        b3: () => new Response(JSON.stringify({ ...ROWS[2], status: "succeeded" }), { status: 200 }),
        b4: () => new Response(JSON.stringify({ ...ROWS[3], status: "succeeded" }), { status: 200 }),
      }, calls));
      render(
        <>
          <ActionsPage />
          <GlobalDialogs />
        </>,
      );

      await screen.findByText("Wartung ki-server");
      fireEvent.click(screen.getByLabelText("Alle auswählen"));
      const button = screen.getByRole("button", { name: "Ausgewählte freigeben (4)" });
      fireEvent.click(button);
      fireEvent.click(button); // zweiter Klick, bevor der Dialog beantwortet ist
      fireEvent.click(await screen.findByRole("button", { name: "Freigeben" }));

      expect(await screen.findByText("4 gestartet")).toBeInTheDocument();
      expect(calls).toHaveLength(4);
      // Kein zweiter Dialog nach dem ersten Durchlauf.
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("gibt nichts frei, wenn die Rückfrage abgebrochen wird", async () => {
      loginAsAdmin();
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockBulkFetch({}, calls));
      render(
        <>
          <ActionsPage />
          <GlobalDialogs />
        </>,
      );

      await screen.findByText("Wartung ki-server");
      fireEvent.click(screen.getByLabelText("Alle auswählen"));
      fireEvent.click(screen.getByRole("button", { name: "Ausgewählte freigeben (4)" }));
      fireEvent.click(await screen.findByRole("button", { name: "Abbrechen" }));

      await waitFor(() => expect(screen.getByRole("button", { name: "Ausgewählte freigeben (4)" })).toBeEnabled());
      expect(calls).toEqual([]);
    });

    it("verwirft die ausgewählten Aktionen", async () => {
      loginAsAdmin();
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockBulkFetch({}, calls));
      render(
        <>
          <ActionsPage />
          <GlobalDialogs />
        </>,
      );

      await screen.findByText("Wartung ki-server");
      const boxes = screen.getAllByLabelText(/Auswählen: proxmox\/vm\.stop auf Proxmox-Knoten pve2/);
      expect(boxes).toHaveLength(4);
      fireEvent.click(boxes[0]!);
      fireEvent.click(boxes[2]!);
      fireEvent.click(screen.getByRole("button", { name: "Ausgewählte verwerfen (2)" }));
      expect(await screen.findByRole("dialog")).toHaveTextContent("2 ausgewählte Aktionen verwerfen");
      fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));

      expect(await screen.findByText("2 verworfen")).toBeInTheDocument();
      expect(calls).toEqual(["/actions/b1/dismiss", "/actions/b3/dismiss"]);
    });
  });
});
