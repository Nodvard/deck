import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fixtures, VIEW_KIND_COUNT } from "./__fixtures__";
import {
  ActionsView,
  ChartView,
  GaugeView,
  ListView,
  LogView,
  MarkdownView,
  StatView,
  StatusGridView,
  TableView,
} from "./views";

const NEEDS_EXT_CONTEXT = new Set(["list", "table", "actions"]);

/**
 * Renderer-Konformitaetstest:
 * jedes der 9 View-Kinds (docs/02-EXTENSION-API.md §4) bekommt eine feste Fixture und
 * muss die erwarteten Textfragmente rendern. `VIEW_KIND_COUNT` haelt fest, dass hier
 * ALLE Kinds abgedeckt sind -- ein zehntes Kind ohne Fixture faellt sofort auf.
 */
describe("Widget-Renderer-Konformitaet", () => {
  it("deckt alle dokumentierten View-Kinds ab", () => {
    expect(fixtures.length).toBe(VIEW_KIND_COUNT);
  });

  for (const fixture of fixtures) {
    it(`rendert "${fixture.name}" gemaess Fixture`, () => {
      const extProps = NEEDS_EXT_CONTEXT.has(fixture.name) ? { extId: "hello-world", onAction: () => {} } : {};

      switch (fixture.name) {
        case "stat":
          render(<StatView view={fixture.view as never} data={fixture.data} />);
          break;
        case "list":
          render(<ListView view={fixture.view as never} data={fixture.data} {...(extProps as { extId: string; onAction: () => void })} />);
          break;
        case "table":
          render(<TableView view={fixture.view as never} data={fixture.data} {...(extProps as { extId: string; onAction: () => void })} />);
          break;
        case "chart":
          render(<ChartView view={fixture.view as never} data={fixture.data} />);
          break;
        case "status_grid":
          render(<StatusGridView view={fixture.view as never} data={fixture.data} />);
          break;
        case "gauge":
          render(<GaugeView view={fixture.view as never} data={fixture.data} />);
          break;
        case "markdown":
          render(<MarkdownView view={fixture.view as never} data={fixture.data} />);
          break;
        case "actions":
          render(<ActionsView view={fixture.view as never} data={fixture.data} {...(extProps as { extId: string; onAction: () => void })} />);
          break;
        case "log":
          render(<LogView view={fixture.view as never} data={fixture.data} />);
          break;
        default:
          throw new Error(`Keine Renderer-Zuordnung fuer Fixture "${(fixture as { name: string }).name}"`);
      }

      for (const text of fixture.expectContains) {
        expect(document.body.textContent).toContain(text);
      }
      const notContains = (fixture as { expectNotContains?: string[] }).expectNotContains;
      if (notContains) {
        for (const text of notContains) {
          expect(document.body.innerHTML).not.toContain(text);
        }
      }
    });
  }
});

/**
 * Live-Test: Dashboard-Kacheln schnitten Namen ab -- `truncate`
 * auf Titeln plus eine an der VIEWPORT-Breite (`sm:grid-cols-3`) statt an der
 * Kartenbreite ausgerichtete Kachelzahl. jsdom misst kein Layout; diese Tests halten
 * deshalb den Vertrag fest, der den Fehler ausschliesst (kein Ellipsis-Abschneiden,
 * kein Viewport-Breakpoint im Kachelraster), damit er nicht still zurueckkehrt.
 */
describe("Dashboard-Kacheln schneiden keine Namen ab", () => {
  const LONG = "ein-sehr-langer-container-name_ohne_leerzeichen_1234567890";

  it("StatusGridView: volle Namen, Kachelzahl folgt der Kartenbreite statt dem Viewport", () => {
    const view = { kind: "status_grid", tile_title: "{{ name }}", tile_subtitle: "{{ host }}", tile_tone: "good" };
    const { container } = render(<StatusGridView view={view as never} data={[{ name: LONG, host: "Raspberry Pi" }]} />);

    const title = [...container.querySelectorAll("p")].find((p) => p.textContent === LONG);
    expect(title).toBeDefined();
    expect(title!.className).not.toContain("truncate");
    expect(title!.className).toContain("break-words");

    const grid = container.firstElementChild as HTMLElement;
    expect(grid.className).not.toMatch(/\bsm:grid-cols-/);
    expect(grid.className).toContain("auto-fill");
    // Live gefunden: ein fester 7.5rem-Mindest-Track ist breiter als die schmalste
    // Karte (~114px Inhalt) -> waagerechter Scrollbalken IN der Karte. `min(.., 100%)`
    // haelt eine einzelne Spalte immer innerhalb des Containers.
    expect(grid.className).toContain("minmax(min(7.5rem,100%)");
  });

  it("ListView: volle Titel, Badge/Knoepfe duerfen in eine eigene Zeile umbrechen", () => {
    const view = {
      kind: "list",
      item: { title: "{{ title }}", subtitle: "{{ sub }}", badge: { text: "{{ state }}", tone: "warn" }, actions: [] },
      empty_text: "leer",
      max_items: null,
    };
    const { container } = render(
      <ListView view={view as never} data={[{ title: LONG, sub: "Untertitel", state: "läuft" }]} extId="x" onAction={() => {}} />,
    );

    const title = [...container.querySelectorAll("p")].find((p) => p.textContent === LONG);
    expect(title).toBeDefined();
    expect(title!.className).not.toContain("truncate");
    const li = container.querySelector("li")!;
    expect(li.className).toContain("flex-wrap");
    // Der Badge-/Knopf-Block muss selbst umbrechen koennen -- mit `shrink-0` blieb er
    // in einer schmalen Karte breiter als die Karte (live gefunden: Mini-Scrollbalken).
    const actionBlock = li.lastElementChild as HTMLElement;
    expect(actionBlock.className).toContain("flex-wrap");
    expect(actionBlock.className).not.toContain("shrink-0");
  });
});

describe("StatusGridView: Links aus den Daten", () => {
  const view = { kind: "status_grid", tile_title: "{{ name }}", tile_tone: "good", tile_link: "{{ link }}" };

  it("verlinkt Webadressen und Pfade im Dashboard, sonst nichts", () => {
    const data = [
      { name: "web", link: "https://a.example/x" },
      { name: "intern", link: "/hosts/h1" },
      { name: "js", link: "javascript:alert(1)" },
      { name: "fremd", link: "//evil.example/x" },
      { name: "leer", link: "" },
    ];
    const { container } = render(<StatusGridView view={view as never} data={data} />);
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["https://a.example/x", "/hosts/h1"]);
    expect(container.textContent).toContain("js");
    expect(container.textContent).toContain("fremd");
  });
});

/**
 * Ruhiger: Pillen nur bei Warnung und Störung, „gut“ nur als grüner Punkt, Knöpfe schlicht
 * mit Farbe erst beim Darüberfahren.
 */
describe("Widgets ruhiger", () => {
  const listView = (tone: string, actions: object[] = []) => ({
    kind: "list",
    item: { title: "{{ title }}", subtitle: null, badge: { text: "{{ state }}", tone }, actions },
    empty_text: "leer",
    max_items: null,
  });

  it("Ton „good“: nur der Punkt, das Wort als Tooltip und für Screenreader", () => {
    const { container } = render(<ListView view={listView("good") as never} data={[{ title: "pve1", state: "gesund" }]} extId="x" onAction={() => {}} />);
    const word = [...container.querySelectorAll("span")].find((el) => el.textContent === "gesund");
    expect(word).toHaveClass("sr-only");
    expect(container.querySelector(".status-dot")).toHaveAttribute("title", "gesund");
    expect(container.querySelector(".rounded-full")).toBeNull();
  });

  it("Ton „neutral“: schlichter grauer Text, keine Pille; „warn“ und „danger“ bleiben Pillen", () => {
    const neutral = render(<ListView view={listView("neutral") as never} data={[{ title: "a", state: "unbekannt" }]} extId="x" onAction={() => {}} />);
    const plain = [...neutral.container.querySelectorAll("span")].find((el) => el.textContent === "unbekannt")!;
    expect(plain.className).not.toContain("rounded-full");
    expect(plain).not.toHaveClass("sr-only");
    neutral.unmount();

    for (const tone of ["warn", "danger"]) {
      const { container, unmount } = render(<ListView view={listView(tone) as never} data={[{ title: "a", state: "läuft ab" }]} extId="x" onAction={() => {}} />);
      const pill = [...container.querySelectorAll("span")].find((el) => el.textContent === "läuft ab")!;
      expect(pill.className).toContain("rounded-full");
      unmount();
    }
  });

  it("Knöpfe schlicht: keine gefüllte Akzent- oder Rotfläche, Farbe erst beim Darüberfahren", () => {
    const action = (id: string, style: string) => ({
      id, label: id, endpoint: "x", method: "POST", confirm: false, confirm_text: null, style, permissions: [], show_if: null, payload: null,
    });
    const view = listView("good", [action("primär", "primary"), action("gefährlich", "danger")]);
    const { getByRole } = render(<ListView view={view as never} data={[{ title: "a", state: "ok" }]} extId="x" onAction={() => {}} />);
    const primary = getByRole("button", { name: "primär" });
    const danger = getByRole("button", { name: "gefährlich" });
    expect(primary.className).not.toContain("accent-gradient");
    expect(primary.className).toContain("hover:text-[var(--color-accent-text)]");
    expect(danger.className).not.toMatch(/(^|\s)bg-red-/);
    expect(danger.className).toContain("hover:bg-red-500/15");
  });
});
