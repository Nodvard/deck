/**
 * Text in die Zwischenablage kopieren. `navigator.clipboard` gibt es nur in einem sicheren
 * Kontext (https oder localhost) -- das Dashboard laeuft aber meist auf
 * `http://192.168.x.y:8080`. Dort hilft der alte Weg: ein Textfeld markieren und
 * `document.execCommand("copy")`. Klappt auch das nicht, sagt die Oberflaeche dem Nutzer, er
 * soll den (markierten) Text selbst kopieren.
 */

export const COPY_FAILED_TEXT = "Bitte den markierten Text kopieren (Strg+C bzw. lange drücken).";

function copyViaTextarea(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.readOnly = true;
  area.setAttribute("aria-hidden", "true");
  // Ausserhalb des Bildes, aber nicht `display:none` (dann laesst sich nichts markieren).
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "-9999px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  try {
    area.focus();
    area.select();
    area.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

/** `true`, wenn der Text in der Zwischenablage liegt. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // abgelehnt (keine Berechtigung, Seite nicht im Vordergrund): der Umweg unten versucht es noch einmal.
  }
  return copyViaTextarea(text);
}
