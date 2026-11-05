import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fadeStyle, ScrollArrows, useScrollRow } from "./scrollRow";

function Bar({ active }: { active: string }) {
  const { ref, row, edges } = useScrollRow<HTMLDivElement>(active);
  return (
    <div className="relative">
      <div ref={ref} role="tablist" data-testid="bar" style={fadeStyle(edges)}>
        {["a", "b", "c"].map((id) => (
          <button key={id} type="button" role="tab" aria-selected={id === active} data-id={id}>{id}</button>
        ))}
      </div>
      <ScrollArrows edges={edges} row={row} />
    </div>
  );
}

function widths(bar: HTMLElement, scrollWidth: number, clientWidth: number) {
  Object.defineProperty(bar, "scrollWidth", { configurable: true, value: scrollWidth });
  Object.defineProperty(bar, "clientWidth", { configurable: true, value: clientWidth });
}

function scrollTo(bar: HTMLElement, left: number) {
  bar.scrollLeft = left;
  fireEvent.scroll(bar);
}

/** Lage der Leiste (300 px breit) und einzelner Reiter auf dem Bildschirm. */
function boxes(items: Record<string, { left: number; width: number }>) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const box = items[this.dataset.id ?? ""] ?? { left: 0, width: 300 };
    return { ...box, right: box.left + box.width, top: 0, bottom: 30, height: 30, x: box.left, y: 0, toJSON() {} };
  });
}

afterEach(() => vi.restoreAllMocks());

describe("fadeStyle", () => {
  it("blendet nur die Ränder aus, an denen noch etwas liegt", () => {
    expect(fadeStyle({ left: false, right: false })).toBeUndefined();
    expect(fadeStyle({ left: false, right: true })?.maskImage).toBe(
      "linear-gradient(to right, #000, #000 calc(100% - 40px), transparent)",
    );
    expect(fadeStyle({ left: true, right: false })?.maskImage).toBe("linear-gradient(to right, transparent, #000 40px, #000)");
    const both = fadeStyle({ left: true, right: true });
    expect(both?.maskImage).toBe("linear-gradient(to right, transparent, #000 40px, #000 calc(100% - 40px), transparent)");
    expect(both?.WebkitMaskImage).toBe(both?.maskImage);
  });
});

describe("useScrollRow", () => {
  it("ohne Überlauf keine Pfeile", () => {
    render(<Bar active="a" />);
    expect(screen.queryByRole("button", { name: /blättern/ })).toBeNull();
  });

  it("zeigt je Rand, ob dort noch etwas liegt", () => {
    render(<Bar active="a" />);
    const bar = screen.getByTestId("bar");
    widths(bar, 600, 300);

    scrollTo(bar, 0);
    expect(screen.getByRole("button", { name: "Nach rechts blättern" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nach links blättern" })).toBeNull();

    scrollTo(bar, 150);
    expect(screen.getAllByRole("button", { name: /blättern/ })).toHaveLength(2);

    scrollTo(bar, 300);
    expect(screen.getByRole("button", { name: "Nach links blättern" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nach rechts blättern" })).toBeNull();
  });

  it("ein Pfeil blättert um gut eine halbe Breite weiter", () => {
    render(<Bar active="a" />);
    const bar = screen.getByTestId("bar");
    widths(bar, 600, 300);
    const scrollBy = vi.fn();
    bar.scrollBy = scrollBy as unknown as typeof bar.scrollBy;
    scrollTo(bar, 0);

    fireEvent.click(screen.getByRole("button", { name: "Nach rechts blättern" }));
    expect(scrollBy).toHaveBeenCalledWith({ left: 210, behavior: "smooth" });
  });

  it("holt den aktiven Reiter in die Mitte, ohne die Seite zu scrollen", () => {
    boxes({ c: { left: 400, width: 80 } });
    const intoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = intoView;
    try {
      render(<Bar active="c" />);
      expect(screen.getByTestId("bar").scrollLeft).toBe(290);
      expect(intoView).not.toHaveBeenCalled();
    } finally {
      delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
    }
  });

  it("lässt die Leiste stehen, wenn der aktive Reiter schon zu sehen ist", () => {
    boxes({ b: { left: 150, width: 80 } });
    render(<Bar active="b" />);
    expect(screen.getByTestId("bar").scrollLeft).toBe(0);
  });

  it("ein Reiter im ausgeblendeten Rand gilt als verdeckt und rückt in die Mitte", () => {
    boxes({ b: { left: 270, width: 20 } });
    render(<Bar active="b" />);
    expect(screen.getByTestId("bar").scrollLeft).toBe(130);
  });
});
