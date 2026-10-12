import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MoreMenu } from "./menu";

function setup() {
  const restart = vi.fn();
  const stop = vi.fn();
  render(
    <MoreMenu
      testId="more"
      items={[
        { id: "restart", label: "Neustarten", run: restart },
        { id: "stop", label: "Hart ausschalten", run: stop, danger: true },
        { id: "busy", label: "…", run: vi.fn(), disabled: true },
      ]}
    />,
  );
  return { menu: screen.getByTestId("more"), restart, stop };
}

/** Das Ereignis „toggle“ kommt (wie im Browser) erst im nächsten Takt; erst dann hört das Menü auf Klicks daneben. */
async function open(menu: HTMLElement) {
  fireEvent.click(screen.getByText("Mehr"));
  expect(menu).toHaveAttribute("open");
  await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
}

describe("MoreMenu", () => {
  it("eine Wahl führt die Aktion aus, schließt das Menü und gibt den Fokus an „Mehr“ zurück", async () => {
    const { menu, restart } = setup();
    await open(menu);
    fireEvent.click(screen.getByRole("button", { name: "Neustarten" }));
    expect(restart).toHaveBeenCalledOnce();
    expect(menu).not.toHaveAttribute("open");
    expect(document.activeElement?.tagName).toBe("SUMMARY");
  });

  it("riskante Punkte sind rot, gesperrte nicht wählbar", () => {
    setup();
    expect(screen.getByRole("button", { name: "Hart ausschalten" }).className).toContain("text-red-300");
    expect(screen.getByRole("button", { name: "Neustarten" }).className).not.toContain("red");
    expect(screen.getByRole("button", { name: "…" })).toBeDisabled();
  });

  it("schließt mit Escape und beim Antippen daneben", async () => {
    const { menu, restart, stop } = setup();
    await open(menu);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(menu).not.toHaveAttribute("open");
    expect(document.activeElement?.tagName).toBe("SUMMARY");

    await open(menu);
    fireEvent.pointerDown(document.body); // Handy: iOS schickt beim Antippen keine Mausereignisse
    expect(menu).not.toHaveAttribute("open");
    expect(restart).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it("schließt, wenn der Fokus das Menü verlässt (Tab-Taste), nicht beim Wechsel innerhalb", async () => {
    const { menu } = setup();
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    try {
      await open(menu);
      fireEvent.blur(screen.getByRole("button", { name: "Neustarten" }), { relatedTarget: screen.getByRole("button", { name: "Hart ausschalten" }) });
      expect(menu).toHaveAttribute("open");
      fireEvent.blur(screen.getByRole("button", { name: "Hart ausschalten" }), { relatedTarget: outside });
      expect(menu).not.toHaveAttribute("open");
    } finally {
      outside.remove();
    }
  });

  it("hat für Lesegeräte den sichtbaren Text und `title` im Namen", () => {
    render(<MoreMenu testId="m2" title="Weitere Aktionen für docker" items={[]} />);
    // Der sichtbare Text „Mehr“ steht vorne im Namen: so klappt auch die Sprachsteuerung („Klick auf Mehr“).
    expect(screen.getByTestId("m2").querySelector("summary")).toHaveAttribute("aria-label", "Mehr: Weitere Aktionen für docker");
  });
});
