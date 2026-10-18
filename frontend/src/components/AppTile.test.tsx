import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ServiceOut } from "../lib/overview";
import { AppTile } from "./AppTile";

const service = (url: string | null): ServiceOut => ({
  id: "s1", name: "grafana", host: "docker", host_id: "h1", state: "running", tone: "good", url, image: "grafana/grafana",
});

describe("AppTile", () => {
  it("verlinkt eine Webadresse in einem neuen Tab", () => {
    render(<AppTile service={service("http://10.0.0.5:3000")} />);
    const tile = screen.getByTestId("app-s1");
    expect(tile.tagName).toBe("A");
    expect(tile).toHaveAttribute("href", "http://10.0.0.5:3000");
    expect(tile).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each(["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "//evil.example/x", " JavaScript:alert(1)"])(
    "macht aus %s keinen Link",
    (url) => {
      render(<AppTile service={service(url)} />);
      const tile = screen.getByTestId("app-s1");
      expect(tile.tagName).toBe("DIV");
      expect(tile).not.toHaveAttribute("href");
      expect(tile).toHaveAttribute("title", "Die Adresse dieses Dienstes ist nicht gültig.");
    },
  );

  it("ohne Adresse: kein Link, Hinweis auf die fehlende Oberfläche", () => {
    render(<AppTile service={service(null)} />);
    expect(screen.getByTestId("app-s1")).toHaveAttribute("title", "Keine Web-Oberfläche erkannt");
  });
});
