/**
 * Eigene App-Kacheln ("+ App hinzufügen") im Cockpit: Typen, Formularprüfung, Filter und Schreib-Aufrufe (`/apps`);
 * gelesen werden sie mit den erkannten Diensten aus `GET /overview` (lib/overview.ts). Die Prüfung hier ist nur
 * Komfort, maßgeblich ist das Backend (`services/custom_apps.py`). Der Server ruft die Adressen nie selbst ab.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import { isAppIconName } from "./appIcons";
import type { AppTileOut, OverviewOut } from "./overview";

export const NAME_MAX = 60;
export const GROUP_MAX = 40;
export const URL_MAX = 1000;

export interface AppFormValues {
  name: string;
  url: string;
  /** Name aus der festen Auswahl, ein einzelnes Emoji oder leer. */
  icon: string;
  /** `#rrggbb` oder leer (= Farbe nach dem Namen). */
  color: string;
  group: string;
  open_in_new_tab: boolean;
  /** Server-ID oder leer. */
  host_id: string;
}

export type AppFormField = "name" | "url" | "icon" | "color" | "group" | "host_id";
export type AppFormErrors = Partial<Record<AppFormField, string>>;

export const EMPTY_FORM: AppFormValues = { name: "", url: "", icon: "", color: "", group: "", open_in_new_tab: true, host_id: "" };

export function formFromApp(app: AppTileOut): AppFormValues {
  return {
    name: app.name,
    url: app.url ?? "",
    icon: app.icon ?? "",
    color: app.color ?? "",
    group: app.group ?? "",
    open_in_new_tab: app.open_in_new_tab,
    host_id: app.host_id ?? "",
  };
}

/** Farben zur Auswahl (leer = automatisch nach dem Namen). */
export const APP_COLORS: { value: string; label: string }[] = [
  { value: "#3b82f6", label: "Blau" },
  { value: "#10b981", label: "Grün" },
  { value: "#ef4444", label: "Rot" },
  { value: "#f59e0b", label: "Orange" },
  { value: "#8b5cf6", label: "Violett" },
  { value: "#ec4899", label: "Pink" },
  { value: "#14b8a6", label: "Türkis" },
  { value: "#64748b", label: "Grau" },
];

// --- Adresse ---------------------------------------------------------------------------------------------------

const HTTP_START = /^https?:\/\//i;
/** `host:8080` oder `host:8080/pfad` -- ein Port, kein Schema wie `javascript:`. */
const HOST_WITH_PORT = /^[^:/?#\s]+:\d+(?:[/?#]|$)/;
/** Sieht nach einem anderen Schema aus (`javascript:`, `data:`, `ftp://`, `mailto:` ...). */
const OTHER_SCHEME = /^[a-z][a-z0-9+.-]*:/i;

/** Ergänzt `http://`, wenn jemand nur `192.168.2.1` oder `nas.local:5000` tippt. Alles, was nach einem anderen
 * Schema aussieht (`javascript:`, `data:`, `ftp://` ...), bleibt unverändert, die Prüfung lehnt es dann ab. */
export function normalizeUrl(input: string): string {
  const text = input.trim();
  if (!text || HTTP_START.test(text)) return text;
  if (text.startsWith("//")) return text;
  if (OTHER_SCHEME.test(text) && !HOST_WITH_PORT.test(text)) return text;
  return `http://${text}`;
}

const URL_ERRORS = {
  empty: "Bitte gib eine Adresse an.",
  scheme: "Die Adresse muss mit http:// oder https:// beginnen.",
  chars: "In der Adresse dürfen keine Leerzeichen, Steuerzeichen oder Rückwärts-Schrägstriche stehen.",
  port: "Der Port in der Adresse ist ungültig.",
  credentials: "Bitte keinen Benutzernamen und kein Passwort in die Adresse schreiben.",
  host: "Die Adresse braucht einen Rechnernamen oder eine IP-Adresse, zum Beispiel http://192.168.2.1.",
};

const HOST_NAME = /^[\p{L}\p{N}\p{M}_.-]+$/u;
const HOST_IPV6 = /^\[[0-9a-fA-F:.]*:[0-9a-fA-F:.]*\]$/;

/** Eine Meldung, wenn die Adresse nicht taugt, sonst `null` (erwartet die schon ergänzte Adresse). Gleiche Regeln
 * wie `clean_url` im Backend: nur http/https, Rechnername oder IP, kein Benutzer/Passwort, gültiger Port. */
export function validateUrl(value: string): string | null {
  const text = value.trim();
  if (!text) return URL_ERRORS.empty;
  if (text.length > URL_MAX) return `Die Adresse ist zu lang (höchstens ${URL_MAX} Zeichen).`;
  if (!HTTP_START.test(text)) return URL_ERRORS.scheme;
  if (/[\s\\\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(text)) return URL_ERRORS.chars;
  const authority = /^https?:\/\/([^/?#]*)/i.exec(text)?.[1] ?? "";
  if (authority.includes("@")) return URL_ERRORS.credentials;
  const [, host = "", port] = /^(\[[^\]]*\]|[^:]*)(?::(.*))?$/.exec(authority) ?? [];
  if (port !== undefined && port !== "" && (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535)) return URL_ERRORS.port;
  if (!host || !(HOST_NAME.test(host) || HOST_IPV6.test(host))) return URL_ERRORS.host;
  try {
    new URL(text);
  } catch {
    return URL_ERRORS.host;
  }
  return null;
}

/** Für `href`: nur http/https, sonst `undefined` (kein Link). Auch wenn die Daten nicht vom Formular kommen. */
export function safeHref(url: string | null | undefined): string | undefined {
  if (!url || !HTTP_START.test(url.trim())) return undefined;
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? url.trim() : undefined;
  } catch {
    return undefined;
  }
}

/** Für Links innerhalb des Dashboards: nur `/pfad`, kein `//host`, kein `\\` und keine Steuerzeichen. Browser lesen
 * `/\\host` wie `//host` und entfernen Tabulator und Zeilenumbruch vorher, aus `/\t/host` wird also auch `//host`. */
export function internalPath(path: unknown): string | undefined {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("\\")) return undefined;
  for (let i = 0; i < path.length; i += 1) {
    const code = path.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return undefined;
  }
  return path;
}

/** Link aus Daten einer Erweiterung: eine Webadresse (`safeHref`) oder ein Pfad im Dashboard, sonst `undefined`. */
export function dataLink(value: string | null | undefined): string | undefined {
  return safeHref(value) ?? internalPath(value);
}

/** `192.168.2.1:8080` aus einer Adresse, als Untertitel der Kachel. */
export function urlHost(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

// --- Zeichen (wie `services/custom_apps.py`) ---------------------------------------------------------------------

/** Die Emoji-Blöcke (Mahjong bis „Symbole und Piktogramme, Erweiterung A“). Nur hier gilt ein Zeichen, das der
 * Browser noch nicht kennt, als (neues) Emoji, außerhalb wären es unsichtbare, nicht vergebene Zeichen. */
const inEmojiBlocks = (code: number) => code >= 0x1f000 && code <= 0x1faff;
/** Füllzeichen (Hangul-Füller, Braille-Leerzeichen): Unicode führt sie als Buchstabe/Symbol, sie zeigen aber nichts. */
const BLANK_CHARS = new Set([0x115f, 0x1160, 0x3164, 0xffa0, 0x2800]);
const UNASSIGNED = /^\p{Cn}$/u;
const UNWANTED = /^[\p{C}\p{Zl}\p{Zp}\p{Zs}]$/u;
const VISIBLE = /^[\p{L}\p{N}\p{S}\p{P}]$/u;

const isNewEmoji = (ch: string, code: number) => inEmojiBlocks(code) && UNASSIGNED.test(ch);

/** Steuer- und Formatzeichen, Zeilen-/Absatztrenner und alle Leerzeichen außer dem normalen, ausgenommen der
 * Zero-Width-Joiner (hält Emoji-Folgen zusammen) und noch nicht vergebene Zeichen in den Emoji-Blöcken. */
function isUnwanted(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  if (code === 0x200d || ch === " " || isNewEmoji(ch, code)) return false;
  return UNWANTED.test(ch);
}

/** Ein Zeichen, das etwas anzeigt: Buchstabe, Ziffer, Symbol oder Satzzeichen (auch ein neues Emoji). */
function isVisible(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  return !BLANK_CHARS.has(code) && (VISIBLE.test(ch) || isNewEmoji(ch, code));
}

export const hasBadCharacters = (text: string): boolean => Array.from(text).some(isUnwanted);
export const hasVisibleCharacter = (text: string): boolean => Array.from(text).some(isVisible);

// --- Symbol ----------------------------------------------------------------------------------------------------

const EMOJI_MAX_CODEPOINTS = 12;
const SYMBOL = /^\p{So}$/u;
const SKIN_TONE = /^[\u{1F3FB}-\u{1F3FF}]$/u;
/** Emoji, die Unicode nicht als „Symbol“ führt (Ausrufezeichen-Paar, Info, Pfeile, Welle), wie im Backend. */
const TEXT_STYLE_EMOJI = new Set([0x203c, 0x2049, 0x2139, 0x2194, 0x2195, 0x2196, 0x2197, 0x2198, 0x2199, 0x21a9, 0x21aa, 0x3030, 0x303d]);

function isSymbol(ch: string): boolean {
  const code = ch.codePointAt(0) ?? -1;
  if (BLANK_CHARS.has(code)) return false;
  return SYMBOL.test(ch) || TEXT_STYLE_EMOJI.has(code) || isNewEmoji(ch, code);
}

/** Ein einzelnes Emoji (auch zusammengesetzt: Flagge, Hautfarbe, Person am Computer) -- wie `_is_emoji` im Backend. */
export function isEmoji(text: string): boolean {
  const chars = Array.from(text);
  if (chars.length === 0 || chars.length > EMOJI_MAX_CODEPOINTS) return false;
  let symbols = 0;
  for (const ch of chars) {
    if (isSymbol(ch)) symbols += 1;
    else if (ch !== "\u200d" && ch !== "\ufe0f" && !SKIN_TONE.test(ch)) return false;
  }
  return symbols > 0 && isSymbol(chars[0]);
}

export const ICON_ERROR = "Wähle ein Symbol aus der Liste oder gib ein einzelnes Emoji ein.";

// --- Formular ---------------------------------------------------------------------------------------------------

/** Alle Fehler des Formulars auf einmal (leeres Objekt = in Ordnung). Die Adresse wird so geprüft, wie
 * `normalizeUrl` sie speichern würde. */
export function validateAppForm(values: AppFormValues): AppFormErrors {
  const errors: AppFormErrors = {};
  const name = values.name.trim();
  if (!name) errors.name = "Bitte gib einen Namen an.";
  else if (name.length > NAME_MAX) errors.name = `Der Name ist zu lang (höchstens ${NAME_MAX} Zeichen).`;
  else if (hasBadCharacters(name)) errors.name = "Der Name darf keine Zeilenumbrüche oder Steuerzeichen enthalten.";
  else if (!hasVisibleCharacter(name)) errors.name = "Bitte gib einen Namen an."; // nur Füll- oder Verbindungszeichen

  const urlError = validateUrl(normalizeUrl(values.url));
  if (urlError) errors.url = urlError;

  const group = values.group.trim();
  if (group.length > GROUP_MAX) errors.group = `Die Gruppe ist zu lang (höchstens ${GROUP_MAX} Zeichen).`;
  else if (hasBadCharacters(group)) errors.group = "Die Gruppe darf keine Zeilenumbrüche oder Steuerzeichen enthalten.";
  else if (group && !hasVisibleCharacter(group)) errors.group = "Die Gruppe braucht mindestens ein sichtbares Zeichen.";

  const icon = values.icon.trim();
  if (icon && !isAppIconName(icon) && !isEmoji(icon)) errors.icon = ICON_ERROR;
  if (values.color && !/^#[0-9a-fA-F]{6}$/.test(values.color.trim())) {
    errors.color = "Die Farbe muss wie #3b82f6 aussehen (ein # und sechs Ziffern oder Buchstaben a–f).";
  }
  return errors;
}

/** Der Body für POST/PATCH: geprüft, getrimmt, Adresse mit `http://` ergänzt, Leeres als `null`. */
export function bodyFromForm(values: AppFormValues) {
  return {
    name: values.name.trim(),
    url: normalizeUrl(values.url),
    icon: values.icon.trim() || null,
    color: values.color.trim().toLowerCase() || null,
    group: values.group.trim() || null,
    open_in_new_tab: values.open_in_new_tab,
    host_id: values.host_id || null,
  };
}

// --- Anzeige: Gruppen, Filter ----------------------------------------------------------------------------------

/** Auswahl im Gruppen-Filter: alles, eine Gruppe eigener Apps, eigene ohne Gruppe oder nur die erkannten Dienste. */
export type GroupFilter = string;
export const FILTER_ALL: GroupFilter = "all";
export const FILTER_UNGROUPED: GroupFilter = "ungrouped";
export const FILTER_DETECTED: GroupFilter = "detected";
export const groupFilter = (name: string): GroupFilter => `group:${name}`;

export interface GroupChip {
  key: GroupFilter;
  label: string;
  count: number;
}

/** Die Knöpfe des Gruppen-Filters, nur wenn es eigene Apps gibt: „Alle“, jede Gruppe, „Ohne Gruppe“ (nur bei Gruppen)
 * und „Erkannt“ (nur wenn die Liste erkannte Dienste zeigt, ohne Web-Oberfläche nur mit `showAll`). Die Anzahl zählt,
 * was nach dem Suchtext übrig ist; ein Knopf mit 0 bleibt, damit die Auswahl beim Tippen nicht auf „Alle“ springt. */
export function groupChips(apps: AppTileOut[], visible: AppTileOut[], showAll = false): GroupChip[] {
  const custom = apps.filter((a) => a.source === "custom");
  if (custom.length === 0) return [];
  const chips: GroupChip[] = [{ key: FILTER_ALL, label: "Alle", count: visible.length }];
  const seen = new Set<string>();
  for (const app of custom) {
    if (app.group && !seen.has(app.group)) {
      seen.add(app.group);
      chips.push({ key: groupFilter(app.group), label: app.group, count: visible.filter((a) => a.source === "custom" && a.group === app.group).length });
    }
  }
  if (seen.size > 0 && custom.some((a) => !a.group)) {
    chips.push({ key: FILTER_UNGROUPED, label: "Ohne Gruppe", count: visible.filter((a) => a.source === "custom" && !a.group).length });
  }
  if (apps.some((a) => a.source === "detected" && (showAll || a.url))) {
    chips.push({ key: FILTER_DETECTED, label: "Erkannt", count: visible.filter((a) => a.source === "detected").length });
  }
  return chips;
}

/** Alle Gruppen, die es schon gibt (Vorschläge für das Feld „Gruppe“). */
export function existingGroups(apps: AppTileOut[]): string[] {
  const names = new Set<string>();
  for (const app of apps) if (app.source === "custom" && app.group) names.add(app.group);
  return [...names].sort((a, b) => a.localeCompare(b, "de"));
}

/** Was die Liste zeigt: erkannte Dienste ohne Web-Oberfläche nur auf Wunsch, eigene Apps immer; dazu Suchtext
 * und Gruppen-Filter. Die Reihenfolge der Eingabe bleibt (eigene zuerst, nach Position). */
export function filterApps(apps: AppTileOut[], opts: { query: string; filter: GroupFilter; showAll: boolean }): AppTileOut[] {
  const q = opts.query.trim().toLowerCase();
  return apps.filter((a) => {
    if (a.source === "detected" && !opts.showAll && !a.url) return false;
    if (q && !`${a.name} ${a.host ?? ""} ${a.image ?? ""} ${a.group ?? ""} ${a.source === "custom" ? urlHost(a.url) : ""}`.toLowerCase().includes(q)) return false;
    if (opts.filter === FILTER_ALL) return true;
    if (opts.filter === FILTER_DETECTED) return a.source === "detected";
    if (opts.filter === FILTER_UNGROUPED) return a.source === "custom" && !a.group;
    return a.source === "custom" && a.group === opts.filter.slice("group:".length);
  });
}

// --- Schreiben ----------------------------------------------------------------------------------------------------

/** Antwort von `POST`/`PATCH /apps` (`CustomAppOut` im Backend). Die Felder hinter Kennung und Name sind hier
 * optional, damit eine knappe Antwort die Anzeige nicht stört: fehlt etwas, gilt, was das Formular geschickt hat. */
export interface SavedApp {
  id: string;
  name?: string;
  url?: string | null;
  icon?: string | null;
  color?: string | null;
  group?: string | null;
  sort_order?: number;
  open_in_new_tab?: boolean;
  host_id?: string | null;
  host?: string | null;
}

type Body = ReturnType<typeof bodyFromForm>;
type Change = (apps: AppTileOut[]) => AppTileOut[];

const isCustom = (a: AppTileOut) => a.source === "custom";

/** Reihenfolge wie im Backend (`list_apps`): Position, dann Name (ohne Groß-/Kleinschreibung), dann Kennung. */
const byPosition = (a: AppTileOut, b: AppTileOut) =>
  (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.id.localeCompare(b.id);

/** Eigene Apps nach Position, danach die erkannten Dienste in ihrer Reihenfolge. */
const arranged = (apps: AppTileOut[]) => [...apps.filter(isCustom).sort(byPosition), ...apps.filter((a) => !isCustom(a))];

function tileFromSaved(id: string, saved: Partial<SavedApp> | null | undefined, body: Body, sortOrder: number): AppTileOut {
  const s = saved ?? {};
  return {
    id, source: "custom", name: s.name ?? body.name, url: s.url ?? body.url, host: s.host ?? null,
    host_id: s.host_id ?? body.host_id, state: null, tone: null, image: null, icon: s.icon ?? body.icon,
    color: s.color ?? body.color, group: s.group ?? body.group, open_in_new_tab: s.open_in_new_tab ?? body.open_in_new_tab,
    sort_order: s.sort_order ?? sortOrder,
  };
}

/** Anlegen, Ändern, Löschen, Reihenfolge. Das Cockpit zeigt die Änderung sofort und lädt die Übersicht dann im
 * Hintergrund neu; der Aufruf wartet nicht darauf (bei hängendem Server bis zu 12 s, der Dialog bliebe gesperrt).
 * Fehler (`ApiError`) gehen an den Aufrufer, der sie anzeigt. */
export function useAppActions() {
  const queryClient = useQueryClient();

  async function show(change: Change) {
    // Eine schon laufende, ältere Abfrage darf die Änderung nicht wieder überschreiben.
    await queryClient.cancelQueries({ queryKey: ["overview"] });
    queryClient.setQueryData<OverviewOut>(["overview"], (old) => {
      if (!old) return old;
      try {
        return { ...old, apps: change(old.apps) };
      } catch {
        return old; // die Anzeige ist nur Komfort: der Neuabruf unten holt den Stand des Servers
      }
    });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    void queryClient.invalidateQueries({ queryKey: ["apps", "suggestions"] });
  }

  return {
    create: async (values: AppFormValues) => {
      const body = bodyFromForm(values);
      const saved = await api.post<SavedApp>("/apps", body);
      const id = saved?.id;
      await show((apps) => {
        if (!id) return apps; // keine brauchbare Antwort: der Neuabruf zeigt die Kachel
        const next = Math.max(-1, ...apps.filter(isCustom).map((a) => a.sort_order ?? 0)) + 1;
        return arranged([...apps, tileFromSaved(id, saved, body, next)]);
      });
      return saved;
    },
    update: async (id: string, values: AppFormValues) => {
      const body = bodyFromForm(values);
      const saved = await api.patch<SavedApp>(`/apps/${encodeURIComponent(id)}`, body);
      await show((apps) =>
        arranged(apps.map((a) => (isCustom(a) && a.id === id ? tileFromSaved(id, saved, body, a.sort_order ?? 0) : a))),
      );
      return saved;
    },
    remove: async (id: string) => {
      await api.delete(`/apps/${encodeURIComponent(id)}`);
      await show((apps) => apps.filter((a) => !(isCustom(a) && a.id === id)));
    },
    reorder: async (ids: string[]) => {
      await api.put("/apps/order", { ids });
      // Wie `reorder` im Backend: die genannten vorn, die übrigen dahinter, Positionen neu von 0 an.
      await show((apps) => {
        const mine = apps.filter(isCustom).sort(byPosition);
        const named = new Set(ids);
        const ordered = [
          ...ids.map((id) => mine.find((a) => a.id === id)).filter((a): a is AppTileOut => a !== undefined),
          ...mine.filter((a) => !named.has(a.id)),
        ].map((a, position) => ({ ...a, sort_order: position }));
        return [...ordered, ...apps.filter((a) => !isCustom(a))];
      });
    },
  };
}

// --- Seite „Apps“: Vorschläge und Ziehen -------------------------------------------------------------------------

/** Ein Vorschlag aus `GET /apps/suggestions`: eine Adresse, die eine Erweiterung schon kennt. */
export interface AppSuggestionOut {
  name: string;
  url: string;
  /** Woher er stammt (Name der Quelle oder der Erweiterung, z. B. „Nginx Proxy Manager“). */
  source: string;
  host?: string | null;
  /** Bei einem erkannten Dienst die Kennung seiner Kachel (`apps[].id` in der Übersicht). Fehlt bei einem älteren
   * Backend. */
  service_id?: string | null;
}

export interface AppSuggestionsOut {
  suggestions: AppSuggestionOut[];
  /** Je Quelle, die nicht antwortete, ein Satz. */
  errors?: string[];
}

/** Vorschläge nur für Nutzer, die Apps anlegen dürfen (`apps.write`). Nach jeder Änderung an den Apps neu (die
 * übernommenen fallen dann weg). */
export function useAppSuggestions(enabled: boolean) {
  return useQuery({
    queryKey: ["apps", "suggestions"],
    queryFn: () => api.get<AppSuggestionsOut>("/apps/suggestions"),
    enabled,
    staleTime: 60_000,
  });
}

/** Verschiebt `dragged` vor `target` (beide Kennungen in `ids`); unbekannte Kennungen ändern nichts. */
export function moveBefore(ids: string[], dragged: string, target: string): string[] {
  if (dragged === target || !ids.includes(dragged) || !ids.includes(target)) return ids;
  const rest = ids.filter((id) => id !== dragged);
  rest.splice(rest.indexOf(target), 0, dragged);
  return rest;
}

/** Verschiebt `dragged` ans Ende der Apps, die zu `members` gehören (Ordner); ohne Mitglieder ans Ende. */
export function moveToEndOf(ids: string[], dragged: string, members: string[]): string[] {
  const rest = ids.filter((id) => id !== dragged);
  const last = Math.max(-1, ...members.filter((m) => m !== dragged).map((m) => rest.indexOf(m)));
  rest.splice(last < 0 ? rest.length : last + 1, 0, dragged);
  return rest;
}
