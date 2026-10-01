/**
 * Automatik & Sicherheit: wie selbststaendig Aktionen ausgefuehrt werden, zusaetzliche
 * gesperrte Befehle und Wartungsfenster (GET/PUT /settings/{key}).
 */
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { SchedulePicker } from "../../components/SchedulePicker";
import { api } from "../../lib/api";
import { Button, Card, Field, NoticeLine, PageHeader, errorText, inputClass, type Notice } from "./ui";

interface MaintenanceWindow {
  cron: string;
  duration_minutes: number;
  host_ids: "all" | string[];
}

interface HostRow {
  id: string;
  display_name: string;
  name: string;
}

const MODES = [
  { value: "propose", title: "Nur vorschlagen", text: "Jede Aktion wartet auf eine Freigabe unter „Aktionen“. Empfohlen für den Anfang." },
  { value: "full", title: "Selbstständig handeln", text: "Aktionen bis zur gewählten Risikostufe laufen ohne Rückfrage; alles darüber wartet auf Freigabe." },
];

const RISKS = [
  { value: "low", label: "Niedrig", text: "z. B. Status abfragen, Logs lesen" },
  { value: "medium", label: "Mittel", text: "z. B. Dienst neu starten" },
  { value: "high", label: "Hoch", text: "z. B. Updates einspielen, Container ersetzen" },
  { value: "critical", label: "Kritisch", text: "z. B. Server neu starten, Daten löschen" },
];

const DURATIONS = [
  { minutes: 30, label: "30 Minuten" },
  { minutes: 60, label: "1 Stunde" },
  { minutes: 120, label: "2 Stunden" },
  { minutes: 240, label: "4 Stunden" },
  { minutes: 480, label: "8 Stunden" },
];

export function AutomationSettings(): JSX.Element {
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [hosts, setHosts] = useState<HostRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ key: string; value: unknown }[]>("/settings")
      .then((rows) => setValues(Object.fromEntries(rows.map((r) => [r.key, r.value]))))
      .catch((err: unknown) => setLoadError(errorText(err)));
    api.get<HostRow[]>("/hosts").then(setHosts).catch(() => setHosts([]));
  }, []);

  if (loadError) return <NoticeLine notice={{ kind: "error", text: loadError }} />;
  if (!values) return <p className="text-sm text-white/50">Lade …</p>;

  return (
    <div>
      <PageHeader title="Automatik & Sicherheit" description="Lege fest, was das System selbst tun darf und wann Wartung erlaubt ist." />
      <AutonomyCard mode={String(values["autonomy.mode"])} maxRisk={String(values["autonomy.max_risk"])} />
      <DenyPatternsCard initial={(values["security.deny_patterns"] as string[]) ?? []} />
      <MaintenanceCard initial={(values["maintenance.windows"] as MaintenanceWindow[]) ?? []} hosts={hosts} />
    </div>
  );
}

async function putSetting(key: string, value: unknown) {
  await api.put(`/settings/${key}`, { value });
}

function AutonomyCard({ mode: initialMode, maxRisk: initialRisk }: { mode: string; maxRisk: string }) {
  const [mode, setMode] = useState(initialMode);
  const [maxRisk, setMaxRisk] = useState(initialRisk);
  const [saved, setSaved] = useState({ mode: initialMode, maxRisk: initialRisk });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const dirty = mode !== saved.mode || maxRisk !== saved.maxRisk;

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      await putSetting("autonomy.mode", mode);
      await putSetting("autonomy.max_risk", maxRisk);
      setSaved({ mode, maxRisk });
      setNotice({ kind: "ok", text: "Gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Selbstständigkeit"
      description="Gilt für Aktionen, die von Regeln, Skripten oder Nodvard KI vorgeschlagen werden."
      footer={<Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Speichern</Button>}
    >
      <NoticeLine notice={notice} />
      <div role="radiogroup" aria-label="Modus" className="grid gap-3 sm:grid-cols-2">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={mode === m.value}
            onClick={() => setMode(m.value)}
            className={`rounded-lg border p-3 text-left transition ${
              mode === m.value ? "border-[var(--color-accent)] bg-white/[0.06]" : "border-white/10 hover:border-white/25"
            }`}
          >
            <span className="flex items-center gap-2 text-sm font-medium">
              <span className={`h-3.5 w-3.5 rounded-full border-2 ${mode === m.value ? "border-[var(--color-accent)] bg-[var(--color-accent)]" : "border-white/30"}`} />
              {m.title}
            </span>
            <span className="mt-1 block pl-5 text-xs text-white/55">{m.text}</span>
          </button>
        ))}
      </div>
      <div className={`mt-5 ${mode === "full" ? "" : "opacity-50"}`}>
        <p className="mb-2 text-sm text-white/70">Ohne Rückfrage erlaubt bis Risikostufe</p>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {RISKS.map((r) => (
            <button
              key={r.value}
              type="button"
              disabled={mode !== "full"}
              aria-pressed={maxRisk === r.value}
              onClick={() => setMaxRisk(r.value)}
              className={`rounded-lg border px-3 py-2 text-left transition disabled:cursor-not-allowed ${
                maxRisk === r.value ? "border-[var(--color-accent)] bg-white/[0.06]" : "border-white/10 hover:border-white/25"
              }`}
            >
              <span className="block text-sm font-medium">{r.label}</span>
              <span className="block text-[11px] text-white/45">{r.text}</span>
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}

function DenyPatternsCard({ initial }: { initial: string[] }) {
  const [patterns, setPatterns] = useState(initial);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function store(next: string[], text: string) {
    setBusy(true);
    setNotice(null);
    try {
      await putSetting("security.deny_patterns", next);
      setPatterns(next);
      setNotice({ kind: "ok", text });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  function add() {
    const value = draft.trim();
    if (!value) return;
    try {
      new RegExp(value);
    } catch {
      setNotice({ kind: "error", text: "Das ist kein gültiger regulärer Ausdruck." });
      return;
    }
    if (patterns.includes(value)) return setNotice({ kind: "error", text: "Dieses Muster steht schon in der Liste." });
    setDraft("");
    void store([...patterns, value], "Muster hinzugefügt.");
  }

  return (
    <Card
      title="Gesperrte Befehle"
      description="Befehle, die niemals automatisch ausgeführt werden – auch nicht mit Freigabe. Gefährliche Standardfälle (z. B. rm -rf /, Festplatte formatieren, SSH-Zugang kappen) sind immer gesperrt; hier ergänzt du eigene Muster (reguläre Ausdrücke)."
    >
      <NoticeLine notice={notice} />
      {patterns.length === 0 ? (
        <p className="mb-3 text-sm text-white/45">Keine eigenen Muster – es gelten nur die eingebauten Sperren.</p>
      ) : (
        <ul className="mb-3 divide-y divide-white/[0.06] rounded-lg border border-white/10">
          {patterns.map((p) => (
            <li key={p} className="flex items-center justify-between gap-2 px-3 py-2">
              <code className="truncate font-mono text-xs">{p}</code>
              <Button variant="ghost" disabled={busy} onClick={() => void store(patterns.filter((x) => x !== p), "Muster entfernt.")}>
                <Trash2 size={14} /> <span className="sr-only">Entfernen</span>
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input
          aria-label="Neues Muster"
          value={draft}
          placeholder="z. B. docker\s+volume\s+rm"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") add(); }}
          className={`${inputClass} font-mono`}
        />
        <Button busy={busy} onClick={add}><Plus size={14} /> Hinzufügen</Button>
      </div>
    </Card>
  );
}

function MaintenanceCard({ initial, hosts }: { initial: MaintenanceWindow[]; hosts: HostRow[] }) {
  const [windows, setWindows] = useState<MaintenanceWindow[]>(initial);
  const [saved, setSaved] = useState(JSON.stringify(initial));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const dirty = JSON.stringify(windows) !== saved;

  function update(index: number, patch: Partial<MaintenanceWindow>) {
    setWindows(windows.map((w, i) => (i === index ? { ...w, ...patch } : w)));
  }

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      await putSetting("maintenance.windows", windows);
      setSaved(JSON.stringify(windows));
      setNotice({ kind: "ok", text: "Wartungsfenster gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Wartungsfenster"
      description="Zeiträume für geplante Arbeiten (z. B. nächtliche Updates und Neustarts): Meldungen zu den betroffenen Servern werden währenddessen stummgeschaltet."
      footer={
        <>
          <Button onClick={() => setWindows([...windows, { cron: "0 3 * * *", duration_minutes: 60, host_ids: "all" }])}>
            <Plus size={14} /> Fenster hinzufügen
          </Button>
          <Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Speichern</Button>
        </>
      }
    >
      <NoticeLine notice={notice} />
      {windows.length === 0 && <p className="text-sm text-white/45">Kein Wartungsfenster festgelegt.</p>}
      <div className="space-y-3">
        {windows.map((w, i) => (
          <div key={i} data-testid="maintenance-window" className="rounded-lg border border-white/10 p-3">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium">Wartungsfenster {i + 1}</p>
              <Button variant="ghost" onClick={() => setWindows(windows.filter((_, j) => j !== i))}>
                <Trash2 size={14} /> <span className="sr-only">Fenster entfernen</span>
              </Button>
            </div>
            <p className="mb-1.5 text-sm text-white/70">Beginn</p>
            <SchedulePicker label={`Beginn Wartungsfenster ${i + 1}`} value={w.cron} onChange={(cron) => update(i, { cron: cron || "0 3 * * *" })} />
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Field label="Dauer">
                <select
                  value={DURATIONS.some((d) => d.minutes === w.duration_minutes) ? w.duration_minutes : "custom"}
                  onChange={(e) => e.target.value !== "custom" && update(i, { duration_minutes: Number(e.target.value) })}
                  className={inputClass}
                >
                  {DURATIONS.map((d) => <option key={d.minutes} value={d.minutes}>{d.label}</option>)}
                  {!DURATIONS.some((d) => d.minutes === w.duration_minutes) && <option value="custom">{w.duration_minutes} Minuten</option>}
                </select>
              </Field>
              <Field label="Gilt für">
                <select
                  value={w.host_ids === "all" ? "all" : "some"}
                  onChange={(e) => update(i, { host_ids: e.target.value === "all" ? "all" : [] })}
                  className={inputClass}
                >
                  <option value="all">Alle Server</option>
                  <option value="some">Ausgewählte Server</option>
                </select>
              </Field>
            </div>
            {w.host_ids !== "all" && (
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-sm">
                {hosts.length === 0 && <span className="text-xs text-white/45">Keine Server bekannt.</span>}
                {hosts.map((h) => {
                  const ids = w.host_ids as string[];
                  return (
                    <label key={h.id} className="flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={ids.includes(h.id)}
                        onChange={() => update(i, { host_ids: ids.includes(h.id) ? ids.filter((x) => x !== h.id) : [...ids, h.id] })}
                      />
                      {h.display_name || h.name}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
