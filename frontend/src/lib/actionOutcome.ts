/**
 * Gemeinsame Auswertung einer Gate-Aktion fuer die Anzeige: Aktionen-Seite,
 * Server-Seite und Widget-Knoepfe meldeten frueher "Bestätigt" bzw.
 * das englische Rohwort ("failed"), auch wenn die Ausfuehrung gescheitert oder vom
 * Gate gesperrt war -- den Grund sah man nur im Protokoll.
 *
 * Eingabe ist `ActionOut` aus api/v1/actions.py (bzw. die kurze Antwort der
 * Extensions `{action_id, status, risk}`, dann ohne Grund): `result` ist das
 * `ActionResult` des Executors (`error` bei Fehlschlag, core/gate.py
 * _record_outcome()), `gate_decision` die Regel samt `detail` (Sperrliste,
 * Flap-Schutz) bzw. `user_reason` bei einer Ablehnung von Hand (gate.reject()).
 */

export interface ActionOutcomeSource {
  status?: string | null;
  result?: { success?: boolean; error?: string | null } | null;
  gate_decision?: { rule?: string | null; detail?: string | null; user_reason?: string | null } | null;
}

export type OutcomeTone = "success" | "error" | "pending" | "neutral";

export interface ActionOutcome {
  tone: OutcomeTone;
  text: string;
}

/** Textfarbe je Ergebnis, fuer Meldungszeilen unter Knoepfen/Tabellen. */
export const OUTCOME_TEXT_CLASS: Record<OutcomeTone, string> = {
  success: "text-emerald-300",
  error: "text-red-400",
  pending: "text-amber-300",
  neutral: "opacity-80",
};

function nonEmpty(value: string | null | undefined): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Ablehnung von Hand (Aktionen-Seite "Ablehnen") statt einer Gate-Regel. */
function isUserReject(a: ActionOutcomeSource): boolean {
  return a.gate_decision?.rule === "user:reject";
}

/** Warum eine Aktion nicht lief: Fehlermeldung der Ausfuehrung bzw. Begruendung der
 * Sperre/Ablehnung. `null` bei allen anderen Zustaenden oder ohne Angabe. */
export function actionReason(a: ActionOutcomeSource): string | null {
  if (a.status === "failed") return nonEmpty(a.result?.error);
  if (a.status === "denied") return nonEmpty(a.gate_decision?.detail) ?? nonEmpty(a.gate_decision?.user_reason);
  return null;
}

export function describeActionOutcome(a: ActionOutcomeSource): ActionOutcome {
  const reason = actionReason(a);
  switch (a.status) {
    case "succeeded":
      return { tone: "success", text: "Ausgeführt" };
    case "failed":
      return { tone: "error", text: `Fehlgeschlagen: ${reason ?? "unbekannter Fehler"}` };
    case "denied":
      if (isUserReject(a)) return { tone: "neutral", text: reason ? `Abgelehnt: ${reason}` : "Abgelehnt" };
      return { tone: "error", text: reason ? `Gesperrt: ${reason}` : "Von einer Sicherheitsregel gesperrt" };
    case "approved":
    case "executing":
      return { tone: "pending", text: "Läuft noch …" };
    case "proposed":
      return { tone: "pending", text: "Wartet auf Freigabe" };
    case "expired":
      return { tone: "neutral", text: "Abgelaufen" };
    case "dismissed":
      return { tone: "neutral", text: "Verworfen" };
    default:
      return { tone: "neutral", text: a.status ? `Unbekannter Zustand (${a.status})` : "Unbekannter Zustand" };
  }
}

/** Sieht `value` wie eine ActionOut-Antwort aus? Z. B. der Body einer 403 von
 * `POST /hosts/{id}/actions/{type}`, wenn das Gate sperrt (api/v1/hosts.py). */
export function isActionOutcomeSource(value: unknown): value is ActionOutcomeSource {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.status === "string" && ("gate_decision" in v || "result" in v);
}
