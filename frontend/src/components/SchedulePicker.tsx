/**
 * Zeitplan-Waehler im Wecker-Stil: Haeufigkeit, grosse Uhrzeit, Wochentage zum
 * Antippen, Klartext und naechster Lauf. Liest und schreibt einen Cron-Ausdruck
 * (Minute Stunde Tag Monat Wochentag), damit Backend und Scheduler unveraendert
 * bleiben. Alles, was sich nicht in die einfachen Formen uebersetzen laesst, bleibt
 * als "Eigener (Cron)" bearbeitbar.
 *
 * Gemeinsam genutzt vom Kern (Einstellungen) und von Erweiterungsseiten (Skripte);
 * deshalb nur React, keine weiteren Abhaengigkeiten.
 */
import { useEffect, useState } from "react";

import { browserTimeZone, foreignDeckTimezone, useDeckTimezone } from "../lib/deckTimezone";

type Mode = "off" | "hourly" | "daily" | "weekly" | "monthly" | "custom";

export interface Schedule {
  mode: Mode;
  hour: number;
  minute: number;
  days: number[]; // 0 = Sonntag ... 6 = Samstag
  dayOfMonth: number;
  cron: string;
}

const DAY_SHORT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const DAY_LONG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0];

const pad = (n: number) => String(n).padStart(2, "0");
const isInt = (s: string) => /^\d+$/.test(s);

export function parseCron(cron: string | null | undefined): Schedule {
  const base: Schedule = { mode: "daily", hour: 3, minute: 0, days: [0], dayOfMonth: 1, cron: cron ?? "" };
  if (!cron || !cron.trim()) return { ...base, mode: "off" };
  const p = cron.trim().split(/\s+/);
  if (p.length !== 5) return { ...base, mode: "custom" };
  const [mi, h, dom, mon, dow] = p;
  if (!isInt(mi) || mon !== "*") return { ...base, mode: "custom" };
  const minute = Number(mi);
  if (h === "*" && dom === "*" && dow === "*") return { ...base, mode: "hourly", minute };
  if (!isInt(h)) return { ...base, mode: "custom" };
  const hour = Number(h);
  if (dom === "*" && dow === "*") return { ...base, mode: "daily", hour, minute };
  if (dom === "*" && /^[0-7](,[0-7])*$/.test(dow)) {
    const days = [...new Set(dow.split(",").map((d) => Number(d) % 7))].sort();
    return { ...base, mode: days.length === 7 ? "daily" : "weekly", hour, minute, days };
  }
  if (isInt(dom) && dow === "*") return { ...base, mode: "monthly", hour, minute, dayOfMonth: Number(dom) };
  return { ...base, mode: "custom" };
}

export function toCron(s: Schedule): string {
  switch (s.mode) {
    case "off": return "";
    case "hourly": return `${s.minute} * * * *`;
    case "daily": return `${s.minute} ${s.hour} * * *`;
    case "weekly": return `${s.minute} ${s.hour} * * ${(s.days.length ? s.days : [0]).slice().sort().join(",")}`;
    case "monthly": return `${s.minute} ${s.hour} ${s.dayOfMonth} * *`;
    default: return s.cron;
  }
}

export function describeSchedule(cron: string | null | undefined, offLabel = "Manuell"): string {
  const s = parseCron(cron);
  const time = `${pad(s.hour)}:${pad(s.minute)}`;
  switch (s.mode) {
    case "off": return offLabel;
    case "hourly": return s.minute === 0 ? "Stündlich zur vollen Stunde" : `Stündlich um :${pad(s.minute)}`;
    case "daily": return `Täglich um ${time}`;
    case "weekly": {
      const ordered = WEEK_ORDER.filter((d) => s.days.includes(d));
      if (ordered.join() === "1,2,3,4,5") return `Werktags um ${time}`;
      if (ordered.join() === "6,0") return `Am Wochenende um ${time}`;
      if (ordered.length === 1) return `Jeden ${DAY_LONG[ordered[0]]} um ${time}`;
      return `${ordered.map((d) => DAY_SHORT[d]).join(", ")} um ${time}`;
    }
    case "monthly": return `Monatlich am ${s.dayOfMonth}. um ${time}`;
    default: return `Eigener Zeitplan (${cron})`;
  }
}

const formatters = new Map<string, Intl.DateTimeFormat>();

/** Uhrzeit an der Wand in `timeZone` als "UTC-naive" Millisekunden (Datum und Uhrzeit, auf die Minute). */
function wallClock(date: Date, timeZone: string): number {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
    });
    formatters.set(timeZone, f);
  }
  const v: Record<string, number> = {};
  for (const p of f.formatToParts(date)) if (p.type !== "literal") v[p.type] = Number(p.value);
  return Date.UTC(v.year, v.month - 1, v.day, v.hour % 24, v.minute);
}

/** Der Zeitpunkt, an dem die Wanduhr in `timeZone` `wall` zeigt; `null`, wenn es die Zeit dort nicht
 * gibt (Sprung auf Sommerzeit). In der doppelten Stunde beim Zurueckstellen gilt der fruehere. */
function instantAt(wall: number, timeZone: string): Date | null {
  const DAY = 86_400_000;
  const offset = (t: number) => wallClock(new Date(t), timeZone) - Math.floor(t / 60000) * 60000;
  // Der Versatz kurz davor und kurz danach: bei einer Umstellung ergeben sich zwei Kandidaten.
  const candidates = [...new Set([offset(wall - DAY), offset(wall + DAY)])]
    .map((o) => wall - o)
    .filter((t) => wallClock(new Date(t), timeZone) === wall)
    .sort((a, b) => a - b);
  return candidates.length ? new Date(candidates[0]) : null;
}

/**
 * Naechster Lauf fuer die einfachen Formen, sonst null. Ohne `timeZone` (oder in der Zone des
 * Geraets) gilt die lokale Zeit; mit `timeZone` meint der Zeitplan die Uhrzeit DORT (Einstellung
 * System, Zeitzone) -- der Browser darf woanders stehen.
 */
export function nextRun(cron: string | null | undefined, from = new Date(), timeZone?: string | null): Date | null {
  const s = parseCron(cron);
  if (s.mode === "off" || s.mode === "custom") return null;
  const matches = (minute: number, hour: number, weekday: number, dayOfMonth: number) =>
    minute === s.minute &&
    (s.mode === "hourly" || hour === s.hour) &&
    (s.mode !== "weekly" || s.days.includes(weekday)) &&
    (s.mode !== "monthly" || dayOfMonth === s.dayOfMonth);
  const limit = 60 * 24 * 62;

  if (timeZone && timeZone !== browserTimeZone()) {
    // Durch die Wanduhr der Zone laufen (Minuten als UTC-naive Zeit, ohne Zeitumstellung) und den
    // Treffer erst am Ende in einen echten Zeitpunkt umrechnen.
    const t = new Date(wallClock(from, timeZone) + 60_000);
    for (let i = 0; i < limit; i++) {
      if (matches(t.getUTCMinutes(), t.getUTCHours(), t.getUTCDay(), t.getUTCDate())) {
        const at = instantAt(t.getTime(), timeZone);
        if (at && at.getTime() > from.getTime()) return at;
      }
      t.setUTCMinutes(t.getUTCMinutes() + 1);
    }
    return null;
  }

  const t = new Date(from.getTime());
  t.setSeconds(0, 0);
  t.setMinutes(t.getMinutes() + 1);
  for (let i = 0; i < limit; i++) {
    if (matches(t.getMinutes(), t.getHours(), t.getDay(), t.getDate())) return t;
    t.setMinutes(t.getMinutes() + 1);
  }
  return null;
}

const MODES: { id: Mode; label: string }[] = [
  { id: "hourly", label: "Stündlich" },
  { id: "daily", label: "Täglich" },
  { id: "weekly", label: "Wöchentlich" },
  { id: "monthly", label: "Monatlich" },
  { id: "custom", label: "Eigener" },
];

export function SchedulePicker({
  value, onChange, allowOff = false, offLabel = "Manuell", label = "Zeitplan",
}: {
  value: string | null | undefined;
  onChange: (cron: string) => void;
  allowOff?: boolean;
  offLabel?: string;
  label?: string;
}) {
  const [s, setS] = useState<Schedule>(() => parseCron(value));
  useEffect(() => {
    if ((value ?? "") !== toCron(s)) setS(parseCron(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  function update(patch: Partial<Schedule>) {
    const next = { ...s, ...patch };
    if (patch.mode === "custom" && !next.cron) next.cron = toCron({ ...s, mode: s.mode === "off" ? "daily" : s.mode });
    setS(next);
    onChange(toCron(next));
  }

  const cron = toCron(s);
  const deckZone = useDeckTimezone();
  const foreignZone = foreignDeckTimezone(deckZone);
  const next = nextRun(cron, new Date(), deckZone);
  const modes = allowOff ? [{ id: "off" as Mode, label: offLabel }, ...MODES] : MODES;

  return (
    <div className="rounded-xl border border-white/10 bg-black/20 p-4" role="group" aria-label={label}>
      <div className="mb-4 flex flex-wrap gap-1 rounded-lg bg-white/[0.04] p-1">
        {modes.map((m) => (
          <button
            key={m.id}
            type="button"
            aria-pressed={s.mode === m.id}
            onClick={() => update({ mode: m.id })}
            className={`flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition ${
              s.mode === m.id ? "bg-[var(--color-accent)] text-white shadow" : "text-white/60 hover:bg-white/[0.06] hover:text-white"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {s.mode !== "off" && s.mode !== "custom" && (
        <div className="flex flex-wrap items-center gap-5">
          <div className="flex items-center gap-1 font-mono text-4xl font-semibold tabular-nums tracking-tight">
            {s.mode === "hourly" ? (
              <span className="text-white/35">--</span>
            ) : (
              <select
                aria-label="Stunde"
                value={s.hour}
                onChange={(e) => update({ hour: Number(e.target.value) })}
                className="cursor-pointer appearance-none rounded-lg bg-white/[0.06] px-2 py-1 text-center outline-none hover:bg-white/[0.1] focus:ring-2 focus:ring-[var(--color-accent)]"
              >
                {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{pad(h)}</option>)}
              </select>
            )}
            <span className="text-white/40">:</span>
            <select
              aria-label="Minute"
              value={s.minute}
              onChange={(e) => update({ minute: Number(e.target.value) })}
              className="cursor-pointer appearance-none rounded-lg bg-white/[0.06] px-2 py-1 text-center outline-none hover:bg-white/[0.1] focus:ring-2 focus:ring-[var(--color-accent)]"
            >
              {Array.from({ length: 12 }, (_, i) => i * 5).concat(s.minute % 5 ? [s.minute] : []).sort((a, b) => a - b)
                .map((m) => <option key={m} value={m}>{pad(m)}</option>)}
            </select>
          </div>

          {s.mode === "weekly" && (
            <div className="flex gap-1.5">
              {WEEK_ORDER.map((d) => {
                const on = s.days.includes(d);
                return (
                  <button
                    key={d}
                    type="button"
                    aria-pressed={on}
                    aria-label={DAY_LONG[d]}
                    onClick={() => {
                      const days = on ? s.days.filter((x: number) => x !== d) : [...s.days, d];
                      update({ days: days.length ? days : [d] });
                    }}
                    className={`h-9 w-9 rounded-full text-xs font-semibold transition ${
                      on ? "accent-gradient text-white shadow" : "bg-white/[0.06] text-white/55 hover:bg-white/[0.12] hover:text-white"
                    }`}
                  >
                    {DAY_SHORT[d]}
                  </button>
                );
              })}
            </div>
          )}

          {s.mode === "monthly" && (
            <label className="flex items-center gap-2 text-sm text-white/70">
              am
              <select
                aria-label="Tag im Monat"
                value={s.dayOfMonth}
                onChange={(e) => update({ dayOfMonth: Number(e.target.value) })}
                className="rounded-lg border border-white/10 bg-black/25 px-2 py-1.5 text-sm outline-none"
              >
                {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}.</option>)}
              </select>
              Tag
            </label>
          )}
        </div>
      )}

      {s.mode === "custom" && (
        <label className="block text-sm">
          <span className="mb-1.5 block text-white/60">Cron-Ausdruck (Minute Stunde Tag Monat Wochentag)</span>
          <input
            aria-label="Cron-Ausdruck"
            value={s.cron}
            placeholder="*/15 * * * *"
            onChange={(e) => update({ cron: e.target.value })}
            className="w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 font-mono text-sm outline-none focus:border-[var(--color-accent)]"
          />
        </label>
      )}

      <p className="mt-3 flex flex-wrap items-center gap-x-2 text-sm">
        <span className="font-medium text-white">{describeSchedule(cron, offLabel)}</span>
        {next && (
          <span className="text-white/45">
            · nächster Lauf{" "}
            {next.toLocaleString("de-DE", {
              weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: deckZone ?? undefined,
            })}
          </span>
        )}
      </p>
      {foreignZone && <p className="mt-1 text-xs text-white/45">Uhrzeiten in {foreignZone}</p>}
    </div>
  );
}
