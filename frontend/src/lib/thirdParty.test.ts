/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

import { NOVNC_SOURCE_URL, NOVNC_VERSION, THIRD_PARTY_LICENSES_IMAGE_PATH, THIRD_PARTY_LICENSES_URL } from "./thirdParty";

// Das Kern-Projekt hat keine Node-Typen, darum geht `fs` ueber einen Import mit zusammengesetztem Namen
// (wie in styles/contract.test.ts).
const nodeFs = "node:" + "fs";
const { readFileSync } = (await import(/* @vite-ignore */ nodeFs)) as { readFileSync: (file: string, encoding: "utf-8") => string };
const readFromHere = (relative: string) => readFileSync(decodeURIComponent(new URL(relative, import.meta.url).pathname), "utf-8");

describe("Hinweise auf mitgelieferte Fremdsoftware", () => {
  it("die genannte noVNC-Version ist die, die package-lock.json wirklich einbaut", () => {
    const lock = JSON.parse(readFromHere("../../package-lock.json")) as { packages: Record<string, { version?: string }> };
    expect(NOVNC_VERSION).toBe(lock.packages["node_modules/@novnc/novnc"]?.version);
    expect(NOVNC_SOURCE_URL).toBe(`https://github.com/novnc/noVNC/tree/v${NOVNC_VERSION}`);
  });

  it("verweist auf die Datei im Quellcode und im Image (deploy/Dockerfile kopiert sie dorthin)", () => {
    expect(THIRD_PARTY_LICENSES_URL).toBe("https://github.com/nodvard/deck/blob/main/THIRD_PARTY_LICENSES");
    const dockerfile = readFromHere("../../../deploy/Dockerfile");
    expect(dockerfile).toMatch(new RegExp(`^COPY\\s+THIRD_PARTY_LICENSES\\s+${THIRD_PARTY_LICENSES_IMAGE_PATH}\\s*$`, "m"));
  });
});
