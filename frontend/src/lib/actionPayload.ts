/**
 * Aus den Eingabefeldern der Server-Seite wird der `payload` für `POST /hosts/{id}/actions/{typ}`.
 *
 * Ein Eingabefeld liefert immer Text. Der Kern prüft die Angaben aber vorab gegen das `params_schema` der Aktion
 * (`core/schema_check.py`) und lehnt „4“ für ein Feld vom Typ `integer` mit 422 ab. Darum wird hier nach dem Typ des
 * Feldes umgewandelt: `integer` und `number` werden zu Zahlen, alles andere bleibt Text wie eingegeben.
 */

export interface PayloadFieldSchema {
  type?: string;
  title?: string;
}

export type ActionPayloadResult =
  | { ok: true; payload: Record<string, string | number> }
  | { ok: false; error: string };

type NumberType = "integer" | "number";
export type NumberParse = { value: number } | { problem: "format" | "range" };

const INTEGER = /^[+-]?\d+$/;
// Dezimalkomma wie Punkt; ein Tausenderpunkt („1.000,5“) ist bewusst kein gültiges Format: lieber nachfragen als raten.
const DECIMAL = /^[+-]?(\d+([.,]\d+)?|[.,]\d+)$/;

/** Text aus einem Zahlenfeld in eine Zahl wandeln. `format`: keine Zahl dieser Art (z. B. „abc“, „1.5“ bei `integer`);
 * `range`: eine Zahl, die sich nicht genau darstellen lässt (unendlich, bei `integer` jenseits von 2^53). */
export function parseNumberInput(text: string, type: NumberType): NumberParse {
  const t = text.trim();
  if (!(type === "integer" ? INTEGER : DECIMAL).test(t)) return { problem: "format" };
  const value = Number(t.replace(",", "."));
  if (!Number.isFinite(value) || (type === "integer" && !Number.isSafeInteger(value))) return { problem: "range" };
  return { value: value === 0 ? 0 : value }; // aus „-0“ wird 0
}

/**
 * `fields` sind die Schlüssel der Felder, `values` was eingegeben wurde, `properties` das Schema der Felder.
 * Ein leeres Feld (nach dem Kürzen) fehlt im Ergebnis; ein nicht lesbarer Zahlenwert liefert eine deutsche Meldung,
 * dann soll nichts abgeschickt werden.
 */
export function buildActionPayload(
  fields: readonly string[],
  values: Readonly<Record<string, string>>,
  properties?: Readonly<Record<string, PayloadFieldSchema>> | null,
): ActionPayloadResult {
  const payload: Record<string, string | number> = {};
  for (const key of fields) {
    const text = values[key] ?? "";
    if (text.trim() === "") continue;
    const schema = properties?.[key];
    const type = schema?.type;
    if (type !== "integer" && type !== "number") {
      payload[key] = text;
      continue;
    }
    const name = schema?.title ?? key;
    const parsed = parseNumberInput(text, type);
    if ("problem" in parsed) {
      const what = type === "integer" ? "eine ganze Zahl" : "eine Zahl";
      const example = type === "integer" ? "3" : "1,5";
      return {
        ok: false,
        error: parsed.problem === "range" ? `Die Zahl bei „${name}“ ist zu groß.` : `Bitte bei „${name}“ ${what} eingeben (zum Beispiel ${example}).`,
      };
    }
    payload[key] = parsed.value;
  }
  return { ok: true, payload };
}
