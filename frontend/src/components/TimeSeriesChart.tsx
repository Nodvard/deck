/**
 * Zeitreihen-Diagramm fuer den Metrik-Verlauf der Server-Seite, so genau ablesbar wie
 * in Grafana, Task-Manager oder HWiNFO. Bewusst ohne Chart-Bibliothek:
 * reines SVG, ein paar hundert Punkte je Kurve, offline-tauglich.
 *
 * Lesbarkeit vor Deko: 2px-Linien mit leichter Flaeche darunter, ruhiges Raster, EINE
 * y-Achse je Diagramm (zwei Groessen = zwei Diagramme), Fadenkreuz mit Tooltip, das
 * ALLE Kurven am gewaehlten Zeitpunkt zeigt, und eine Legende mit aktuell/Min/Mittel/Max
 * wie bei HWiNFO. Luecken (Host nicht erreichbar) bleiben Luecken.
 */
import { useEffect, useMemo, useRef, useState } from "react";

export interface ChartSeries {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
}

interface Props {
  title: string;
  timestamps: number[];
  series: ChartSeries[];
  format: (value: number) => string;
  /** Feste Obergrenze (z. B. 100 fuer Prozent, Gesamt-RAM); sonst aus den Daten. */
  yMax?: number;
  height?: number;
  rangeSeconds: number;
}

const PAD = { top: 10, right: 12, bottom: 22, left: 58 };

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  const f = value / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * exp;
}

function timeLabel(ts: number, rangeSeconds: number): string {
  const d = new Date(ts * 1000);
  if (rangeSeconds > 2 * 86400) return d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function stats(values: (number | null)[]): { current: number | null; min: number | null; avg: number | null; max: number | null } {
  const present = values.filter((v): v is number => v !== null);
  if (!present.length) return { current: null, min: null, avg: null, max: null };
  let current: number | null = null;
  for (let i = values.length - 1; i >= 0; i--) {
    if (values[i] !== null) {
      current = values[i];
      break;
    }
  }
  return {
    current,
    min: Math.min(...present),
    avg: present.reduce((a, b) => a + b, 0) / present.length,
    max: Math.max(...present),
  };
}

export function TimeSeriesChart({ title, timestamps, series, format, yMax, height = 170, rangeSeconds }: Props): JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => setWidth(Math.max(240, entries[0].contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const t0 = timestamps[0] ?? 0;
  const t1 = timestamps[timestamps.length - 1] ?? 1;
  const dataMax = Math.max(0, ...series.flatMap((s) => s.values.filter((v): v is number => v !== null)));
  const top = yMax ?? niceMax(dataMax * 1.1);
  const x = (ts: number) => PAD.left + (t1 === t0 ? 0 : ((ts - t0) / (t1 - t0)) * plotW);
  const y = (v: number) => PAD.top + plotH - (Math.min(v, top) / top) * plotH;

  const paths = useMemo(
    () =>
      series.map((s) => {
        let line = "";
        let area = "";
        let segStart: number | null = null;
        let prevX = 0;
        s.values.forEach((v, i) => {
          const px = x(timestamps[i]);
          if (v === null) {
            if (segStart !== null) area += `L${prevX},${PAD.top + plotH}L${segStart},${PAD.top + plotH}Z`;
            segStart = null;
            return;
          }
          const py = y(v);
          if (segStart === null) {
            line += `M${px},${py}`;
            area += `M${px},${py}`;
            segStart = px;
          } else {
            line += `L${px},${py}`;
            area += `L${px},${py}`;
          }
          prevX = px;
        });
        if (segStart !== null) area += `L${prevX},${PAD.top + plotH}L${segStart},${PAD.top + plotH}Z`;
        return { key: s.key, color: s.color, line, area };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [series, timestamps, width, top, height],
  );

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * top);
  // Schmale Diagramme (Handy): drei statt fuenf Zeitmarken, sonst ueberlappen die Beschriftungen.
  const xTicks = (width < (t1 - t0 > 2 * 86400 ? 720 : 480) ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1]).map((f) => t0 + f * (t1 - t0));

  function onMove(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - rect.left;
    if (px < PAD.left || px > width - PAD.right || timestamps.length === 0) {
      setHover(null);
      return;
    }
    const ts = t0 + ((px - PAD.left) / plotW) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < timestamps.length; i++) {
      if (Math.abs(timestamps[i] - ts) < Math.abs(timestamps[best] - ts)) best = i;
    }
    setHover(best);
  }

  const hasData = series.some((s) => s.values.some((v) => v !== null));
  const hoverX = hover !== null ? x(timestamps[hover]) : 0;

  return (
    <div className="panel min-w-0 p-4" data-testid={`chart-${title}`}>
      <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/60">{title}</p>
      <div ref={wrapRef} className="relative w-full min-w-0 overflow-hidden">
        {!hasData ? (
          <p className="py-10 text-center text-xs text-white/40">Noch keine Messwerte in diesem Zeitraum.</p>
        ) : (
          <svg width={width} height={height} onPointerMove={onMove} onPointerLeave={() => setHover(null)} role="img" aria-label={title} className="block touch-none select-none">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="rgba(255,255,255,0.07)" />
                <text x={PAD.left - 6} y={y(t) + 3} textAnchor="end" className="fill-white/45 text-[10px] tabular-nums">{format(t)}</text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text key={i} x={x(t)} y={height - 5} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"} className="fill-white/45 text-[10px] tabular-nums">
                {timeLabel(t, rangeSeconds)}
              </text>
            ))}
            {paths.map((p) => <path key={`${p.key}-a`} d={p.area} fill={p.color} opacity={0.12} />)}
            {paths.map((p) => <path key={p.key} d={p.line} fill="none" stroke={p.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
            {hover !== null && (
              <g>
                <line x1={hoverX} x2={hoverX} y1={PAD.top} y2={PAD.top + plotH} stroke="rgba(255,255,255,0.35)" />
                {series.map((s) => {
                  const v = s.values[hover];
                  return v === null ? null : <circle key={s.key} cx={hoverX} cy={y(v)} r={4} fill={s.color} stroke="#0b0f17" strokeWidth={2} />;
                })}
              </g>
            )}
          </svg>
        )}
        {hover !== null && hasData && (
          <div
            className="pointer-events-none absolute top-1 z-10 min-w-[150px] rounded-lg border border-white/10 bg-[#0b0f17]/95 px-3 py-2 text-xs shadow-xl"
            style={{ left: hoverX > width / 2 ? hoverX - 170 : hoverX + 12 }}
            role="tooltip"
          >
            <p className="mb-1 text-[10px] text-white/50">{new Date(timestamps[hover] * 1000).toLocaleString()}</p>
            {series.map((s) => (
              <p key={s.key} className="flex items-center gap-2">
                <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} />
                <span className="font-semibold tabular-nums text-white">{s.values[hover] === null ? "–" : format(s.values[hover] as number)}</span>
                <span className="text-white/55">{s.label}</span>
              </p>
            ))}
          </div>
        )}
      </div>
      {hasData && (
        <table className="mt-2 w-full text-[11px]">
          <thead>
            <tr className="text-left text-white/40">
              <th className="font-normal" />
              <th className="text-right font-normal">aktuell</th>
              <th className="text-right font-normal">min</th>
              <th className="text-right font-normal">Ø</th>
              <th className="text-right font-normal">max</th>
            </tr>
          </thead>
          <tbody>
            {series.map((s) => {
              const st = stats(s.values);
              const f = (v: number | null) => (v === null ? "–" : format(v));
              return (
                <tr key={s.key} className="text-white/80">
                  <td className="py-0.5">
                    <span className="mr-1.5 inline-block h-0.5 w-3 rounded align-middle" style={{ background: s.color }} />
                    {s.label}
                  </td>
                  <td className="text-right tabular-nums">{f(st.current)}</td>
                  <td className="text-right tabular-nums">{f(st.min)}</td>
                  <td className="text-right tabular-nums">{f(st.avg)}</td>
                  <td className="text-right tabular-nums">{f(st.max)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
