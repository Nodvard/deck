/**
 * Metrik-Verlauf auf der Server-Seite: CPU, RAM, Last, Netz, Datentraeger, Temperatur
 * ueber einen waehlbaren Zeitraum (`GET /hosts/{id}/metrics/history`). Die Quelle
 * entscheidet der Kern: Proxmox-Hosts aus Proxmox' eigenen RRD-Daten, alle anderen aus
 * dem Sammler von Nodvard Deck (alle 30 s). Es erscheinen nur Diagramme, fuer die der Host
 * tatsaechlich Werte liefert.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { type ChartSeries, TimeSeriesChart } from "../components/TimeSeriesChart";
import { api } from "../lib/api";
import { loginUnproven, neverAnswered } from "../lib/hosts";
import { formatBytes, hostHealth, type HostOut } from "../lib/overview";

interface HistoryOut {
  range: string;
  source: "provider" | "lattice";
  step_s: number;
  timestamps: number[];
  series: Record<string, (number | null)[]>;
  max: Record<string, (number | null)[]>;
}

const RANGES = [
  { value: "1h", label: "1 Std.", seconds: 3600 },
  { value: "6h", label: "6 Std.", seconds: 6 * 3600 },
  { value: "24h", label: "24 Std.", seconds: 86400 },
  { value: "7d", label: "7 Tage", seconds: 7 * 86400 },
  { value: "30d", label: "30 Tage", seconds: 30 * 86400 },
];

// Kategoriale Farben fuer dunkle Flaechen (validierte Reihenfolge: Blau, Orange, Tuerkis).
const C1 = "#3987e5";
const C2 = "#d95926";
const C3 = "#199e70";

const pct = (v: number) => `${v.toFixed(v < 10 ? 1 : 0)} %`;
const rate = (v: number) => (v < 1024 ? `${Math.round(v)} B/s` : `${formatBytes(v)}/s`);
const bytes = (v: number) => (v <= 0 ? "0" : formatBytes(v));
const load = (v: number) => v.toFixed(2);
const temp = (v: number) => `${v.toFixed(1)} °C`;

interface Panel {
  title: string;
  keys: { key: string; label: string; color: string }[];
  format: (v: number) => string;
  yMaxKey?: string;
  yMax?: number;
}

const PANELS: Panel[] = [
  { title: "CPU", keys: [{ key: "cpu_percent", label: "Auslastung", color: C1 }, { key: "cpu_iowait_percent", label: "Warten auf I/O", color: C2 }], format: pct, yMax: 100 },
  { title: "Arbeitsspeicher", keys: [{ key: "mem_used_bytes", label: "belegt", color: C1 }], format: bytes, yMaxKey: "mem_total_bytes" },
  { title: "Auslagerung (Swap)", keys: [{ key: "swap_used_bytes", label: "belegt", color: C2 }], format: bytes, yMaxKey: "swap_total_bytes" },
  { title: "Last", keys: [{ key: "load_1", label: "1 min", color: C1 }, { key: "load_5", label: "5 min", color: C2 }, { key: "load_15", label: "15 min", color: C3 }], format: load },
  { title: "Netzwerk", keys: [{ key: "net_in_bps", label: "empfangen", color: C1 }, { key: "net_out_bps", label: "gesendet", color: C2 }], format: rate },
  { title: "Datenträger", keys: [{ key: "disk_read_bps", label: "lesen", color: C1 }, { key: "disk_write_bps", label: "schreiben", color: C2 }], format: rate },
  { title: "Datenträger aktiv", keys: [{ key: "disk_busy_percent", label: "aktive Zeit", color: C1 }], format: pct, yMax: 100 },
  { title: "Temperatur", keys: [{ key: "temp_c", label: "höchster Sensor", color: C2 }], format: temp },
  { title: "Systemplatte", keys: [{ key: "root_used_bytes", label: "belegt", color: C1 }], format: bytes, yMaxKey: "root_total_bytes" },
];

function lastValue(values: (number | null)[] | undefined): number | undefined {
  if (!values) return undefined;
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return values[i] as number;
  return undefined;
}

type HistoryHost = Pick<HostOut, "status" | "last_seen_at"> & Partial<Pick<HostOut, "login_ok_at" | "credential">>;

/** Was statt der Kurven steht, solange es keine Messwerte gibt. Ohne Angaben zum Server: der allgemeine Satz. */
function EmptyHistory({ source, host, canCheck }: { source: HistoryOut["source"]; host?: HistoryHost; canCheck: boolean }) {
  const health = host ? hostHealth(host.status) : "online";
  if (host && health !== "online" && source !== "provider") {
    // Der Server hat (noch) nicht geantwortet: „in ein paar Minuten“ waere falsch, es wird so nichts kommen.
    const never = neverAnswered(host);
    return (
      <p className="text-sm text-white/60" data-testid="history-no-connection">
        {never
          ? canCheck ? "Noch keine Verbindung – prüfe zuerst den Zugang." : "Noch keine Verbindung zu diesem Server. Sobald er antwortet, erscheinen hier die Kurven."
          : "Noch keine Messwerte, und der Server antwortet gerade nicht. Sobald er wieder da ist, erscheinen hier die Kurven."}
        {never && canCheck && (
          <>
            {" "}
            <a href="#zugang" className="underline underline-offset-2 hover:text-white">Zum Zugang</a>
          </>
        )}
      </p>
    );
  }
  if (host && source !== "provider" && loginUnproven(host)) {
    // Der SSH-Port antwortet, angemeldet hat sich Nodvard Deck aber nie: so kommen keine Kurven.
    return (
      <p className="text-sm text-white/60" data-testid="history-login-unproven">
        Noch keine Messwerte. Der Server antwortet, aber die Anmeldung ist noch nicht bestätigt – prüfe zuerst den Zugang.
        {canCheck && (
          <>
            {" "}
            <a href="#zugang" className="underline underline-offset-2 hover:text-white">Zum Zugang</a>
          </>
        )}
      </p>
    );
  }
  return (
    <p className="text-sm text-white/50">
      Noch keine Messwerte. {source === "lattice" ? "Nodvard Deck misst diesen Server alle 30 Sekunden – in ein paar Minuten erscheinen die ersten Kurven." : ""}
    </p>
  );
}

export function HostHistory({ hostId, host, canCheck = false }: {
  hostId: string;
  /** Zustand des Servers: ohne Antwort steht statt „in ein paar Minuten“ ein klarer Hinweis. */
  host?: HistoryHost;
  /** Darf die Person die Verbindung pruefen (Zugangs-Karte vorhanden)? Dann fuehrt der Hinweis dorthin. */
  canCheck?: boolean;
}): JSX.Element | null {
  const [range, setRange] = useState("1h");
  const selected = RANGES.find((r) => r.value === range) ?? RANGES[0];
  const { data, isLoading, error } = useQuery({
    queryKey: ["hosts", hostId, "history", range],
    queryFn: () => api.get<HistoryOut>(`/hosts/${hostId}/metrics/history?range=${range}`),
    refetchInterval: range === "1h" ? 30_000 : 120_000,
    retry: false,
  });

  if (error && (error as { status?: number }).status === 404) return null; // kein Anbieter fuer diesen Host

  const panels = data
    ? PANELS.map((panel) => {
        const series: ChartSeries[] = panel.keys
          .filter((k) => data.series[k.key]?.some((v) => v !== null))
          .map((k) => ({ ...k, values: data.series[k.key] }));
        const yMax = panel.yMax ?? (panel.yMaxKey ? lastValue(data.series[panel.yMaxKey]) : undefined);
        return { panel, series, yMax };
      }).filter((p) => p.series.length > 0)
    : [];

  return (
    <section className="mt-6" data-testid="host-history">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Verlauf</h2>
        <div className="flex overflow-hidden rounded-lg border border-white/10" role="group" aria-label="Zeitraum">
          {RANGES.map((r) => (
            <button
              key={r.value}
              type="button"
              aria-pressed={r.value === range}
              onClick={() => setRange(r.value)}
              className={`px-2.5 py-1 text-xs ${r.value === range ? "bg-white/15 text-white" : "text-white/60 hover:bg-white/5"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
        {data && (
          <span className="text-[11px] text-white/40">
            {data.source === "provider" ? "Quelle: Proxmox" : "Quelle: Eigene Messung"} · Auflösung {data.step_s < 120 ? `${data.step_s} s` : `${Math.round(data.step_s / 60)} min`}
          </span>
        )}
      </div>
      {isLoading && <p className="text-sm text-white/50">Lade Verlauf …</p>}
      {error && (error as { status?: number }).status !== 404 && <p className="text-sm text-red-400">Verlauf nicht abrufbar: {(error as Error).message}</p>}
      {data && panels.length === 0 && <EmptyHistory source={data.source} host={host} canCheck={canCheck} />}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {panels.map(({ panel, series, yMax }) => (
          <TimeSeriesChart
            key={panel.title}
            title={panel.title}
            timestamps={data!.timestamps}
            series={series}
            format={panel.format}
            yMax={yMax}
            rangeSeconds={selected.seconds}
          />
        ))}
      </div>
    </section>
  );
}
