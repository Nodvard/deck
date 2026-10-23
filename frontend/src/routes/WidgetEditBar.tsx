import type { Ref } from "react";

import { SIZE_ROWS, type SizeKey } from "../widgets/fit";
import type { WidgetGroupKey } from "../widgets/types";

const controlClass =
  "max-w-full rounded border border-white/10 bg-black/25 px-1.5 py-1 text-xs text-white outline-none focus:border-[var(--color-accent)]";

const SIZE_LABEL: Record<SizeKey, string> = { s: "Klein", m: "Mittel", l: "Groß" };

interface Props {
  title: string;
  size: SizeKey | null;
  /** `null`: eine gezogene Breite, weder schmal noch breit. */
  wide: boolean | null;
  /** Ohne Raster (Handy) gibt es keine Breite zu wählen. */
  showWidth: boolean;
  group: WidgetGroupKey;
  groups: { key: WidgetGroupKey; title: string }[];
  groupRef?: Ref<HTMLSelectElement>;
  onSize: (size: SizeKey | null) => void;
  onWide: (wide: boolean) => void;
  onGroup: (group: WidgetGroupKey) => void;
  onRemove: () => void;
}

/** Die Steuerung einer Karte im Bearbeiten-Modus: Höhe, Breite, Gruppe und Entfernen. */
export function WidgetEditBar({ title, size, wide, showWidth, group, groups, groupRef, onSize, onWide, onGroup, onRemove }: Props) {
  const widthButton = (isWide: boolean, label: string) => (
    <button
      type="button"
      aria-pressed={wide === isWide}
      onClick={() => wide !== isWide && onWide(isWide)}
      className={`px-2 py-1 text-xs ${wide === isWide ? "bg-white/20 text-white" : "text-white/60 hover:bg-white/10"}`}
    >
      {label}
    </button>
  );

  return (
    <div
      role="group"
      aria-label={`Bearbeiten: ${title}`}
      data-testid="widget-edit-bar"
      className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-white/[0.06] bg-black/15 px-3 py-2 text-xs text-white/50"
    >
      <label className="inline-flex items-center gap-1.5">
        Höhe
        <select
          value={size ?? "auto"}
          onChange={(e) => onSize(e.target.value === "auto" ? null : (e.target.value as SizeKey))}
          className={controlClass}
        >
          <option value="auto">Automatisch</option>
          {(Object.keys(SIZE_ROWS) as SizeKey[]).map((key) => (
            <option key={key} value={key}>{SIZE_LABEL[key]}</option>
          ))}
        </select>
      </label>
      {showWidth && (
        <div role="group" aria-label="Breite" className="inline-flex divide-x divide-white/10 overflow-hidden rounded border border-white/10">
          {widthButton(false, "Schmal")}
          {widthButton(true, "Breit")}
        </div>
      )}
      <label className="inline-flex min-w-0 items-center gap-1.5">
        Gruppe
        <select ref={groupRef} value={group} onChange={(e) => onGroup(e.target.value as WidgetGroupKey)} className={controlClass}>
          {groups.map((g) => (
            <option key={g.key} value={g.key}>{g.title}</option>
          ))}
        </select>
      </label>
      <button
        type="button"
        onClick={onRemove}
        className="ml-auto rounded px-2 py-1 text-xs text-red-300 hover:bg-red-400/10"
      >
        Entfernen
      </button>
    </div>
  );
}
