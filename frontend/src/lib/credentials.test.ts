import { describe, expect, it } from "vitest";

import { emailProblem, passwordProblem, usernameProblem } from "./credentials";
import { AUTH_FIELD_LABELS, validationText } from "./validation";

describe("usernameProblem", () => {
  it.each(["admin", "Kollege", "  nico  ", "max.mustermann", "a-b_c9", "007"])("%j ist in Ordnung", (name) => {
    expect(usernameProblem(name)).toBeNull();
  });

  it.each(["", "ko", "  ab  "])("%j ist zu kurz", (name) => {
    expect(usernameProblem(name)).toBe("Benutzername: Mindestens 3 Zeichen.");
  });

  it.each(["Kollege Max", "kollege max", "a b c", "mäx", "-max", ".max", "max@home", "max/x"])("%j wird abgelehnt (Sonderzeichen oder Leerzeichen)", (name) => {
    const problem = usernameProblem(name);
    expect(problem).toMatch(/^Benutzername: Nur Kleinbuchstaben, Ziffern sowie \. - und _ erlaubt, ohne Leerzeichen/);
  });
});

describe("passwordProblem", () => {
  it("verlangt mindestens 8 Zeichen", () => {
    expect(passwordProblem("kurz")).toBe("Passwort: Mindestens 8 Zeichen.");
    expect(passwordProblem("1234567")).toBe("Passwort: Mindestens 8 Zeichen.");
    expect(passwordProblem("12345678")).toBeNull();
    expect(passwordProblem("mit leerzeichen drin")).toBeNull();
  });
});

describe("emailProblem", () => {
  it.each(["", "   ", "name@beispiel.de", "  name@beispiel.de  ", "a@b", "vor.nach+tag@mail.example.org"])("%j ist in Ordnung (leer ist erlaubt)", (mail) => {
    expect(emailProblem(mail)).toBeNull();
  });

  it.each(["kein-email", "name@", "@beispiel.de", "a b@beispiel.de", "name@bei spiel.de", "a@@b", "a@b@c"])("%j wird abgelehnt", (mail) => {
    expect(emailProblem(mail)).toBe("E-Mail: Das sieht nicht nach einer Adresse aus. Sie braucht ein @, z. B. name@beispiel.de.");
  });
});

describe("validationText", () => {
  it("stellt das deutsche Wort des Feldes vor die Standard-Pruefungen", () => {
    expect(validationText([{ type: "string_too_short", loc: ["body", "password"], msg: "Mindestens 8 Zeichen." }], AUTH_FIELD_LABELS)).toBe(
      "Passwort: Mindestens 8 Zeichen.",
    );
  });

  it("eigene Pruefungen (value_error) stehen ohne Vorsatz da, sie nennen ihr Feld selbst", () => {
    expect(validationText([{ type: "value_error", loc: ["body", "name"], msg: "Kurzname: nur Kleinbuchstaben." }])).toBe("Kurzname: nur Kleinbuchstaben.");
  });

  it("ohne bekanntes Wort bleibt der Feldname des Servers, ohne Feld steht nur der Satz", () => {
    expect(validationText([{ type: "missing", loc: ["body", "payload", "snapname"], msg: "Pflichtangabe fehlt." }])).toBe("payload.snapname: Pflichtangabe fehlt.");
    expect(validationText([{ type: "json_invalid", loc: ["body"], msg: "Das ist kein gültiges JSON." }])).toBe("Das ist kein gültiges JSON.");
  });

  it("hängt mehrere Meldungen mit Leerzeichen an, ohne \".;\" und mit einheitlicher Großschreibung", () => {
    const text = validationText(
      [
        { type: "string_too_short", loc: ["body", "username"], msg: "Mindestens 3 Zeichen." },
        { type: "string_too_short", loc: ["body", "password"], msg: "Mindestens 8 Zeichen." },
      ],
      AUTH_FIELD_LABELS,
    );
    expect(text).toBe("Benutzername: Mindestens 3 Zeichen. Passwort: Mindestens 8 Zeichen.");
    expect(text).not.toContain(".;");
    // Die eigene Prüfung im Browser schreibt es genauso wie der Server.
    expect(usernameProblem("ko")).toBe("Benutzername: Mindestens 3 Zeichen.");
  });

  it("nimmt auch Texte und Unbekanntes in der Liste hin", () => {
    expect(validationText(["schon ein Satz", null, { type: "x" }])).toBe("schon ein Satz. null. {\"type\":\"x\"}");
  });
});
