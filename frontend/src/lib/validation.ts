/**
 * Lesbarer Text aus der Fehlerliste, die der Server bei einer abgelehnten Eingabe schickt (422):
 * `[{ type, loc: ["body", "password"], msg: "Mindestens 8 Zeichen." }, ...]`.
 *
 * Der Server liefert `msg` auf Deutsch (api/errors.py). Eigene Pruefungen (`type: "value_error"`)
 * nennen ihr Feld schon im Satz ("Kurzname: nur Kleinbuchstaben ..."), sie stehen unveraendert da.
 * Bei den Standard-Pruefungen ("Mindestens 8 Zeichen.", "Pflichtangabe fehlt.") kommt der Name des
 * Feldes davor -- als deutsches Wort aus `labels`, sonst so, wie ihn der Server nennt.
 *
 * Eigene Datei (statt in lib/api.ts), weil auch state/auth.ts sie braucht und api.ts selbst den
 * Auth-Zustand importiert.
 */

/** Feldnamen der Anmeldung und Einrichtung, so wie die Person sie auf der Seite sieht. */
export const AUTH_FIELD_LABELS: Record<string, string> = {
  username: "Benutzername",
  password: "Passwort",
  setup_code: "Einrichtungscode",
  code: "Code",
};

export function validationText(items: unknown[], labels: Record<string, string> = {}): string {
  return items
    .map((item) => {
      if (typeof item !== "object" || item === null) return String(item);
      const { type, loc, msg } = item as { type?: unknown; loc?: unknown; msg?: unknown };
      const text = typeof msg === "string" ? msg : JSON.stringify(item);
      if (type === "value_error") return text;
      const where = Array.isArray(loc) ? loc.filter((part) => !["body", "query", "path"].includes(String(part))).join(".") : "";
      const label = labels[where] ?? where;
      return label ? `${label}: ${text}` : text;
    })
    .map((sentence) => (/[.!?…)}]$/.test(sentence) ? sentence : `${sentence}.`))
    .join(" ");
}
