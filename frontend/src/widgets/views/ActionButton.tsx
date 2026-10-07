import { useState } from "react";

import { actionReason, describeActionOutcome, type ActionOutcomeSource } from "../../lib/actionOutcome";
import {
  ActionWaitTimeout,
  GAVE_UP_WAITING_TEXT,
  isAbortError,
  isActionRunning,
  RUNNING_IN_BACKGROUND_TEXT,
  throwIfAborted,
  waitForAction,
} from "../../lib/actions";
import { api } from "../../lib/api";
import { useUnmountSignal } from "../../lib/lifecycle";
import { useAuthStore } from "../../state/auth";
import { confirmDialog } from "../../state/dialogs";
import { renderTemplate } from "../template";
import type { WidgetAction } from "../types";

const STYLE_CLASS: Record<WidgetAction["style"], string> = {
  primary: "accent-gradient text-white",
  secondary: "bg-white/10 text-current hover:bg-white/15",
  danger: "bg-red-500/20 text-red-200 hover:bg-red-500/30",
};

/**
 * Antwortform propose-artiger Endpunkte -- Feldnamen variieren je Extension
 * (generisches `/hosts/{id}/actions` liefert `id`, gameserver/backups liefern
 * `action_id`), deshalb werden beide akzeptiert.
 */
interface ProposeResult extends ActionOutcomeSource {
  id?: string;
  action_id?: string;
  status?: string;
  risk?: string;
}

/** Status, bei denen die direkte Antwort sicher ein Aktionsergebnis ist -- andere
 * Endpunkte liefern ebenfalls `status` (z. B. Shield-Vorfaelle "dismissed"). */
const DIRECT_FAILURE = new Set(["failed", "denied"]);

/** Fehlermeldung zu einer gescheiterten/gesperrten Aktion. Extensions
 * antworten meist nur mit `{action_id, status, risk}` -- den Grund liefert dann der
 * Eintrag unter `/actions/{id}`; ist der nicht lesbar, bleibt es beim Status. */
async function failureText(res: ProposeResult): Promise<string> {
  const actionId = res.id ?? res.action_id;
  let source: ActionOutcomeSource = res;
  if (!actionReason(res) && actionId) {
    source = await api.get<ActionOutcomeSource>(`/actions/${actionId}`).catch(() => res);
  }
  return describeActionOutcome(source).text;
}

/**
 * Fuehrt eine WidgetAction aus: `endpoint` ist ein Template relativ zu
 * `/api/v1/ext/<ext_id>/` (docs/02-EXTENSION-API.md §4). `confirm` fragt ueber den
 * echten globalen Dialog (state/dialogs.ts) statt `window.confirm()`.
 *
 * Der Core Action Gate (core/gate.py) laesst JEDE Aktion erst als `status="proposed"`
 * stehen -- ohne einen Folge-Call auf `/actions/{id}/approve` bliebe sie haengen. Fuer
 * jede WidgetAction ueber diese gemeinsame Komponente (u.a. gameserver Start/Stop)
 * gilt deshalb dieselbe "Auto-Freigabe wenn berechtigt"-Logik wie auf
 * ProxmoxNodePage.tsx/BackupsPage.tsx, hier zentral statt pro Extension.
 */
const FALSY = new Set(["", "false", "0", "no", "none", "null", "undefined"]);

/** `show_if` (SDK `WidgetAction.show_if`): fehlt es, ist der Knopf immer sichtbar. */
export function isActionVisible(action: WidgetAction, row: unknown): boolean {
  if (!action.show_if) return true;
  return !FALSY.has(renderTemplate(action.show_if, row).trim().toLowerCase());
}

export function ActionButton({ action, row, extId, onDone }: { action: WidgetAction; row: unknown; extId: string; onDone?: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Nachfragen zu einer Aktion im Hintergrund endet mit der Kachel. Das Signal gibt
  // es schon vor der Freigabe-Antwort.
  const unmountSignal = useUnmountSignal();

  /** Laeuft die Aktion nach der Antwort noch (202 'executing'), bis zum Ende
   * nachfragen -- der Knopf bleibt so lange auf "…". */
  async function settle(res: ProposeResult, actionId: string | undefined): Promise<ProposeResult> {
    if (!actionId || !isActionRunning(res.status)) return res;
    const signal = unmountSignal();
    throwIfAborted(signal); // Kachel schon weg, waehrend die Freigabe noch wartete
    setNotice(RUNNING_IN_BACKGROUND_TEXT);
    const final = await waitForAction<ProposeResult>(actionId, { signal });
    setNotice(null);
    return { ...final, id: final.id ?? actionId };
  }

  async function run() {
    if (action.confirm) {
      const ok = await confirmDialog(action.confirm_text ?? `"${action.label}" wirklich ausführen?`, {
        danger: action.style === "danger",
        confirmLabel: action.label,
      });
      // Kachel inzwischen abgebaut (Seite verlassen, waehrend die Rueckfrage offen war):
      // nicht mehr ausloesen.
      if (!ok || unmountSignal().aborted) return;
    }
    setPending(true);
    setError(null);
    setNotice(null);
    try {
      const endpoint = renderTemplate(action.endpoint, row);
      const path = `/ext/${extId}/${endpoint}`;
      let result: ProposeResult | undefined;
      if (action.method === "POST") result = await api.post<ProposeResult>(path, action.body ?? undefined);
      else if (action.method === "PUT") result = await api.put<ProposeResult>(path, action.body ?? undefined);
      else await api.delete(path);

      if (result?.status === "proposed") {
        const actionId = result.id ?? result.action_id;
        const risk = result.risk;
        if (actionId && risk && useAuthStore.getState().hasPermission(`actions.approve:${risk}`)) {
          // approve liefert das Ergebnis mit -- es wurde bisher verworfen.
          const approved = await settle(await api.post<ProposeResult>(`/actions/${actionId}/approve`), actionId);
          if (approved?.status && DIRECT_FAILURE.has(approved.status)) {
            setError(await failureText({ ...approved, id: approved.id ?? actionId }));
          }
        } else {
          setNotice(`"${action.label}" vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
        }
      } else if (result && isActionRunning(result.status) && (result.id ?? result.action_id)) {
        const final = await settle(result, result.id ?? result.action_id);
        if (final.status && DIRECT_FAILURE.has(final.status)) setError(await failureText(final));
      } else if (result?.status && DIRECT_FAILURE.has(result.status)) {
        // Gate-Sperre (Sperrliste, Flap-Schutz) oder sofortige Ausfuehrung im
        // Vollautonomie-Modus, die gescheitert ist.
        setError(await failureText(result));
      }
      onDone?.();
    } catch (err) {
      // Kachel geschlossen/Seite verlassen: nichts mehr anzeigen.
      if (isAbortError(err)) return;
      if (err instanceof ActionWaitTimeout) {
        setNotice(GAVE_UP_WAITING_TEXT);
        onDone?.();
      } else {
        setNotice(null);
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setPending(false);
    }
  }

  if (!isActionVisible(action, row)) return null;

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        disabled={pending}
        onClick={() => void run()}
        className={`rounded-md px-2 py-0.5 text-[11px] font-medium disabled:opacity-50 ${STYLE_CLASS[action.style]}`}
      >
        {pending ? "…" : action.label}
      </button>
      {error && <span className="text-[10px] text-red-400">{error}</span>}
      {notice && <span className="text-[10px] text-amber-400">{notice}</span>}
    </span>
  );
}
