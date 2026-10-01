/**
 * Eigenstaendige Terminal-Seite: Host waehlen, echte SSH-Sitzung, mehrere Tabs
 * nebeneinander -- statt fuer jede Shell einen eigenen SSH-Client aufzumachen.
 * Kern-Seite ohne PageSpec wie Dateien/Konsole: sie kennt kein SSH, nur die
 * Terminal-API (`POST /terminal/sessions` + WS-Bruecke,
 * `api/v1/terminal.py`); welcher Anbieter dahinter steht (heute die terminal-
 * Extension ueber `core/ssh.py`), sieht sie nicht.
 *
 * Tabs bleiben beim Umschalten GEMOUNTET (nur ausgeblendet) -- sonst risse jeder
 * Tabwechsel die SSH-Sitzung ab. xterm.js wird erst hier dynamisch geladen, das
 * Haupt-Bundle zahlt nicht dafuer.
 */
import "@xterm/xterm/css/xterm.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

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
  status: string;
  /** Standard-Zugang, `null` ohne Zugang. */
  credential?: { kind: string } | null;
}

interface TerminalSessionOut {
  session_id: string;
  ws_url: string;
}

type TabStatus = "connecting" | "connected" | "closed" | "error";

interface Tab {
  key: string;
  hostId: string;
  title: string;
  status: TabStatus;
}

const STATUS_DOT: Record<TabStatus, string> = {
  connecting: "bg-amber-400",
  connected: "bg-emerald-400",
  closed: "bg-white/40",
  error: "bg-red-400",
};

/** Close-Codes aus api/v1/terminal.py -- in Worte gefasst statt nur als Zahl. */
const CLOSE_REASON: Record<number, string> = {
  4404: "Sitzungsticket unbekannt oder abgelaufen.",
  4501: "Für diesen Host bietet keine Extension ein Terminal an.",
  4500: "Sitzung konnte nicht geöffnet werden.",
};

/** Einmal laden, fuer alle Tabs teilen -- jeder neue Tab wartete sonst auf einen
 * eigenen dynamischen Import desselben Pakets. Ein Fehlschlag (alter Chunk nach einem
 * Deploy) bleibt nicht gespeichert, "Neu verbinden" versucht es wieder. */
const loadXterm = loadOnce(() => Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]));

export function terminalSocketUrl(wsPath: string, loc: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}${wsPath}`;
}

function TerminalView({
  hostId,
  visible,
  onStatus,
}: {
  hostId: string;
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
            setMessage(control.message ?? "Sitzung konnte nicht geöffnet werden.");
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
  }, [hostId, attempt]);

  // Beim Einblenden und bei jeder Groessenaenderung neu einpassen -- xterm misst
  // in einem ausgeblendeten Container 0x0.
  useEffect(() => {
    if (!visible) return;
    const refit = () => {
      try {
        fitRef.current?.fit();
      } catch {
        /* noch nicht geoeffnet */
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

/** Warum die Liste leer ist, und der Knopf zum naechsten Schritt (nur mit dem passenden Recht). */
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
  // Zugaenge gibt es, aber keiner taugt fuers Terminal: meist ist das Modul „Terminal“ aus.
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
  const [query, setQuery] = useState("");
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

  const openTab = useCallback((host: HostOut) => {
    counter.current += 1;
    const key = `${host.id}:${counter.current}`;
    setTabs((prev) => {
      const same = prev.filter((t) => t.hostId === host.id).length;
      const base = host.display_name || host.name;
      return [...prev, { key, hostId: host.id, title: same > 0 ? `${base} (${same + 1})` : base, status: "connecting" }];
    });
    setActive(key);
  }, []);

  // `?host=<id>` (z. B. von einer Host- oder Proxmox-Seite) oeffnet direkt einen Tab.
  useEffect(() => {
    const wanted = searchParams.get("host");
    if (!wanted || !hosts) return;
    const host = hosts.find((h) => h.id === wanted);
    if (host && terminalIds.has(host.id)) openTab(host);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("host");
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

  const shownHosts = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (hosts ?? [])
      .filter((h) => terminalIds.has(h.id) || consoleIds.has(h.id))
      .filter((h) => !needle || `${h.display_name} ${h.name} ${h.address}`.toLowerCase().includes(needle))
      .sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name));
  }, [hosts, terminalIds, consoleIds, query]);

  const hasUsable = (hosts ?? []).some((h) => terminalIds.has(h.id) || consoleIds.has(h.id));

  if (error) return <p className="p-6 text-sm text-red-400">Fehler: {error}</p>;

  return (
    // Handy: Hostliste ueber dem Terminal statt 240 px daneben; die Liste
    // scrollt dort in sich, das Terminal behaelt eine brauchbare Mindesthoehe.
    <div className="flex h-full flex-col gap-4 p-4 lg:flex-row">
      <aside className="flex w-full shrink-0 flex-col gap-2 lg:w-60">
        <h2 className="text-lg font-semibold">Terminal</h2>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Host suchen …"
          aria-label="Host suchen"
          className="rounded bg-white/10 px-2 py-1 text-sm"
        />
        {!hosts && <p className="text-sm opacity-60">Lade Hosts …</p>}
        {hosts && shownHosts.length === 0 && hasUsable && <p className="text-sm opacity-60">Kein Server passt zur Suche.</p>}
        {hosts && !hasUsable && (
          <NoTerminalHosts
            hasHosts={hosts.length > 0}
            hasAccess={hosts.some((h) => h.credential)}
            canWriteHosts={canWriteHosts}
            canManageExtensions={canManageExtensions}
          />
        )}
        <ul className="flex max-h-48 min-h-0 flex-col gap-1 overflow-auto lg:max-h-none">
          {shownHosts.map((h) => (
            <li key={h.id} className="rounded border border-white/10 px-2 py-1.5">
              <p className="break-words text-sm font-medium">{h.display_name || h.name}</p>
              <p className="break-words text-xs opacity-60">{h.address}</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {terminalIds.has(h.id) && (
                  <button
                    type="button"
                    onClick={() => openTab(h)}
                    className="accent-soft rounded px-2 py-0.5 text-xs hover:brightness-125"
                  >
                    Terminal öffnen
                  </button>
                )}
                {consoleIds.has(h.id) && (
                  <Link to={`/console/${h.id}`} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
                    Konsole
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
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
          <p className="text-sm opacity-60">Links einen Host wählen -- jede Sitzung öffnet sich als eigener Tab.</p>
        )}
        {tabs.map((t) => (
          <TerminalView
            key={t.key}
            hostId={t.hostId}
            visible={active === t.key}
            onStatus={(s) => setTabStatus(t.key, s)}
          />
        ))}
      </section>
    </div>
  );
}
