/**
 * Client fuer den WS-Multiplex-Hub (docs/04-API.md §4, core/ws_hub.py,
 * api/v1/ws.py). EINE Verbindung fuer die ganze App (Multiplex, daher der Name) --
 * Widgets/Seiten abonnieren nur Kanaele, statt je eine eigene Verbindung aufzumachen.
 *
 * Protokoll (Client<->Server, exakt wie backend/src/nodvard_deck/api/v1/ws.py):
 *   -> {type:"auth", token}              <- {type:"auth_ok"} | {type:"auth_error"}
 *   -> {type:"subscribe", channel}       <- {type:"subscribed", channel}
 *   -> {type:"unsubscribe", channel}     <- {type:"subscribed", channel, payload:{subscribed:false}}
 *   <- {type:"ping"}                     -> {type:"pong"}
 *      {type:"event", channel, payload}  (nur an abonnierte + RBAC-berechtigte Verbindungen)
 */
import { useEffect, useRef } from "react";

import { useAuthStore } from "../state/auth";

type EventHandler = (payload: unknown) => void;

type ServerMessage =
  | { type: "auth_ok" }
  | { type: "auth_error" }
  | { type: "subscribed"; channel: string; payload?: { subscribed: boolean } }
  | { type: "ping" }
  | { type: "pong" }
  | { type: "event"; channel: string; payload: unknown }
  | { type: "error"; payload: { title: string } };

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 5000, 10000];

class WsMultiplexClient {
  private socket: WebSocket | null = null;
  private authenticated = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private handlers = new Map<string, Set<EventHandler>>();
  private desiredChannels = new Set<string>();
  private connectRequested = false;

  ensureConnected(): void {
    this.connectRequested = true;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.open();
  }

  disconnect(): void {
    this.connectRequested = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.authenticated = false;
    this.socket?.close();
    this.socket = null;
  }

  subscribe(channel: string, handler: EventHandler): () => void {
    let set = this.handlers.get(channel);
    if (!set) {
      set = new Set();
      this.handlers.set(channel, set);
    }
    set.add(handler);
    this.desiredChannels.add(channel);
    this.ensureConnected();
    if (this.authenticated) this.send({ type: "subscribe", channel });

    return () => {
      set?.delete(handler);
      if (set && set.size === 0) {
        this.handlers.delete(channel);
        this.desiredChannels.delete(channel);
        if (this.authenticated) this.send({ type: "unsubscribe", channel });
      }
    };
  }

  private open(): void {
    const token = useAuthStore.getState().accessToken;
    if (!token) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws`);
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.send({ type: "auth", token: useAuthStore.getState().accessToken });
    });

    socket.addEventListener("message", (ev: MessageEvent<string>) => {
      let message: ServerMessage;
      try {
        message = JSON.parse(ev.data) as ServerMessage;
      } catch {
        return;
      }
      this.handleMessage(message);
    });

    socket.addEventListener("close", () => {
      this.authenticated = false;
      if (this.connectRequested) this.scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      socket.close();
    });
  }

  private handleMessage(message: ServerMessage): void {
    switch (message.type) {
      case "auth_ok":
        this.authenticated = true;
        this.reconnectAttempt = 0;
        for (const channel of this.desiredChannels) {
          this.send({ type: "subscribe", channel });
        }
        break;
      case "auth_error":
        this.authenticated = false;
        // Token vermutlich abgelaufen -- ein stiller Refresh (state/auth.ts) plus
        // Reconnect uebernimmt das Nachziehen; kein manueller Eingriff noetig. Ist der
        // Server gerade nicht erreichbar, wirft refresh() -- der Takt in
        // lib/tokenRefresh.ts versucht es dann weiter.
        useAuthStore.getState().refresh().catch(() => {});
        break;
      case "ping":
        this.send({ type: "pong" });
        break;
      case "event": {
        const set = this.handlers.get(message.channel);
        if (set) for (const handler of set) handler(message.payload);
        break;
      }
      case "subscribed":
      case "pong":
      case "error":
        break;
    }
  }

  private send(message: unknown): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    const delay = RECONNECT_DELAYS_MS[Math.min(this.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)];
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.connectRequested) this.open();
    }, delay);
  }
}

export const wsClient = new WsMultiplexClient();

/** Abonniert `channel` fuer die Lebensdauer der Komponente; ruft `onEvent` bei jedem WsHub.publish() auf. */
export function useWsSubscription(channel: string | null | undefined, onEvent: EventHandler): void {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (!channel) return;
    const unsubscribe = wsClient.subscribe(channel, (payload) => handlerRef.current(payload));
    return unsubscribe;
  }, [channel]);
}
