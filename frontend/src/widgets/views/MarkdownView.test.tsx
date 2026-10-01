import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { MarkdownView as MarkdownViewSpec } from "../types";
import { MarkdownView } from "./MarkdownView";

const VIEW: MarkdownViewSpec = { kind: "markdown", content_field: "content" };

/**
 * Der vorherige handgerollte
 * Markdown-Parser kannte nur Ueberschrift/fett/kursiv/Inline-Code/Links/Absaetze --
 * dieser Test beweist die STRUKTUR der neuen markdown-it-Ausgabe (echte <ul>/<pre>/
 * <table>-Elemente), nicht nur den sichtbaren Text (das haette der alte Parser durch
 * seinen Absatz-Fallback auch bestanden).
 */
describe("MarkdownView", () => {
  it("rendert eine Liste als echtes <ul>/<li>, nicht als Absatz-Fallback", () => {
    const { container } = render(<MarkdownView view={VIEW} data={{ content: "- eins\n- zwei" }} />);
    const items = container.querySelectorAll("ul > li");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("eins");
  });

  it("rendert einen Codeblock als <pre><code>", () => {
    const { container } = render(<MarkdownView view={VIEW} data={{ content: "```\necho hallo\n```" }} />);
    expect(container.querySelector("pre code")).toHaveTextContent("echo hallo");
  });

  it("rendert eine GFM-Tabelle als echtes <table>", () => {
    const { container } = render(
      <MarkdownView view={VIEW} data={{ content: "| A | B |\n|---|---|\n| 1 | 2 |" }} />,
    );
    const cells = container.querySelectorAll("td");
    expect(Array.from(cells).map((c) => c.textContent)).toEqual(["1", "2"]);
  });

  it("escaped eingebettetes HTML statt es auszufuehren (XSS)", () => {
    const { container } = render(
      <MarkdownView view={VIEW} data={{ content: "Text <script>alert(1)</script> danach" }} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).toContain("&lt;script&gt;");
  });

  it("versieht Links mit target=_blank und rel=noreferrer", () => {
    const { container } = render(
      <MarkdownView view={VIEW} data={{ content: "[pve2](https://pve.home.example)" }} />,
    );
    const link = container.querySelector("a");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(link).toHaveAttribute("href", "https://pve.home.example");
  });
});
