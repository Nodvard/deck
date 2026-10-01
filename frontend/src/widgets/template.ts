/**
 * Rendert die winzige Template-Sprache aus docs/02-EXTENSION-API.md §4:
 * `{{ feld.pfad }}` plus feste Filterliste, KEIN Ausdruck, KEINE Bedingung, KEINE
 * Schleife -- absichtlich nicht Turing-vollstaendig, damit die Flutter-Seite keinen
 * zweiten Interpreter braucht. Backend-Quelle der Filterliste:
 * sdk/python/nodvard_sdk/widgets.py ALLOWED_FILTERS (definiert, aber dort selbst nicht
 * konsumiert -- die Engine existiert bisher nur hier im Frontend).
 *
 * Ein String ohne "{{" ist kein Template, sondern ein Literal (z.B. `Badge.tone` ist
 * `Template | Tone` -- "danger" ohne geschweifte Klammern ist der Tone-Enum-Wert
 * direkt, kein zu interpolierender Ausdruck). Der Regex unten aendert daran nichts.
 */
import type { Tone } from "./types";

const EXPR_RE = /\{\{\s*([^}]+?)\s*\}\}/g;

export function resolvePath(data: unknown, path: string): unknown {
  let current: unknown = data;
  for (const segment of path.split(".")) {
    if (current === null || current === undefined) return undefined;
    if (typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function toDate(value: unknown): Date | null {
  if (value instanceof Date) return value;
  const num = toNumber(value);
  if (num !== null) {
    // Sekunden- vs. Millisekunden-Epoch unterscheiden: alles unter ~ Jahr 5138 in ms
    // waere vor 1970 in s-Interpretation unwahrscheinlich fuer Zeitstempel -> Heuristik
    // ueber Groessenordnung (Sekunden-Epochen liegen aktuell < 1e11).
    const ms = num < 1e11 ? num * 1000 : num;
    return new Date(ms);
  }
  if (typeof value === "string") {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  return null;
}

function filterRelative(value: unknown): string {
  const date = toDate(value);
  if (!date) return "";
  const diffMs = Date.now() - date.getTime();
  const diffS = Math.round(diffMs / 1000);
  const abs = Math.abs(diffS);
  const future = diffS < 0;

  const units: [string, string, number][] = [
    ["Sekunde", "Sekunden", 60],
    ["Minute", "Minuten", 60],
    ["Stunde", "Stunden", 24],
    ["Tag", "Tage", 30],
    ["Monat", "Monate", 12],
    ["Jahr", "Jahre", Infinity],
  ];
  if (abs < 5) return "gerade eben";

  let value_ = abs;
  let label = "Sekunden";
  let singular = "Sekunde";
  for (const [sing, plur, factor] of units) {
    if (value_ < factor) {
      label = plur;
      singular = sing;
      break;
    }
    value_ = Math.floor(value_ / factor);
    label = plur;
    singular = sing;
  }
  const word = value_ === 1 ? singular : label;
  return future ? `in ${value_} ${word}` : `vor ${value_} ${word}`;
}

function filterDatetime(value: unknown): string {
  const date = toDate(value);
  return date ? date.toLocaleString("de-DE") : "";
}

function filterDate(value: unknown): string {
  const date = toDate(value);
  return date ? date.toLocaleDateString("de-DE") : "";
}

function filterBytes(value: unknown): string {
  const num = toNumber(value);
  if (num === null) return "";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let n = Math.abs(num);
  let unitIdx = 0;
  while (n >= 1024 && unitIdx < units.length - 1) {
    n /= 1024;
    unitIdx += 1;
  }
  const sign = num < 0 ? "-" : "";
  const formatted = unitIdx === 0 ? String(n) : n.toLocaleString("de-DE", { maximumFractionDigits: 1 });
  return `${sign}${formatted} ${units[unitIdx]}`;
}

function filterPercent(value: unknown): string {
  const num = toNumber(value);
  if (num === null) return "";
  return `${num.toLocaleString("de-DE", { maximumFractionDigits: 1 })}%`;
}

function filterNumber(value: unknown): string {
  const num = toNumber(value);
  if (num === null) return String(value ?? "");
  return num.toLocaleString("de-DE");
}

function filterDuration(value: unknown): string {
  const num = toNumber(value);
  if (num === null) return "";
  let seconds = Math.round(Math.abs(num));
  const sign = num < 0 ? "-" : "";
  const days = Math.floor(seconds / 86400);
  seconds -= days * 86400;
  const hours = Math.floor(seconds / 3600);
  seconds -= hours * 3600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;

  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}m`);
  if (seconds || parts.length === 0) parts.push(`${seconds}s`);
  return sign + parts.slice(0, 2).join(" ");
}

const TONE_KEYWORDS: [Tone, string[]][] = [
  ["danger", ["critical", "danger", "error", "down", "failed", "fatal", "kritisch", "fehler"]],
  ["warn", ["warn", "warning", "high", "medium", "degraded", "pending", "achtung"]],
  ["good", ["ok", "good", "healthy", "success", "low", "info", "up", "running", "gut"]],
  ["accent", ["accent"]],
];

/**
 * Bildet einen rohen Feld-Wert (z.B. `severity: "critical"`) auf einen Tone ab.
 * ANNAHME (kein fester Vertrag): Stichwort-Heuristik ueber uebliche
 * Severity-/Status-Vokabeln.
 */
function filterTone(value: unknown): Tone {
  const str = String(value ?? "").toLowerCase();
  for (const [tone, keywords] of TONE_KEYWORDS) {
    if (keywords.some((kw) => str.includes(kw))) return tone;
  }
  return "neutral";
}

const TRUNCATE_LENGTH = 60;
function filterTruncate(value: unknown): string {
  const str = String(value ?? "");
  return str.length > TRUNCATE_LENGTH ? `${str.slice(0, TRUNCATE_LENGTH - 1)}…` : str;
}

function filterUpper(value: unknown): string {
  return String(value ?? "").toUpperCase();
}

function filterLower(value: unknown): string {
  return String(value ?? "").toLowerCase();
}

const FILTERS: Record<string, (value: unknown) => string> = {
  relative: filterRelative,
  datetime: filterDatetime,
  date: filterDate,
  bytes: filterBytes,
  percent: filterPercent,
  number: filterNumber,
  duration: filterDuration,
  tone: filterTone,
  truncate: filterTruncate,
  upper: filterUpper,
  lower: filterLower,
};

export const ALLOWED_FILTERS = Object.keys(FILTERS);

/**
 * Rendert einen Template-String gegen ein Datenobjekt. Ein String ohne "{{" wird
 * unveraendert zurueckgegeben (Literal, siehe Modul-Kommentar). Ein unbekannter
 * Feldpfad ergibt einen leeren String statt eines Wurfs -- ein einzelnes fehlendes
 * Feld soll das Widget nicht abstuerzen lassen (die Formvalidierung dafuer sitzt in
 * WidgetCard, nicht hier).
 */
export function renderTemplate(template: string, data: unknown): string {
  if (!template.includes("{{")) return template;
  return template.replace(EXPR_RE, (_match, expr: string) => {
    const [pathPart, ...filterParts] = expr.split("|").map((s) => s.trim());
    let value = resolvePath(data, pathPart);
    for (const filterName of filterParts) {
      const filterFn = FILTERS[filterName];
      if (!filterFn) {
        // Unbekannter Filtername -- keine stille Verschluckung, aber auch kein Crash:
        // Rohwert durchreichen und in der Konsole auf den Extension-Fehler hinweisen
        // (docs/02 §4: "meldet Abweichungen als Extension-Fehler statt als kaputtes UI").
        // eslint-disable-next-line no-console
        console.warn(`[widget-template] unbekannter Filter "${filterName}" in "${template}"`);
        continue;
      }
      value = filterFn(value);
    }
    return value === undefined || value === null ? "" : String(value);
  });
}

/** Wie renderTemplate, aber gibt einen Tone zurueck statt eines Strings (Badge/Tile-Tone-Felder). */
export function renderTone(template: string, data: unknown): Tone {
  const rendered = renderTemplate(template, data);
  const valid: Tone[] = ["neutral", "good", "warn", "danger", "accent"];
  return (valid as string[]).includes(rendered) ? (rendered as Tone) : "neutral";
}
