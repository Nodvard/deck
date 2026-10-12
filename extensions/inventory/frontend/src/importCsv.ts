/**
 * Bestellliste oder Inventar als CSV einlesen -- ganz im Browser, nichts geht an fremde Dienste.
 *
 * Erkannt werden die Spalten am Kopf: der Bestellverlauf aus dem Datenexport von Amazon („Product Name“,
 * „Order Date“, „Unit Price“, „Quantity“, „ASIN“ …, ältere Exporte mit „Title“, „ASIN/ISBN“, „Purchase Price Per
 * Unit“), der CSV-Export dieses Inventars („Name“, „Kaufdatum“, „Preis (EUR)“ …) und ähnlich benannte Spalten
 * anderer Shops. Trennzeichen Semikolon, Komma oder Tabulator; Excel-Dateien in Windows-Zeichensatz gehen auch.
 */

export interface ImportRow {
  name: string;
  quantity: number;
  purchase_date: string | null;
  purchase_price_cents: number | null;
  warranty_until: string | null;
  description: string | null;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  product_url: string | null;
  notes: string | null;
  /** Namen aus der Datei; die Seite ordnet sie vorhandenen Kategorien und Standorten zu. */
  category: string | null;
  location: string | null;
}

/** Mehr Zeilen nimmt die Vorschau nicht (der Browser bliebe sonst lange hängen). */
export const MAX_IMPORT_ROWS = 5000;
/** Größere Dateien liest die Seite gar nicht erst ein. */
export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;
/** Mehr Stück einer Zeile sind ein Lesefehler, keine Bestellung. */
const MAX_QUANTITY = 100_000;
/** Teurer (10 Millionen €) ist ein Lesefehler; dieselbe Grenze wie beim Server. */
const MAX_PRICE_CENTS = 1_000_000_000;

/**
 * Datei-Inhalt als Text. Erst UTF-8; ist die Datei kein gültiges UTF-8, war es eine Datei im Windows-Zeichensatz
 * (Excel speichert „CSV“ so): dann als windows-1252, damit „Größe“ nicht zu „Gr��e“ wird.
 */
export function decodeCsv(bytes: ArrayBuffer): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    try {
      return new TextDecoder("windows-1252").decode(bytes);
    } catch {
      return new TextDecoder("utf-8").decode(bytes);
    }
  }
}

/** Wie oft jedes mögliche Trennzeichen in einer Zeile vorkommt (außerhalb von Anführungszeichen). */
function separatorCounts(line: string): Record<string, number> {
  // Reihenfolge = Vorrang bei Gleichstand: Tabulator und Semikolon stehen selten in Text, Kommas oft.
  const counts: Record<string, number> = { "\t": 0, ";": 0, ",": 0 };
  let quoted = false;
  for (const ch of line) {
    if (ch === '"') quoted = !quoted;
    else if (!quoted && ch in counts) counts[ch] += 1;
  }
  return counts;
}

/** Steht in der Kopfzeile kein Trennzeichen, hat die Datei nur eine Spalte: dann trennt nichts (auch kein Komma im Namen). */
const NO_SEPARATOR = "\u0000";

/**
 * Trennzeichen: das häufigste in der Kopfzeile. Bei Gleichstand das, was in den nächsten Zeilen genauso oft vorkommt,
 * sonst Tabulator vor Semikolon vor Komma. So gewinnt bei „Name<Tab>Preis, brutto“ der Tabulator.
 */
function detectSeparator(lines: string[]): string {
  const [head, ...rest] = lines.map(separatorCounts);
  if (!head) return NO_SEPARATOR;
  const top = Math.max(...Object.values(head));
  if (top === 0) return NO_SEPARATOR;
  const tied = Object.keys(head).filter((sep) => head[sep] === top);
  return tied.find((sep) => rest.every((counts) => counts[sep] === top)) ?? tied[0];
}

export interface ParsedCsv {
  rows: string[][];
  /** Ein Anführungszeichen wurde bis zum Dateiende nicht geschlossen: der Rest steckt womöglich in einem Feld. */
  unclosed: boolean;
}

/** Zerlegt CSV-Text in Zeilen und Felder (Anführungszeichen, verdoppelte "" und Zeilenumbrüche in Feldern). */
export function parseCsvDetailed(text: string): ParsedCsv {
  let clean = text.replace(/^﻿/, "");
  // Excel schreibt manchmal „sep=;“ als erste Zeile: das sagt das Trennzeichen und ist keine Kopfzeile.
  const sepLine = clean.match(/^sep=(.)\r?\n/i);
  if (sepLine) clean = clean.slice(sepLine[0].length);
  const sep = sepLine ? sepLine[1] : detectSeparator(clean.split(/\r?\n/, 6).filter((l) => l.trim() !== "").slice(0, 5));
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let fieldStart = true;
  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (quoted) {
      if (ch === '"' && clean[i + 1] === '"') { field += '"'; i += 1; } else if (ch === '"') quoted = false; else field += ch;
      continue;
    }
    // Ein Anführungszeichen zählt nur am Anfang eines Felds (Leerzeichen davor zählen nicht: `A, "b, c"`);
    // mitten im Text (12" Monitor) ist es ein Zeichen.
    if (ch === '"' && fieldStart) { quoted = true; fieldStart = false; field = ""; }
    else if (ch === sep) { row.push(field); field = ""; fieldStart = true; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && clean[i + 1] === "\n") i += 1;
      row.push(field);
      field = "";
      fieldStart = true;
      if (row.some((f) => f.trim() !== "")) rows.push(row);
      row = [];
    } else {
      field += ch;
      if (ch !== " ") fieldStart = false;
    }
  }
  row.push(field);
  if (row.some((f) => f.trim() !== "")) rows.push(row);
  return { rows, unclosed: quoted };
}

export function parseCsv(text: string): string[][] {
  return parseCsvDetailed(text).rows;
}

const COLUMNS = {
  name: ["product name", "produktname", "name", "artikel", "artikelname", "bezeichnung", "title", "titel", "produkt"],
  quantity: ["quantity", "menge", "anzahl", "stück", "stueck"],
  purchase_date: ["order date", "bestelldatum", "kaufdatum", "datum", "date", "purchase date"],
  // Preis je Stück ...
  unit_price: ["unit price", "purchase price per unit", "preis (eur)", "preis", "einzelpreis", "stückpreis", "price"],
  // ... oder Summe der Zeile (wird durch die Menge geteilt).
  total_price: ["item total", "total owed", "gesamtpreis", "betrag", "summe", "total"],
  currency: ["currency", "währung", "waehrung"],
  warranty_until: ["garantie bis", "warranty until", "warranty"],
  description: ["beschreibung", "description"],
  manufacturer: ["hersteller", "manufacturer", "marke", "brand"],
  model: ["modell", "model"],
  serial_number: ["item serial number", "seriennummer", "serial number", "serial"],
  product_url: ["produktlink", "product url", "url", "link"],
  notes: ["notizen", "notes", "bemerkung"],
  category: ["kategorie", "category"],
  location: ["standort", "lagerort", "location"],
  asin: ["asin", "asin/isbn"],
  website: ["website"],
  status: ["order status", "bestellstatus"],
} as const;

type Column = keyof typeof COLUMNS;

function findColumn(header: string[], names: readonly string[]): number {
  const norm = header.map((h) => h.trim().toLowerCase());
  for (const name of names) {
    const i = norm.indexOf(name);
    if (i >= 0) return i;
  }
  return -1;
}

function isoDate(year: number, month: number, day: number): string | null {
  const d = new Date(Date.UTC(year, month - 1, day));
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return null;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/**
 * Zweistelliges Jahr. Ein Kaufdatum liegt in der Vergangenheit: 24 -> 2024, 99 -> 1999. Ein Garantie-Ende darf
 * bis zu 30 Jahre in der Zukunft liegen: 29 -> 2029.
 */
function fullYear(y: string, future: boolean, today = new Date()): number {
  if (y.length !== 2) return Number(y);
  const year = 2000 + Number(y);
  return year > today.getFullYear() + (future ? 30 : 1) ? year - 100 : year;
}

/** Reihenfolge bei Daten mit Schrägstrich: US (Monat zuerst) oder Tag zuerst (UK und viele andere). */
export type SlashOrder = "month-first" | "day-first";

/** Steht in einer Spalte irgendwo ein Datum wie 25/03/2024, gilt die ganze Spalte als „Tag zuerst“. */
export function slashOrder(values: string[]): SlashOrder | undefined {
  let dayFirst = false;
  let monthFirst = false;
  for (const value of values) {
    const m = value.trim().match(/^(\d{1,2})\/(\d{1,2})\//);
    if (!m) continue;
    if (Number(m[1]) > 12) dayFirst = true;
    if (Number(m[2]) > 12) monthFirst = true;
  }
  return dayFirst && !monthFirst ? "day-first" : monthFirst && !dayFirst ? "month-first" : undefined;
}

/**
 * "2024-03-02T10:11:12Z", "02.03.2024", "2.3.24", "3/2/2024" (US: Monat zuerst), "25/12/2024" (Tag zuerst, weil es
 * keinen 25. Monat gibt) -> "2024-03-02". `order` legt die Reihenfolge für die ganze Spalte fest. Unmögliche Tage
 * (31.02.) und alles andere -> null.
 */
export function parseDate(value: string, order?: SlashOrder, future = false): string | null {
  const v = value.trim();
  let m = v.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return isoDate(Number(m[1]), Number(m[2]), Number(m[3]));
  m = v.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)/);
  if (m) return isoDate(fullYear(m[3], future), Number(m[2]), Number(m[1]));
  m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4}|\d{2})(?!\d)/);
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])];
    const dayFirst = order ? order === "day-first" : a > 12;
    return dayFirst ? isoDate(fullYear(m[3], future), b, a) : isoDate(fullYear(m[3], future), a, b);
  }
  return null;
}

const CURRENCY_CODES = [
  "USD", "GBP", "CHF", "PLN", "CZK", "SEK", "DKK", "NOK", "JPY", "CAD", "AUD", "CNY", "HUF", "TRY", "BRL", "INR", "MXN",
  "AED", "SAR", "SGD", "RON", "ZAR", "KRW", "HKD", "NZD", "BGN", "ILS",
];

/**
 * Andere Währung als Euro? Erst die Spalte „Currency“ (alles außer EUR ist fremd), sonst Code oder Zeichen im Preis
 * ("$ 12.99", "12,99 USD", "R$ 12", "£5"). Gibt den Code zurück, bei Euro oder ohne Angabe null.
 */
export function foreignCurrency(price: string, column = ""): string | null {
  // Die Spalte zählt nur, wenn sie wie ein Währungscode aussieht („USD“). „Euro“, „€“ oder Amazons „Not Applicable“
  // sagen nichts Fremdes: dann entscheidet der Preis selbst.
  const col = column.trim().toUpperCase();
  if (/^[A-Z]{3}$/.test(col) && col !== "EUR") return col;
  const v = price.toUpperCase();
  if (/€|\bEUR\b|\bEURO/.test(v)) return null;
  const code = v.match(new RegExp(`\\b(${CURRENCY_CODES.join("|")})\\b`));
  if (code) return code[1];
  const symbols: [RegExp, string][] = [
    [/R\$/, "BRL"], [/CA?\$/, "CAD"], [/A\$/, "AUD"], [/₹/, "INR"], [/₩/, "KRW"], [/ZŁ/, "PLN"], [/KČ/, "CZK"],
    [/\$/, "USD"], [/£/, "GBP"], [/¥/, "JPY"], [/\bFR\./, "CHF"], [/\bKR\b/, "Kronen"],
  ];
  return symbols.find(([re]) => re.test(v))?.[1] ?? null;
}

/**
 * "24.99", "24,99", "€ 1.234,50", "1,234.50 EUR", "1.234" (Tausender) -> Cent. Leer, unlesbar, unglaubwürdig groß
 * oder negativ (Gutschrift) -> null.
 */
export function parsePriceCents(value: string): number | null {
  // Minus direkt an der Zahl ("-12,99", "€ −12,99", "12,99–") oder Klammern wie in der Buchhaltung: Gutschrift.
  if (/[-−–‒]\s*\d|\d\s*[-−–‒]|^\s*\(.*\)\s*$/.test(value)) return null;
  // Nur die erste Zahl ("12,99 EUR (inkl. 19% MwSt.)"); Leerzeichen als Tausendertrenner ("1 234,50") zählen mit,
  // ein Satzzeichen am Ende ("12,99 EUR.") nicht.
  const joined = value.replace(/(\d)[\s\u00a0\u202f](?=\d{3}(?!\d))/g, "$1");
  let v = (joined.match(/[.,]?\d[\d.,]*/)?.[0] ?? "").replace(/[.,]+$/, "");
  if (!v) return null;
  const lastComma = v.lastIndexOf(",");
  const lastDot = v.lastIndexOf(".");
  if (lastComma >= 0 && lastDot >= 0) {
    // Beide da: das hintere Zeichen trennt die Cent, das andere die Tausender.
    v = lastComma > lastDot ? v.replace(/\./g, "").replace(",", ".") : v.replace(/,/g, "");
  } else {
    const mark = lastComma >= 0 ? "," : ".";
    const parts = v.split(mark);
    // Nur ein Zeichen: genau drei Ziffern dahinter (auch mehrfach: 1.234.567) sind Tausender, sonst Cent.
    const thousands = parts.length > 1 && parts.slice(1).every((p) => p.length === 3) && parts[0] !== "" && parts[0] !== "0";
    v = thousands ? parts.join("") : parts.length > 2 ? "" : parts.join(".");
  }
  const num = Number(v);
  if (!v || !Number.isFinite(num)) return null;
  const cents = Math.round(num * 100);
  return cents <= MAX_PRICE_CENTS ? cents : null;
}

/** Nur echte Web-Adressen: http(s) mit Rechnername, ohne Leer- und Steuerzeichen, nicht übermäßig lang. */
function cleanUrl(value: string): string | null {
  // eslint-disable-next-line no-control-regex
  if (!value || value.length > 2000 || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
  // Wie der Server: vollständig mit „//“ und Rechnername ("https:example.com" nimmt er nicht), ohne Zugangsdaten.
  if (!/^https?:\/\/[^/\s]/i.test(value)) return null;
  // Auch ein leeres „@“ vor der Adresse („https://@example.com“) lehnt der Server ab.
  if (/^https?:\/\/[^/?#]*@/i.test(value)) return null;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname && !url.username && !url.password ? value : null;
  } catch {
    return null;
  }
}

/** Kürzt, ohne ein Emoji oder anderes Zeichen aus zwei Teilen zu zerschneiden (das ließe die Datenbank scheitern). */
function cut(text: string, max: number): string {
  return text.length <= max ? text : Array.from(text).slice(0, max).join("");
}

export interface ImportResult {
  rows: ImportRow[];
  /** Woran die Datei erkannt wurde, für den Hinweis über der Vorschau. */
  source: "amazon" | "inventar" | "andere";
  skipped: number;
  /** Hinweise zur ganzen Datei (z. B. Preise in fremder Währung, abgeschnittene Liste). */
  notes: string[];
}

const EMPTY = new Set(["-", "–", "—", "n/a", "not available"]);

/** Erkennt die Spalten und macht aus jeder Zeile einen Vorschlag; Stornierte und Zeilen ohne Namen fallen weg. */
export function rowsFromCsv(text: string): ImportResult {
  const { rows: table, unclosed } = parseCsvDetailed(text);
  const [header, ...lines] = table;
  if (!header) return { rows: [], source: "andere", skipped: 0, notes: [] };
  const col = Object.fromEntries(Object.entries(COLUMNS).map(([key, names]) => [key, findColumn(header, names)])) as Record<Column, number>;
  if (col.name < 0) return { rows: [], source: "andere", skipped: lines.length, notes: [] };
  const source = col.asin >= 0 ? "amazon" : findColumn(header, ["garantie bis"]) >= 0 ? "inventar" : "andere";
  const get = (line: string[], key: Column) => {
    let value = col[key] >= 0 ? (line[col[key]] ?? "").trim() : "";
    // Der eigene Export schützt Formeln mit einem Apostroph davor ('=…); das kommt hier wieder weg.
    if (/^'[=+\-@]/.test(value)) value = value.slice(1);
    return EMPTY.has(value.toLowerCase()) ? "" : value;
  };
  const dateOrder = slashOrder(lines.map((line) => get(line, "purchase_date")));
  const warrantyOrder = slashOrder(lines.map((line) => get(line, "warranty_until")));
  const rows: ImportRow[] = [];
  const currencies = new Map<string, number>();
  let skipped = 0;
  let cutOff = 0;
  for (const line of lines) {
    const name = get(line, "name");
    if (!name || /cancel|storniert/i.test(get(line, "status"))) { skipped += 1; continue; }
    if (rows.length >= MAX_IMPORT_ROWS) { cutOff += 1; continue; }
    // "2", "2.0" (aus Tabellenprogrammen), "2 Stk.", "2x" -> 2
    const quantityMatch = get(line, "quantity").match(/^(\d{1,9})(?:[.,]0+)?(?![\d.,])/);
    const quantityRaw = quantityMatch ? Number(quantityMatch[1]) : NaN;
    const quantity = quantityRaw > 0 && quantityRaw <= MAX_QUANTITY ? quantityRaw : 1;

    const unitText = get(line, "unit_price");
    const priceText = unitText || get(line, "total_price");
    const currency = priceText ? foreignCurrency(priceText, get(line, "currency")) : null;
    let price: number | null = null;
    if (currency) currencies.set(currency, (currencies.get(currency) ?? 0) + 1);
    else {
      price = parsePriceCents(priceText);
      if (price != null && !unitText) price = Math.round(price / quantity);
    }

    const asin = get(line, "asin").toUpperCase();
    const website = get(line, "website").toLowerCase().replace(/^www\./, "");
    const shop = /^amazon\.[a-z]{2,3}(\.[a-z]{2})?$/.test(website) ? website : "amazon.de";
    rows.push({
      name: cut(name, 300),
      quantity,
      purchase_date: parseDate(get(line, "purchase_date"), dateOrder),
      purchase_price_cents: price,
      warranty_until: parseDate(get(line, "warranty_until"), warrantyOrder, true),
      description: cut(get(line, "description"), 2000) || null,
      manufacturer: cut(get(line, "manufacturer"), 200) || null,
      model: cut(get(line, "model"), 200) || null,
      serial_number: cut(get(line, "serial_number"), 200) || null,
      // Bei Amazon gibt es keinen Link, aber die ASIN: daraus wird der Produktlink (im Shop der Bestellung).
      product_url: cleanUrl(get(line, "product_url")) ?? (/^[A-Z0-9]{10}$/.test(asin) ? `https://www.${shop}/dp/${asin}` : null),
      notes: cut(get(line, "notes"), 5000) || null,
      category: get(line, "category") || null,
      location: get(line, "location") || null,
    });
  }
  const notes: string[] = [];
  for (const [code, count] of currencies) {
    notes.push(`${count === 1 ? "1 Preis" : `${count} Preise`} in ${code}: nicht übernommen (das Inventar rechnet in Euro).`);
  }
  if (cutOff > 0) {
    notes.push(`Die Datei hat mehr als ${MAX_IMPORT_ROWS} Einträge: angezeigt werden die ersten ${MAX_IMPORT_ROWS}, ${cutOff} weitere nicht.`);
  }
  if (unclosed) {
    notes.push("In der Datei fehlt ein schließendes Anführungszeichen: die letzten Einträge sind womöglich unvollständig. Prüfe sie vor dem Übernehmen.");
  }
  return { rows, source, skipped, notes };
}
