import { useState } from "react";

import { minMax } from "../../lib/minMax";
import { resolvePath } from "../template";
import type { ChartView as ChartViewSpec, Tone } from "../types";

const TONE_STROKE: Record<Tone, string> = {
  neutral: "#9ca3af",
  good: "rgb(var(--emerald-400))",
  warn: "rgb(var(--amber-400))",
  danger: "rgb(var(--red-400))",
  accent: "var(--color-accent)",
};

const WIDTH = 480;
const HEIGHT = 160;
const PADDING = 8;

type Point = [number, number];

function numberAt(row: unknown, field: string): number {
  return Number(resolvePath(row, field) ?? 0);
}

/** Zerlegt die Punkte an ungültigen Werten (`null`) in zusammenhängende Abschnitte, damit dort eine Lücke bleibt. */
function segments(points: (Point | null)[]): Point[][] {
  const out: Point[][] = [];
  let current: Point[] = [];
  for (const point of points) {
    if (point) {
      current.push(point);
    } else if (current.length) {
      out.push(current);
      current = [];
    }
  }
  if (current.length) out.push(current);
  return out;
}

/**
 * Winziges handgerolltes SVG für line/bar/area statt eines Chart-Pakets, um keine neue Abhängigkeit einzuführen.
 * Y-Achsen-Beschriftung (min/max) und ein Hover-Tooltip (senkrechte Linie, Werte aller Serien am nächsten Index) machen
 * exakte Werte ablesbar; Zoom und Legenden-Interaktion fehlen, dafür wäre eine Bibliothek sinnvoll.
 */
export function ChartView({ view, data }: { view: ChartViewSpec; data: unknown }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const rows = Array.isArray(data) ? data : [];
  if (rows.length === 0) {
    return <p className="text-sm opacity-60">Keine Daten</p>;
  }

  const range = minMax(view.series.flatMap((series) => rows.map((row) => numberAt(row, series.field))));
  const min = Math.min(0, range?.min ?? 0);
  const max = view.y_max ?? Math.max(1, range?.max ?? 1);
  const xStep = rows.length > 1 ? (WIDTH - 2 * PADDING) / (rows.length - 1) : 0;

  function pointsFor(field: string): (Point | null)[] {
    return rows.map((row, idx) => {
      const raw = numberAt(row, field);
      if (!Number.isFinite(raw)) return null;
      const x = PADDING + idx * xStep;
      const y = HEIGHT - PADDING - ((raw - min) / (max - min || 1)) * (HEIGHT - 2 * PADDING);
      return [x, y];
    });
  }

  function formatValue(n: number): string {
    if (!Number.isFinite(n)) return "–";
    return Number.isInteger(n) ? String(n) : n.toFixed(1);
  }

  const seriesPoints = view.series.map((series) => ({ series, points: pointsFor(series.field) }));
  const hoverX = hoverIdx !== null ? PADDING + hoverIdx * xStep : null;

  return (
    <div>
      <div className="relative">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img">
          {seriesPoints.map(({ series, points }) => {
            const stroke = TONE_STROKE[series.tone];
            if (view.chart === "bar") {
              const barWidth = xStep > 0 ? Math.max(2, xStep * 0.6) : 8;
              return (
                <g key={series.field}>
                  {points.map(
                    (point, idx) =>
                      point && (
                        <rect key={idx} x={point[0] - barWidth / 2} y={point[1]} width={barWidth} height={HEIGHT - PADDING - point[1]} fill={stroke} opacity={0.8} />
                      ),
                  )}
                </g>
              );
            }
            const parts = segments(points);
            const lines = parts.map((part) => part.map(([x, y], idx) => `${idx === 0 ? "M" : "L"}${x},${y}`).join(" "));
            const path = lines.join(" ");
            if (view.chart === "area") {
              const areaPath = parts
                .map((part, i) => `${lines[i]} L${part[part.length - 1][0]},${HEIGHT - PADDING} L${part[0][0]},${HEIGHT - PADDING} Z`)
                .join(" ");
              return (
                <g key={series.field}>
                  <path d={areaPath} fill={stroke} opacity={0.2} />
                  <path d={path} fill="none" stroke={stroke} strokeWidth={2} />
                </g>
              );
            }
            return <path key={series.field} d={path} fill="none" stroke={stroke} strokeWidth={2} />;
          })}

          {/* Y-Achsen-Beschriftung: min/max, damit Werte grob ablesbar sind. */}
          <text x={PADDING} y={PADDING + 8} fontSize={9} fill="currentColor" opacity={0.5}>
            {formatValue(max)}
          </text>
          <text x={PADDING} y={HEIGHT - PADDING - 2} fontSize={9} fill="currentColor" opacity={0.5}>
            {formatValue(min)}
          </text>

          {/* Hover-Zonen: eine unsichtbare Spalte pro Index statt pro Punkt, zeigt alle Serien gleichzeitig und ist
              treffsicherer als winzige Hover-Ziele auf dünnen Linien. */}
          {rows.map((_, idx) => (
            <rect
              key={idx}
              x={PADDING + idx * xStep - xStep / 2}
              y={0}
              width={xStep || WIDTH}
              height={HEIGHT}
              fill="transparent"
              onMouseEnter={() => setHoverIdx(idx)}
              onMouseLeave={() => setHoverIdx((current) => (current === idx ? null : current))}
            />
          ))}

          {hoverX !== null && (
            <line x1={hoverX} y1={0} x2={hoverX} y2={HEIGHT} stroke="currentColor" strokeOpacity={0.25} strokeWidth={1} />
          )}
        </svg>

        {hoverIdx !== null && (
          <div
            className="pointer-events-none absolute top-1 rounded border border-white/10 bg-[var(--color-surface)] px-2 py-1 text-xs shadow"
            style={{ left: `${Math.min(85, (hoverX! / WIDTH) * 100)}%` }}
          >
            {view.series.map((series) => (
              <div key={series.field} className="whitespace-nowrap">
                <span style={{ color: TONE_STROKE[series.tone] }}>{series.label}</span>: {formatValue(numberAt(rows[hoverIdx], series.field))}
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="mt-1 flex flex-wrap gap-3 text-xs opacity-70">
        {view.series.map((series) => (
          <span key={series.field} className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: TONE_STROKE[series.tone] }} />
            {series.label}
          </span>
        ))}
      </div>
    </div>
  );
}
