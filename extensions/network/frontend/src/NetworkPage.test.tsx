import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NetworkPage } from "./NetworkPage";

describe("Netzwerk", () => {
  it("verweist auf die beiden Module, die den Inhalt übernommen haben", () => {
    render(<NetworkPage />);
    const moved = within(screen.getByTestId("moved"));
    expect(moved.getByRole("link", { name: "Pi-hole öffnen" })).toHaveAttribute("href", "/ext/pihole/pihole");
    expect(moved.getByRole("link", { name: "Nginx Proxy Manager öffnen" })).toHaveAttribute("href", "/ext/npm/npm");
    expect(moved.getAllByRole("link", { name: "Einstellungen" }).map((a) => a.getAttribute("href"))).toEqual([
      "/settings/extensions/pihole", "/settings/extensions/npm",
    ]);
  });
});
