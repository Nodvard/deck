/** Eigene Vorlagen für „Neuer Stack“: nur die Compose-Datei, ohne .env und ohne feste Passwörter. */
import { useState } from "react";

import { errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Button } from "../../../_shared/frontend/src/ui";

import { JSON_HEADERS } from "./StackEditor";

export interface OwnListed { id: string; name: string; summary: string; updated_at: string }
export interface OwnList { templates: OwnListed[]; max: number; problem?: string | null }
export interface OwnTemplate extends OwnListed { compose: string }

export const OWN_URL = "/ext/docker/own-templates";
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const SAVE_NOTE =
  "Gespeichert wird nur diese Compose-Datei, keine .env. Feste Passwörter, Tokens und Schlüssel werden abgelehnt, soweit sie sich erkennen " +
  "lassen. Eigene Vorlagen sehen alle, die Stacks anlegen dürfen.";

/**
 * Variablen ohne Vorgabe, die der Stack aus einer .env braucht; `required` sind die mit `:?`, ohne die schon das Anlegen
 * scheitert. `$$NAME` wertet erst der Container aus.
 */
export function neededValues(compose: string): { open: string[]; required: string[] } {
  const required = new Set([...compose.matchAll(/(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*):?\?/g)].map((m) => m[1]));
  const names = [...compose.matchAll(/(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_]|:?-)|(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)/g)].map((m) => m[1] ?? m[2]);
  return { open: [...new Set(names)].filter((n) => !required.has(n)), required: [...required] };
}

/** Vorschlag für den Namen eines Stacks aus dem Namen der Vorlage; leer, wenn sich keiner bilden lässt. */
export function stackNameOf(name: string): string {
  const slug = name.toLowerCase().replace(/ß/g, "ss").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+/, "").replace(/-+$/, "").slice(0, 63);
  return NAME_RE.test(slug) ? slug : "";
}

export function SaveTemplate({ compose, current, suggested, onSaved, onCancel }: {
  compose: string;
  /** Die gewählte eigene Vorlage; dann lässt sie sich ersetzen. */
  current?: OwnTemplate | null;
  suggested?: string;
  onSaved: (saved: OwnTemplate) => void;
  onCancel: () => void;
}): JSX.Element {
  const [name, setName] = useState(current?.name ?? suggested ?? "");
  const [summary, setSummary] = useState(current?.summary ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const ready = !busy && name.trim() !== "" && compose.trim() !== "";

  async function save(replace: boolean) {
    setBusy(true);
    setProblem(null);
    try {
      const url = replace && current ? `${OWN_URL}/${encodeURIComponent(current.id)}` : OWN_URL;
      onSaved(await fetchJson<OwnTemplate>(url, { method: replace && current ? "PUT" : "POST", headers: JSON_HEADERS, body: JSON.stringify({ name, summary, compose }) }));
    } catch (err) {
      setProblem(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1.5 rounded border border-white/10 bg-white/[0.03] p-2 text-xs" data-testid="save-template">
      <p className="opacity-70">{SAVE_NOTE}</p>
      <label className="flex flex-col gap-1">
        <span>Name der Vorlage</span>
        <input aria-label="Name der Vorlage" value={name} maxLength={60} onChange={(e) => setName(e.target.value)}
          className="rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]" />
      </label>
      <label className="flex flex-col gap-1">
        <span>Beschreibung (freiwillig)</span>
        <input aria-label="Beschreibung der Vorlage" value={summary} maxLength={300} onChange={(e) => setSummary(e.target.value)}
          className="rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]" />
      </label>
      {problem && <p className="break-words text-red-400" role="alert">{problem}</p>}
      <div className="flex flex-wrap gap-1.5">
        {current && <Button small variant="primary" disabled={!ready} onClick={() => void save(true)}>Vorlage „{current.name}“ ersetzen</Button>}
        <Button small variant={current ? "secondary" : "primary"} disabled={!ready} onClick={() => void save(false)}>
          {current ? "Als neue Vorlage speichern" : "Vorlage speichern"}
        </Button>
        <Button small disabled={busy} onClick={onCancel}>Abbrechen</Button>
      </div>
    </div>
  );
}
