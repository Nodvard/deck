/**
 * Live-Ansicht eines Linux-Servers wie Task-Manager/HWiNFO, moeglichst vollstaendig
 * und genau: pro Kern mit Takt, CPU-Aufschluesselung, RAM-Aufteilung,
 * Temperaturen/Luefter, jede Platte und jede Netzwerkkarte einzeln, Dateisysteme,
 * Prozesse. Fragt `GET /ext/system/hosts/{id}/live` alle 3 s ab, solange die Seite
 * sichtbar ist, und fuehrt je Kennzahl einen kleinen Verlauf der letzten 3 Minuten
 * (nur im Browser, der gespeicherte Verlauf steht auf der Server-Seite).
 */
import { useEffect, useRef, useState } from "react";

import { formatBytes, formatUptime } from "./format";

export interface LiveData {
  complete: boolean;
  interval_s: number;
  uptime_s: number | null;
  cpu: {
    model: string | null;
    cores: number;
    total: { percent: number; user: number; system: number; iowait: number; steal: number } | null;
    per_core: { id: number; percent: number | null; freq_mhz: number | null }[];
    load: number[];
  };
  memory: {
    total: number; used: number; available: number; free: number | null; buffers: number | null;
    cached: number | null; shared: number | null; dirty: number | null; swap_total: number | null; swap_used: number | null;
  } | null;
  temperatures: { source: string; label: string; celsius: number }[];
  fans: { label: string; rpm: number }[];
  disks: { name: string; read_bps: number; write_bps: number; read_iops: number; write_iops: number; busy_percent: number }[];
  network: {
    name: string; virtual: boolean; rx_bps: number; tx_bps: number; rx_total: number; tx_total: number;
    errors: number; drops: number; speed_mbps: number | null; state: string | null;
  }[];
  filesystems: { device: string; fstype: string; mount: string; size: number; used: number; available: number; percent: number }[];
  processes: { pid: number; name: string; user: string | null; cpu_percent: number; mem_bytes: number }[];
  process_count: number;
  throttled: { raw: string; flags: string[] } | null;
}

const POLL_MS = 3000;
const HISTORY = 60; // 60 x 3 s = 3 Minuten
const BLUE = "#3987e5";
const ORANGE = "#d95926";

const rate = (v: number) => (v < 1024 ? `${Math.round(v)} B/s` : `${formatBytes(v)}/s`);
const pct = (v: number | null | undefined) => (v == null ? "–" : `${v.toFixed(v < 10 ? 1 : 0)} %`);

function tempTone(c: number): string {
  return c >= 80 ? "text-red-300" : c >= 70 ? "text-amber-300" : "text-white";
}

function barColor(percent: number): string {
  return percent >= 90 ? "#e66767" : percent >= 75 ? "#c98500" : BLUE;
}

/** Kleine Verlaufskurve wie in den Task-Manager-Kacheln. */
function Spark({ values, max, color = BLUE }: { values: number[]; max?: number; color?: string }): JSX.Element {
  const w = 160;
  const h = 36;
  const top = max ?? Math.max(1, ...values) * 1.15;
  const offset = HISTORY - values.length;
  const shifted = values.map((v, i) => `${((i + offset) / Math.max(1, HISTORY - 1)) * w},${h - (Math.min(v, top) / top) * h}`);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="mt-1 h-9 w-full" aria-hidden="true">
      <line x1={0} x2={w} y1={h - 0.5} y2={h - 0.5} stroke="rgba(255,255,255,0.1)" />
      {shifted.length > 1 && (
        <>
          <polygon points={`${shifted[0].split(",")[0]},${h} ${shifted.join(" ")} ${w},${h}`} fill={color} opacity={0.15} />
          <polyline points={shifted.join(" ")} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        </>
      )}
    </svg>
  );
}

function Tile({ label, value, sub, spark }: { label: string; value: string; sub?: string; spark?: JSX.Element }): JSX.Element {
  return (
    <div className="panel p-3">
      <p className="text-[11px] uppercase tracking-wider opacity-50">{label}</p>
      <p className="text-xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="truncate text-[11px] opacity-60">{sub}</p>}
      {spark}
    </div>
  );
}

function Meter({ percent }: { percent: number }): JSX.Element {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-white/10">
      <div className="h-full rounded" style={{ width: `${Math.min(100, Math.max(0, percent))}%`, background: barColor(percent) }} />
    </div>
  );
}

function Section({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }): JSX.Element {
  return (
    <section className="panel p-4">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider opacity-70">{title}</h3>
        {extra}
      </div>
      {children}
    </section>
  );
}

type History = Record<"cpu" | "mem" | "disk" | "netIn" | "netOut" | "temp", number[]>;
const EMPTY_HISTORY: History = { cpu: [], mem: [], disk: [], netIn: [], netOut: [], temp: [] };

function push(list: number[], value: number | undefined): number[] {
  if (value === undefined) return list;
  const next = [...list, value];
  return next.length > HISTORY ? next.slice(next.length - HISTORY) : next;
}

export function LiveView({ hostId, fetchLive }: { hostId: string; fetchLive: (id: string) => Promise<LiveData> }): JSX.Element {
  const [data, setData] = useState<LiveData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [history, setHistory] = useState<History>(EMPTY_HISTORY);
  const [procSort, setProcSort] = useState<"cpu" | "mem">("cpu");
  const [showVirtual, setShowVirtual] = useState(false);
  const busy = useRef(false);

  useEffect(() => {
    setData(null);
    setHistory(EMPTY_HISTORY);
  }, [hostId]);

  useEffect(() => {
    if (paused) return;
    let cancelled = false;
    async function tick() {
      if (busy.current || (typeof document !== "undefined" && document.hidden)) return;
      busy.current = true;
      try {
        const live = await fetchLive(hostId);
        if (cancelled) return;
        setData(live);
        setError(null);
        setUpdatedAt(new Date());
        const physical = live.network.filter((n) => !n.virtual);
        setHistory((h) => ({
          cpu: push(h.cpu, live.cpu.total?.percent),
          mem: push(h.mem, live.memory ? (100 * live.memory.used) / live.memory.total : undefined),
          disk: push(h.disk, live.disks.length ? Math.max(...live.disks.map((d) => d.busy_percent)) : undefined),
          netIn: push(h.netIn, physical.reduce((a, n) => a + n.rx_bps, 0)),
          netOut: push(h.netOut, physical.reduce((a, n) => a + n.tx_bps, 0)),
          temp: push(h.temp, live.temperatures.length ? Math.max(...live.temperatures.map((t) => t.celsius)) : undefined),
        }));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        busy.current = false;
      }
    }
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hostId, paused, fetchLive]);

  const controls = (
    <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
      <button type="button" onClick={() => setPaused((p) => !p)} className="rounded bg-white/10 px-2 py-1 hover:bg-white/20">
        {paused ? "Fortsetzen" : "Pausieren"}
      </button>
      <span className="opacity-50">
        {paused ? "angehalten" : "aktualisiert alle 3 s"}
        {updatedAt ? ` · Stand ${updatedAt.toLocaleTimeString()}` : ""}
      </span>
      {error && <span className="text-red-400">Fehler: {error}</span>}
    </div>
  );

  if (!data) {
    return (
      <div data-testid="live-view">
        {controls}
        {!error && <p className="text-sm opacity-60">Messe … (die Raten brauchen eine Sekunde Messzeit)</p>}
      </div>
    );
  }

  const { cpu, memory } = data;
  const physical = data.network.filter((n) => !n.virtual);
  const nics = showVirtual ? data.network : physical;
  const netIn = physical.reduce((a, n) => a + n.rx_bps, 0);
  const netOut = physical.reduce((a, n) => a + n.tx_bps, 0);
  const diskBusy = data.disks.length ? Math.max(...data.disks.map((d) => d.busy_percent)) : null;
  const hottest = data.temperatures.length ? Math.max(...data.temperatures.map((t) => t.celsius)) : null;
  const procs = [...data.processes].sort((a, b) => (procSort === "cpu" ? b.cpu_percent - a.cpu_percent || b.mem_bytes - a.mem_bytes : b.mem_bytes - a.mem_bytes));
  const freqs = cpu.per_core.map((c) => c.freq_mhz).filter((f): f is number => f != null);

  return (
    <div className="space-y-4" data-testid="live-view">
      {controls}
      {data.throttled && data.throttled.flags.length > 0 && (
        <p className="panel px-3 py-2 text-sm text-amber-300" data-testid="throttled">
          Raspberry Pi meldet: {data.throttled.flags.join(", ")} ({data.throttled.raw}) -- meist ein zu schwaches Netzteil oder Hitze.
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="CPU" value={pct(cpu.total?.percent)} sub={freqs.length ? `${Math.max(...freqs)} MHz · ${cpu.cores} Kerne` : `${cpu.cores} Kerne`} spark={<Spark values={history.cpu} max={100} />} />
        <Tile label="Arbeitsspeicher" value={memory ? pct((100 * memory.used) / memory.total) : "–"} sub={memory ? `${formatBytes(memory.used)} von ${formatBytes(memory.total)}` : undefined} spark={<Spark values={history.mem} max={100} />} />
        <Tile label="Datenträger aktiv" value={pct(diskBusy)} sub={data.disks.map((d) => d.name).join(", ") || "keine"} spark={<Spark values={history.disk} max={100} />} />
        <Tile label="Netzwerk" value={rate(netIn + netOut)} sub={`↓ ${rate(netIn)} · ↑ ${rate(netOut)}`} spark={<Spark values={history.netIn} color={BLUE} />} />
        <Tile label="Temperatur" value={hottest != null ? `${hottest.toFixed(1)} °C` : "–"} sub={hottest != null ? "höchster Sensor" : "keine Sensoren"} spark={<Spark values={history.temp} color={ORANGE} />} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Prozessor" extra={<span className="text-[11px] opacity-50">{cpu.model ?? ""}</span>}>
          {cpu.total && (
            <p className="mb-3 text-xs opacity-80" data-testid="cpu-breakdown">
              Benutzer {pct(cpu.total.user)} · System {pct(cpu.total.system)} · Warten auf I/O {pct(cpu.total.iowait)}
              {cpu.total.steal > 0 ? ` · Steal ${pct(cpu.total.steal)}` : ""} · Last {cpu.load.map((l) => l.toFixed(2)).join(" / ")} · läuft seit {formatUptime(data.uptime_s)}
            </p>
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="cores">
            {cpu.per_core.map((core) => (
              <div key={core.id} className="rounded-lg bg-white/[0.04] p-2">
                <div className="flex items-baseline justify-between text-[11px]">
                  <span className="opacity-60">Kern {core.id}</span>
                  <span className="font-semibold tabular-nums">{pct(core.percent)}</span>
                </div>
                <Meter percent={core.percent ?? 0} />
                {core.freq_mhz != null && <p className="mt-1 text-[10px] tabular-nums opacity-50">{core.freq_mhz} MHz</p>}
              </div>
            ))}
          </div>
        </Section>

        <Section title="Arbeitsspeicher">
          {memory ? (
            <>
              <div className="mb-2 flex h-3 w-full overflow-hidden rounded bg-white/10" data-testid="mem-bar" aria-label="Aufteilung des Arbeitsspeichers">
                <div style={{ width: `${(100 * memory.used) / memory.total}%`, background: BLUE }} title="belegt" />
                <div style={{ width: `${(100 * (memory.cached ?? 0)) / memory.total}%`, background: "#199e70" }} title="Cache" />
                <div style={{ width: `${(100 * (memory.buffers ?? 0)) / memory.total}%`, background: "#c98500" }} title="Puffer" />
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
                <div><dt className="opacity-50"><span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: BLUE }} />belegt</dt><dd className="tabular-nums">{formatBytes(memory.used)}</dd></div>
                <div><dt className="opacity-50"><span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: "#199e70" }} />Cache</dt><dd className="tabular-nums">{formatBytes(memory.cached)}</dd></div>
                <div><dt className="opacity-50"><span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: "#c98500" }} />Puffer</dt><dd className="tabular-nums">{formatBytes(memory.buffers)}</dd></div>
                <div><dt className="opacity-50">verfügbar</dt><dd className="tabular-nums">{formatBytes(memory.available)}</dd></div>
                <div><dt className="opacity-50">gemeinsam</dt><dd className="tabular-nums">{formatBytes(memory.shared)}</dd></div>
                <div><dt className="opacity-50">gesamt</dt><dd className="tabular-nums">{formatBytes(memory.total)}</dd></div>
              </dl>
              {memory.swap_total ? (
                <div className="mt-3 text-xs">
                  <div className="mb-1 flex justify-between"><span className="opacity-60">Swap</span><span className="tabular-nums">{formatBytes(memory.swap_used)} von {formatBytes(memory.swap_total)}</span></div>
                  <Meter percent={(100 * (memory.swap_used ?? 0)) / memory.swap_total} />
                </div>
              ) : (
                <p className="mt-3 text-xs opacity-50">Kein Swap eingerichtet.</p>
              )}
            </>
          ) : (
            <p className="text-sm opacity-50">Keine Angaben.</p>
          )}
        </Section>

        <Section title="Sensoren">
          {data.temperatures.length === 0 && data.fans.length === 0 ? (
            <p className="text-sm opacity-50">Dieser Server meldet keine Sensoren (bei VMs normal).</p>
          ) : (
            <table className="w-full text-sm" data-testid="sensors">
              <tbody className="divide-y divide-white/5">
                {data.temperatures.map((t) => (
                  <tr key={`${t.source}-${t.label}`}>
                    <td className="py-1 opacity-70">{t.label}</td>
                    <td className="py-1 text-xs opacity-40">{t.source}</td>
                    <td className={`py-1 text-right font-semibold tabular-nums ${tempTone(t.celsius)}`}>{t.celsius.toFixed(1)} °C</td>
                  </tr>
                ))}
                {data.fans.map((f) => (
                  <tr key={f.label}>
                    <td className="py-1 opacity-70">{f.label}</td>
                    <td className="py-1 text-xs opacity-40">Lüfter</td>
                    <td className="py-1 text-right font-semibold tabular-nums">{f.rpm} U/min</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>

        <Section title="Datenträger">
          {data.disks.length === 0 ? (
            <p className="text-sm opacity-50">Keine physischen Datenträger erkannt.</p>
          ) : (
            <table className="w-full text-sm" data-testid="live-disks">
              <thead>
                <tr className="text-left text-[11px] uppercase opacity-50">
                  <th className="py-1 font-normal">Gerät</th>
                  <th className="py-1 text-right font-normal">Lesen</th>
                  <th className="py-1 text-right font-normal">Schreiben</th>
                  <th className="py-1 text-right font-normal">IOPS l/s</th>
                  <th className="w-28 py-1 pl-3 font-normal">Aktiv</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {data.disks.map((d) => (
                  <tr key={d.name}>
                    <td className="py-1 font-mono text-xs">{d.name}</td>
                    <td className="py-1 text-right tabular-nums">{rate(d.read_bps)}</td>
                    <td className="py-1 text-right tabular-nums">{rate(d.write_bps)}</td>
                    <td className="py-1 text-right tabular-nums">{d.read_iops.toFixed(0)} / {d.write_iops.toFixed(0)}</td>
                    <td className="py-1 pl-3"><div className="flex items-center gap-2"><Meter percent={d.busy_percent} /><span className="w-14 whitespace-nowrap text-right text-xs tabular-nums">{pct(d.busy_percent)}</span></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>

      <Section
        title="Netzwerk"
        extra={
          data.network.some((n) => n.virtual) ? (
            <label className="ml-auto flex items-center gap-1 text-[11px] opacity-70">
              <input type="checkbox" checked={showVirtual} onChange={(e) => setShowVirtual(e.target.checked)} /> virtuelle Schnittstellen zeigen
            </label>
          ) : undefined
        }
      >
        <table className="w-full text-sm" data-testid="live-network">
          <thead>
            <tr className="text-left text-[11px] uppercase opacity-50">
              <th className="py-1 font-normal">Schnittstelle</th>
              <th className="py-1 font-normal">Status</th>
              <th className="py-1 text-right font-normal">Empfangen</th>
              <th className="py-1 text-right font-normal">Gesendet</th>
              <th className="py-1 text-right font-normal">Gesamt ↓ / ↑</th>
              <th className="py-1 text-right font-normal" title="Übertragungsfehler / vom System verworfene Pakete">Fehler / verworfen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {nics.map((n) => (
              <tr key={n.name} className={n.virtual ? "opacity-60" : ""}>
                <td className="py-1 font-mono text-xs">{n.name}</td>
                <td className="py-1 text-xs">{n.state === "up" ? "verbunden" : n.state ?? "?"}{n.speed_mbps ? ` · ${n.speed_mbps >= 1000 ? `${n.speed_mbps / 1000} Gbit/s` : `${n.speed_mbps} Mbit/s`}` : ""}</td>
                <td className="py-1 text-right tabular-nums">{rate(n.rx_bps)}</td>
                <td className="py-1 text-right tabular-nums">{rate(n.tx_bps)}</td>
                <td className="py-1 text-right text-xs tabular-nums opacity-70">{formatBytes(n.rx_total)} / {formatBytes(n.tx_total)}</td>
                <td className="py-1 text-right text-xs tabular-nums">
                  <span className={n.errors > 0 ? "text-amber-300" : "opacity-50"}>{n.errors}</span>
                  <span className="opacity-40"> / {n.drops}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Dateisysteme">
        <table className="w-full text-sm" data-testid="live-filesystems">
          <tbody className="divide-y divide-white/5">
            {data.filesystems.map((f) => (
              <tr key={`${f.device}-${f.mount}`}>
                <td className="py-1 font-mono text-xs">{f.mount}</td>
                <td className="py-1 text-xs opacity-50">{f.device} · {f.fstype}</td>
                <td className="w-48 py-1"><Meter percent={f.percent} /></td>
                <td className="py-1 pl-3 text-right text-xs tabular-nums">{formatBytes(f.used)} / {formatBytes(f.size)} ({f.percent.toFixed(0)} %)</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section
        title={`Prozesse (${data.process_count})`}
        extra={
          <div className="ml-auto flex gap-1 text-[11px]" role="group" aria-label="Sortierung">
            <button type="button" aria-pressed={procSort === "cpu"} onClick={() => setProcSort("cpu")} className={`rounded px-2 py-0.5 ${procSort === "cpu" ? "bg-white/15" : "opacity-60 hover:opacity-100"}`}>nach CPU</button>
            <button type="button" aria-pressed={procSort === "mem"} onClick={() => setProcSort("mem")} className={`rounded px-2 py-0.5 ${procSort === "mem" ? "bg-white/15" : "opacity-60 hover:opacity-100"}`}>nach RAM</button>
          </div>
        }
      >
        <table className="w-full text-sm" data-testid="processes">
          <thead>
            <tr className="text-left text-[11px] uppercase opacity-50">
              <th className="py-1 font-normal">PID</th>
              <th className="py-1 font-normal">Name</th>
              <th className="py-1 font-normal">Benutzer</th>
              <th className="py-1 text-right font-normal">CPU</th>
              <th className="py-1 text-right font-normal">RAM</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {procs.slice(0, 15).map((p) => (
              <tr key={p.pid}>
                <td className="py-1 font-mono text-xs opacity-60">{p.pid}</td>
                <td className="py-1">{p.name}</td>
                <td className="py-1 text-xs opacity-60">{p.user ?? "?"}</td>
                <td className="py-1 text-right tabular-nums">{p.cpu_percent.toFixed(1)} %</td>
                <td className="py-1 text-right tabular-nums">{formatBytes(p.mem_bytes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[11px] opacity-40">CPU wie im Task-Manager: Anteil an allen {cpu.cores} Kernen, gemessen über {data.interval_s.toFixed(1)} s.</p>
      </Section>
    </div>
  );
}
