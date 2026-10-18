import { dataLink } from "../../lib/apps";
import { renderTemplate, renderTone } from "../template";
import type { StatusGridView as StatusGridViewSpec, Tone } from "../types";

const TONE_DOT: Record<Tone, string> = {
  neutral: "stopped",
  good: "online",
  warn: "unknown",
  danger: "problem",
  accent: "online",
};

export function StatusGridView({ view, data, limit = null }: { view: StatusGridViewSpec; data: unknown; limit?: number | null }) {
  const all = Array.isArray(data) ? data : [];
  // `limit`: so viele Kacheln passen in die Karte, den Rest zählt die Karte als „+ x weitere“ (WidgetCard).
  const tiles = limit !== null ? all.slice(0, limit) : all;
  if (tiles.length === 0) {
    return <p className="text-sm opacity-60">Keine Einträge</p>;
  }

  // Spaltenzahl nach der Kartenbreite statt `sm:` (das reagiert auf den Viewport: Schmale Karten quetschten auf breiten
  // Bildschirmen drei Spalten in ~130px), mind. 7.5rem pro Kachel; lange Namen brechen um. `min(7.5rem,100%)`: In der
  // schmalsten Karte wäre der Track sonst breiter als sie (waagerechter Scrollbalken).
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(min(7.5rem,100%),1fr))] gap-2">
      {tiles.map((tile, idx) => {
        const tone = renderTone(view.tile_tone, tile);
        const link = view.tile_link ? dataLink(renderTemplate(view.tile_link, tile)) : undefined;
        const content = (
          <div key={idx} data-fit-row={link ? undefined : true} className={`flex min-w-0 items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.03] px-2.5 py-2 ${link ? "hover:border-white/20" : ""}`}>
            <span className={`status-dot ${TONE_DOT[tone]}`} />
            <span className="min-w-0">
              <p className="break-words text-[13px] font-medium">{renderTemplate(view.tile_title, tile)}</p>
              {view.tile_subtitle && <p className="break-words text-[11px] text-white/50">{renderTemplate(view.tile_subtitle, tile)}</p>}
            </span>
          </div>
        );
        return link ? (
          <a key={idx} href={link} target="_blank" rel="noreferrer" className="min-w-0" data-fit-row>
            {content}
          </a>
        ) : (
          content
        );
      })}
    </div>
  );
}
