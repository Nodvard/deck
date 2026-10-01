import { renderTemplate, renderTone } from "../template";
import type { ListView as ListViewSpec, Tone } from "../types";
import { ActionButton } from "./ActionButton";

const TONE_BADGE: Record<Tone, string> = {
  neutral: "bg-white/10 text-white/70",
  good: "bg-emerald-500/15 text-emerald-300",
  warn: "bg-amber-500/15 text-amber-300",
  danger: "bg-red-500/15 text-red-300",
  accent: "accent-soft",
};

const TONE_DOT: Record<Tone, string> = {
  neutral: "stopped",
  good: "online",
  warn: "unknown",
  danger: "problem",
  accent: "online",
};

/**
 * Liste im Widget: Statuspunkt links (Farbe = Badge-Ton), Titel und
 * Untertitel in voller Laenge, Badge und Aktionen rechts.
 * Die Karten sind jetzt mindestens 4 Spalten breit (DashboardPage MIN_SIZE) -- vorher
 * wurde bei ~170 px Breite umgebrochen und gescrollt.
 */
export function ListView({ view, data, extId, onAction }: { view: ListViewSpec; data: unknown; extId: string; onAction: () => void }) {
  const rows = Array.isArray(data) ? data : [];
  const shown = view.max_items ? rows.slice(0, view.max_items) : rows;

  if (shown.length === 0) {
    return <p className="py-4 text-center text-sm text-white/45">{view.empty_text}</p>;
  }

  return (
    <ul className="-mx-2 flex flex-col">
      {shown.map((row, idx) => {
        const item = view.item;
        const badge = item.badge;
        const badgeTone = badge ? renderTone(badge.tone, row) : null;
        const title = renderTemplate(item.title, row);
        const subtitle = item.subtitle ? renderTemplate(item.subtitle, row) : null;
        return (
          // Volle Namen (umbrechen statt
          // "..."), und Badge/Knoepfe rutschen bei Platzmangel in eine eigene Zeile.
          <li key={idx} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg px-2 py-1.5 hover:bg-white/[0.03]">
            {badgeTone && <span className={`status-dot ${TONE_DOT[badgeTone]}`} />}
            <div className="min-w-0 flex-1 basis-32">
              <p className="break-words text-[13px] font-medium">{title}</p>
              {subtitle && <p className="break-words text-[11px] text-white/50">{subtitle}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {badge && badgeTone && (
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${TONE_BADGE[badgeTone]}`}>
                  {renderTemplate(badge.text, row)}
                </span>
              )}
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
