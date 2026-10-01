import { renderTemplate, renderTone } from "../template";
import type { StatusGridView as StatusGridViewSpec, Tone } from "../types";

const TONE_DOT: Record<Tone, string> = {
  neutral: "stopped",
  good: "online",
  warn: "unknown",
  danger: "problem",
  accent: "online",
};

export function StatusGridView({ view, data }: { view: StatusGridViewSpec; data: unknown }) {
  const tiles = Array.isArray(data) ? data : [];
  if (tiles.length === 0) {
    return <p className="text-sm opacity-60">Keine Einträge</p>;
  }

  // Nicht `grid-cols-2 sm:grid-cols-3`: `sm:` reagiert
  // auf die VIEWPORT-Breite, nicht auf die der Karte. Eine schmale Dashboard-Karte
  // (GridSize w=2 von 12 Spalten) bekam auf einem breiten Bildschirm drei Kachelspalten
  // in ~130px gequetscht, dazu `truncate` -- Namen waren praktisch unlesbar. Jetzt
  // richtet sich die Spaltenzahl nach der Kartenbreite (mind. 7.5rem pro Kachel), und
  // lange Namen brechen um statt abgeschnitten zu werden (Kartenkoerper scrollt ohnehin).
  // `min(7.5rem, 100%)`: in der schmalsten Karte (~114px Inhalt) waere ein 7.5rem-Track
  // breiter als die Karte selbst -- so wird eine einzelne Spalte nie breiter als der
  // Container (sonst entstuende ein waagerechter Mini-Scrollbalken in der Karte).
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(min(7.5rem,100%),1fr))] gap-2">
      {tiles.map((tile, idx) => {
        const tone = renderTone(view.tile_tone, tile);
        const link = view.tile_link ? renderTemplate(view.tile_link, tile) : null;
        const content = (
          <div key={idx} className={`flex min-w-0 items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.03] px-2.5 py-2 ${link ? "hover:border-white/20" : ""}`}>
            <span className={`status-dot ${TONE_DOT[tone]}`} />
            <span className="min-w-0">
              <p className="break-words text-[13px] font-medium">{renderTemplate(view.tile_title, tile)}</p>
              {view.tile_subtitle && <p className="break-words text-[11px] text-white/50">{renderTemplate(view.tile_subtitle, tile)}</p>}
            </span>
          </div>
        );
        return link ? (
          <a key={idx} href={link} target="_blank" rel="noreferrer" className="min-w-0">
            {content}
          </a>
        ) : (
          content
        );
      })}
    </div>
  );
}
