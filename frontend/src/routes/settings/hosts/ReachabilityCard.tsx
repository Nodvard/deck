/**
 * Karte „Erreichbarkeit prüfen“ (Einstellungen -> Server & Zugänge): Schalter und Abstand der
 * regelmäßigen Prüfung für von Hand angelegte Server (`hosts.reachability.enabled` und
 * `hosts.reachability.interval_minutes`, GET/PUT /settings/{key}, Recht `settings.write`).
 * Ohne das Recht gibt es die Karte nicht.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../../lib/api";
import { useAuthStore } from "../../../state/auth";
import { Button, Card, Field, NoticeLine, Toggle, errorText, inputClass, type Notice } from "../ui";

export const KEY_ENABLED = "hosts.reachability.enabled";
export const KEY_INTERVAL = "hosts.reachability.interval_minutes";
export const DEFAULT_INTERVAL_MINUTES = 2;
export const INTERVAL_CHOICES = [1, 2, 3, 5, 10, 15, 30, 60];

interface SettingRow {
  key: string;
  value: unknown;
}

export function intervalLabel(minutes: number): string {
  if (minutes === 60) return "Jede Stunde";
  return minutes === 1 ? "Jede Minute" : `Alle ${minutes} Minuten`;
}

function ReachabilityForm({ settings }: { settings: SettingRow[] }) {
  const queryClient = useQueryClient();
  const stored = Object.fromEntries(settings.map((r) => [r.key, r.value]));
  const savedEnabled = typeof stored[KEY_ENABLED] === "boolean" ? (stored[KEY_ENABLED] as boolean) : true;
  const savedInterval = typeof stored[KEY_INTERVAL] === "number" ? (stored[KEY_INTERVAL] as number) : DEFAULT_INTERVAL_MINUTES;

  const [saved, setSaved] = useState({ enabled: savedEnabled, interval: savedInterval });
  const [enabled, setEnabled] = useState(savedEnabled);
  const [interval, setIntervalMinutes] = useState(savedInterval);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const choices = INTERVAL_CHOICES.includes(interval) ? INTERVAL_CHOICES : [...INTERVAL_CHOICES, interval].sort((a, b) => a - b);
  const dirty = enabled !== saved.enabled || interval !== saved.interval;

  async function save() {
    setBusy(true);
    setNotice(null);
    const next = { ...saved };
    try {
      if (enabled !== saved.enabled) {
        await api.put(`/settings/${KEY_ENABLED}`, { value: enabled });
        next.enabled = enabled;
      }
      if (interval !== saved.interval) {
        await api.put(`/settings/${KEY_INTERVAL}`, { value: interval });
        next.interval = interval;
      }
      setSaved(next);
      setNotice({ kind: "ok", text: "Gespeichert." });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      // Was schon gespeichert ist, als gespeichert merken.
      setSaved(next);
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Erreichbarkeit prüfen"
      description="Nodvard Deck schaut regelmäßig nach, ob deine von Hand angelegten Server noch antworten, und meldet es, wenn einer ausfällt oder wieder da ist."
      footer={<Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Speichern</Button>}
    >
      <NoticeLine notice={notice} />

      <div className="space-y-5">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm text-white/80">Erreichbarkeit regelmäßig prüfen</span>
          <Toggle checked={enabled} onChange={setEnabled} label="Erreichbarkeit regelmäßig prüfen" disabled={busy} />
        </div>

        <Field label="Wie oft" className="max-w-xs">
          <select
            aria-label="Wie oft prüfen"
            className={inputClass}
            value={interval}
            disabled={!enabled || busy}
            onChange={(e) => setIntervalMinutes(Number(e.target.value))}
          >
            {choices.map((m) => <option key={m} value={m}>{intervalLabel(m)}</option>)}
          </select>
        </Field>

        <p className="text-xs text-white/40">
          Geprüft wird nur, ob der SSH-Port des Servers (Standard 22, sonst der Port des Zugangs) eine Verbindung annimmt – ganz
          ohne Anmeldung, auch für Server ohne Zugang. Ein Server gilt erst nach zwei Fehlversuchen hintereinander als nicht
          erreichbar, bei „wieder da“ sofort. Meldungen gibt es nur bei einem Wechsel, im Wartungsfenster nur im Verlauf, und für einen
          Server, der noch nie geantwortet hat, nur den Zustand. Server, die ein Modul selbst einliest (z. B. über Proxmox),
          und Beispiel-Server werden hier nicht geprüft.
        </p>
      </div>
    </Card>
  );
}

export function ReachabilityCard() {
  const allowed = useAuthStore((s) => s.hasPermission)("settings.write");
  useAuthStore((s) => s.user);
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<SettingRow[]>("/settings"),
    enabled: allowed,
    retry: false,
  });

  if (!allowed) return null;
  if (settings.isError) {
    return (
      <Card title="Erreichbarkeit prüfen">
        <NoticeLine notice={{ kind: "error", text: "Die Einstellung konnte nicht geladen werden." }} />
      </Card>
    );
  }
  if (!settings.data) return null;
  return <ReachabilityForm settings={settings.data} />;
}
