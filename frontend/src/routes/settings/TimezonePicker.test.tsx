import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { filterTimezones, orderTimezones, TimezonePicker, timezoneOptions } from "./TimezonePicker";
import { foldForSearch, germanZoneLabel } from "./timezoneNames";

const ZONES = timezoneOptions(["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London", "Europe/Paris", "America/New_York", "Asia/Tokyo", "Africa/Abidjan"]);

describe("Zeitzonen-Suche mit deutschen Namen", () => {
  it.each([
    ["Wien", "Europe/Vienna"],
    ["wien", "Europe/Vienna"],
    ["Österreich", "Europe/Vienna"],
    ["oesterreich", "Europe/Vienna"],
    ["Zürich", "Europe/Zurich"],
    ["zuerich", "Europe/Zurich"],
    ["Schweiz", "Europe/Zurich"],
    ["München", "Europe/Berlin"],
    ["muenchen", "Europe/Berlin"],
    ["Deutschland", "Europe/Berlin"],
    ["Großbritannien", "Europe/London"],
    ["grossbritannien", "Europe/London"],
    ["Frankreich", "Europe/Paris"],
    ["USA Ostküste", "America/New_York"],
    ["Japan", "Asia/Tokyo"],
    ["Tokio", "Asia/Tokyo"],
  ])("„%s“ findet %s", (query, zone) => {
    expect(filterTimezones(ZONES, query)).toContain(zone);
  });

  it("„Mitteleuropa“ findet die Zonen der Mitteleuropäischen Zeit, nicht London oder Tokio", () => {
    const found = filterTimezones(ZONES, "Mitteleuropa");
    expect(found).toEqual(expect.arrayContaining(["Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/Paris"]));
    expect(found).not.toContain("Europe/London");
    expect(found).not.toContain("Asia/Tokyo");
  });

  it("englische Namen gehen weiter, Unsinn findet nichts", () => {
    expect(filterTimezones(ZONES, "vienna")).toEqual(["Europe/Vienna"]);
    expect(filterTimezones(ZONES, "america new york")).toEqual(["America/New_York"]);
    expect(filterTimezones(ZONES, "gibt-es-nicht")).toEqual([]);
  });

  it("ein Suchwort steht am Wortanfang: „Wien“ findet nicht „Moldawien“", () => {
    const zones = timezoneOptions(["Europe/Chisinau", "Europe/Vienna"]);
    expect(filterTimezones(zones, "Wien")).toEqual(["Europe/Vienna"]);
  });

  it("Vereinfachung: Umlaute, ß und Ersatzschreibung", () => {
    expect(foldForSearch("Zürich")).toBe(foldForSearch("Zuerich"));
    expect(foldForSearch("Großbritannien")).toBe("grossbritannien");
    expect(foldForSearch("America/New_York")).toBe("america new york");
  });

  it("ohne Suche stehen die gewählte und die gängigen Zonen vor „Africa/Abidjan“", () => {
    const ordered = orderTimezones(ZONES, "Asia/Tokyo");
    expect(ordered.slice(0, 3)).toEqual(["Asia/Tokyo", "Europe/Berlin", "Europe/Vienna"]);
    expect(ordered.indexOf("Europe/Zurich")).toBeLessThan(ordered.indexOf("Africa/Abidjan"));
    expect(ordered).toHaveLength(ZONES.length);
    expect(new Set(ordered).size).toBe(ordered.length);
  });

  it("deutsche Beschriftung: Land und wichtigste Stadt", () => {
    expect(germanZoneLabel("Europe/Vienna")).toBe("Österreich, Wien");
    expect(germanZoneLabel("Africa/Abidjan")).toBeNull();
  });
});

describe("TimezonePicker", () => {
  it("„Wien“ in das Suchfeld tippen zeigt Europe/Vienna mit deutscher Beschriftung", () => {
    const onChange = vi.fn();
    render(<TimezonePicker value="Europe/Berlin" savedZone="Europe/Berlin" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Zeitzone suchen"), { target: { value: "Wien" } });
    const options = within(screen.getByRole("listbox", { name: "Zeitzonen" })).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toContain("Europe/Vienna");
    expect(options[0].textContent).toContain("Österreich, Wien");
    expect(screen.queryByText("Keine Zeitzone gefunden.")).toBeNull();
    fireEvent.click(within(options[0]).getByRole("button"));
    expect(onChange).toHaveBeenCalledWith("Europe/Vienna");
  });

  it("ohne Suche steht die gewählte Zone oben, danach Wien und Zürich statt Afrika", () => {
    render(<TimezonePicker value="Europe/Berlin" savedZone="Europe/Berlin" onChange={() => {}} />);
    const options = within(screen.getByRole("listbox", { name: "Zeitzonen" })).getAllByRole("option");
    expect(options[0].textContent).toMatch(/^Europe\/Berlin/);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    const names = options.map((o) => o.textContent ?? "");
    expect(names.findIndex((n) => n.startsWith("Europe/Vienna"))).toBeLessThan(10);
    expect(names.findIndex((n) => n.startsWith("Europe/Zurich"))).toBeLessThan(10);
    expect(names[0]).not.toMatch(/^Africa\/Abidjan/);
  });
});
