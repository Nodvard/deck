import { renderTemplate, resolvePath } from "../template";
import type { GaugeView as GaugeViewSpec } from "../types";

/** Ring wie auf der Startseite (Cockpit): Wert in der Mitte, Farbe ab 75/90 % warnend. */
export function GaugeView({ view, data }: { view: GaugeViewSpec; data: unknown }) {
  const value = Number(resolvePath(data, view.value_field) ?? 0);
  const max = view.max_field ? Number(resolvePath(data, view.max_field) ?? view.max_value) : view.max_value;
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const circumference = 2 * Math.PI * 40;
  const color = ratio >= 0.9 ? "#f87171" : ratio >= 0.75 ? "#fbbf24" : "var(--color-accent)";
  const shown = Number.isInteger(value) ? String(value) : value.toFixed(1);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-2">
      <div className="relative h-24 w-24">
        <svg viewBox="0 0 100 100" className="h-24 w-24 -rotate-90">
          <circle cx="50" cy="50" r="40" fill="none" stroke="currentColor" strokeOpacity={0.08} strokeWidth={9} />
          <circle
            cx="50"
            cy="50"
            r="40"
            fill="none"
            stroke={color}
            strokeWidth={9}
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - ratio)}
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 0.6s ease" }}
          />
        </svg>
        <p className="absolute inset-0 grid place-items-center text-xl font-semibold tabular-nums">
          <span>
            {shown}
            <span className="text-xs text-white/50"> / {max}</span>
          </span>
        </p>
      </div>
      {view.label && <p className="text-center text-xs text-white/60">{renderTemplate(view.label, data)}</p>}
    </div>
  );
}
