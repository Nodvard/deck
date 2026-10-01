import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { checkSummary, fieldErrors, formatDate, splitCommands, type ConnectionCheck } from "./hosts";

function check(statuses: ConnectionCheck["items"][number]["status"][]): ConnectionCheck {
  return {
    ok: statuses.every((s) => s !== "fail" && s !== "confirm"),
    checked_at: "2026-09-30T10:00:00Z",
    items: statuses.map((status, i) => ({ id: `i${i}`, label: `Punkt ${i}`, status, detail: "", hint: "" })),
    host_key: null, os: null, credential_id: null,
  };
}

describe("fieldErrors", () => {
  it("ordnet die deutschen Meldungen des Backends ihrem Feld zu", () => {
    const err = new ApiError(422, "x", {
      detail: [
        { type: "value_error", loc: ["body", "name"], msg: "Kurzname: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen)." },
        { type: "value_error", loc: ["body", "address"], msg: "Adresse: nur IP-Adresse oder Rechnername, ohne http:// und ohne Port." },
      ],
    });
    expect(fieldErrors(err)).toEqual({
      name: "Kurzname: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen).",
      address: "Adresse: nur IP-Adresse oder Rechnername, ohne http:// und ohne Port.",
    });
  });

  it("liefert für alles andere (409, Text, kein Fehler) nichts", () => {
    expect(fieldErrors(new ApiError(409, "Kurzname schon vergeben", { detail: "Kurzname schon vergeben" }))).toEqual({});
    expect(fieldErrors(new Error("boom"))).toEqual({});
    expect(fieldErrors(null)).toEqual({});
  });

  it("nimmt bei Listenfeldern (tags.0) das Feld davor", () => {
    const err = new ApiError(422, "x", { detail: [{ loc: ["body", "tags"], msg: "Ungültige Markierung 'A B'." }] });
    expect(fieldErrors(err).tags).toBe("Ungültige Markierung 'A B'.");
    const err2 = new ApiError(422, "x", { detail: [{ loc: ["body", "tags", 0], msg: "zu lang" }] });
    expect(fieldErrors(err2).tags).toBe("zu lang");
  });
});

describe("checkSummary", () => {
  it("zählt die geprüften Punkte, übersprungene nicht", () => {
    expect(checkSummary(check(["ok", "ok", "warn", "skipped", "ok"]))).toBe("3 von 4 in Ordnung");
  });

  it("sagt bei einem neuen Schlüssel, dass er noch bestätigt werden muss", () => {
    expect(checkSummary(check(["ok", "confirm"]))).toBe("Server-Schlüssel noch nicht bestätigt");
  });

  it("sagt bei allem in Ordnung „Alles in Ordnung“", () => {
    expect(checkSummary(check(["ok", "ok"]))).toBe("Alles in Ordnung");
  });
});

describe("splitCommands", () => {
  it("erkennt Befehle in Anführungszeichen und den Fingerabdruck-Vergleich", () => {
    const parts = splitCommands("Läuft dort SSH? „sudo apt install openssh-server“ und mehr. Zum Vergleichen: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub");
    expect(parts.filter((p) => p.code).map((p) => p.text)).toEqual([
      "sudo apt install openssh-server",
      "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub",
    ]);
    expect(parts.map((p) => p.text).join("")).toContain("Läuft dort SSH? ");
  });

  it("lässt normalen Text in Anführungszeichen stehen", () => {
    expect(splitCommands("Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen.")).toEqual([
      { text: "Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen.", code: false },
    ]);
  });
});

describe("formatDate", () => {
  it("schreibt Tag.Monat.Jahr", () => {
    expect(formatDate("2026-09-30T12:00:00Z")).toBe("30.09.2026");
  });
  it("ist bei Unsinn still", () => {
    expect(formatDate("")).toBe("");
  });
});
