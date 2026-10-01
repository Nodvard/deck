/**
 * Karte "Zeit & Protokoll" (Einstellungen, System): Zeitzone fuer Zeitplaene und Wartungsfenster
 * (`system.timezone`), wie lange das Protokoll aufbewahrt wird (`audit.retention_days`) und wie lange
 * der Verlauf der Job-Läufe samt Protokolldateien (`jobs.run_retention_days`). GET/PUT /settings/{key}.
 */
import { useMemo, useState } from "react";

import { api } from "../../lib/api";
import { setDeckTimezone } from "../../lib/deckTimezone";
import { TimezonePicker } from "./TimezonePicker";
import { Button, Card, Field, NoticeLine, errorText, inputClass, type Notice } from "./ui";

export const RETENTION_MIN_DAYS = 7;
export const RETENTION_MAX_DAYS = 3650;
export const RUN_RETENTION_MIN_DAYS = 1;
export const RUN_RETENTION_MAX_DAYS = 3650;
export const RUN_RETENTION_DEFAULT_DAYS = 30;

/** Zeile von GET /settings. */
interface SettingRow {
  key: string;
  value: unknown;
}

export function TimeAndLogCard({ settings }: { settings: SettingRow[] }): JSX.Element {
  const stored = useMemo(() => Object.fromEntries(settings.map((r) => [r.key, r.value])), [settings]);
  const savedZone = typeof stored["system.timezone"] === "string" ? (stored["system.timezone"] as string) : "Europe/Berlin";
  const savedDays = typeof stored["audit.retention_days"] === "number" ? (stored["audit.retention_days"] as number) : 90;

  const savedRunDays =
    typeof stored["jobs.run_retention_days"] === "number" ? (stored["jobs.run_retention_days"] as number) : RUN_RETENTION_DEFAULT_DAYS;

  const [saved, setSaved] = useState({ zone: savedZone, days: savedDays, runDays: savedRunDays });
  const [zone, setZone] = useState(savedZone);
  const [days, setDays] = useState(String(savedDays));
  const [runDays, setRunDays] = useState(String(savedRunDays));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const parsedDays = /^\d+$/.test(days.trim()) ? Number(days.trim()) : NaN;
  const daysValid = parsedDays >= RETENTION_MIN_DAYS && parsedDays <= RETENTION_MAX_DAYS;
  const parsedRunDays = /^\d+$/.test(runDays.trim()) ? Number(runDays.trim()) : NaN;
  const runDaysValid = parsedRunDays >= RUN_RETENTION_MIN_DAYS && parsedRunDays <= RUN_RETENTION_MAX_DAYS;
  const dirty = zone !== saved.zone || (daysValid && parsedDays !== saved.days) || (runDaysValid && parsedRunDays !== saved.runDays);

  async function save() {
    setBusy(true);
    setNotice(null);
    const next = { ...saved };
    try {
      if (zone !== saved.zone) {
        await api.put(`/settings/system.timezone`, { value: zone });
        next.zone = zone;
        setDeckTimezone(zone);
      }
      if (daysValid && parsedDays !== saved.days) {
        await api.put(`/settings/audit.retention_days`, { value: parsedDays });
        next.days = parsedDays;
      }
      if (runDaysValid && parsedRunDays !== saved.runDays) {
        await api.put(`/settings/jobs.run_retention_days`, { value: parsedRunDays });
        next.runDays = parsedRunDays;
      }
      setSaved(next);
      setNotice({ kind: "ok", text: "Gespeichert." });
    } catch (err) {
      // Was schon gespeichert ist, als gespeichert merken (die Zone kann geklappt haben, die Tage nicht).
      setSaved(next);
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Zeit & Protokoll"
      description="Zeitzone der Zeitpläne und wie lange Protokoll und Job-Verlauf aufbewahrt werden."
      footer={<Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Speichern</Button>}
    >
      <NoticeLine notice={notice} />

      <div className="space-y-5">
        <div>
          <p className="mb-1.5 text-sm text-white/70">Zeitzone</p>
          <TimezonePicker value={zone} savedZone={saved.zone} onChange={setZone} />
          <p className="mt-2 text-xs text-white/40">
            Gilt für alle Zeitpläne (auch die der Erweiterungen, z. B. das Morgen-Briefing) und die Wartungsfenster.
            Protokoll und Meldungen zeigen weiterhin die Uhrzeit deines Geräts. Bei einem Wechsel der Zone
            schiebt sich jeder Lauf auf dieselbe Uhrzeit in der neuen Zone; rund um die Zeitumstellung kann ein Lauf
            einmal ausfallen oder doppelt laufen.
          </p>
        </div>

        <Field
          label="Protokoll aufbewahren (Tage)"
          hint={`Zwischen ${RETENTION_MIN_DAYS} und ${RETENTION_MAX_DAYS} Tagen. Ältere Einträge werden jede Nacht gelöscht.`}
          className="max-w-xs"
        >
          <input
            type="number"
            inputMode="numeric"
            min={RETENTION_MIN_DAYS}
            max={RETENTION_MAX_DAYS}
            step={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            aria-label="Protokoll aufbewahren (Tage)"
            aria-invalid={!daysValid}
            className={inputClass}
          />
        </Field>
        {!daysValid && (
          <p role="alert" className="-mt-3 text-xs text-red-300">
            Bitte eine ganze Zahl zwischen {RETENTION_MIN_DAYS} und {RETENTION_MAX_DAYS} eingeben.
          </p>
        )}

        <Field
          label="Job-Verlauf aufbewahren (Tage)"
          hint={`Zwischen ${RUN_RETENTION_MIN_DAYS} und ${RUN_RETENTION_MAX_DAYS} Tagen. Ältere Läufe der Zeitpläne und ihre Protokolldateien werden jede Nacht gelöscht; die letzten 20 Läufe je Job und laufende Läufe bleiben immer.`}
          className="max-w-xs"
        >
          <input
            type="number"
            inputMode="numeric"
            min={RUN_RETENTION_MIN_DAYS}
            max={RUN_RETENTION_MAX_DAYS}
            step={1}
            value={runDays}
            onChange={(e) => setRunDays(e.target.value)}
            aria-label="Job-Verlauf aufbewahren (Tage)"
            aria-invalid={!runDaysValid}
            className={inputClass}
          />
        </Field>
        {!runDaysValid && (
          <p role="alert" className="-mt-3 text-xs text-red-300">
            Bitte eine ganze Zahl zwischen {RUN_RETENTION_MIN_DAYS} und {RUN_RETENTION_MAX_DAYS} eingeben.
          </p>
        )}
      </div>
    </Card>
  );
}
