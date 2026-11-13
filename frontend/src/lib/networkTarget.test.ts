import { describe, expect, it } from "vitest";

import { leavesLocalNetwork } from "./networkTarget";

describe("Netzwerkhinweis", () => {
  it.each([
    "http://localhost:11434", "https://demo.localhost.", "http://127.8.2.3", "http://10.0.0.1",
    "http://172.16.0.1", "http://172.31.255.254", "http://192.168.0.1", "http://169.254.4.2",
    "http://100.64.0.1", "http://100.127.255.254", "http://[::1]", "http://[fd12::1]", "http://[fe80::1]",
    "http://[::ffff:192.168.0.1]", "http://[::ffff:127.0.0.1]",
  ])("bleibt bei %s aus", (url) => {
    expect(leavesLocalNetwork(url)).toBe(false);
  });

  it("erkennt den privaten DNS-Suffix unabhängig von Großschreibung und Abschlusspunkt", () => {
    const suffix = ["ts", "net"].join(".");
    expect(leavesLocalNetwork(`https://demo.${suffix.toUpperCase()}.`)).toBe(false);
    expect(leavesLocalNetwork(`https://demo.${suffix}.invalid`)).toBe(true);
    expect(leavesLocalNetwork(`https://demo-${suffix}`)).toBe(true);
  });

  it.each([
    "https://203.0.113.10/v1", "https://api.invalid/v1", "http://172.32.0.1", "http://100.128.0.1",
    "https://[2001:db8::1]/v1", "https://[::ffff:203.0.113.10]/v1", "https://localhost@api.invalid/v1",
    "https://demo.local/v1", "https://demo/v1",
  ])("warnt bei %s", (url) => {
    expect(leavesLocalNetwork(url)).toBe(true);
  });

  it.each([undefined, null, "", "keine Adresse", "ftp://api.invalid"]) ("ignoriert fehlende oder ungültige Adressen", (url) => {
    expect(leavesLocalNetwork(url)).toBe(false);
  });
});
