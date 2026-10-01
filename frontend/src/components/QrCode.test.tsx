import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QrCode, qrPath } from "./QrCode";

describe("QrCode", () => {
  it("zeichnet einen QR-Code als SVG mit weissem Grund und Beschriftung", () => {
    render(<QrCode value="otpauth://totp/Nodvard%20Deck:nico?secret=ABCDEFGHIJKLMNOP&issuer=Nodvard%20Deck" label="QR-Code für die App" />);
    const svg = screen.getByRole("img", { name: "QR-Code für die App" });
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg.querySelector("rect")).toHaveAttribute("fill", "#ffffff");
    expect(svg.querySelector("path")?.getAttribute("d")?.length ?? 0).toBeGreaterThan(200);
  });

  it("liefert für denselben Text immer dasselbe Bild, für anderen Text ein anderes", () => {
    const a = qrPath("otpauth://totp/a?secret=AAAA");
    expect(qrPath("otpauth://totp/a?secret=AAAA")).toEqual(a);
    expect(qrPath("otpauth://totp/a?secret=BBBB")?.path).not.toEqual(a?.path);
  });

  it("Version 1 ist 21 Module breit, plus Ruhezone von je 4 Modulen", () => {
    expect(qrPath("A")?.size).toBe(21 + 8);
  });

  it("zeigt nichts, wenn der Text für einen QR-Code zu lang ist", () => {
    expect(qrPath("x".repeat(5000))).toBeNull();
    const { container } = render(<QrCode value={"x".repeat(5000)} label="zu lang" />);
    expect(container).toBeEmptyDOMElement();
  });
});
