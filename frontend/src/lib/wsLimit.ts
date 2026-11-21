/** Hinweis, solange der Server weitere Live-Verbindungen dieses Kontos abweist; gesetzt von lib/ws.ts. */
import { useSyncExternalStore } from "react";

let current: string | null = null;
const listeners = new Set<() => void>();

export function getWsLimitMessage(): string | null {
  return current;
}

export function setWsLimitMessage(message: string | null): void {
  if (current === message) return;
  current = message;
  for (const listener of listeners) listener();
}

export function onWsLimitChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useWsLimitMessage(): string | null {
  return useSyncExternalStore(onWsLimitChange, getWsLimitMessage);
}
