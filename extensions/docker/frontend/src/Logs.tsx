/**
 * Logs eines Containers als laufender Text-Strom (`docker logs --follow`), gelesen per `fetch()`-Stream, weil
 * EventSource keinen Auth-Header kann. Schließen bricht den Strom ab und beendet damit den Prozess auf dem Server.
 * Suche und Herunterladen bleiben im Browser: Freitext geht nie an den Server.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { authedFetch, errorText } from "../../../_shared/frontend/src/api";

export interface LogTarget {
  name: string;
  host: string;
  host_id?: string;
  container?: string;
}

export const MAX_LINES = 5000;

interface Range { value: string; label: string; tail: number; since?: string }

export const LOG_RANGES: Range[] = [
  { value: "100", label: "Letzte 100 Zeilen", tail: 100 },
  { value: "200", label: "Letzte 200 Zeilen", tail: 200 },
  { value: "500", label: "Letzte 500 Zeilen", tail: 500 },
  { value: "2000", label: "Letzte 2000 Zeilen", tail: 2000 },
  { value: "10m", label: "Letzte 10 Minuten", tail: MAX_LINES, since: "10m" },
  { value: "1h", label: "Letzte Stunde", tail: MAX_LINES, since: "1h" },
  { value: "4h", label: "Letzte 4 Stunden", tail: MAX_LINES, since: "4h" },
  { value: "24h", label: "Letzte 24 Stunden", tail: MAX_LINES, since: "24h" },
  { value: "168h", label: "Letzte 7 Tage", tail: MAX_LINES, since: "168h" },
];

const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
const SMALL_BUTTON = "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";
const FIELD = "px-1 py-0.5 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]";

/** Groß- und Kleinschreibung egal; die Treffer kommen aus dem Original, auch bei Zeichen, die klein anders lang sind. */
export function searchPattern(text: string): RegExp | null {
  return text.trim() ? new RegExp(`(${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "i") : null;
}

function Marked({ line, split }: { line: string; split: RegExp }): JSX.Element {
  const parts = line.split(split);
  return <>{parts.map((part, i) => (i % 2 === 1 ? <mark key={i} className="rounded-sm bg-amber-400/40 text-inherit">{part}</mark> : part))}</>;
}

export function logFileName(container: string, now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `${container}-logs-${stamp}.txt`;
}

function download(name: string, lines: string[]): void {
  const url = URL.createObjectURL(new Blob([`${lines.join("\n")}\n`], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Manche Browser lesen die Adresse erst nach dem Klick; sofort freigegeben, käme eine leere Datei an.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function LogPanel({ entry, onClose }: { entry: LogTarget; onClose: () => void }): JSX.Element {
  const [lines, setLines] = useState<string[]>([]);
  const [range, setRange] = useState("200");
  const [running, setRunning] = useState(true);
  const [follow, setFollow] = useState(true);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const boxRef = useRef<HTMLPreElement | null>(null);
  const chosen = LOG_RANGES.find((r) => r.value === range) ?? LOG_RANGES[1];

  useEffect(() => {
    if (!running || !entry.host_id || !entry.container) return;
    const controller = new AbortController();
    setLines([]);
    setError(null);
    setEnded(false);

    (async () => {
      try {
        const params = new URLSearchParams({ tail: String(chosen.tail) });
        if (chosen.since) params.set("since", chosen.since);
        const res = await authedFetch(
          `/ext/docker/containers/${entry.host_id}/${encodeURIComponent(entry.container!)}/logs?${params}`,
          { signal: controller.signal },
        );
        if (!res.ok || !res.body) throw new Error(await errorText(res));
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let rest = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          rest += decoder.decode(value, { stream: true });
          const parts = rest.split("\n");
          rest = parts.pop() ?? "";
          if (parts.length > 0) {
            const clean = parts.map((l) => l.replace(ANSI_RE, ""));
            setLines((prev) => {
              const next = prev.concat(clean);
              return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
            });
          }
        }
        if (rest) setLines((prev) => prev.concat(rest.replace(ANSI_RE, "")));
        setEnded(true);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => controller.abort();
  }, [entry.host_id, entry.container, chosen, running]);

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines, follow]);

  const pattern = useMemo(() => searchPattern(search), [search]);
  const split = useMemo(() => (pattern ? new RegExp(pattern.source, "gi") : null), [pattern]);
  const shown = useMemo(() => (pattern ? lines.filter((l) => pattern.test(l)) : lines), [lines, pattern]);

  return (
    <div className="flex flex-col gap-2" data-testid="log-panel">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">Logs: {entry.name}</span>
        <span className={`rounded-full px-2 py-0.5 ${running && !ended && !error ? "bg-emerald-500/20 text-emerald-300" : "bg-white/10 opacity-70"}`}>
          {error ? "Fehler" : !running ? "angehalten" : ended ? "beendet" : "live"}
        </span>
        <select value={range} onChange={(e) => setRange(e.target.value)} aria-label="Zeitraum" className={FIELD}>
          {LOG_RANGES.map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </select>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Suchen …"
          aria-label="In den Logs suchen"
          className={`w-40 ${FIELD} px-2`}
        />
        {pattern && <span data-testid="log-hits" className="opacity-70">{shown.length} von {lines.length} Zeilen</span>}
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          Mitlaufen
        </label>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <button type="button" onClick={() => setRunning((r) => !r)} className={SMALL_BUTTON}>
            {running ? "Anhalten" : "Fortsetzen"}
          </button>
          <button type="button" onClick={() => setLines([])} className={SMALL_BUTTON}>
            Leeren
          </button>
          <button type="button" disabled={shown.length === 0} onClick={() => download(logFileName(entry.container ?? entry.name), shown)}
            title="Speichert die Zeilen als Textdatei auf deinem Gerät, mit Uhrzeit vor jeder Zeile."
            className={SMALL_BUTTON}>
            {pattern ? "Treffer herunterladen" : "Herunterladen"}
          </button>
          <button type="button" onClick={onClose} className={SMALL_BUTTON}>
            Schließen
          </button>
        </div>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {lines.length >= MAX_LINES && (
        <p className="text-xs opacity-70" data-testid="log-cut">Gezeigt werden nur die letzten {MAX_LINES} Zeilen. Ältere stehen weiter im Log des Containers, hier aber nicht.</p>
      )}
      <pre
        ref={boxRef}
        className="h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px] leading-snug"
      >
        {shown.length === 0
          ? pattern ? "Keine passenden Zeilen unter den geladenen." : chosen.since && ended ? "In diesem Zeitraum gibt es keine Ausgabe." : "Noch keine Ausgabe …"
          : split
            ? shown.map((line, i) => <Fragment key={i}>{i > 0 && "\n"}<Marked line={line} split={split} /></Fragment>)
            : shown.join("\n")}
      </pre>
    </div>
  );
}
