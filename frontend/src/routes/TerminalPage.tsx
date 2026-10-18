/**
 * Eigenständige Terminal-Seite (Host wählen, echte SSH-Sitzung, mehrere Tabs). Sie kennt nur die Terminal-API
 * (`POST /terminal/sessions` + WebSocket-Brücke, `api/v1/terminal.py`), nicht den Anbieter dahinter. Tabs bleiben beim
 * Umschalten gemountet (sonst risse der Wechsel die SSH-Sitzung ab); xterm.js wird erst hier dynamisch geladen.
 */
import "@xterm/xterm/css/xterm.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { HostPicker, statusTone, tileFromHost, type HostTile } from "../../../extensions/_shared/frontend/src/HostPicker";
import { ButtonLink, EmptyState } from "../components/EmptyState";
import { api } from "../lib/api";
import { loadOnce } from "../lib/chunkLoad";
import { useAuthStore } from "../state/auth";

interface HostOut {
  id: string;
  name: string;
  display_name: string;
  address: string;
  kind: string | null;
  /** Art und An/Aus (siehe `hostKind.tsx`); fehlen bei älteren Servern. */
  category?: string;
  parent_label?: string | null;
  power_state?: string | null;
  /** Seit wann der Anbieter den Gast nicht mehr kennt. */
  gone_since?: string | null;
  status: string;
  /** Standard-Zugang, `null` ohne Zugang. */
  credential?: { kind: string } | null;
  os_family?: string;
}

interface TerminalSessionOut {
  session_id: string;
  ws_url: string;
}

type TabStatus = "connecting" | "connected" | "closed" | "error";

/** Shell in etwas auf dem Server statt auf ihm selbst (z. B. in einem Container): `scope` = worin, `ref` = welches. */
interface ShellScope {
  scope: string;
  ref: string;
}

interface Tab {
  key: string;
  hostId: string;
  title: string;
  status: TabStatus;
  inside?: ShellScope;
}

const SCOPE_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;
const REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,254}$/;

/** `?scope=…&ref=…` aus der Adresse, nur in gültiger Form (wie im Backend). */
export function shellScopeFrom(params: URLSearchParams): ShellScope | null {
  const scope = params.get("scope");
  const ref = params.get("ref");
  if (!scope || !ref || !SCOPE_PATTERN.test(scope) || !REF_PATTERN.test(ref)) return null;
  return { scope, ref };
}

const STATUS_DOT: Record<TabStatus, string> = {
  connecting: "bg-amber-400",
  connected: "bg-emerald-400",
  closed: "bg-white/40",
  error: "bg-red-400",
};

/** Close-Codes aus api/v1/terminal.py: in Worte gefasst statt nur als Zahl. */
const CLOSE_REASON: Record<number, string> = {
  4404: "Sitzungsticket unbekannt oder abgelaufen.",
  4501: "Dafür bietet kein Modul ein Terminal an.",
  4500: "Sitzung konnte nicht geöffnet werden.",
  4401: "Die Sitzung wurde beendet: Konto, Berechtigung oder Anmeldung gelten nicht mehr.",
  4408: "Die Sitzung wurde wegen Leerlauf beendet.",
};

/** Einmal laden und für alle Tabs teilen, sonst wartete jeder neue Tab auf einen eigenen Import desselben Pakets.
 * Ein Fehlschlag (alter Chunk nach einem Deploy) bleibt nicht gespeichert, "Neu verbinden" versucht es wieder. */
const loadXterm = loadOnce(() => Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]));

export function terminalSocketUrl(wsPath: string, loc: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}${wsPath}`;
}

function TerminalView({
  hostId,
  inside,
  visible,
  onStatus,
}: {
  hostId: string;
  inside?: ShellScope;
  visible: boolean;
  onStatus: (status: TabStatus) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const fitRef = useRef<{ fit: () => void } | null>(null);
  const termRef = useRef<{ focus: () => void } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let term: import("@xterm/xterm").Terminal | null = null;
    const report = (s: TabStatus) => {
      if (!disposed) onStatusRef.current(s);
    };
    setMessage(null);
    setEnded(false);
    report("connecting");

    (async () => {
      const [{ Terminal }, { FitAddon }] = await loadXterm();
      if (disposed || !containerRef.current) return;
      term = new Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily: 'ui-monospace, "Cascadia Mono", Consolas, Menlo, monospace',
        scrollback: 5000,
        theme: { background: "#0b0f14" },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(containerRef.current);
      fitRef.current = fit;
      termRef.current = term;
      try {
        fit.fit();
      } catch {
        /* unsichtbarer Container: wird beim Einblenden nachgeholt */
      }

      const session = await api.post<TerminalSessionOut>("/terminal/sessions", {
        host_id: hostId,
        cols: term.cols,
        rows: term.rows,
        ...(inside ? { scope: inside.scope, ref: inside.ref } : {}),
      });
      if (disposed) return;

      const socket = new WebSocket(terminalSocketUrl(session.ws_url));
      ws = socket;
      socket.binaryType = "arraybuffer";
      const encoder = new TextEncoder();
      let finished = false;

      socket.onopen = () => {
        report("connected");
        term?.focus();
      };
      socket.onmessage = (ev: MessageEvent) => {
        if (typeof ev.data === "string") {
          let control: { type?: string; code?: number | null; message?: string } = {};
          try {
            control = JSON.parse(ev.data);
          } catch {
            return;
          }
          if (control.type === "exit") {
            finished = true;
            term?.write(`\r\n\x1b[2m[Sitzung beendet${control.code != null ? `, Code ${control.code}` : ""}]\x1b[0m\r\n`);
            setEnded(true);
            report("closed");
          } else if (control.type === "error") {
            finished = true;
            // `||` statt `??`: ein leerer Grund ("") soll den Standardtext zeigen, nicht gar nichts.
            setMessage(control.message || "Sitzung konnte nicht geöffnet werden.");
            setEnded(true);
            report("error");
          }
          return;
        }
        term?.write(new Uint8Array(ev.data as ArrayBuffer));
      };
      socket.onclose = (ev: CloseEvent) => {
        if (disposed || finished) return;
        setEnded(true);
        const reason = CLOSE_REASON[ev.code];
        if (reason) {
          setMessage(reason);
          report("error");
        } else {
          term?.write("\r\n\x1b[2m[Verbindung getrennt]\x1b[0m\r\n");
          report("closed");
        }
      };
      term.onData((data) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
      });
      term.onResize(({ cols, rows }) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "resize", cols, rows }));
      });
    })().catch((err: unknown) => {
      if (disposed) return;
      setMessage(err instanceof Error ? err.message : String(err));
      setEnded(true);
      report("error");
    });

    return () => {
      disposed = true;
      ws?.close();
      term?.dispose();
      fitRef.current = null;
      termRef.current = null;
    };
  }, [hostId, inside?.scope, inside?.ref, attempt]);

  // Beim Einblenden und bei jeder Größenänderung neu einpassen: xterm misst in einem ausgeblendeten Container 0x0.
  useEffect(() => {
    if (!visible) return;
    const refit = () => {
      try {
        fitRef.current?.fit();
      } catch {
        /* noch nicht geöffnet */
      }
    };
    refit();
    termRef.current?.focus();
    if (typeof ResizeObserver === "undefined" || !containerRef.current) {
      window.addEventListener("resize", refit);
      return () => window.removeEventListener("resize", refit);
    }
    const observer = new ResizeObserver(refit);
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [visible]);

  return (
    <div className={`${visible ? "flex" : "hidden"} min-h-0 flex-1 flex-col`}>
      {(message || ended) && (
        <div className="mb-2 flex items-center gap-2 text-sm">
          {message && <span className="text-red-400">{message}</span>}
          <button
            type="button"
            onClick={() => setAttempt((n) => n + 1)}
            className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20"
          >
            Neu verbinden
          </button>
        </div>
      )}
      <div ref={containerRef} className="min-h-0 flex-1 rounded border border-white/10 bg-[#0b0f14] p-1" data-testid="terminal-screen" />
    </div>
  );
}

/** Warum die Liste leer ist, und der Knopf zum nächsten Schritt (nur mit dem passenden Recht). */
function NoTerminalHosts({
  hasHosts, hasAccess, canWriteHosts, canManageExtensions,
}: { hasHosts: boolean; hasAccess: boolean; canWriteHosts: boolean; canManageExtensions: boolean }) {
  if (!hasHosts) {
    return (
      <EmptyState
        compact
        icon="terminal"
        testId="terminal-empty"
        title="Noch kein Server angelegt"
        text="Leg unter Einstellungen → Server & Zugänge einen Server an und hinterlege seinen SSH-Zugang. Danach öffnest du hier ein Terminal."
        action={canWriteHosts ? <ButtonLink to="/settings/hosts">Server hinzufügen</ButtonLink> : undefined}
      />
    );
  }
  if (!hasAccess) {
    return (
      <EmptyState
        compact
        icon="terminal"
        testId="terminal-empty"
        title="Noch kein Server mit Terminal"
        text="Ein Server braucht einen SSH-Zugang, dann erscheint er hier. Das richtest du unter Einstellungen → Server & Zugänge ein."
        action={canWriteHosts ? <ButtonLink to="/settings/hosts">SSH-Zugang einrichten</ButtonLink> : undefined}
      />
    );
  }
  // Zugänge gibt es, aber keiner taugt fürs Terminal: meist ist das Modul „Terminal“ aus.
  return (
    <EmptyState
      compact
      icon="terminal"
      testId="terminal-empty"
      title="Das Terminal ist noch nicht bereit"
      text="Dafür muss das Modul „Terminal“ eingeschaltet sein. Danach erscheinen hier die Server mit SSH-Zugang."
      action={canManageExtensions ? <ButtonLink to="/settings/extensions">Module ansehen</ButtonLink> : undefined}
    />
  );
}

export function TerminalPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [hosts, setHosts] = useState<HostOut[] | null>(null);
  const [terminalIds, setTerminalIds] = useState<Set<string>>(new Set());
  const [consoleIds, setConsoleIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const counter = useRef(0);
  const canWriteHosts = useAuthStore((s) => s.hasPermission("hosts.write"));
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));

  useEffect(() => {
    Promise.all([
      api.get<HostOut[]>("/hosts"),
      api.get<string[]>("/terminal/hosts"),
      api.get<string[]>("/console/hosts").catch(() => [] as string[]),
    ])
      .then(([allHosts, terminal, consoles]) => {
        setHosts(allHosts);
        setTerminalIds(new Set(terminal));
        setConsoleIds(new Set(consoles));
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const openTab = useCallback((host: HostOut, inside?: ShellScope) => {
    counter.current += 1;
    const key = `${host.id}:${counter.current}`;
    setTabs((prev) => {
      const base = inside ? `${inside.ref} @ ${host.display_name || host.name}` : host.display_name || host.name;
      const same = prev.filter((t) => t.hostId === host.id && t.inside?.ref === inside?.ref && t.inside?.scope === inside?.scope).length;
      return [...prev, { key, hostId: host.id, title: same > 0 ? `${base} (${same + 1})` : base, status: "connecting", inside }];
    });
    setActive(key);
  }, []);

  // `?host=<id>` (z. B. von einer Host- oder Proxmox-Seite) öffnet direkt einen Tab; mit `&scope=…&ref=…` (z. B.
  // „Konsole“ eines Containers) eine Shell darin; ob es die gibt, entscheidet das Backend.
  useEffect(() => {
    const wanted = searchParams.get("host");
    if (!wanted || !hosts) return;
    const host = hosts.find((h) => h.id === wanted);
    const inside = shellScopeFrom(searchParams);
    if (!inside && (searchParams.has("scope") || searchParams.has("ref"))) {
      // Ein halber oder ungültiger Link auf eine Shell in etwas: nichts öffnen, nie stattdessen die Shell des Servers.
      setLinkError("Der Link auf diese Konsole ist unvollständig oder ungültig. Es wurde nichts geöffnet.");
    } else if (host && (inside || terminalIds.has(host.id))) {
      openTab(host, inside ?? undefined);
    }
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("host");
      next.delete("scope");
      next.delete("ref");
      return next;
    }, { replace: true });
  }, [hosts, terminalIds, searchParams, setSearchParams, openTab]);

  const closeTab = (key: string) => {
    const index = tabs.findIndex((t) => t.key === key);
    const remaining = tabs.filter((t) => t.key !== key);
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (active === key) setActive(remaining[Math.min(index, remaining.length - 1)]?.key ?? null);
  };

  const setTabStatus = useCallback((key: string, status: TabStatus) => {
    setTabs((prev) => prev.map((t) => (t.key === key && t.status !== status ? { ...t, status } : t)));
  }, []);

  // Kacheln der gemeinsamen Server-Auswahl, als Starter: je Server "Terminal öffnen" und, wo es sie gibt, "Konsole".
  const tiles = useMemo<HostTile[] | null>(() => {
    if (!hosts) return null;
    return hosts
      .filter((h) => terminalIds.has(h.id) || consoleIds.has(h.id))
      .sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name))
      .map(tileFromHost);
  }, [hosts, terminalIds, consoleIds]);
  const hostById = useMemo(() => new Map((hosts ?? []).map((h) => [h.id, h])), [hosts]);

  const hasUsable = (hosts ?? []).some((h) => terminalIds.has(h.id) || consoleIds.has(h.id));

  if (error) return <p className="p-6 text-sm text-red-400">Fehler: {error}</p>;

  return (
    // Handy: Hostliste über dem Terminal statt 240 px daneben; die Liste scrollt dort in sich, das Terminal behält eine
    // brauchbare Mindesthöhe.
    <div className="flex h-full flex-col gap-4 p-4 lg:flex-row">
      <aside className="flex w-full shrink-0 flex-col gap-2 lg:w-60">
        <h2 className="text-lg font-semibold">Terminal</h2>
        {linkError && <p role="alert" className="text-sm text-red-400">{linkError}</p>}
        {hosts && !hasUsable && (
          <NoTerminalHosts
            hasHosts={hosts.length > 0}
            hasAccess={hosts.some((h) => h.credential)}
            canWriteHosts={canWriteHosts}
            canManageExtensions={canManageExtensions}
          />
        )}
        {hasUsable && (
          <p className="text-xs text-white/50" data-testid="terminal-explain">
            <b className="font-medium text-white/70">Terminal</b>: SSH mit dem Zugang von Nodvard Deck.{" "}
            <b className="font-medium text-white/70">Konsole</b>: Bildschirm des Gasts über Proxmox, geht auch ohne SSH.
          </p>
        )}
        <div className="max-h-48 min-h-0 overflow-auto lg:max-h-none">
          <HostPicker
            hosts={tiles}
            value={null}
            onChange={() => undefined}
            storageKey="terminal"
            searchFrom={2}
            gridClass="grid-cols-1"
            actions={(h) => {
              // Nicht erreichbar, aus oder in Proxmox gelöscht: Knöpfe gedimmt, aber bedienbar (der Status kann veraltet sein).
              const tone = statusTone(h.status, h.power_state, h.gone_since).tone;
              const offline = tone === "bad" || tone === "off";
              const why = h.gone_since ? "Nicht mehr vorhanden." : tone === "off" ? "Ausgeschaltet." : "Gerade nicht erreichbar.";
              const dim = offline ? " opacity-50" : "";
              return (
                <>
                  {terminalIds.has(h.id) && (
                    <button
                      type="button"
                      onClick={() => { const host = hostById.get(h.id); if (host) openTab(host); }}
                      className={`accent-soft rounded px-2 py-0.5 text-xs hover:brightness-125${dim}`}
                      title={`${offline ? `${why} ` : ""}SSH mit dem Zugang von Nodvard Deck`}
                    >
                      Terminal öffnen
                    </button>
                  )}
                  {consoleIds.has(h.id) && (
                    <Link
                      to={`/console/${h.id}`}
                      className={`rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20${dim}`}
                      title={offline ? `${why} Bildschirm des Gasts über Proxmox.` : "Bildschirm des Gasts über Proxmox, geht auch ohne SSH"}
                    >
                      Konsole
                    </Link>
                  )}
                </>
              );
            }}
          />
        </div>
      </aside>

      <section className="flex min-h-[20rem] min-w-0 flex-1 flex-col lg:min-h-0">
        <div role="tablist" className="mb-2 flex flex-wrap gap-1 border-b border-white/10 pb-1">
          {tabs.map((t) => (
            <div
              key={t.key}
              className={`flex items-center gap-1.5 rounded-t px-2 py-1 text-sm ${active === t.key ? "bg-white/10 font-medium" : "opacity-70 hover:opacity-100"}`}
            >
              <span className={`h-2 w-2 rounded-full ${STATUS_DOT[t.status]}`} aria-hidden />
              <button type="button" role="tab" aria-selected={active === t.key} onClick={() => setActive(t.key)}>
                {t.title}
              </button>
              <button type="button" onClick={() => closeTab(t.key)} aria-label={`${t.title} schließen`} className="opacity-60 hover:opacity-100">
                ×
              </button>
            </div>
          ))}
        </div>
        {tabs.length === 0 && (
          <p className="text-sm opacity-60">Links einen Server wählen – jede Sitzung öffnet sich als eigener Tab.</p>
        )}
        {tabs.map((t) => (
          <TerminalView
            key={t.key}
            hostId={t.hostId}
            inside={t.inside}
            visible={active === t.key}
            onStatus={(s) => setTabStatus(t.key, s)}
          />
        ))}
      </section>
    </div>
  );
}
