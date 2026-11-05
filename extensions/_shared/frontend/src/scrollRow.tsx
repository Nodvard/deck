/**
 * Seitlich scrollende Leiste wie die Reiter am Handy: Verlauf und Pfeil zeigen, dass am Rand noch etwas liegt (eigene
 * Datei, damit sich nur die Bundles ändern, die sie nutzen).
 */
import { useCallback, useEffect, useState, type CSSProperties } from "react";

import { Icon } from "./ui";

export interface Edges {
  left: boolean;
  right: boolean;
}

const FADE_PX = 40;

/** Merkt sich, ob die Leiste links oder rechts über den Rand reicht, und schiebt den aktiven Eintrag ins Bild. */
export function useScrollRow<T extends HTMLElement>(active: unknown): {
  ref: (row: T | null) => void;
  row: T | null;
  edges: Edges;
} {
  // Als Callback-Ref: Die Leiste kann erst nach dem ersten Rendern erscheinen (etwa nach einer Weiterleitung).
  const [row, setRow] = useState<T | null>(null);
  const [edges, setEdges] = useState<Edges>({ left: false, right: false });

  const measure = useCallback(() => {
    if (!row) return;
    const max = row.scrollWidth - row.clientWidth;
    const left = row.scrollLeft > 1;
    const right = row.scrollLeft < max - 1;
    setEdges((old) => (old.left === left && old.right === right ? old : { left, right }));
  }, [row]);

  useEffect(() => {
    if (!row) return;
    measure();
    row.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    // Auch die Einträge: Sie werden breiter, wenn etwa ein Abzeichen nachlädt, die Leiste selbst bleibt gleich breit.
    for (const el of [row, ...Array.from(row.children)]) observer?.observe(el);
    return () => {
      row.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [row, measure]);

  useEffect(() => {
    const item = row?.querySelector<HTMLElement>('[aria-selected="true"], [aria-current="page"]');
    if (!row || !item) return;
    // Nur die Leiste waagerecht schieben, nicht die Seite: scrollIntoView würde auch senkrecht springen.
    const box = row.getBoundingClientRect();
    const own = item.getBoundingClientRect();
    // Ein Eintrag im ausgeblendeten Rand zählt als verdeckt.
    if (own.left < box.left + FADE_PX || own.right > box.right - FADE_PX) {
      row.scrollLeft += own.left - box.left - (box.width - own.width) / 2;
    }
    measure();
  }, [active, row, measure]);

  return { ref: setRow, row, edges };
}

/** Blendet die Leiste an einem Rand aus, an dem noch etwas liegt. */
export function fadeStyle(edges: Edges): CSSProperties | undefined {
  if (!edges.left && !edges.right) return undefined;
  const mask = `linear-gradient(to right, ${edges.left ? `transparent, #000 ${FADE_PX}px` : "#000"}, ${
    edges.right ? `#000 calc(100% - ${FADE_PX}px), transparent` : "#000"
  })`;
  return { maskImage: mask, WebkitMaskImage: mask };
}

/** Pfeile über den ausgeblendeten Rändern; das Elternelement braucht `relative`. */
export function ScrollArrows({ edges, row }: { edges: Edges; row: HTMLElement | null }): JSX.Element {
  function go(direction: -1 | 1) {
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row?.scrollBy({ left: direction * row.clientWidth * 0.7, behavior: calm ? "auto" : "smooth" });
  }
  const arrow = "absolute top-0 bottom-0 flex w-8 items-center text-white/70 hover:text-white";
  return (
    <>
      {edges.left && (
        <button
          type="button" tabIndex={-1} aria-label="Nach links blättern" onClick={() => go(-1)}
          className={`${arrow} left-0 justify-start`}
        >
          <Icon name="chevron-right" size={16} className="rotate-180" />
        </button>
      )}
      {edges.right && (
        <button
          type="button" tabIndex={-1} aria-label="Nach rechts blättern" onClick={() => go(1)}
          className={`${arrow} right-0 justify-end`}
        >
          <Icon name="chevron-right" size={16} />
        </button>
      )}
    </>
  );
}
