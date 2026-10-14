/**
 * Befehle so darstellen, dass nichts Verstecktes mitläuft: Wer eine Aktion freigibt, muss genau den Befehl lesen, der
 * ausgeführt wird. Unsichtbare oder die Anzeige verändernde Zeichen (Richtungsumkehr, Breite null, Wagenrücklauf,
 * Escape-Folgen, Leerzeichen, die die Shell nicht als Trenner liest) werden durch eine Marke wie `⟦U+202E⟧` ersetzt.
 */

// Steuerzeichen (ohne Zeilenumbruch und Tabulator, inkl. \r, \x1b und C1), Formatzeichen
// (Richtung, Breite null, Wortverbinder, Tag-Zeichen), Zeilen-/Absatztrenner, Leerzeichen-Arten
// außer dem normalen Leerzeichen sowie Füllzeichen, die leer aussehen.
const HIDDEN_CHARS =
  /(?![\n\t])\p{Cc}|\p{Cf}|\p{Zl}|\p{Zp}|(?! )\p{Zs}|[\u115F\u1160\u2800\u3164\uFFA0]/gu;

export function visibleCommand(command: string): string {
  return command.replace(HIDDEN_CHARS, (ch) => {
    const code = ch.codePointAt(0) ?? 0;
    return `⟦U+${code.toString(16).toUpperCase().padStart(4, "0")}⟧`;
  });
}
