/**
 * Eigene Temperaturgrenze eines Servers: ab wann die Meldung „… ist heiß“ kommt.
 * Spricht `GET` und `PUT /ext/system/hosts/{id}/temp-limit` (Ändern braucht `settings.write`).
 */
import { useCallback, useEffect, useState } from "react";

import { deck } from "../../../_shared/frontend/src/deck";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { Button, Field, inputClass, Notice } from "../../../_shared/frontend/src/ui";

interface TempLimitOut {
  host_id: string;
  own_c: number | null;
  global_c: number;
  effective_c: number;
}

export const TEMP_LIMIT_MIN = 40;
export const TEMP_LIMIT_MAX = 110;

/** Die eingetippte Zahl, wenn sie eine ganze Zahl in der erlaubten Spanne ist, sonst `null`. */
export function parseLimit(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,3}$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= TEMP_LIMIT_MIN && value <= TEMP_LIMIT_MAX ? value : null;
}

function draftOf(limit: TempLimitOut): string {
  return limit.own_c === null ? "" : String(limit.own_c);
}

export function TempLimit({ hostId, onChanged }: { hostId: string; onChanged?: () => void }): JSX.Element {
  const [limit, setLimit] = useState<TempLimitOut | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const canChange = deck().hasPermission("settings.write");
  const url = `/ext/system/hosts/${encodeURIComponent(hostId)}/temp-limit`;

  const load = useCallback((isCurrent: () => boolean = () => true) => {
    setLoadError(null);
    fetchJson<TempLimitOut>(url)
      .then((out) => {
        if (!isCurrent()) return;
        setLimit(out);
        setDraft(draftOf(out));
      })
      .catch((err: unknown) => { if (isCurrent()) setLoadError(errorMessage(err)); });
  }, [url]);

  useEffect(() => {
    let current = true;
    setLimit(null);
    setMessage(null);
    load(() => current);
    return () => { current = false; };
  }, [load]);

  async function save(ownC: number | null) {
    setBusy(true);
    setMessage(null);
    try {
      const out = await fetchJson<TempLimitOut>(url, { method: "PUT", body: JSON.stringify({ own_c: ownC }) });
      setLimit(out);
      setDraft(draftOf(out));
      setMessage({
        kind: "ok",
        text: out.own_c === null
          ? `Gespeichert. Dieser Server warnt wieder ab ${out.global_c} °C, wie alle anderen.`
          : `Gespeichert. Dieser Server warnt jetzt ab ${out.own_c} °C.`,
      });
      onChanged?.();
    } catch (err) {
      setMessage({ kind: "error", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  if (loadError !== null) return <LoadError compact what="Die Temperaturgrenze" error={loadError} onRetry={() => load()} />;
  if (limit === null) return <p className="text-sm opacity-60">Lade die Temperaturgrenze …</p>;

  const typed = parseLimit(draft);
  const invalid = draft.trim() !== "" && typed === null;
  return (
    <div className="text-sm" data-testid="temp-limit">
      <p data-testid="temp-limit-current">
        {limit.own_c !== null
          ? `Dieser Server warnt ab ${limit.effective_c} °C (eigene Grenze). Für die anderen Server gilt ${limit.global_c} °C.`
          : `Dieser Server warnt ab ${limit.effective_c} °C (die Grenze für alle Server).`}
      </p>
      <p className="mt-1 text-xs opacity-60">Die Meldung kommt erst, wenn die Temperatur bei zwei Prüfungen hintereinander darüber liegt.</p>
      {message && <div className="mt-2"><Notice kind={message.kind} text={message.text} onClose={() => setMessage(null)} /></div>}
      {canChange ? (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <Field label="Warnen ab (°C)" className="w-32">
            <input
              type="number" inputMode="numeric" min={TEMP_LIMIT_MIN} max={TEMP_LIMIT_MAX} step={1}
              value={draft} placeholder={String(limit.global_c)} disabled={busy}
              aria-invalid={invalid} aria-describedby={invalid ? "temp-limit-hint" : undefined}
              onChange={(e) => setDraft(e.target.value)} className={inputClass}
            />
          </Field>
          <Button variant="primary" disabled={busy || typed === null || typed === limit.own_c} onClick={() => void save(typed)}>
            {busy ? "Speichert …" : "Speichern"}
          </Button>
          {limit.own_c !== null && (
            <Button disabled={busy} onClick={() => void save(null)}>Allgemeine Grenze nutzen</Button>
          )}
          {invalid && (
            <p id="temp-limit-hint" role="alert" className="basis-full text-xs text-red-300">
              Bitte eine ganze Zahl von {TEMP_LIMIT_MIN} bis {TEMP_LIMIT_MAX} eingeben.
            </p>
          )}
        </div>
      ) : (
        <p className="mt-1 text-xs opacity-60">Ändern dürfen nur Benutzer, die Einstellungen ändern dürfen.</p>
      )}
    </div>
  );
}
