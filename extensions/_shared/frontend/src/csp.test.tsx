import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { createStyle, installStyleNonce, styleRef } from "./csp";

afterEach(() => {
  vi.restoreAllMocks();
  document.querySelector('meta[name="csp-nonce"]')?.remove();
});

it("setzt die Nonce vor dem Einhängen, auch bei Stylesheets von Bibliotheken", () => {
  const meta = document.createElement("meta");
  meta.name = "csp-nonce";
  meta.content = "antwort-nonce";
  document.head.append(meta);
  expect(createStyle().nonce).toBe("antwort-nonce");
  vi.spyOn(document, "createElement");
  installStyleNonce();
  expect(document.createElement("style").nonce).toBe("antwort-nonce");
  expect(document.createElement("script").nonce).toBe("");
});

it("erhält Maße, Farben und Refs ohne Stilattribute und räumt alte Regeln auf", () => {
  const ref = createRef<HTMLDivElement>();
  const view = (width: number) => <div data-testid="bar" ref={styleRef({ width, opacity: 0.5, background: "red" }, ref)} />;
  const { rerender, unmount } = render(view(25));
  const bar = screen.getByTestId("bar");
  expect(ref.current).toBe(bar);
  expect(bar).not.toHaveAttribute("style");
  expect(getComputedStyle(bar).width).toBe("25px");
  expect(getComputedStyle(bar).opacity).toBe("0.5");
  expect(getComputedStyle(bar).backgroundColor).toBe("rgb(255, 0, 0)");
  const count = document.querySelectorAll("style").length;
  rerender(view(50));
  expect(getComputedStyle(bar).width).toBe("50px");
  expect(document.querySelectorAll("style")).toHaveLength(count);
  unmount();
  expect(ref.current).toBeNull();
  expect(document.querySelectorAll("style")).toHaveLength(count - 1);
});

it("behandelt CSS-Werte als Eigenschaften und verhindert zusätzliche Regeln", () => {
  const node = document.createElement("div");
  const apply = styleRef({ color: "red; } body { display: none", height: 0 });
  apply(node);
  expect(node).not.toHaveAttribute("style");
  const sheet = document.head.querySelector<HTMLStyleElement>("style:last-of-type")!.sheet!;
  expect(sheet.cssRules).toHaveLength(1);
  expect((sheet.cssRules[0] as CSSStyleRule).style.color).toBe("");
  apply(null);
});
