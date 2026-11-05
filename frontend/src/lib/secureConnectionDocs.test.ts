import { describe, expect, it } from "vitest";

import { guideUrl } from "./docs";

const nodeFs = "node:" + "fs";
const fs = (await import(/* @vite-ignore */ nodeFs)) as {
  readFileSync: (file: URL, encoding: "utf-8") => string;
};
const here = (relative: string) => new URL(relative, import.meta.url);

describe("Anleitung zum geschützten Zugriff", () => {
  it.each([
    ["11-ERST-EINRICHTUNG.md", "Verbindung absichern", "12-REVERSE-PROXY.md"],
    ["en/FIRST-SETUP.md", "Secure your connection", "REVERSE-PROXY.md"],
  ])("erklärt HTTPS und VPN in %s", (file, heading, proxyGuide) => {
    const guide = fs.readFileSync(here(`../../../docs/${file}`), "utf-8");
    expect(guide).toContain(`### ${heading}\n`);
    const section = guide.split(`### ${heading}\n`)[1].split("\n##")[0];
    for (const term of ["HTTPS", "Nginx Proxy Manager", "Caddy", "Tailscale", "VPN", "system.trusted_proxies", proxyGuide]) {
      expect(section).toContain(term);
    }
  });

  it("verlinkt den neuen Abschnitt", () => {
    expect(guideUrl("secureConnection")).toBe("https://github.com/nodvard/deck/blob/main/docs/11-ERST-EINRICHTUNG.md#verbindung-absichern");
  });
});
