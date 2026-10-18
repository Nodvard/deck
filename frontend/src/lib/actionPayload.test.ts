import { describe, expect, it } from "vitest";

import { buildActionPayload, parseNumberInput } from "./actionPayload";

const PROPS = {
  cores: { type: "integer", title: "Kerne" },
  ratio: { type: "number", title: "Faktor" },
  name: { type: "string" },
  frei: {},
};

describe("parseNumberInput", () => {
  it("ganze Zahlen: nur Ziffern, mit Vorzeichen und Leerraum drumherum", () => {
    expect(parseNumberInput("4", "integer")).toEqual({ value: 4 });
    expect(parseNumberInput("  -12 ", "integer")).toEqual({ value: -12 });
    expect(parseNumberInput("+7", "integer")).toEqual({ value: 7 });
    expect(parseNumberInput("007", "integer")).toEqual({ value: 7 });
    expect(Object.is((parseNumberInput("-0", "integer") as { value: number }).value, 0)).toBe(true);
  });

  it.each(["abc", "1.5", "1,5", "3.0", "1e3", "0x10", "", " ", "4 5", "--1", "٣"])("ganze Zahl: %j ist keine", (text) => {
    expect(parseNumberInput(text, "integer")).toEqual({ problem: "format" });
  });

  it("Dezimalzahlen: Komma und Punkt, auch ohne Ziffer vor dem Trenner", () => {
    expect(parseNumberInput("1,5", "number")).toEqual({ value: 1.5 });
    expect(parseNumberInput("1.5", "number")).toEqual({ value: 1.5 });
    expect(parseNumberInput("-0,25", "number")).toEqual({ value: -0.25 });
    expect(parseNumberInput(",5", "number")).toEqual({ value: 0.5 });
    expect(parseNumberInput("3", "number")).toEqual({ value: 3 });
  });

  it.each(["abc", "1,2,3", "1.000,5", "1.", "1e3", "Infinity", "NaN", "0x10", ".", ","])("Zahl: %j ist keine", (text) => {
    expect(parseNumberInput(text, "number")).toEqual({ problem: "format" });
  });

  it("zu große Zahlen: nicht endlich oder nicht genau darstellbar", () => {
    expect(parseNumberInput("9".repeat(400), "number")).toEqual({ problem: "range" });
    expect(parseNumberInput("9".repeat(400), "integer")).toEqual({ problem: "range" });
    expect(parseNumberInput("9007199254740993", "integer")).toEqual({ problem: "range" });
    expect(parseNumberInput("9007199254740991", "integer")).toEqual({ value: 9007199254740991 });
  });
});

describe("buildActionPayload", () => {
  it("wandelt nach dem Typ des Feldes: Zahl bleibt Zahl, Text bleibt Text", () => {
    const result = buildActionPayload(["cores", "ratio", "name", "frei"], { cores: "4", ratio: "1,5", name: "web-1", frei: "x y" }, PROPS);
    expect(result).toEqual({ ok: true, payload: { cores: 4, ratio: 1.5, name: "web-1", frei: "x y" } });
    if (result.ok) {
      expect(typeof result.payload.cores).toBe("number");
      expect(typeof result.payload.name).toBe("string");
    }
  });

  it("Text kommt unverändert an, auch ein Text, der wie eine Zahl aussieht", () => {
    expect(buildActionPayload(["name"], { name: " 42 " }, PROPS)).toEqual({ ok: true, payload: { name: " 42 " } });
    expect(buildActionPayload(["name"], { name: "42" }, PROPS)).toEqual({ ok: true, payload: { name: "42" } });
  });

  it("ohne Schema (Befehlsfeld, Schema fehlt) ist alles Text", () => {
    expect(buildActionPayload(["command"], { command: "ls -la" }, undefined)).toEqual({ ok: true, payload: { command: "ls -la" } });
    expect(buildActionPayload(["command"], { command: "5" }, null)).toEqual({ ok: true, payload: { command: "5" } });
  });

  it("leere Felder (auch nur Leerzeichen) fehlen im Ergebnis, egal welcher Typ", () => {
    const result = buildActionPayload(["cores", "ratio", "name", "frei", "nie-eingegeben"], { cores: "", ratio: "  ", name: "\t", frei: "" }, PROPS);
    expect(result).toEqual({ ok: true, payload: {} });
    if (result.ok) expect(Object.keys(result.payload)).toEqual([]);
    expect(buildActionPayload(["cores", "name"], { cores: "2", name: "" }, PROPS)).toEqual({ ok: true, payload: { cores: 2 } });
  });

  it("nur die genannten Felder gehen raus", () => {
    expect(buildActionPayload(["cores"], { cores: "2", name: "übrig" }, PROPS)).toEqual({ ok: true, payload: { cores: 2 } });
  });

  it("ungültiger Text: deutsche Meldung mit dem Namen des Feldes, nichts geht raus", () => {
    expect(buildActionPayload(["cores"], { cores: "abc" }, PROPS)).toEqual({
      ok: false, error: "Bitte bei „Kerne“ eine ganze Zahl eingeben (zum Beispiel 3).",
    });
    expect(buildActionPayload(["cores"], { cores: "1.5" }, PROPS)).toMatchObject({ ok: false });
    expect(buildActionPayload(["ratio"], { ratio: "viel" }, PROPS)).toEqual({
      ok: false, error: "Bitte bei „Faktor“ eine Zahl eingeben (zum Beispiel 1,5).",
    });
    expect(buildActionPayload(["ratio"], { ratio: "9".repeat(400) }, PROPS)).toEqual({ ok: false, error: "Die Zahl bei „Faktor“ ist zu groß." });
  });

  it("ohne title nennt die Meldung den Schlüssel; schon ein ungültiges Feld reicht", () => {
    const result = buildActionPayload(["retries", "name"], { retries: "x", name: "ok" }, { retries: { type: "integer" }, name: { type: "string" } });
    expect(result).toEqual({ ok: false, error: "Bitte bei „retries“ eine ganze Zahl eingeben (zum Beispiel 3)." });
  });
});
