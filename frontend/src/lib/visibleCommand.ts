/**
 * Befehle so darstellen, dass nichts Verstecktes mitläuft.
 *
 * Wer eine Aktion freigibt, muss genau den Befehl lesen, der ausgeführt wird. Manche Zeichen
 * sind unsichtbar oder verändern die Anzeige (Richtungsumkehr, Zeichen ohne Breite, Zeilenanfang
 * per Wagenrücklauf, Escape-Folgen). Sie werden hier durch eine sichtbare Marke wie `⟦U+202E⟧`
 * ersetzt. Zeilenumbruch und Tabulator bleiben, normale Leerzeichen auch; andere Leerzeichen-
 * Arten (geschütztes Leerzeichen, schmale Leerzeichen) bekommen eine Marke, weil die Shell sie
 * nicht als Trenner liest.
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
