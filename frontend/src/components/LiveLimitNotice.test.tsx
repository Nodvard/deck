import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WS_TOO_MANY_MESSAGE, wsClient } from "../lib/ws";
import { useAuthStore } from "../state/auth";
import { LiveLimitNotice } from "./LiveLimitNotice";

type Listener = (ev: { code?: number; data?: string }) => void;

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.CONNECTING;
  listeners = new Map<string, Listener[]>();
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  send() {}
  close() {
    this.readyState = 3;
  }
  fire(type: string, ev: { code?: number }) {
    for (const listener of this.listeners.get(type) ?? []) listener(ev);
  }
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  useAuthStore.setState({ accessToken: "tok" });
});

afterEach(() => {
  wsClient.disconnect();
  vi.unstubAllGlobals();
});

describe("LiveLimitNotice", () => {
  it("ohne Abweisung nichts", () => {
    wsClient.ensureConnected();
    render(<LiveLimitNotice />);
    act(() => FakeSocket.instances[0].fire("close", { code: 1006 }));

    expect(screen.queryByTestId("live-limit-notice")).toBeNull();
  });

  it("nach 4429 Hinweis mit Grund, der Knopf verbindet erneut", () => {
    wsClient.ensureConnected();
    render(<LiveLimitNotice />);
    act(() => FakeSocket.instances[0].fire("close", { code: 4429 }));

    expect(screen.getByTestId("live-limit-notice").textContent).toContain(WS_TOO_MANY_MESSAGE);
    fireEvent.click(screen.getByRole("button", { name: /Erneut verbinden/ }));

    expect(FakeSocket.instances).toHaveLength(2);
    expect(screen.queryByTestId("live-limit-notice")).toBeNull();
  });
});
