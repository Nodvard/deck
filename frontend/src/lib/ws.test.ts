import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerUnavailableError, useAuthStore } from "../state/auth";
import { wsClient } from "./ws";

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
