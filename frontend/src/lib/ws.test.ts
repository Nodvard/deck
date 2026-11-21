import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerUnavailableError, useAuthStore } from "../state/auth";
import { WS_TOO_MANY_MESSAGE, wsClient } from "./ws";

type Listener = (ev: { data?: string }) => void;

/** Minimaler WebSocket-Ersatz: merkt sich die Zuhoerer, damit der Test Nachrichten des Servers nachstellen kann. */
class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.CONNECTING;
  listeners = new Map<string, Listener[]>();
  sent: string[] = [];
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  receive(message: unknown) {
    for (const listener of this.listeners.get("message") ?? []) listener({ data: JSON.stringify(message) });
  }
}

/** Die Tests laufen in Node; @types/node ist im Frontend bewusst nicht eingebunden. */
const nodeProcess = (globalThis as unknown as {
  process: { on(event: string, listener: (reason: unknown) => void): void; off(event: string, listener: (reason: unknown) => void): void };
}).process;

const USER = { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] };
const { refresh: realRefresh } = useAuthStore.getState();

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
});

afterEach(() => {
  wsClient.disconnect();
  useAuthStore.setState({ refresh: realRefresh });
  vi.unstubAllGlobals();
});

describe("WS-Client -- auth_error", () => {
  it("Server nicht erreichbar: das Erneuern schlägt fehl, ohne unbehandelte Ablehnung, Anmeldung bleibt", async () => {
    // Bewusst kein vi.fn: das haengt an zurueckgegebene Promises eigene Zuhoerer, und die
    // Ablehnung wuerde nie als "unbehandelt" auffallen.
    let refreshCalls = 0;
    const refresh = () => {
      refreshCalls += 1;
      return Promise.reject(new ServerUnavailableError());
    };
    useAuthStore.setState({ refresh });
    const unhandled = vi.fn();
    nodeProcess.on("unhandledRejection", unhandled);
    try {
      wsClient.subscribe("hosts", () => {});
      const socket = FakeSocket.instances[0];
      expect(socket).toBeDefined();

      socket.receive({ type: "auth_error" });
      // Unbehandelte Ablehnungen meldet Node erst nach dem Leeren der Microtask-Warteschlange.
      await new Promise((resolve) => setTimeout(resolve, 20));

      expect(refreshCalls).toBe(1);
      expect(unhandled).not.toHaveBeenCalled();
      const state = useAuthStore.getState();
      expect(state.status).toBe("authenticated");
      expect(state.accessToken).toBe("tok");
      expect(state.user).toEqual(USER);
    } finally {
      nodeProcess.off("unhandledRejection", unhandled);
    }
  });

  it("Erneuern klappt: kein Fehler, Zustand kommt vom Erneuern", async () => {
    const refresh = vi.fn(async () => {
      useAuthStore.setState({ accessToken: "tok-neu" });
      return true;
    });
    useAuthStore.setState({ refresh });

    wsClient.subscribe("hosts", () => {});
    FakeSocket.instances[0].receive({ type: "auth_error" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().accessToken).toBe("tok-neu");
  });
});

describe("WS-Client -- abgelöste Verbindungen", () => {
  /** Wie der Browser: `close()` meldet das `close`-Ereignis erst später. */
  function fireClose(socket: FakeSocket) {
    for (const listener of socket.listeners.get("close") ?? []) listener({});
  }

  it("ein verspätetes close der alten Verbindung öffnet keine weitere", () => {
    vi.useFakeTimers();
    try {
      wsClient.subscribe("hosts", () => {});
      const first = FakeSocket.instances[0];
      wsClient.disconnect();
      wsClient.subscribe("jobs", () => {});
      expect(FakeSocket.instances).toHaveLength(2);

      fireClose(first);
      vi.advanceTimersByTime(30_000);

      expect(FakeSocket.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("nach disconnect während eines geplanten Wiederverbindens klappt das nächste Wiederverbinden", () => {
    vi.useFakeTimers();
    try {
      wsClient.subscribe("hosts", () => {});
      fireClose(FakeSocket.instances[0]);
      wsClient.disconnect();
      wsClient.subscribe("hosts", () => {});
      expect(FakeSocket.instances).toHaveLength(2);

      fireClose(FakeSocket.instances[1]);
      vi.advanceTimersByTime(30_000);

      expect(FakeSocket.instances).toHaveLength(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("WS-Client -- zu viele Verbindungen", () => {
  function closeWith(socket: FakeSocket, code: number) {
    for (const listener of socket.listeners.get("close") ?? []) (listener as (ev: { code: number }) => void)({ code });
  }

  it("Close-Code 4429: kein Wiederverbinden, Hinweis bleibt stehen", () => {
    vi.useFakeTimers();
    try {
      wsClient.subscribe("hosts", () => {});
      closeWith(FakeSocket.instances[0], 4429);
      expect(vi.getTimerCount()).toBe(0);
      vi.advanceTimersByTime(120_000);
      wsClient.subscribe("jobs", () => {});
      wsClient.ensureConnected();

      expect(FakeSocket.instances).toHaveLength(1);
      expect(wsClient.getLimitMessage()).toBe(WS_TOO_MANY_MESSAGE);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ein anderer Close-Code verbindet weiter neu", () => {
    vi.useFakeTimers();
    try {
      wsClient.subscribe("hosts", () => {});
      closeWith(FakeSocket.instances[0], 1006);
      vi.advanceTimersByTime(30_000);

      expect(FakeSocket.instances).toHaveLength(2);
      expect(wsClient.getLimitMessage()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("zeigt den Text des Servers, wenn er vor dem Schließen ankam", () => {
    wsClient.subscribe("hosts", () => {});
    const socket = FakeSocket.instances[0];
    socket.receive({ type: "error", payload: { title: "Bitte Tabs schließen." } });
    closeWith(socket, 4429);

    expect(wsClient.getLimitMessage()).toBe("Bitte Tabs schließen.");
  });

  it("retry versucht es erneut und nimmt den Hinweis weg", () => {
    wsClient.subscribe("hosts", () => {});
    closeWith(FakeSocket.instances[0], 4429);
    const listener = vi.fn();
    const off = wsClient.onLimitChange(listener);
    try {
      wsClient.retry();
    } finally {
      off();
    }

    expect(FakeSocket.instances).toHaveLength(2);
    expect(wsClient.getLimitMessage()).toBeNull();
    expect(listener).toHaveBeenCalled();
  });
});
