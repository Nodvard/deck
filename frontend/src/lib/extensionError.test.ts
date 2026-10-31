import { describe, expect, it } from "vitest";

import { extensionErrorText } from "./extensionError";

describe("extensionErrorText", () => {
  it("übersetzt die technischen Gründe des Kerns", () => {
    expect(extensionErrorText("on_start() fehlgeschlagen: Verbindung zu game-win abgelehnt")).toBe(
      "Beim Starten des Moduls ist ein Fehler aufgetreten: Verbindung zu game-win abgelehnt",
    );
    expect(extensionErrorText("setup() fehlgeschlagen: kaputt")).toBe("Beim Vorbereiten des Moduls ist ein Fehler aufgetreten: kaputt");
    expect(extensionErrorText("on_start() hat Routen angehängt, die keine Anmeldung prüfen: /x")).toBe(
      "Das Modul wollte Seiten ohne Anmeldung anbieten und wurde deshalb gestoppt. Technisch: /x",
    );
    expect(extensionErrorText("Import von 'pkg.mod:Ext' fehlgeschlagen: No module named 'pkg'")).toBe(
      "Das Modul ließ sich nicht laden: No module named 'pkg'",
    );
    expect(extensionErrorText("api_version '2.0' inkompatibel mit Kern-SDK '1.4'.")).toBe(
      "Das Modul ist für eine andere Version von Nodvard Deck gebaut (Schnittstelle 2.0, hier 1.4).",
    );
  });

  it("ohne Einzelheiten endet der Satz mit einem Punkt", () => {
    expect(extensionErrorText("on_start() fehlgeschlagen: ")).toBe("Beim Starten des Moduls ist ein Fehler aufgetreten.");
    expect(extensionErrorText("setup() fehlgeschlagen: ")).toBe("Beim Vorbereiten des Moduls ist ein Fehler aufgetreten.");
  });

  it("lässt verständliche Texte stehen", () => {
    const text = "Die Erweiterung wurde nicht gefunden (Ordner oder Paket fehlt, oder das Manifest ist ungültig).";
    expect(extensionErrorText(text)).toBe(text);
  });
});
