import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { browserTimeZone, setDeckTimezone } from "../lib/deckTimezone";
import { SchedulePicker, describeSchedule, nextRun, parseCron, toCron } from "./SchedulePicker";

describe("Zeitplan", () => {
  it("übersetzt Cron in Klartext und zurück", () => {
    expect(describeSchedule("0 2 * * *")).toBe("Täglich um 02:00");
    expect(describeSchedule("30 3 * * 0")).toBe("Jeden Sonntag um 03:30");
    expect(describeSchedule("0 6 * * 1,2,3,4,5")).toBe("Werktags um 06:00");
    expect(describeSchedule("15 * * * *")).toBe("Stündlich um :15");
    expect(describeSchedule("0 4 1 * *")).toBe("Monatlich am 1. um 04:00");
    expect(describeSchedule("*/5 * * * *")).toBe("Eigener Zeitplan (*/5 * * * *)");
    expect(describeSchedule("")).toBe("Manuell");
    for (const c of ["0 2 * * *", "30 3 * * 0", "0 6 * * 1,3,5", "0 4 1 * *", "5 * * * *"]) {
      expect(toCron(parseCron(c))).toBe(c);
    }
  });

  it("berechnet den nächsten Lauf", () => {
    const from = new Date(2026, 8, 25, 22, 0); // Freitag
    expect(nextRun("30 3 * * 0", from)?.toString()).toBe(new Date(2026, 8, 27, 3, 30).toString());
    expect(nextRun("0 2 * * *", from)?.toString()).toBe(new Date(2026, 8, 26, 2, 0).toString());
    expect(nextRun("*/5 * * * *", from)).toBeNull();
  });

  it("Wecker-Bedienung: Häufigkeit, Uhrzeit und Wochentage", () => {
    const onChange = vi.fn();
    render(<SchedulePicker value="0 2 * * *" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Wöchentlich" }));
    expect(onChange).toHaveBeenLastCalledWith("0 2 * * 0");
    fireEvent.click(screen.getByRole("button", { name: "Mittwoch" }));
    expect(onChange).toHaveBeenLastCalledWith("0 2 * * 0,3");
    fireEvent.change(screen.getByLabelText("Minute"), { target: { value: "45" } });
    expect(onChange).toHaveBeenLastCalledWith("45 2 * * 0,3");
    expect(screen.getByText("Mi, So um 02:45")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Eigener" }));
    fireEvent.change(screen.getByLabelText("Cron-Ausdruck"), { target: { value: "*/10 * * * *" } });
    expect(onChange).toHaveBeenLastCalledWith("*/10 * * * *");
  });
});

describe("Zeitplan in der Zeitzone des Dashboards", () => {
  // Die Kern-Shell legt window.__lattice an (main.tsx); hier nur die Zeitzone daraus.
  beforeAll(() => {
    window.__lattice ??= {} as Window["__lattice"];
  });
  afterEach(() => setDeckTimezone(null));

  // Freitag 25.09.2026 20:00 UTC = 22:00 in Berlin (Sommerzeit, +2) = Samstag 05:00 in Tokio (+9).
  const from = new Date(Date.UTC(2026, 8, 25, 20, 0));

  it("rechnet den nächsten Lauf in der eingestellten Zone, unabhängig vom Browser", () => {
    // Sonntag 03:30 Berlin = 01:30 UTC
    expect(nextRun("30 3 * * 0", from, "Europe/Berlin")?.toISOString()).toBe("2026-09-27T01:30:00.000Z");
    // Sonntag 03:30 in Tokio = Samstag 18:30 UTC
    expect(nextRun("30 3 * * 0", from, "Asia/Tokyo")?.toISOString()).toBe("2026-09-26T18:30:00.000Z");
    // Täglich 02:00: Berlin am Samstag 00:00 UTC, Tokio erst am Sonntag (Samstag 02:00 ist dort vorbei: 05:00)
    expect(nextRun("0 2 * * *", from, "Europe/Berlin")?.toISOString()).toBe("2026-09-26T00:00:00.000Z");
    expect(nextRun("0 2 * * *", from, "Asia/Tokyo")?.toISOString()).toBe("2026-09-26T17:00:00.000Z");
    // Stündlich: nächste volle Stunde, dazu in Zonen mit halbem Versatz die Minute der Wanduhr
    expect(nextRun("0 * * * *", from, "Asia/Kolkata")?.toISOString()).toBe("2026-09-25T20:30:00.000Z");
    // Monatlich am 1.: Der Monatswechsel liegt in Tokio schon im Oktober
    expect(nextRun("0 0 1 * *", new Date(Date.UTC(2026, 8, 30, 14, 59)), "Asia/Tokyo")?.toISOString())
      .toBe("2026-09-30T15:00:00.000Z");
  });

  it("kommt mit der Zeitumstellung klar", () => {
    // Nacht auf Sonntag 25.10.2026: um 03:00 Sommerzeit springt die Uhr auf 02:00 zurück.
    const before = new Date(Date.UTC(2026, 9, 24, 12, 0));
    expect(nextRun("0 3 * * *", before, "Europe/Berlin")?.toISOString()).toBe("2026-10-25T02:00:00.000Z");
    // Die doppelte Stunde 02:30 läuft einmal, zur früheren Zeit (Sommerzeit, 00:30 UTC).
    expect(nextRun("30 2 * * *", before, "Europe/Berlin")?.toISOString()).toBe("2026-10-25T00:30:00.000Z");
    // Sprung auf Sommerzeit am 29.03.2026: 02:30 gibt es nicht, der Lauf entfällt an diesem Tag.
    const spring = new Date(Date.UTC(2026, 2, 28, 12, 0));
    expect(nextRun("30 2 * * *", spring, "Europe/Berlin")?.toISOString()).toBe("2026-03-30T00:30:00.000Z");
    // Ein Lauf liegt immer nach dem Ausgangszeitpunkt.
    expect(nextRun("30 2 * * *", spring, "Europe/Berlin")!.getTime()).toBeGreaterThan(spring.getTime());
  });

  it("ohne Zone oder in der Zone des Geräts gilt unverändert die lokale Zeit", () => {
    const local = new Date(2026, 8, 25, 22, 0);
    const expected = new Date(2026, 8, 27, 3, 30).toString();
    expect(nextRun("30 3 * * 0", local)?.toString()).toBe(expected);
    expect(nextRun("30 3 * * 0", local, null)?.toString()).toBe(expected);
    expect(nextRun("30 3 * * 0", local, browserTimeZone())?.toString()).toBe(expected);
    expect(nextRun("*/5 * * * *", from, "Asia/Tokyo")).toBeNull();
  });

  it("zeigt den Hinweis nur, wenn das Gerät in einer anderen Zone steht", () => {
    const other = browserTimeZone() === "Asia/Tokyo" ? "Europe/Berlin" : "Asia/Tokyo";
    render(<SchedulePicker value="0 2 * * *" onChange={() => undefined} />);
    expect(screen.queryByText(/Uhrzeiten in/)).not.toBeInTheDocument();

    act(() => setDeckTimezone(browserTimeZone()));
    expect(screen.queryByText(/Uhrzeiten in/)).not.toBeInTheDocument();

    act(() => setDeckTimezone(other));
    expect(screen.getByText(`Uhrzeiten in ${other}`)).toBeInTheDocument();
    // Der nächste Lauf steht in der Zone des Dashboards: täglich 02:00 -> "02:00".
    expect(screen.getByText(/nächster Lauf .*02:00/)).toBeInTheDocument();

    act(() => setDeckTimezone(null));
    expect(screen.queryByText(/Uhrzeiten in/)).not.toBeInTheDocument();
  });
});
