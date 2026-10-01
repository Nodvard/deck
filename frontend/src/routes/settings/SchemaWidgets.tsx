/**
 * Auswahl-Widgets fuer das Einstellungs-Formular (siehe SchemaForm.tsx):
 *  - "host" / "host-tag": Server bzw. Markierungen aus der Serverliste (GET /hosts)
 *  - "remote-select": Auswahlliste, die eine Erweiterung selbst liefert (z. B. die
 *    Modelle eines KI-Servers)
 * Jedes Widget hat einen Rueckfall auf ein normales Textfeld, falls die Daten nicht
 * geladen werden koennen -- die Einstellungsseite bleibt so immer benutzbar.
 */
import { RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api";
import { inputClass } from "./ui";

export interface Choice {
  value: string;
  label: string;
}

interface HostRow {
  name: string;
  display_name?: string;
  tags?: string[];
}

// Mehrere Felder auf einer Seite fragen dieselbe Liste: eine laufende Anfrage wird geteilt,
// nach ihrem Ende wird nichts zwischengespeichert (Bearbeiten soll nie alte Daten zeigen).
const inFlight = new Map<string, Promise<unknown>>();
function sharedGet<T>(path: string): Promise<T> {
  let pending = inFlight.get(path) as Promise<T> | undefined;
  if (!pending) {
    pending = api.get<T>(path).finally(() => inFlight.delete(path));
    inFlight.set(path, pending);
  }
  return pending;
}

type Loaded<T> = { state: "loading" } | { state: "failed" } | { state: "ready"; data: T };

function useHosts(): Loaded<HostRow[]> {
  const [result, setResult] = useState<Loaded<HostRow[]>>({ state: "loading" });
  useEffect(() => {
    let alive = true;
    sharedGet<HostRow[]>("/hosts")
      .then((data) => alive && setResult({ state: "ready", data }))
      .catch(() => alive && setResult({ state: "failed" }));
    return () => { alive = false; };
  }, []);
  return result;
}

export function hostChoices(hosts: HostRow[]): Choice[] {
  return [...hosts]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((h) => ({ value: h.name, label: h.display_name && h.display_name !== h.name ? `${h.display_name} (${h.name})` : h.name }));
}

export function tagChoices(hosts: HostRow[]): Choice[] {
  const counts = new Map<string, number>();
  for (const h of hosts) for (const t of h.tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([tag, n]) => ({ value: tag, label: `${tag} (${n} Server)` }));
}

/** Auswahl aus `choices`: ein Wert (Text) oder mehrere (Liste). */
export function ChoiceField({
  id, choices, value, onChange, multiple, emptyLabel, addLabel,
}: {
  id: string;
  choices: Choice[];
  value: string | string[] | undefined;
  onChange: (v: string | string[] | undefined) => void;
  multiple: boolean;
  emptyLabel: string;
  addLabel: string;
}) {
  const labelOf = (v: string) => choices.find((c) => c.value === v)?.label ?? v;
  if (multiple) {
    const selected = Array.isArray(value) ? value : [];
    const open = choices.filter((c) => !selected.includes(c.value));
    return (
      <div className="rounded-lg border border-white/10 bg-black/25 p-2">
        <div className="flex flex-wrap gap-1.5">
          {selected.map((v) => (
            <span key={v} className="inline-flex items-center gap-1 rounded-md bg-white/[0.08] px-2 py-0.5 text-xs">
              {labelOf(v)}
              <button type="button" aria-label={`${v} entfernen`} onClick={() => onChange(selected.filter((x) => x !== v))} className="text-white/40 hover:text-white">
                <X size={12} />
              </button>
            </span>
          ))}
          {selected.length === 0 && <span className="px-1 py-0.5 text-xs text-white/40">Noch nichts ausgewählt.</span>}
        </div>
        <select
          id={id}
          value=""
          disabled={open.length === 0}
          onChange={(e) => e.target.value && onChange([...selected, e.target.value])}
          className={`${inputClass} mt-2`}
        >
          <option value="">{open.length === 0 ? "Alle sind schon ausgewählt" : addLabel}</option>
          {open.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
      </div>
    );
  }
  const current = typeof value === "string" ? value : "";
  const known = current === "" || choices.some((c) => c.value === current);
  return (
    <select id={id} value={current} onChange={(e) => onChange(e.target.value || undefined)} className={inputClass}>
      <option value="">{emptyLabel}</option>
      {!known && <option value={current}>{current} (nicht in der Liste)</option>}
      {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
    </select>
  );
}

/** `x-widget: "host"` (Servernamen) bzw. `"host-tag"` (Markierungen). */
export function HostWidget({
  id, kind, multiple, value, onChange, emptyLabel, fallback,
}: {
  id: string;
  kind: "host" | "host-tag";
  multiple: boolean;
  value: string | string[] | undefined;
  onChange: (v: string | string[] | undefined) => void;
  emptyLabel: string;
  /** Textfeld bzw. Wortliste, falls die Serverliste nicht geladen werden kann. */
  fallback: JSX.Element;
}) {
  const hosts = useHosts();
  if (hosts.state === "failed") return fallback;
  if (hosts.state === "loading") {
    return <select id={id} disabled className={inputClass}><option>Lade Serverliste …</option></select>;
  }
  const choices = kind === "host" ? hostChoices(hosts.data) : tagChoices(hosts.data);
  if (choices.length === 0) return fallback;
  return (
    <ChoiceField
      id={id} choices={choices} value={value} onChange={onChange} multiple={multiple} emptyLabel={emptyLabel}
      addLabel={kind === "host" ? "Server hinzufügen …" : "Markierung hinzufügen …"}
    />
  );
}

interface RemoteOptions {
  options: Choice[];
  error?: string | null;
}

/**
 * `x-widget: "remote-select"`: Auswahlliste von `x-options-url` (Pfad unter /api/v1, Antwort
 * `{options: [{value, label}], error}`). Es werden bewusst keine Formularwerte mitgeschickt: die
 * Erweiterung fragt nur das, was gespeichert ist (sonst waere der Endpunkt ein Weg fuer
 * Anfragen an beliebige Adressen). Nach einer neuen Adresse: speichern, dann "Neu laden".
 */
export function RemoteSelect({
  id, url, value, onChange, placeholder, emptyLabel,
}: {
  id: string;
  url: string;
  value: string | undefined;
  onChange: (v: string | undefined) => void;
  placeholder?: string;
  emptyLabel: string;
}) {
  const path = url;

  const [result, setResult] = useState<Loaded<RemoteOptions>>({ state: "loading" });
  const [reloads, setReloads] = useState(0);
  const load = useCallback(() => setReloads((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    setResult({ state: "loading" });
    api.get<RemoteOptions>(path)
      .then((data) => alive && setResult({ state: "ready", data }))
      .catch(() => alive && setResult({ state: "failed" }));
    return () => { alive = false; };
  }, [path, reloads]);

  const text = (
    <input
      id={id}
      value={value ?? ""}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      className={inputClass}
    />
  );

  if (result.state === "loading") {
    return <select id={id} disabled className={inputClass}><option>{value ?? "Lade Auswahl …"}</option></select>;
  }
  const options = result.state === "ready" ? result.data.options : [];
  const error = result.state === "ready" ? result.data.error : "Die Auswahl konnte nicht geladen werden.";
  if (options.length === 0) {
    return (
      <div>
        {text}
        <p className="mt-1 flex items-center gap-2 text-xs text-amber-300/90">
          {error ?? "Keine Auswahl verfügbar."} Der Name lässt sich auch von Hand eintragen.
          <button type="button" onClick={load} className="inline-flex items-center gap-1 text-white/60 hover:text-white"><RefreshCw size={11} /> Neu laden</button>
        </p>
      </div>
    );
  }
  return (
    <div>
      <div className="flex gap-2">
        <ChoiceField id={id} choices={options} value={value} onChange={(v) => onChange(v as string | undefined)} multiple={false} emptyLabel={emptyLabel} addLabel="" />
        <button type="button" aria-label="Auswahl neu laden" onClick={load} className="flex-none rounded-lg border border-white/10 bg-white/[0.06] px-2.5 text-white/60 hover:bg-white/[0.12] hover:text-white">
          <RefreshCw size={14} />
        </button>
      </div>
    </div>
  );
}
