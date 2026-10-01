/**
 * Fetch-Attrappe fuer die Tests der Seite „Server & Zugaenge“: eine Tabelle
 * `"METHOD /pfad" -> Antwort`. Eine Funktion wird bei jedem Aufruf neu gefragt; `reply(status, body)`
 * setzt einen anderen HTTP-Status; `undefined` heisst 204. Ein nicht eingetragener Aufruf wirft --
 * so faellt jede unerwartete Anfrage sofort auf.
 */
import { vi } from "vitest";

export interface Call {
  method: string;
  path: string;
  query: string;
  body: unknown;
}

export class Reply {
  constructor(public status: number, public body: unknown) {}
}

export const reply = (status: number, body: unknown) => new Reply(status, body);

type Handler = unknown | ((call: Call) => unknown);

export function mockHostsApi(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = (typeof input === "string" ? input : input.toString()).replace(/^\/api\/v1/, "");
      const [path, query = ""] = url.split("?");
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
      const call: Call = { method, path, query, body };
      calls.push(call);
      const key = `${method} ${url}` in routes ? `${method} ${url}` : `${method} ${path}` in routes ? `${method} ${path}` : null;
      if (key === null) throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
      const handler = routes[key];
      const result = typeof handler === "function" ? (handler as (c: Call) => unknown)(call) : handler;
      if (result instanceof Reply) return new Response(JSON.stringify(result.body), { status: result.status });
      return result === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(result), { status: 200 });
    }),
  );
  return calls;
}

// ---------------------------------------------------------------------------
// Beispieldaten
// ---------------------------------------------------------------------------

export function hostFixture(over: Record<string, unknown> = {}) {
  return {
    id: "h1", name: "bastel-pi", display_name: "Bastel-Pi", address: "192.168.2.72", os_family: "linux", kind: null,
    tags: ["docker"], managed_tags: [], credential: null, is_managed: true, enabled: true, status: "up",
    last_seen_at: null, provider_ext_id: null, provider_ref: null, created_at: "2026-09-01T10:00:00Z", updated_at: "2026-09-01T10:00:00Z",
    ...over,
  };
}

export function credentialFixture(over: Record<string, unknown> = {}) {
  return {
    id: "c1", host_id: "h1", kind: "ssh_key", username: "lattice", port: 22, is_default: true,
    created_at: "2026-09-30T10:00:00Z", ...over,
  };
}

export const FINGERPRINT = "SHA256:q3Zc1oB0m7uVxk9sYtEw2nPjR4dLhGfA8yKcVbNzXe0";
export const OLD_FINGERPRINT = "SHA256:Zk4n8WcP1xR7aYdT5uLoH2vJmQe9sBgF3iNbXtCyU6A";

export function checkFixture(over: Record<string, unknown> = {}) {
  return {
    ok: true, checked_at: "2026-09-30T10:00:00Z", host_key: { status: "known", key_type: "ssh-ed25519", fingerprint: FINGERPRINT, expected: FINGERPRINT },
    os: null, credential_id: "c1",
    items: [
      { id: "reachable", label: "Server erreichbar", status: "ok", detail: "SSH-Dienst antwortet.", hint: "" },
      { id: "host_key", label: "Server-Schlüssel", status: "ok", detail: "Bekannt (ssh-ed25519).", hint: "" },
      { id: "login", label: "Anmeldung als lattice", status: "ok", detail: "Mit Schlüssel angemeldet.", hint: "" },
      { id: "root", label: "Root-Rechte", status: "warn", detail: "sudo verlangt ein Passwort.", hint: "Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen." },
      { id: "os", label: "Betriebssystem", status: "ok", detail: "Debian 12", hint: "" },
    ],
    ...over,
  };
}
