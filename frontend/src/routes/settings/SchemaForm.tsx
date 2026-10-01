/**
 * Baut ein Formular aus dem Einstellungs-Schema einer Erweiterung
 * (settings.schema.json). Unterstuetzt: Text, Zahl, Ja/Nein, Auswahl (enum),
 * Wortlisten (Array aus Strings), Listen von Eintraegen (Array aus Objekten) und
 * verschachtelte Objekte. Alles andere wird als JSON bearbeitet.
 *
 * Schema-Zusaetze fuer die Oberflaeche: `title`, `description`, `x-advanced`
 * (unter "Erweitert" eingeklappt), `x-hidden` (nicht anzeigen), `x-item-title`,
 * `x-enum-labels` (lesbare Texte fuer die Werte einer Auswahl), `x-widget`
 * ("schedule", "host", "host-tag", "remote-select"), `x-empty-label`,
 * `x-options-url` (remote-select), `pattern` und `x-pattern-message`.
 * Alle Zusaetze sind in docs/02-EXTENSION-API.md beschrieben.
 */
import { ChevronDown, ChevronRight, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";

import { SchedulePicker } from "../../components/SchedulePicker";
import { HostWidget, RemoteSelect } from "./SchemaWidgets";
import { Button, Toggle, inputClass } from "./ui";

export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  "x-advanced"?: boolean;
  "x-hidden"?: boolean;
  "x-item-title"?: string;
  "x-enum-labels"?: Record<string, string>;
  /**
   * "schedule": Cron-Feld als Zeitplan-Waehler (Wecker-Stil) statt Textfeld.
   * "host": Server aus der Serverliste (Text oder Liste von Namen).
   * "host-tag": Markierung (Tag) aus der Serverliste (Text oder Liste).
   * "remote-select": Auswahlliste von `x-options-url`, mit Textfeld als Rueckfall.
   */
  "x-widget"?: string;
  /** Text fuer "nichts gewaehlt" bei einer Auswahl (Standard: "– nicht gesetzt –"). */
  "x-empty-label"?: string;
  /** remote-select: Pfad unter /api/v1, der `{options: [{value, label}], error}` liefert. */
  "x-options-url"?: string;
  /** Regulaerer Ausdruck, den ein Text erfuellen muss (wie JSON-Schema, nicht verankert). */
  pattern?: string;
  /** Laengste erlaubte Laenge eines Textes mit `pattern` (Standard 2000, wie im Backend). */
  maxLength?: number;
  /** Deutsche Meldung, wenn `pattern` nicht passt. */
  "x-pattern-message"?: string;
  /** Kopfebene: die Erweiterung kann Nachrichten senden -> Knopf "Testnachricht senden". */
  "x-test-message"?: boolean;
}

const GENERIC_PATTERN_MESSAGE = "Das Format stimmt nicht.";
const PATTERN_MAX_LENGTH = 2000;

/** Die Meldung, wenn `value` das `pattern` des Schemas verletzt, sonst `null`. Leere Werte
 * sind immer erlaubt (Pflichtfelder prueft das Backend). */
export function patternError(schema: JsonSchema, value: unknown): string | null {
  if (!schema.pattern || typeof value !== "string" || value === "") return null;
  const limit = schema.maxLength && schema.maxLength > 0 ? schema.maxLength : PATTERN_MAX_LENGTH;
  if (value.length > limit) return `Zu lang (höchstens ${limit} Zeichen).`;
  try {
    return new RegExp(schema.pattern).test(value) ? null : (schema["x-pattern-message"] ?? GENERIC_PATTERN_MESSAGE);
  } catch {
    return null; // ein kaputtes Muster im Schema darf das Speichern nie verhindern
  }
}

/** Alle Musterverstoesse in `values` als Liste lesbarer Meldungen (leer = alles in Ordnung). */
export function validateValues(schema: JsonSchema, values: Values, prefix = ""): string[] {
  const out: string[] = [];
  for (const [key, sub] of Object.entries(schema.properties ?? {})) {
    if (sub["x-hidden"]) continue;
    const value = values[key];
    const name = `${prefix}${labelOf(key, sub)}`;
    const message = patternError(sub, value);
    if (message) out.push(`${name}: ${message}`);
    if (sub.type === "object" && sub.properties && value && typeof value === "object" && !Array.isArray(value)) {
      out.push(...validateValues(sub, value as Values, `${name} › `));
    }
    if (sub.type === "array" && Array.isArray(value)) {
      const itemTitle = sub["x-item-title"] ?? "Eintrag";
      value.forEach((item, i) => {
        if (sub.items?.type === "object" && sub.items.properties && item && typeof item === "object") {
          const who = String((item as Values).name ?? "") || String(i + 1);
          out.push(...validateValues(sub.items, item as Values, `${name} › ${itemTitle} ${who} › `));
        } else if (sub.items) {
          const itemMessage = patternError(sub.items, item);
          if (itemMessage) out.push(`${name}: „${String(item)}“ – ${itemMessage}`);
        }
      });
    }
  }
  return out;
}

type Values = Record<string, unknown>;

function labelOf(key: string, schema: JsonSchema): string {
  return schema.title ?? key.replace(/_/g, " ");
}

export function SchemaFields({
  schema, values, onChange, idPrefix = "f",
}: {
  schema: JsonSchema;
  values: Values;
  onChange: (next: Values) => void;
  idPrefix?: string;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const entries = Object.entries(schema.properties ?? {}).filter(([, s]) => !s["x-hidden"]);
  const basic = entries.filter(([, s]) => !s["x-advanced"]);
  const advanced = entries.filter(([, s]) => s["x-advanced"]);
  const required = new Set(schema.required ?? []);

  const render = ([key, sub]: [string, JsonSchema]) => (
    <FieldFor
      key={key}
      id={`${idPrefix}-${key}`}
      name={key}
      schema={sub}
      required={required.has(key)}
      value={values[key]}
      onChange={(v) => {
        const next = { ...values };
        if (v === undefined) delete next[key];
        else next[key] = v;
        onChange(next);
      }}
    />
  );

  return (
    <div className="space-y-5">
      {basic.map(render)}
      {advanced.length > 0 && (
        <div className="rounded-lg border border-white/[0.08]">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-white/70 hover:text-white"
          >
            {showAdvanced ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            Erweitert ({advanced.length})
          </button>
          {showAdvanced && <div className="space-y-5 border-t border-white/[0.06] p-4">{advanced.map(render)}</div>}
        </div>
      )}
    </div>
  );
}

function FieldFor({
  id, name, schema, value, required, onChange,
}: {
  id: string;
  name: string;
  schema: JsonSchema;
  value: unknown;
  required: boolean;
  onChange: (v: unknown) => void;
}) {
  const label = labelOf(name, schema);
  const head = (
    <div className="mb-1.5">
      <label htmlFor={id} className="text-sm text-white/80">
        {label}
        {required && <span className="ml-0.5 text-[var(--color-accent)]" title="Pflichtfeld">*</span>}
      </label>
      {schema.description && <p className="mt-0.5 text-xs text-white/45">{schema.description}</p>}
    </div>
  );
  const fallback = schema.default;

  if (schema.enum) {
    return (
      <div>
        {head}
        <select id={id} value={String(value ?? fallback ?? "")} onChange={(e) => onChange(e.target.value || undefined)} className={inputClass}>
          {!required && fallback === undefined && <option value="">– nicht gesetzt –</option>}
          {schema.enum.map((opt) => <option key={String(opt)} value={String(opt)}>{schema["x-enum-labels"]?.[String(opt)] ?? String(opt)}</option>)}
        </select>
      </div>
    );
  }

  if (schema["x-widget"] === "schedule") {
    return (
      <div>
        {head}
        <SchedulePicker
          label={label}
          value={typeof value === "string" ? value : typeof fallback === "string" ? fallback : ""}
          onChange={(cron) => onChange(cron || undefined)}
        />
      </div>
    );
  }

  const widget = schema["x-widget"];
  const emptyLabel = schema["x-empty-label"] ?? "– nicht gesetzt –";
  const isWordList = schema.type === "array" && (!schema.items || schema.items.type === "string");
  if ((widget === "host" || widget === "host-tag") && (schema.type === "string" || isWordList)) {
    const multiple = schema.type === "array";
    return (
      <div>
        {head}
        <HostWidget
          id={id}
          kind={widget}
          multiple={multiple}
          value={multiple ? (Array.isArray(value) ? (value as string[]) : (fallback as string[] | undefined) ?? []) : typeof value === "string" ? value : typeof fallback === "string" ? fallback : undefined}
          onChange={onChange}
          emptyLabel={emptyLabel}
          fallback={multiple
            ? <WordList id={id} value={Array.isArray(value) ? (value as string[]) : (fallback as string[] | undefined) ?? []} onChange={onChange} />
            : <TextInput id={id} schema={schema} value={value} fallback={fallback} onChange={onChange} />}
        />
      </div>
    );
  }
  if (widget === "remote-select" && schema.type === "string" && schema["x-options-url"]) {
    return (
      <div>
        {head}
        <RemoteSelect
          id={id}
          url={schema["x-options-url"]}
          value={typeof value === "string" ? value : typeof fallback === "string" ? fallback : undefined}
          onChange={onChange}
          placeholder={typeof fallback === "string" ? `Standard: ${fallback}` : undefined}
          emptyLabel={emptyLabel}
        />
      </div>
    );
  }

  switch (schema.type) {
    case "string":
      return (
        <div>
          {head}
          <TextInput id={id} schema={schema} value={value} fallback={fallback} onChange={onChange} />
        </div>
      );
    case "number":
    case "integer":
      return (
        <div>
          {head}
          <input
            id={id}
            type="number"
            step={schema.type === "integer" ? 1 : "any"}
            value={typeof value === "number" ? value : ""}
            placeholder={typeof fallback === "number" ? `Standard: ${fallback}` : undefined}
            onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
            className={`${inputClass} max-w-xs`}
          />
        </div>
      );
    case "boolean": {
      const checked = typeof value === "boolean" ? value : Boolean(fallback);
      return (
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-white/80">{label}</p>
            {schema.description && <p className="mt-0.5 text-xs text-white/45">{schema.description}</p>}
          </div>
          <Toggle label={label} checked={checked} onChange={onChange} />
        </div>
      );
    }
    case "array":
      if (schema.items?.type === "object") {
        return <ObjectList id={id} label={label} schema={schema} value={Array.isArray(value) ? (value as Values[]) : []} onChange={onChange} head={head} />;
      }
      if (!schema.items || schema.items.type === "string") {
        return (
          <div>
            {head}
            <WordList id={id} itemSchema={schema.items} value={Array.isArray(value) ? (value as string[]) : (fallback as string[] | undefined) ?? []} onChange={onChange} />
          </div>
        );
      }
      break;
    case "object":
      if (schema.properties) {
        return (
          <fieldset className="rounded-lg border border-white/[0.08] p-4">
            <legend className="px-1 text-sm text-white/80">{label}</legend>
            {schema.description && <p className="mb-3 text-xs text-white/45">{schema.description}</p>}
            <SchemaFields schema={schema} values={(value as Values) ?? {}} onChange={onChange} idPrefix={id} />
          </fieldset>
        );
      }
      break;
  }
  return <JsonField id={id} head={head} value={value} onChange={onChange} />;
}

/** Einzeiliges Textfeld mit Musterpruefung (`pattern`). */
function TextInput({ id, schema, value, fallback, onChange }: { id: string; schema: JsonSchema; value: unknown; fallback: unknown; onChange: (v: unknown) => void }) {
  const error = patternError(schema, value);
  return (
    <>
      <input
        id={id}
        value={typeof value === "string" ? value : ""}
        placeholder={typeof fallback === "string" ? `Standard: ${fallback}` : undefined}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        className={`${inputClass} ${error ? "!border-red-500/60" : ""}`}
      />
      {error && <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
    </>
  );
}

function WordList({ id, value, onChange, itemSchema }: { id: string; value: string[]; onChange: (v: string[]) => void; itemSchema?: JsonSchema }) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  function add() {
    const word = draft.trim();
    if (!word || value.includes(word)) return;
    const problem = itemSchema ? patternError(itemSchema, word) : null;
    if (problem) { setError(problem); return; }
    setError(null);
    onChange([...value, word]);
    setDraft("");
  }
  return (
    <div className={`rounded-lg border bg-black/25 p-2 ${error ? "border-red-500/60" : "border-white/10"}`}>
      <div className="flex flex-wrap gap-1.5">
        {value.map((w) => (
          <span key={w} className="inline-flex items-center gap-1 rounded-md bg-white/[0.08] px-2 py-0.5 text-xs">
            {w}
            <button type="button" aria-label={`${w} entfernen`} onClick={() => onChange(value.filter((x) => x !== w))} className="text-white/40 hover:text-white">
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          placeholder={value.length ? "" : "Eintrag eingeben und Enter drücken"}
          onChange={(e) => { setDraft(e.target.value); setError(null); }}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }}
          onBlur={add}
          className="min-w-[10rem] flex-1 bg-transparent px-1 py-0.5 text-sm outline-none"
        />
      </div>
      {error && <p role="alert" className="mt-1 px-1 text-xs text-red-300">{error}</p>}
    </div>
  );
}

function ObjectList({
  id, label, schema, value, onChange, head,
}: {
  id: string;
  label: string;
  schema: JsonSchema;
  value: Values[];
  onChange: (v: Values[]) => void;
  head: React.ReactNode;
}) {
  const itemTitle = schema["x-item-title"] ?? "Eintrag";
  return (
    <div>
      {head}
      <div className="space-y-3">
        {value.length === 0 && <p className="rounded-lg border border-dashed border-white/15 px-4 py-5 text-center text-sm text-white/45">Noch kein {itemTitle} eingetragen.</p>}
        {value.map((item, i) => (
          <div key={i} data-testid={`${id}-item`} className="rounded-lg border border-white/10 bg-black/15 p-4">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium">{String(item.name ?? "") || `${itemTitle} ${i + 1}`}</p>
              <Button variant="ghost" onClick={() => onChange(value.filter((_, j) => j !== i))}>
                <Trash2 size={14} /> <span className="sr-only">{itemTitle} {i + 1} entfernen</span>
              </Button>
            </div>
            <SchemaFields
              schema={schema.items!}
              values={item}
              idPrefix={`${id}-${i}`}
              onChange={(next) => onChange(value.map((x, j) => (j === i ? next : x)))}
            />
          </div>
        ))}
      </div>
      <div className="mt-3">
        <Button onClick={() => onChange([...value, {}])}><Plus size={14} /> {itemTitle} hinzufügen</Button>
      </div>
      <span className="sr-only">{label}</span>
    </div>
  );
}

function JsonField({ id, head, value, onChange }: { id: string; head: React.ReactNode; value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(value === undefined ? "" : JSON.stringify(value, null, 2));
  const [invalid, setInvalid] = useState(false);
  return (
    <div>
      {head}
      <textarea
        id={id}
        rows={5}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (!e.target.value.trim()) { setInvalid(false); onChange(undefined); return; }
          try { onChange(JSON.parse(e.target.value)); setInvalid(false); } catch { setInvalid(true); }
        }}
        className={`${inputClass} font-mono text-xs ${invalid ? "border-red-500/60" : ""}`}
      />
      {invalid && <p className="mt-1 text-xs text-red-300">Kein gültiges JSON.</p>}
    </div>
  );
}
