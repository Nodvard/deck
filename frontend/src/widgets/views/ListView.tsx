import { renderTemplate, renderTone } from "../template";
import type { ListView as ListViewSpec, Tone } from "../types";
import { ActionButton } from "./ActionButton";

/**
 * Ruhig statt bunt: Pillen nur für `warn` und `danger`. `good` zeigt nur den grünen Punkt (das Wort steht im Tooltip
 * und für Screenreader), `neutral` und `accent` sind schlichter Text.
 */
const TONE_BADGE: Record<Tone, string | null> = {
  neutral: "text-[11px] text-white/50",
  good: null,
  warn: "rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300",
  danger: "rounded-full bg-red-500/15 px-2 py-0.5 text-[10px] font-medium text-red-300",
  accent: "text-[11px] text-[var(--color-accent-text)]",
};

const TONE_DOT: Record<Tone, string> = {
  neutral: "stopped",
  good: "online",
  warn: "unknown",
  danger: "problem",
  accent: "online",
};

/**
 * Liste im Widget: Statuspunkt links (Farbe = Badge-Ton), Titel und Untertitel in voller Länge, Badge und Aktionen
 * rechts. Die Karten sind mindestens 4 Spalten breit (DashboardPage MIN_SIZE). Leer: nur der graue Satz, die Karte wird
 * dann so klein wie möglich.
 */
export function ListView({
  view, data, extId, onAction, limit = null,
}: { view: ListViewSpec; data: unknown; extId: string; onAction: () => void; limit?: number | null }) {
  const rows = Array.isArray(data) ? data : [];
  const capped = view.max_items ? rows.slice(0, view.max_items) : rows;
  // `limit`: so viele Zeilen passen in die Karte, den Rest zählt die Karte als „+ x weitere“ (WidgetCard).
  const shown = limit !== null ? capped.slice(0, limit) : capped;

  if (shown.length === 0) {
    return <p className="text-sm text-white/45">{view.empty_text}</p>;
  }

  return (
    <ul className="-mx-2 flex flex-col">
      {shown.map((row, idx) => {
        const item = view.item;
        const badge = item.badge;
        const badgeTone = badge ? renderTone(badge.tone, row) : null;
        const title = renderTemplate(item.title, row);
        const subtitle = item.subtitle ? renderTemplate(item.subtitle, row) : null;
        const badgeText = badge ? renderTemplate(badge.text, row) : null;
        const badgeClass = badgeTone ? TONE_BADGE[badgeTone] : null;
        return (
          // Volle Namen (umbrechen statt "..."), Badge und Knöpfe rutschen bei Platzmangel in eine eigene Zeile.
          <li key={idx} data-fit-row className="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg px-2 py-1.5 hover:bg-white/[0.03]">
            {badgeTone && (
              <span className={`status-dot ${TONE_DOT[badgeTone]}`} title={badgeClass ? undefined : (badgeText ?? undefined)} />
            )}
            <div className="min-w-0 flex-1 basis-32">
              <p className="break-words text-[13px] font-medium">{title}</p>
              {subtitle && <p className="break-words text-[11px] text-white/50">{subtitle}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {badgeText && badgeTone && (badgeClass ? <span className={badgeClass}>{badgeText}</span> : <span className="sr-only">{badgeText}</span>)}
              {item.actions.map((action) => (
                <ActionButton key={action.id} action={action} row={row} extId={extId} onDone={onAction} />
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
