import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import { Button, Card, NoticeLine, PageHeader, errorText, inputClass, type Notice } from "./ui";

interface MessageType { id: string; label: string }
type Priority = "min" | "low" | "default" | "high" | "urgent";
interface Preference { enabled: boolean; priority: Priority | null }
type Preferences = Record<string, Preference>;
const KEY = "notifications.push_types";
const PRIORITIES: Priority[] = ["min", "low", "default", "high", "urgent"];

export function NotificationSettings(): JSX.Element {
  const [types, setTypes] = useState<MessageType[]>([]);
  const [saved, setSaved] = useState<Preferences | null>(null);
  const [values, setValues] = useState<Preferences>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.get<MessageType[]>("/settings/notification-types"),
      api.get<{ key: string; value: unknown }[]>("/settings"),
    ]).then(([kinds, settings]) => {
      if (!active) return;
      const stored = (settings.find((row) => row.key === KEY)?.value ?? {}) as Preferences;
      const initial = Object.fromEntries(kinds.map(({ id }) => [id, stored[id] ?? { enabled: true, priority: null }]));
      setTypes(kinds);
      setSaved(initial);
      setValues(initial);
    }).catch((err: unknown) => { if (active) setNotice({ kind: "error", text: errorText(err) }); });
    return () => { active = false; };
  }, []);

  const dirty = saved !== null && JSON.stringify(saved) !== JSON.stringify(values);
  function change(id: string, preference: Partial<Preference>) {
    setValues((current) => ({ ...current, [id]: { ...current[id], ...preference } }));
  }
  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.put<{ value: Preferences }>(`/settings/${KEY}`, { value: values });
      setSaved(result.value);
      setValues(result.value);
      setNotice({ kind: "ok", text: "Benachrichtigungen gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Benachrichtigungen" description="Push-Meldungen je Art einstellen. Alle Meldungen bleiben im Meldungsverlauf sichtbar." />
      <NoticeLine notice={notice} />
      {saved === null ? (
        !notice && <p className="text-sm text-white/50">Lade …</p>
      ) : (
        <Card title="Push-Meldungen" description="„Wie bisher“ verwendet die Priorität aus den Einstellungen von Push-Nachrichten: standardmäßig Info = default, Warnung = high, Kritisch = urgent. Die Mindest-Stufe und Wartungsfenster gelten weiterhin."
          footer={<Button variant="primary" disabled={!dirty} busy={busy} onClick={() => void save()}>Speichern</Button>}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" aria-label="Meldungsarten">
              <thead><tr className="border-b border-white/10"><th scope="col" className="py-2">Meldungsart</th><th scope="col" className="px-3 py-2">An</th><th scope="col" className="py-2">Priorität</th></tr></thead>
              <tbody>{types.map(({ id, label }) => (
                <tr key={id} className="border-b border-white/[0.06]">
                  <th scope="row" className="py-3 pr-3 font-normal">{label}</th>
                  <td className="px-3 py-3"><input type="checkbox" role="switch" aria-label={`${label}: Push`} checked={values[id].enabled} disabled={busy} onChange={(event) => change(id, { enabled: event.target.checked })} /></td>
                  <td className="py-3"><select className={inputClass} aria-label={`${label}: Priorität`} value={values[id].priority ?? ""} disabled={busy} onChange={(event) => change(id, { priority: (event.target.value || null) as Priority | null })}>
                    <option value="">Wie bisher</option>
                    {PRIORITIES.map((priority) => <option key={priority} value={priority}>{priority}</option>)}
                  </select></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
