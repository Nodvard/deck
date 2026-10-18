import { describe, expect, it } from "vitest";

import { safeHref } from "./links";

describe("safeHref", () => {
  it("gibt nur http(s) als Link frei", () => {
    expect(safeHref("http://192.168.2.1:8096")).toBe("http://192.168.2.1:8096");
    expect(safeHref(" https://a.example/x ")).toBe("https://a.example/x");
    expect(safeHref("javascript:alert(1)")).toBeUndefined();
    expect(safeHref(" JavaScript:alert(1)")).toBeUndefined();
    expect(safeHref("data:text/html,x")).toBeUndefined();
    expect(safeHref("//evil.example")).toBeUndefined();
    expect(safeHref("http://")).toBeUndefined();
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref(undefined)).toBeUndefined();
    expect(safeHref("")).toBeUndefined();
  });
});
