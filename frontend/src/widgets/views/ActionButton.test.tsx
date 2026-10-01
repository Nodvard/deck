import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { useAuthStore } from "../../state/auth";
import { useDialogsStore } from "../../state/dialogs";
import type { WidgetAction } from "../types";
import { ActionButton } from "./ActionButton";

/**
 * `ActionButton`s `confirm` ging bisher ueber `window.confirm()` (siehe
 * dessen fruehere Docstring-Notiz "waere huebscher ... bewusst zurueckgestellt") --
 * jetzt der echte globale Dialog (state/dialogs.ts). `GlobalDialogs` wird hier
 * bewusst MIT gerendert (wie in der echten App via AppShell.tsx) -- `ActionButton`
 * selbst zeichnet kein Dialog-UI mehr, nur der globale Store haelt den Zustand.
 */
const ACTION: WidgetAction = {
  id: "restart", label: "Neustart", endpoint: "actions/restart", method: "POST",
  body: null, confirm: true, confirm_text: "Wirklich neu starten?", style: "danger", permissions: [],
};

function mockFetch(onCall?: () => void, proposeBody: unknown = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.endsWith("/api/v1/ext/hello-world/actions/restart") && init?.method === "POST") {
      onCall?.();
      return new Response(JSON.stringify(proposeBody), { status: 200 });
    }
    if (url.endsWith("/api/v1/actions/a1/approve") && init?.method === "POST") {
      return new Response(JSON.stringify({ id: "a1", status: "approved" }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${init?.method ?? "GET"} ${url}`);
  });
}

function authedUser(permissions: string[]) {
  return {
    id: "u1", username: "owner", display_name: null, email: null, is_owner: false, locale: "de", permissions,
  };
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
  useDialogsStore.setState({ confirmPending: null, promptPending: null });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ActionButton confirm()", () => {
  it("fuehrt die Aktion NICHT aus, wenn der Bestaetigungsdialog abgebrochen wird", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <>
        <ActionButton action={ACTION} row={{}} extId="hello-world" />
        <GlobalDialogs />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Neustart" }));
    await screen.findByText("Wirklich neu starten?");
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));

    await waitFor(() => expect(screen.queryByText("Wirklich neu starten?")).not.toBeInTheDocument());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fuehrt die Aktion aus, sobald im echten Dialog bestaetigt wird", async () => {
    let called = false;
    vi.stubGlobal("fetch", mockFetch(() => { called = true; }));
    render(
      <>
        <ActionButton action={ACTION} row={{}} extId="hello-world" />
        <GlobalDialogs />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Neustart" }));
    await screen.findByText("Wirklich neu starten?");
    // "Neustart" erscheint zweimal (Aktions-Knopf UND Bestaetigen-Knopf im Dialog,
    // dessen Beschriftung `action.label` uebernimmt) -- der Dialog-Knopf ist der
    // zuletzt gerenderte.
    const buttons = screen.getAllByRole("button", { name: "Neustart" });
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => expect(called).toBe(true));
    await waitFor(() => expect(screen.queryByText("Wirklich neu starten?")).not.toBeInTheDocument());
  });
});

describe("ActionButton: Kachel weg, waehrend die Rueckfrage offen ist", () => {
  it("bestaetigt man danach noch, wird die Aktion NICHT mehr gestartet", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(
      <>
        <ActionButton action={ACTION} row={{}} extId="hello-world" />
        <GlobalDialogs />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Neustart" }));
    await screen.findByText("Wirklich neu starten?");

    // Seitenwechsel: die Kachel wird abgebaut, der globale Dialog bleibt offen.
    unmount();
    await act(async () => {
      useDialogsStore.getState().settleConfirm(true);
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

/**
 * Der Core Action Gate laesst jede Aktion erst
 * als "proposed" stehen -- vorher rief `run()` nie `/actions/{id}/approve` auf,
 * jede WidgetAction (u.a. gameserver Start/Stop) blieb dadurch fuer immer haengen.
 * Selbes Muster wie bereits fuer ProxmoxNodePage.tsx/BackupsPage.tsx bewiesen.
 */
describe("ActionButton Freigabe (Action Gate)", () => {
  it("genehmigt eine vorgeschlagene Aktion automatisch, wenn berechtigt", async () => {
    let approveCalled = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/v1/ext/hello-world/actions/restart") && init?.method === "POST") {
        return new Response(JSON.stringify({ id: "a1", status: "proposed", risk: "high" }), { status: 200 });
      }
      if (url.endsWith("/api/v1/actions/a1/approve") && init?.method === "POST") {
        approveCalled = true;
        return new Response(JSON.stringify({ id: "a1", status: "approved" }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${init?.method ?? "GET"} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    useAuthStore.setState({
      accessToken: "tok", status: "authenticated", mfaToken: null,
      user: authedUser(["actions.approve:high"]),
    });
    render(
      <>
        <ActionButton action={ACTION} row={{}} extId="hello-world" />
        <GlobalDialogs />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Neustart" }));
    await screen.findByText("Wirklich neu starten?");
    const buttons = screen.getAllByRole("button", { name: "Neustart" });
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => expect(approveCalled).toBe(true));
  });

  it("zeigt einen Hinweis statt automatisch freizugeben, wenn die Berechtigung fehlt", async () => {
    vi.stubGlobal("fetch", mockFetch(undefined, { id: "a1", status: "proposed", risk: "high" }));
    useAuthStore.setState({
      accessToken: "tok", status: "authenticated", mfaToken: null,
      user: authedUser([]),
    });
    render(
      <>
        <ActionButton action={ACTION} row={{}} extId="hello-world" />
        <GlobalDialogs />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Neustart" }));
    await screen.findByText("Wirklich neu starten?");
    const buttons = screen.getAllByRole("button", { name: "Neustart" });
    fireEvent.click(buttons[buttons.length - 1]);

    await screen.findByText(/Freigabe durch einen Admin nötig/);
  });
});

/**
 * Das Ergebnis der Freigabe (und direkte Antworten "failed"/"denied")
 * wurde verworfen -- ein gescheiterter Gameserver-Start sah aus wie ein verpuffter Klick.
 */
describe("ActionButton Ergebnis", () => {
  const START: WidgetAction = {
    id: "start", label: "Starten", endpoint: "servers/g1/start", method: "POST",
    body: null, confirm: false, confirm_text: null, style: "primary", permissions: [],
  };

  function routes(handlers: Record<string, () => Response | Promise<Response>>) {
    const calls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = (typeof input === "string" ? input : input.toString()).replace(/^.*\/api\/v1/, "");
      const key = `${init?.method ?? "GET"} ${url}`;
      calls.push(key);
      const handler = handlers[key];
      if (!handler) throw new Error(`Unerwarteter Fetch in diesem Test: ${key}`);
      return handler();
    });
    vi.stubGlobal("fetch", fetchMock);
    return calls;
  }

  const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });

  it("zeigt den Fehlergrund, wenn die Ausführung nach der Freigabe scheitert", async () => {
    routes({
      "POST /ext/gameserver/servers/g1/start": json({ action_id: "a1", status: "proposed", risk: "medium" }),
      "POST /actions/a1/approve": json({
        id: "a1", status: "failed", gate_decision: { rule: "autonomy:propose" }, result: { success: false, error: "Dienst startet nicht" },
      }),
    });
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: authedUser(["actions.approve:medium"]) });
    const onDone = vi.fn();
    render(<ActionButton action={START} row={{}} extId="gameserver" onDone={onDone} />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));

    expect(await screen.findByText("Fehlgeschlagen: Dienst startet nicht")).toBeInTheDocument();
    expect(onDone).toHaveBeenCalled();
  });

  it("zeigt die Sperr-Begründung, wenn das Gate direkt sperrt (Grund aus dem Aktions-Eintrag)", async () => {
    const calls = routes({
      "POST /ext/gameserver/servers/g1/start": json({ action_id: "a1", status: "denied", risk: "medium" }),
      "GET /actions/a1": json({
        id: "a1", status: "denied", gate_decision: { rule: "flap_limit", detail: "Bereits mehrfach in kurzer Zeit versucht." }, result: {},
      }),
    });
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: authedUser(["actions.approve:medium"]) });
    render(<ActionButton action={START} row={{}} extId="gameserver" />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));

    expect(await screen.findByText("Gesperrt: Bereits mehrfach in kurzer Zeit versucht.")).toBeInTheDocument();
    expect(calls).not.toContain("POST /actions/a1/approve");
  });

  it("meldet einen direkten Fehlschlag auch ohne lesbaren Aktions-Eintrag", async () => {
    routes({
      "POST /ext/gameserver/servers/g1/start": json({ action_id: "a1", status: "failed", risk: "low" }),
      "GET /actions/a1": json({ detail: "Berechtigung 'hosts.read' fehlt." }, 403),
    });
    render(<ActionButton action={START} row={{}} extId="gameserver" />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));

    expect(await screen.findByText("Fehlgeschlagen: unbekannter Fehler")).toBeInTheDocument();
  });

  it("fragt bei 202 „läuft“ nach und zeigt dann das Ergebnis", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let polls = 0;
    const calls = routes({
      "POST /ext/gameserver/servers/g1/start": json({ action_id: "a1", status: "proposed", risk: "medium" }),
      "POST /actions/a1/approve": json({ id: "a1", status: "executing", gate_decision: {}, result: {} }, 202),
      "GET /actions/a1": () => {
        polls += 1;
        return polls < 2
          ? new Response(JSON.stringify({ id: "a1", status: "executing", gate_decision: {}, result: {} }), { status: 200 })
          : new Response(JSON.stringify({ id: "a1", status: "failed", gate_decision: {}, result: { success: false, error: "Dienst startet nicht" } }), { status: 200 });
      },
    });
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: authedUser(["actions.approve:medium"]) });
    const onDone = vi.fn();
    render(<ActionButton action={START} row={{}} extId="gameserver" onDone={onDone} />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));

    expect(await screen.findByText(/Läuft im Hintergrund/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "…" })).toBeDisabled();
    expect(onDone).not.toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(6000));

    expect(await screen.findByText("Fehlgeschlagen: Dienst startet nicht")).toBeInTheDocument();
    expect(screen.queryByText(/Läuft im Hintergrund/)).toBeNull();
    expect(screen.getByRole("button", { name: "Starten" })).not.toBeDisabled();
    expect(onDone).toHaveBeenCalled();
    expect(calls.filter((c) => c === "GET /actions/a1")).toHaveLength(2);
  });

  it("hört auf nachzufragen, wenn die Kachel verschwindet", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = routes({
      "POST /ext/gameserver/servers/g1/start": json({ action_id: "a1", status: "proposed", risk: "medium" }),
      "POST /actions/a1/approve": json({ id: "a1", status: "executing", gate_decision: {}, result: {} }, 202),
      "GET /actions/a1": json({ id: "a1", status: "executing", gate_decision: {}, result: {} }),
    });
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: authedUser(["actions.approve:medium"]) });
    const { unmount } = render(<ActionButton action={START} row={{}} extId="gameserver" />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    await screen.findByText(/Läuft im Hintergrund/);
    unmount();

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(calls).not.toContain("GET /actions/a1");
  });

  it("fragt nicht nach, wenn die Kachel schon vor der Antwort der Freigabe verschwindet", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let release = (_res: Response) => {};
    const calls = routes({
      "POST /ext/gameserver/servers/g1/start": json({ action_id: "a1", status: "proposed", risk: "medium" }),
      // Die Freigabe wartet bis zu 20 s -- so lange steht der Test still.
      "POST /actions/a1/approve": () => new Promise<Response>((resolve) => { release = resolve; }),
      "GET /actions/a1": json({ id: "a1", status: "executing", gate_decision: {}, result: {} }),
    });
    useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: authedUser(["actions.approve:medium"]) });
    const onDone = vi.fn();
    const { unmount } = render(<ActionButton action={START} row={{}} extId="gameserver" onDone={onDone} />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    await waitFor(() => expect(calls).toContain("POST /actions/a1/approve"));
    unmount();
    await act(async () => {
      release(new Response(JSON.stringify({ id: "a1", status: "executing", gate_decision: {}, result: {} }), { status: 202 }));
    });

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(calls).not.toContain("GET /actions/a1");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("deutet andere Status-Felder (z. B. Vorfall 'dismissed') nicht als Aktionsergebnis", async () => {
    const calls = routes({ "POST /ext/gameserver/servers/g1/start": json({ ok: true, status: "dismissed" }) });
    const onDone = vi.fn();
    render(<ActionButton action={START} row={{}} extId="gameserver" onDone={onDone} />);

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(calls).toEqual(["POST /ext/gameserver/servers/g1/start"]);
    expect(screen.queryByText(/Verworfen|Fehlgeschlagen|Gesperrt/)).toBeNull();
  });
});

describe("ActionButton show_if", () => {
  it("zeigt den Knopf nur, wenn show_if etwas Wahres ergibt", () => {
    const action: WidgetAction = { ...ACTION, label: "Starten", show_if: "{{ can_start }}" };
    const { rerender } = render(<ActionButton action={action} row={{ can_start: false }} extId="gameserver" />);
    expect(screen.queryByRole("button", { name: "Starten" })).toBeNull();

    rerender(<ActionButton action={action} row={{ can_start: true }} extId="gameserver" />);
    expect(screen.getByRole("button", { name: "Starten" })).toBeTruthy();
  });

  it("ohne show_if ist der Knopf immer da (wie bisher)", () => {
    render(<ActionButton action={{ ...ACTION, label: "Immer" }} row={{}} extId="gameserver" />);
    expect(screen.getByRole("button", { name: "Immer" })).toBeTruthy();
  });
});
