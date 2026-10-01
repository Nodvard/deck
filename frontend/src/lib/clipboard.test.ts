import { afterEach, describe, expect, it, vi } from "vitest";

import { COPY_FAILED_TEXT, copyText } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("copyText", () => {
  it("nimmt die Zwischenablage des Browsers, wenn es sie gibt", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    expect(await copyText("abc")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("abc");
  });

  it("fällt ohne Zwischenablage (http, kein sicherer Kontext) auf das Kopieren über ein Textfeld zurück", async () => {
    vi.stubGlobal("navigator", {});
    const exec = vi.fn(() => true);
    (document as unknown as { execCommand: unknown }).execCommand = exec;
    expect(await copyText("sudo sh -c 'x'")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    // Das Hilfsfeld ist danach wieder weg.
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("fällt auch zurück, wenn writeText abgelehnt wird", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    const exec = vi.fn(() => true);
    (document as unknown as { execCommand: unknown }).execCommand = exec;
    expect(await copyText("abc")).toBe(true);
    expect(exec).toHaveBeenCalled();
  });

  it("meldet false, wenn gar nichts geht – der Text dazu steht in COPY_FAILED_TEXT", async () => {
    vi.stubGlobal("navigator", {});
    (document as unknown as { execCommand: unknown }).execCommand = vi.fn(() => false);
    expect(await copyText("abc")).toBe(false);
    expect(COPY_FAILED_TEXT).toBe("Bitte den markierten Text kopieren (Strg+C bzw. lange drücken).");
  });
});
