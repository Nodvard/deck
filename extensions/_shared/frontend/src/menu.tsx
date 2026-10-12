/**
 * Menü „Mehr“: seltene Aktionen hinter einem Knopf, damit eine Zeile nicht aus lauter Knöpfen
 * besteht. Schließt nach einer Wahl (Fokus zurück auf „Mehr“), mit Escape, bei einem Klick oder
 * Antippen daneben und wenn der Fokus das Menü verlässt.
 *
 * Eigene Datei aus demselben Grund wie AuthImage.tsx: nur Bundles, die das Menü einbinden,
 * ändern sich.
 */
import { useEffect, useRef, useState } from "react";

import { Icon, buttonClass } from "./ui";

export interface MenuItem {
  id: string;
  label: string;
  run: () => void;
  disabled?: boolean;
  title?: string;
  /** Rot: zerstörerisch oder riskant (z. B. hart ausschalten). */
  danger?: boolean;
}

export function MoreMenu({
  items, label = "Mehr", title = "Weitere Aktionen", align = "left", small = false, testId,
}: {
  items: MenuItem[];
  label?: string;
  title?: string;
  /** An welcher Kante des Knopfs das Menü ausgerichtet ist: rechts für Knöpfe am rechten Rand (sonst ragt es hinaus). */
  align?: "left" | "right";
  small?: boolean;
  testId?: string;
}): JSX.Element {
  const ref = useRef<HTMLDetailsElement>(null);
  const [open, setOpen] = useState(false);
  const close = (refocus: boolean) => {
    const details = ref.current;
    if (!details) return;
    if (refocus) details.querySelector("summary")?.focus();
    details.open = false;
  };
  useEffect(() => {
    if (!open) return;
    // `pointerdown` statt nur `mousedown`: iOS schickt beim Antippen einer leeren Fläche keine Mausereignisse.
    const outside = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <details
      ref={ref}
      className="relative"
      data-testid={testId}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      onBlur={(e) => {
        if (ref.current?.open && e.relatedTarget && !ref.current.contains(e.relatedTarget as Node)) close(false);
      }}
    >
      {/* Name für Lesegeräte: sichtbarer Text plus `title` („Mehr: Weitere Aktionen für docker“). Mehrere „Mehr“ in
          einer Liste bleiben so unterscheidbar, und wer per Sprache „Mehr“ sagt, trifft den Knopf trotzdem. */}
      <summary title={title} aria-label={`${label}: ${title}`} className={`${buttonClass("secondary", small)} cursor-pointer list-none [&::-webkit-details-marker]:hidden`}>
        {label} <Icon name="chevron-down" size={small ? 12 : 14} />
      </summary>
      {/* Fester Hintergrund statt durchscheinend; nie breiter als der Bildschirm (Handy). */}
      <div
        className={`absolute ${align === "right" ? "right-0" : "left-0"} z-20 mt-1 flex w-56 max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-white/10 bg-[var(--color-surface)] p-1 shadow-xl`}
      >
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            disabled={item.disabled}
            title={item.title}
            onClick={() => {
              close(true);
              item.run();
            }}
            className={`rounded-md px-3 py-2 text-left text-sm disabled:opacity-50 ${
              item.danger ? "text-red-300 hover:bg-red-500/15" : "hover:bg-white/[0.06]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
    </details>
  );
}
