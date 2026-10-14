/**
 * Gemeinsame Server-Auswahl (Server-Zustand, Terminal, Dateien, ...) für Erweiterungen und Kern-Seiten (dieselbe
 * Auflösung, `frontend/kit.resolve.ts`): Kacheln statt Klappliste, ab `searchFrom` Servern mit Suche, der zuletzt
 * gewählte Server (je `storageKey`) vorgewählt. Die Seite bleibt Herr über die Auswahl (`value`/`onChange`).
 */
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { SearchInput } from "./ui";

export interface HostTile {
  id: string;
  name: string;
  address?: string | null;
  /** `os_family` des Servers: `linux`, `windows`, `proxmox` ... */
  os?: string | null;
  /** Status aus dem Kern (`up`, `down`, `unknown`, bei VMs `running`/`stopped`). */
  status?: string | null;
  /** Kurzer Zusatz zum Zustand, wenn die Seite ihn kennt, z. B. die Auslastung ("CPU 12 %") oder der wichtigste Fund. */
  extra?: string | null;
  /** Kurzer Zusatz, z. B. "ohne SSH-Zugang". */
  hint?: string | null;
}

const STORAGE_PREFIX = "nodvard.host-picker.";
const DEFAULT_SEARCH_FROM = 6;

/** Zuletzt gewählter Server dieser Seite -- `null`, wenn keiner gemerkt ist oder der Browser nichts speichert. */
export function rememberedHost(storageKey: string): string | null {
  try {
    return window.localStorage.getItem(STORAGE_PREFIX + storageKey);
  } catch {
    return null;
  }
}

export function rememberHost(storageKey: string, id: string | null): void {
  try {
    if (id) window.localStorage.setItem(STORAGE_PREFIX + storageKey, id);
    else window.localStorage.removeItem(STORAGE_PREFIX + storageKey);
  } catch {
    // Privater Modus oder gesperrter Speicher: dann eben ohne Vorauswahl.
  }
}

const OS_LABELS: Record<string, string> = { linux: "Linux", windows: "Windows", proxmox: "Proxmox", macos: "macOS", bsd: "BSD" };

export function osLabel(os?: string | null): string {
  if (!os) return "";
  return OS_LABELS[os.toLowerCase()] ?? os.charAt(0).toUpperCase() + os.slice(1);
}

type StatusTone = "ok" | "bad" | "unknown";

/** Status des Kerns als Ampel: erreichbar, nicht erreichbar, unbekannt. */
export function statusTone(status?: string | null): { tone: StatusTone; label: string } {
  const s = (status ?? "").toLowerCase();
  if (["up", "online", "running", "ok", "reachable"].includes(s)) return { tone: "ok", label: "erreichbar" };
  if (["down", "offline", "stopped", "unreachable", "error"].includes(s)) return { tone: "bad", label: "nicht erreichbar" };
  return { tone: "unknown", label: "Status unbekannt" };
}

const DOT: Record<StatusTone, string> = { ok: "bg-emerald-400", bad: "bg-red-400", unknown: "bg-white/30" };

export function StatusDot({ status }: { status?: string | null }) {
  const { tone, label } = statusTone(status);
  return <span className={`inline-block h-2 w-2 flex-none rounded-full ${DOT[tone]}`} title={label} aria-label={label} role="img" />;
}

function TileBody({ host, active }: { host: HostTile; active: boolean }) {
  return (
    <>
      <span className="flex items-center gap-2">
        <StatusDot status={host.status} />
        <span className="truncate font-medium">{host.name}</span>
        {active && <span className="ml-auto flex-none text-xs text-sky-300" aria-hidden="true">✓</span>}
      </span>
      <span className="flex flex-wrap gap-x-3 text-xs text-white/50">
        {host.address && <span>{host.address}</span>}
        {host.os && <span>{osLabel(host.os)}</span>}
        {host.extra && <span>{host.extra}</span>}
      </span>
      {host.hint && <span className="text-xs text-amber-300/80">{host.hint}</span>}
    </>
  );
}

function matches(host: HostTile, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [host.name, host.address, osLabel(host.os), host.hint].some((v) => (v ?? "").toLowerCase().includes(q));
}

export function HostPicker({ hosts, value, onChange, storageKey, label = "Server wählen", searchFrom = DEFAULT_SEARCH_FROM, actions, gridClass }: {
  /** `null`: wird noch geladen. */
  hosts: HostTile[] | null;
  value: string | null;
  onChange: (id: string) => void;
  /** Unter diesem Schlüssel merkt sich der Browser den zuletzt gewählten Server (je Seite einer). */
  storageKey: string;
  label?: string;
  /** Ab so vielen Servern erscheint das Suchfeld. */
  searchFrom?: number;
  /** Starter statt Auswahl: Knöpfe je Kachel (z. B. "Terminal öffnen"); kein Wählen, kein Merken. */
  actions?: (host: HostTile) => ReactNode;
  /** Spalten des Rasters, z. B. "grid-cols-1" in einer schmalen Seitenleiste. */
  gridClass?: string;
}): JSX.Element {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const preselected = useRef(false);

  // Vorauswahl: einmal nach dem Laden, wenn die Seite noch keinen Server hat und der gemerkte noch existiert.
  useEffect(() => {
    if (preselected.current || hosts === null || actions) return;
    preselected.current = true;
    if (value) return;
    const remembered = rememberedHost(storageKey);
    if (remembered && hosts.some((h) => h.id === remembered)) onChange(remembered);
  }, [hosts, value, storageKey, onChange, actions]);

  const selected = useMemo(() => (hosts ?? []).find((h) => h.id === value) ?? null, [hosts, value]);
  const shown = useMemo(() => (hosts ?? []).filter((h) => matches(h, query)), [hosts, query]);

  function choose(id: string) {
    rememberHost(storageKey, id);
    setOpen(false);
    setQuery("");
    onChange(id);
  }

  if (hosts === null) return <p className="text-sm text-white/50" aria-label={label}>Lade Server …</p>;
  if (hosts.length === 0) return <></>;

  if (selected && !open && !actions) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm" aria-label={label} data-testid="host-picker-selected">
        <StatusDot status={selected.status} />
        <span className="font-medium">{selected.name}</span>
        {selected.address && <span className="text-white/50">{selected.address}</span>}
        {selected.os && <span className="text-white/50">{osLabel(selected.os)}</span>}
        {selected.extra && <span className="text-white/50">{selected.extra}</span>}
        {selected.hint && <span className="text-amber-300/80">{selected.hint}</span>}
        {hosts.length > 1 && (
          <button type="button" onClick={() => setOpen(true)} className="ml-auto rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20">
            Server wechseln
          </button>
        )}
      </div>
    );
  }

  return (
    <div aria-label={label} data-testid="host-picker">
      {hosts.length >= searchFrom && (
        <div className="mb-3 flex max-w-sm">
          <SearchInput value={query} onChange={setQuery} placeholder="Server suchen …" label="Server suchen" />
        </div>
      )}
      {shown.length === 0 && <p className="text-sm text-white/50">Kein Server passt zu „{query.trim()}“.</p>}
      <div className={`grid gap-2 ${gridClass ?? "sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"}`}>
        {shown.map((h) => {
          if (actions) {
            return (
              <div key={h.id} role="group" aria-label={h.name} className="flex flex-col gap-1 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm">
                <TileBody host={h} active={false} />
                <div className="mt-1 flex flex-wrap gap-1.5">{actions(h)}</div>
              </div>
            );
          }
          const active = h.id === value;
          return (
            <button
              key={h.id}
              type="button"
              aria-pressed={active}
              onClick={() => choose(h.id)}
              className={`flex flex-col gap-1 rounded-lg border px-3 py-2 text-left text-sm transition ${
                active ? "border-sky-400/60 bg-sky-400/10" : "border-white/10 bg-white/[0.04] hover:border-white/25 hover:bg-white/[0.07]"
              }`}
            >
              <TileBody host={h} active={active} />
            </button>
          );
        })}
      </div>
      {selected && !actions && (
        <button type="button" onClick={() => { setOpen(false); setQuery(""); }} className="mt-2 text-xs text-white/50 hover:text-white/80">
          Abbrechen, bei „{selected.name}“ bleiben
        </button>
      )}
    </div>
  );
}
