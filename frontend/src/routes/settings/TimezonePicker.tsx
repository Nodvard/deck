/**
 * Zeitzone waehlen: Suche, Liste, Uhrzeit dort und der Vorschlag "Zone dieses Geraets".
 * Gemeinsam genutzt von Einstellungen -> System (`TimeAndLogCard`) und dem Einrichtungsassistenten.
 * Speichert nichts selbst -- der Aufrufer bekommt die Wahl ueber `onChange` und ruft
 * `PUT /settings/system.timezone` auf.
 */
import { Search } from "lucide-react";
import { useMemo, useState } from "react";

import { browserTimeZone } from "../../lib/deckTimezone";
import { COMMON_ZONES, foldForSearch, germanZoneLabel, germanZoneTerms } from "./timezoneNames";
import { Badge, inputClass } from "./ui";

/** Wird angezeigt, wenn der Browser keine Zonenliste kennt (Intl.supportedValuesOf fehlt). */
const FALLBACK_ZONES = [
  "UTC", "Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London", "Europe/Paris", "Europe/Madrid",
  "Europe/Rome", "Europe/Amsterdam", "Europe/Warsaw", "Europe/Athens", "Europe/Helsinki", "Europe/Moscow",
  "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Sao_Paulo",
  "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
];

/** Alle Zonennamen, die der Browser kennt, sortiert; `UTC` immer dabei, `extra` (die gespeicherte Zone) auch. */
export function timezoneOptions(extra: string[] = []): string[] {
  let zones: string[] = [];
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
    zones = supported ? supported("timeZone") : [];
  } catch {
    zones = [];
  }
  if (zones.length === 0) zones = FALLBACK_ZONES;
  return [...new Set(["UTC", ...zones, ...extra.filter(Boolean)])].sort((a, b) => a.localeCompare(b));
}

/**
 * Suche ohne Rücksicht auf Groß-/Kleinschreibung, Umlaute und Unterstriche (jedes Suchwort steht am
 * Anfang eines Wortes im Namen): "new york" findet
 * "America/New_York", und auch deutsche Namen gehen ("Wien", "Zürich"/"zuerich", "Großbritannien",
 * "Mitteleuropa", siehe timezoneNames.ts).
 */
export function filterTimezones(zones: string[], query: string): string[] {
  const words = foldForSearch(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return zones;
  // Jedes Suchwort muss der Anfang eines Wortes sein: „Wien“ soll Wien finden, nicht „Moldawien“.
  return zones.filter((zone) => {
    const tokens = foldForSearch(`${zone} ${germanZoneTerms(zone).join(" ")}`).split(/\s+/);
    return words.every((w) => tokens.some((token) => token.startsWith(w)));
  });
}

/** Ohne Suche: die gewählte Zone zuerst, dann die gängigen (Berlin, Wien, Zürich …), dann alle anderen. */
export function orderTimezones(zones: string[], selected: string): string[] {
  const present = new Set(zones);
  const head = [selected, ...COMMON_ZONES].filter((z, i, all) => present.has(z) && all.indexOf(z) === i);
  const first = new Set(head);
  return [...head, ...zones.filter((z) => !first.has(z))];
}

function timeIn(zone: string): string | null {
  try {
    return new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: zone });
  } catch {
    return null;
  }
}

export function TimezonePicker({
  value, savedZone, onChange,
}: {
  /** Aktuell gewaehlte Zone. */
  value: string;
  /** Zone, die der Server gerade hat; weicht `value` davon ab, steht "nicht gespeichert" da. */
  savedZone: string;
  onChange: (zone: string) => void;
}): JSX.Element {
  const [query, setQuery] = useState("");
  const zones = useMemo(() => timezoneOptions([savedZone, value]), [savedZone, value]);
  // Ohne Suche steht die gewaehlte Zone ganz oben, sonst laege sie irgendwo in der langen Liste.
  const matches = useMemo(() => {
    return query.trim() ? filterTimezones(zones, query) : orderTimezones(zones, value);
  }, [zones, query, value]);
  const device = browserTimeZone();
  const suggestDevice = device !== value && zones.includes(device);

  return (
    <div>
      <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{value}</span>
        {timeIn(value) && <span className="text-white/45">· dort ist es jetzt {timeIn(value)} Uhr</span>}
        {value !== savedZone && <Badge tone="warn">nicht gespeichert</Badge>}
      </p>
      <div className="relative">
        <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/35" />
        <input
          type="search"
          aria-label="Zeitzone suchen"
          placeholder="Zeitzone suchen, z. B. Wien oder Berlin"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className={`${inputClass} pl-9`}
        />
      </div>
      <ul
        role="listbox"
        aria-label="Zeitzonen"
        className="mt-2 max-h-52 overflow-y-auto rounded-lg border border-white/10 bg-black/20"
      >
        {matches.slice(0, 200).map((z) => (
          <li key={z} role="option" aria-selected={z === value}>
            <button
              type="button"
              onClick={() => onChange(z)}
              className={`block w-full px-3 py-1.5 text-left text-sm transition ${
                z === value ? "bg-white/[0.1] font-medium text-white" : "text-white/70 hover:bg-white/[0.06] hover:text-white"
              }`}
            >
              {z}
              {germanZoneLabel(z) && <span className="ml-2 text-xs font-normal text-white/45">{germanZoneLabel(z)}</span>}
            </button>
          </li>
        ))}
        {matches.length === 0 && <li className="px-3 py-2 text-sm text-white/45">Keine Zeitzone gefunden.</li>}
        {matches.length > 200 && <li className="px-3 py-2 text-xs text-white/40">Weitere Treffer: Suche eingrenzen.</li>}
      </ul>
      {suggestDevice && (
        <p className="mt-2 text-sm text-white/60">
          Dieses Gerät steht in <span className="text-white">{device}</span>.{" "}
          <button type="button" onClick={() => onChange(device)} className="text-[var(--color-accent)] underline underline-offset-2 hover:brightness-125">
            Diese Zone übernehmen
          </button>
        </p>
      )}
    </div>
  );
}
