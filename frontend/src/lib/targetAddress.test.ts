import { describe, expect, it } from "vitest";

import vectors from "../../../sdk/python/tests/vectors/same_target.json";
import { sameTarget, targetForm } from "./targetAddress";

// Dieselben Beispiele prueft `sdk/python/tests/test_addresses.py`: Anzeige und Server urteilen gleich.
describe("Zieladresse vergleichen", () => {
  it.each(vectors.forms)("Vergleichsform von %j ist %j", (value, expected) => {
    expect(targetForm(value)).toBe(expected);
  });

  it.each(vectors.same)("%j und %j sind dasselbe Ziel", (a, b) => {
    expect(sameTarget(a, b)).toBe(true);
    expect(sameTarget(b, a)).toBe(true);
  });

  it.each(vectors.different)("%j und %j sind verschieden", (a, b) => {
    expect(sameTarget(a, b)).toBe(false);
    expect(sameTarget(b, a)).toBe(false);
  });

  it("leere Werte sind alle gleich, andere Typen bleiben, wie sie sind", () => {
    expect(sameTarget(undefined, "")).toBe(true);
    expect(sameTarget(null, "  ")).toBe(true);
    expect(sameTarget(undefined, "http://a")).toBe(false);
    expect(targetForm(8080)).toBe(8080);
  });
});
