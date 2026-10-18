/** Formular-Ansicht einer Compose-Datei: dieselbe Datei wie im Text, geändert wird nur die bearbeitete Stelle. */
import { useMemo, useRef, useState } from "react";

import { Button, inputClass } from "../../../_shared/frontend/src/ui";

import { ENV_KEY_RE, SERVICE_NAME_RE, applyOp, readForm, type FormOp, type ListFieldName, type ServiceForm } from "./composeForm";

const RESTART: [string, string][] = [
  ["", "Kein Eintrag (startet nicht von selbst neu)"],
  ["unless-stopped", "Neu starten, außer von Hand gestoppt"],
  ["always", "Immer neu starten"],
  ["on-failure", "Nur nach einem Fehler neu starten"],
  ["no", "Nie neu starten"],
];
const LISTS: { field: ListFieldName; label: string; hint: string; placeholder: string }[] = [
  { field: "ports", label: "Ports", hint: "Links der Port auf dem Server, rechts der im Container.", placeholder: "8080:80" },
  { field: "volumes", label: "Ordner und Volumes", hint: "Links ein Ordner auf dem Server oder ein Volume, rechts der Ordner im Container.", placeholder: "./daten:/data" },
];
const small = `${inputClass} !px-2 !py-1 !text-xs`;

/** Eingabefeld, das beim Tippen die Datei vom Stand beim Hineinklicken aus ändert, damit sich die Schreibweise nicht unterwegs verschiebt. */
function EditField({ label, value, placeholder, disabled, onEdit, onStart, onEnd }: {
  label: string;
  value: string;
  placeholder?: string;
  disabled?: boolean;
  onEdit: (value: string) => void;
  onStart: () => void;
  onEnd: () => void;
}): JSX.Element {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <input aria-label={label} className={small} value={draft ?? value} placeholder={placeholder} disabled={disabled} spellCheck={false}
      onFocus={() => { onStart(); setDraft(value); }}
      onBlur={() => { setDraft(null); onEnd(); }}
      onChange={(event) => { setDraft(event.target.value); onEdit(event.target.value); }} />
  );
}

function Locked({ reason }: { reason: string }): JSX.Element {
  return <p className="text-amber-300/90">{reason}</p>;
}

export function StackForm({ text, onChange, disabled }: { text: string; onChange: (text: string) => void; disabled?: boolean }): JSX.Element {
  const form = useMemo(() => readForm(text), [text]);
  const [problem, setProblem] = useState<string | null>(null);
  const [adding, setAdding] = useState<Record<string, string>>({});
  const [service, setService] = useState({ name: "", image: "" });
  const base = useRef<string | null>(null);

  function run(op: FormOp, fromBase = false): boolean {
    const out = applyOp(fromBase && base.current != null ? base.current : text, op);
    if ("error" in out) {
      setProblem(out.error);
      return false;
    }
    setProblem(null);
    if (out.text !== text) onChange(out.text);
    return true;
  }

  const typing = { onStart: () => { base.current = text; }, onEnd: () => { base.current = null; }, disabled };

  if (form.locked) return <Locked reason={form.locked} />;

  function addTo(key: string, op: FormOp) {
    if (run(op)) setAdding((all) => ({ ...all, [key]: "" }));
  }

  function list(svc: ServiceForm, field: ListFieldName, label: string, hint: string, placeholder: string): JSX.Element {
    const data = svc[field];
    const key = `${svc.name}/${field}`;
    const draft = adding[key] ?? "";
    return (
      <div className="flex flex-col gap-1" key={field}>
        <span className="opacity-70">{label}</span>
        {data.locked ? <Locked reason={data.locked} /> : (
          <>
            {data.items.map((item, index) => (
              <div key={index} className="flex items-center gap-1.5">
                {item.editable
                  ? <EditField label={`${label} ${index + 1} von ${svc.name}`} value={item.value} {...typing}
                      onEdit={(value) => run({ op: "setItem", service: svc.name, field, index, value }, true)} />
                  : <span className="flex-1 break-all font-mono opacity-80" title="Ausführlich geschrieben, bitte im Text ändern.">{item.value}</span>}
                <Button small disabled={disabled} ariaLabel={`${label} ${index + 1} von ${svc.name} entfernen`}
                  onClick={() => run({ op: "removeItem", service: svc.name, field, index })}>Entfernen</Button>
              </div>
            ))}
            <div className="flex items-center gap-1.5">
              <input aria-label={`${label} für ${svc.name} hinzufügen`} className={small} value={draft} placeholder={placeholder} disabled={disabled} spellCheck={false}
                onChange={(event) => setAdding((all) => ({ ...all, [key]: event.target.value }))} />
              <Button small disabled={disabled || !draft.trim()} onClick={() => addTo(key, { op: "addItem", service: svc.name, field, value: draft.trim() })}>Hinzufügen</Button>
            </div>
            <span className="opacity-60">{hint}</span>
          </>
        )}
      </div>
    );
  }

  function environment(svc: ServiceForm): JSX.Element {
    const env = svc.environment;
    const keyDraft = adding[`${svc.name}/env-key`] ?? "";
    const valueDraft = adding[`${svc.name}/env-value`] ?? "";
    const keyOk = ENV_KEY_RE.test(keyDraft);
    return (
      <div className="flex flex-col gap-1">
        <span className="opacity-70">Umgebungsvariablen</span>
        {env.locked ? <Locked reason={env.locked} /> : (
          <>
            {env.entries.map((entry, index) => (
              <div key={`${entry.key}-${index}`} className="flex items-center gap-1.5">
                <span className="w-2/5 shrink-0 break-all font-mono">{entry.key}</span>
                {entry.editable
                  ? <EditField label={`Wert von ${entry.key} in ${svc.name}`} value={entry.value} {...typing}
                      onEdit={(value) => run({ op: "setEnv", service: svc.name, key: entry.key, value }, true)} />
                  : <span className="flex-1 break-all font-mono opacity-80" title="Besonders geschrieben, bitte im Text ändern.">{entry.value}</span>}
                <Button small disabled={disabled || !entry.editable} ariaLabel={`${entry.key} aus ${svc.name} entfernen`}
                  onClick={() => run({ op: "removeEnv", service: svc.name, key: entry.key })}>Entfernen</Button>
              </div>
            ))}
            <div className="flex items-center gap-1.5">
              <input aria-label={`Name der neuen Variable für ${svc.name}`} className={`${small} !w-2/5`} value={keyDraft} placeholder="NAME" disabled={disabled} spellCheck={false}
                onChange={(event) => setAdding((all) => ({ ...all, [`${svc.name}/env-key`]: event.target.value }))} />
              <input aria-label={`Wert der neuen Variable für ${svc.name}`} className={small} value={valueDraft} placeholder="Wert" disabled={disabled} spellCheck={false}
                onChange={(event) => setAdding((all) => ({ ...all, [`${svc.name}/env-value`]: event.target.value }))} />
              <Button small disabled={disabled || !keyOk || env.entries.some((e) => e.key === keyDraft)}
                onClick={() => { if (run({ op: "setEnv", service: svc.name, key: keyDraft, value: valueDraft })) setAdding((all) => ({ ...all, [`${svc.name}/env-key`]: "", [`${svc.name}/env-value`]: "" })); }}>
                Hinzufügen
              </Button>
            </div>
            {env.list && <span className="opacity-60">Steht in der Datei als Liste „NAME=Wert“ und bleibt so.</span>}
          </>
        )}
      </div>
    );
  }

  const nameTaken = form.services.some((s) => s.name === service.name);
  return (
    <div className="flex flex-col gap-3" data-testid="stack-form">
      <p className="opacity-70">
        Das Formular ändert nur die Stellen, die du hier bearbeitest. Kommentare und der Rest der Datei bleiben, wie sie sind; alles Weitere geht im Text.
      </p>
      {form.notes.map((note) => <p key={note} className="opacity-70">{note}</p>)}
      {problem && <p className="text-red-400" role="alert">{problem}</p>}
      {form.services.length === 0 && <p className="opacity-70">Noch kein Dienst in der Datei.</p>}
      {form.services.map((svc) => (
        <section key={svc.name} className="flex flex-col gap-2 rounded border border-white/10 p-2" aria-label={`Dienst ${svc.name}`}>
          <h4 className="font-semibold">Dienst „{svc.name}“</h4>
          {svc.locked ? <Locked reason={svc.locked} /> : (
            <>
              <label className="flex flex-col gap-1">
                <span className="opacity-70">Image</span>
                {svc.imageLocked ? <Locked reason={svc.imageLocked} /> : (
                  <EditField label={`Image von ${svc.name}`} value={svc.image ?? ""} placeholder="nginx:1.27" {...typing}
                    onEdit={(value) => run({ op: "set", service: svc.name, field: "image", value }, true)} />
                )}
              </label>
              <label className="flex flex-col gap-1">
                <span className="opacity-70">Neustart</span>
                {svc.restartLocked ? <Locked reason={svc.restartLocked} /> : (
                  <select aria-label={`Neustart von ${svc.name}`} className={small} value={svc.restart ?? ""} disabled={disabled}
                    onChange={(event) => run(event.target.value
                      ? { op: "set", service: svc.name, field: "restart", value: event.target.value }
                      : { op: "unset", service: svc.name, field: "restart" })}>
                    {svc.restart && !RESTART.some(([v]) => v === svc.restart) && <option value={svc.restart}>{svc.restart}</option>}
                    {RESTART.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                )}
              </label>
              {LISTS.map(({ field, label, hint, placeholder }) => list(svc, field, label, hint, placeholder))}
              {environment(svc)}
            </>
          )}
        </section>
      ))}
      <div className="flex flex-wrap items-center gap-1.5">
        <input aria-label="Name des neuen Dienstes" className={`${small} !w-40`} value={service.name} placeholder="Name, z. B. app" disabled={disabled} spellCheck={false}
          onChange={(event) => setService((s) => ({ ...s, name: event.target.value }))} />
        <input aria-label="Image des neuen Dienstes" className={`${small} !w-48`} value={service.image} placeholder="Image, z. B. nginx:1.27" disabled={disabled} spellCheck={false}
          onChange={(event) => setService((s) => ({ ...s, image: event.target.value }))} />
        <Button small disabled={disabled || !SERVICE_NAME_RE.test(service.name) || nameTaken || !service.image.trim()}
          onClick={() => { if (run({ op: "addService", name: service.name, image: service.image.trim() })) setService({ name: "", image: "" }); }}>
          Dienst hinzufügen
        </Button>
        {nameTaken && <span className="text-amber-300">Den Namen gibt es schon.</span>}
      </div>
    </div>
  );
}
