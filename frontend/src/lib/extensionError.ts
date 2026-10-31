/**
 * Die technischen Gründe aus `last_error` („on_start() fehlgeschlagen: …“) in Sätze für Menschen; der Rest steht so
 * da, wie ihn der Kern schreibt.
 */
const PATTERNS: [RegExp, (detail: string, match: RegExpMatchArray) => string][] = [
  [/^on_start\(\) hat Routen angehängt, die keine Anmeldung prüfen: ([\s\S]*)$/,
    (d) => `Das Modul wollte Seiten ohne Anmeldung anbieten und wurde deshalb gestoppt${d ? `. Technisch: ${d}` : "."}`],
  [/^on_start\(\) fehlgeschlagen: ([\s\S]*)$/, (d) => `Beim Starten des Moduls ist ein Fehler aufgetreten${d ? `: ${d}` : "."}`],
  [/^setup\(\) fehlgeschlagen: ([\s\S]*)$/, (d) => `Beim Vorbereiten des Moduls ist ein Fehler aufgetreten${d ? `: ${d}` : "."}`],
  [/^Import von '[^']*' fehlgeschlagen: ([\s\S]*)$/, (d) => `Das Modul ließ sich nicht laden${d ? `: ${d}` : "."}`],
  [/^api_version '([^']*)' inkompatibel mit Kern-SDK '([^']*)'\.$/,
    (_, m) => `Das Modul ist für eine andere Version von Nodvard Deck gebaut (Schnittstelle ${m[1]}, hier ${m[2]}).`],
];

export function extensionErrorText(text: string): string {
  for (const [pattern, render] of PATTERNS) {
    const match = text.match(pattern);
    if (match) return render(match[1].trim(), match);
  }
  return text;
}
