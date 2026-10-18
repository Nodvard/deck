import { describe, expect, it } from "vitest";

import { minMax } from "./minMax";

describe("minMax", () => {
  it("liefert bei leerer Liste null", () => {
    expect(minMax([])).toBeNull();
  });

  it("liefert null, wenn nichts endlich ist", () => {
    expect(minMax([NaN, Infinity, -Infinity, null, undefined])).toBeNull();
  });

  it("überspringt ungültige Werte in gemischten Listen", () => {
    expect(minMax([3, NaN, 7, null, Infinity, -2, undefined, -Infinity])).toEqual({ min: -2, max: 7 });
  });

  it("kommt mit nur negativen Werten zurecht", () => {
    expect(minMax([-5, -1, -9])).toEqual({ min: -9, max: -1 });
  });

  it("kennt einen einzelnen Wert", () => {
    expect(minMax([4])).toEqual({ min: 4, max: 4 });
  });

  it("nimmt jedes Iterable", () => {
    expect(minMax(new Set([2, 8, 5]))).toEqual({ min: 2, max: 8 });
  });

  it("wirft bei 200 000 Werten nicht", () => {
    const big = Array.from({ length: 200_000 }, (_, i) => (i % 1000) - 500);
    big[123_456] = NaN;
    expect(minMax(big)).toEqual({ min: -500, max: 499 });
  });
});
