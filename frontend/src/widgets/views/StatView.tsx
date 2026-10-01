import { renderTemplate, renderTone } from "../template";
import type { StatView as StatViewSpec, Tone } from "../types";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-current",
  good: "text-emerald-300",
  warn: "text-amber-300",
  danger: "text-red-300",
  accent: "text-[var(--color-accent)]",
};

export function StatView({ view, data }: { view: StatViewSpec; data: unknown }) {
  const value = renderTemplate(view.value, data);
  const label = view.label ? renderTemplate(view.label, data) : null;
  const delta = view.delta ? renderTemplate(view.delta, data) : null;
  const tone = renderTone(view.tone, data);

  return (
    <div className="flex h-full flex-col justify-center gap-1">
      <span className={`text-4xl font-semibold tabular-nums tracking-tight ${TONE_TEXT[tone]}`}>{value}</span>
      {label && <span className="text-xs text-white/60">{label}</span>}
      {delta && <span className="text-[11px] text-white/45">{delta}</span>}
    </div>
  );
}
