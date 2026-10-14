/**
 * Lesbarer Text aus der Fehlerliste einer abgelehnten Eingabe (422, `[{ type, loc, msg }, ...]`). `msg` kommt deutsch
 * vom Server (api/errors.py); eigene Prüfungen (`value_error`) nennen ihr Feld schon im Satz, bei Standard-Prüfungen
 * steht der Feldname davor (aus `labels`, sonst wie vom Server).
 */

// Eigene Datei statt in lib/api.ts: auch state/auth.ts braucht sie, und api.ts importiert selbst den Auth-Zustand.
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
