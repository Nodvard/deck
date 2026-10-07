/**
 * Bausteine zum Update-Helfer für die Karten „Updates“ (Update per Knopf) und „Kopien vor Updates“ (Rückweg):
 * Zustand des Helfers, Bestätigung mit Passwort (und, wenn Zwei-Faktor an ist, dem Code aus der App), Fortschritt und
 * Ergebnis. Daten und Ablauf: lib/updater.ts; Texte zu den Codes: updaterTexts.ts.
 *
 * Passwort und Code stehen nur im lokalen State des Formulars und gehen direkt an `requestUpdate`/`requestRollback`;
 * nach dem Absenden ist das Passwortfeld leer. Fragt der Server nach dem Code (`totp_missing`) oder stimmt er nicht
 * (`totp_wrong`/`totp_used`), bleibt das Passwort stehen und nur das Codefeld wird geleert (wie bei Sicherungen).
 */
import { AlertTriangle, CheckCircle2, ExternalLink, Info, Loader2, RotateCcw, ShieldAlert, XCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { formatWhen } from "../../lib/backups";
import { browserNavigation, formatDuration } from "../../lib/restore";
import {
  AFTER_SUCCESS_PATH,
  errorCode,
  helperReason,
  updaterTiming,
  useUpdateHelper,
  type FollowState,
  type HelperResult,
  type HelperView,
  type UpdateRequested,
} from "../../lib/updater";
import { SecondFactorField, isTotpMissing, isTotpRejected } from "./SecondFactorField";
import { Button, Field, NoticeLine, errorText, inputClass, type Notice } from "./ui";
import {
  FAILED_MANUAL_STEPS,
  HELPER_GUIDE_LABEL,
  HELPER_GUIDE_URL,
  HELPER_MISSING_LEAD,
  PRESENCE_TEXTS,
  STAGES,
  codeText,
  outcomeText,
  outcomeTitle,
  outcomeTone,
  stageOf,
  stepText,
  type PresenceReason,
} from "./updaterTexts";

/** Unix-Zeit (Sekunden) wie `formatWhen`. */
export function formatUnix(seconds: number | null | undefined): string {
  return typeof seconds === "number" ? formatWhen(new Date(seconds * 1000).toISOString()) : "–";
}

/** Zustand laden, sobald eine Karte ihn braucht; beide Karten teilen ihn. */
export function useHelperView(): { view: HelperView | null; follow: FollowState | null } {
  const view = useUpdateHelper((s) => s.view);
  const loaded = useUpdateHelper((s) => s.loaded);
  const follow = useUpdateHelper((s) => s.follow);
  const load = useUpdateHelper((s) => s.load);
  useEffect(() => {
    if (!loaded) void load();
  }, [loaded, load]);
  // Räumt der Helfer nach dem letzten Vorgang noch auf, gleich noch einmal nachsehen.
  const finishing = view?.ready_reason === "finishing" && !follow;
  useEffect(() => {
    if (!finishing) return;
    const timer = setTimeout(() => void load(), 5000);
    return () => clearTimeout(timer);
  }, [finishing, view, load]);
  return { view, follow };
}

/** Text einer abgelehnten Anforderung: der Satz vom Server, dazu der Grund des Helfers, falls er einen nennt. */
export function refusalText(err: unknown): string {
  const base = errorText(err);
  const reason = helperReason(err);
  if (!reason) return base;
  const extra = PRESENCE_TEXTS[reason as PresenceReason] ?? codeText(reason);
  return extra ? `${base} ${extra}` : base;
}

// ---------------------------------------------------------------------------
// Zustand des Helfers
// ---------------------------------------------------------------------------

const PRESENCE_TITLES: Record<PresenceReason, string> = {
  missing: "nicht eingerichtet",
  stale: "antwortet nicht",
  unsafe: "nicht sicher eingerichtet",
  proto: "passt nicht zu dieser Version von Nodvard Deck",
  invalid: "Rückmeldung unlesbar",
};

function ago(seconds: number | null): string {
  if (seconds === null) return "unbekannt";
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  return diff < 10 ? "gerade eben" : `vor ${formatDuration(diff)}`;
}

/** Eine Zeile zum Helfer: nicht eingerichtet, bereit, nicht bereit oder antwortet nicht. */
export function HelperStatusLine({ view }: { view: HelperView }): JSX.Element {
  if (!view.present) {
    const reason = (view.reason ?? "missing") as PresenceReason;
    if (reason === "missing") {
      return (
        <div className="flex items-start gap-2 text-xs text-white/55" data-testid="helper-state" data-state="missing">
          <Info size={14} className="mt-0.5 flex-none text-white/40" />
          <p className="min-w-0">
            <span className="font-medium text-white/75">Update-Helfer: {PRESENCE_TITLES.missing}.</span> {HELPER_MISSING_LEAD}{" "}
            <a href={HELPER_GUIDE_URL} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
              {HELPER_GUIDE_LABEL}<ExternalLink size={11} className="ml-1 inline align-baseline" />
            </a>
          </p>
        </div>
      );
    }
    return (
      <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100" data-testid="helper-state" data-state={reason}>
        <AlertTriangle size={16} className="mt-0.5 flex-none" />
        <p className="min-w-0">
          <span className="font-medium">Update-Helfer: {PRESENCE_TITLES[reason] ?? "nicht erreichbar"}.</span> {PRESENCE_TEXTS[reason] ?? ""}
        </p>
      </div>
    );
  }
  const details = (
    <span className="text-white/50">
      {view.helper_version ? ` · Version ${view.helper_version}` : ""} · letztes Lebenszeichen {ago(view.heartbeat_at)}
    </span>
  );
  if (view.ready) {
    return (
      <div className="text-sm" data-testid="helper-state" data-state="ready">
        <p className="flex items-start gap-2">
          <CheckCircle2 size={15} className="mt-0.5 flex-none text-emerald-300" />
          <span className="min-w-0"><span className="font-medium">Update-Helfer: bereit</span>{details}</span>
        </p>
        {view.ready_reason && <p className="mt-1 text-xs text-white/50">{codeText(view.ready_reason)}</p>}
      </div>
    );
  }
  const busy = view.state === "busy" || view.ready_reason === "finishing" || view.ready_reason === "busy";
  if (busy) {
    return (
      <div className="text-sm" data-testid="helper-state" data-state="busy">
        <p className="flex items-start gap-2">
          <Loader2 size={15} className="mt-0.5 flex-none animate-spin text-white/60" />
          <span className="min-w-0"><span className="font-medium">Update-Helfer: beschäftigt</span>{details}</span>
        </p>
        {view.ready_reason && <p className="mt-1 text-xs text-white/50">{codeText(view.ready_reason)}</p>}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100" data-testid="helper-state" data-state="not-ready">
      <p className="flex items-start gap-2">
        <AlertTriangle size={15} className="mt-0.5 flex-none" />
        <span className="min-w-0"><span className="font-medium">Update-Helfer: nicht bereit</span>{details}</span>
      </p>
      <p className="mt-1 text-amber-100/90">{codeText(view.ready_reason) ?? "Der Update-Helfer nennt keinen Grund. Sieh mit „docker compose logs updater“ nach."}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bestätigen
// ---------------------------------------------------------------------------

export interface ConfirmProps {
  /** Überschrift im Formular, z. B. „Auf Version 0.7.1 aktualisieren“. */
  title: string;
  submitLabel: string;
  variant: "primary" | "danger";
  /** Hinweise über den Feldern. */
  children?: ReactNode;
  /** Häkchen, das vor dem Absenden gesetzt sein muss (beim Rückweg mit Daten). */
  consent?: string | null;
  /** Fragt der Server erst nach dem Häkchen (`accept_data_loss`), erscheint es mit diesem Text. */
  lateConsent?: string;
  send: (password: string, totpCode: string, consent: boolean) => Promise<UpdateRequested>;
  onSent: (requested: UpdateRequested) => void;
  onCancel: () => void;
}

/**
 * Ablehnungen, nach denen der angezeigte Zustand nicht mehr stimmt (etwa: ein anderer Tab oder Owner hat gerade einen
 * Vorgang gestartet). Dann wird er neu gelesen; läuft ein Vorgang, folgt die Karte ihm (`load`).
 */
const STALE_VIEW_CODES = new Set([
  "helper_missing", "helper_busy", "helper_finishing", "helper_not_ready", "request_pending", "rate_limited", "data_unclear",
]);

/** Passwort, ggf. Code und Häkchen; zeigt Fehler des Servers und lässt das Passwort stehen, wo es stimmte. */
export function ConfirmForm({ title, submitLabel, variant, children, consent = null, lateConsent, send, onSent, onCancel }: ConfirmProps): JSX.Element {
  const [password, setPassword] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [code, setCode] = useState("");
  const [consentText, setConsentText] = useState<string | null>(consent);
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const codeMissing = needsCode && !code.trim();
  const ready = !!password && !codeMissing && (consentText === null || agreed) && !busy;

  async function submit() {
    const typed = password;
    const totp = code;
    setPassword("");
    setCode("");
    setBusy(true);
    setNotice(null);
    try {
      const requested = await send(typed, totp, consentText !== null && agreed);
      onSent(requested);
    } catch (err) {
      const refusal = errorCode(err);
      if (isTotpMissing(err) || isTotpRejected(err)) {
        // Das Passwort stimmte; es fehlt nur ein (gültiger) Code aus der App.
        setNeedsCode(true);
        setPassword(typed);
      } else if (refusal && !["not_owner", "password_missing"].includes(refusal)) {
        // Passwort und Code stimmten, abgelehnt hat die Prüfung danach: nichts noch einmal eintippen lassen. Einen Code
        // aus der App nimmt der Server nur einmal an: Er bleibt nicht stehen.
        setPassword(typed);
        if (refusal === "accept_data_loss" && lateConsent) setConsentText(lateConsent);
      }
      setNotice({ kind: "error", text: refusalText(err) });
      if (refusal && STALE_VIEW_CODES.has(refusal)) void useUpdateHelper.getState().load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="mt-3 space-y-3 rounded-lg bg-black/20 p-3"
      aria-label={title}
      onSubmit={(e) => { e.preventDefault(); if (ready) void submit(); }}
    >
      <p className="text-sm font-medium">{title}</p>
      {children}
      <NoticeLine notice={notice} />
      {consentText !== null && (
        <label className="flex items-start gap-2 text-sm text-white/80">
          <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-1" />
          <span>{consentText}</span>
        </label>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Dein Anmeldepasswort zur Bestätigung" className="min-w-0 basis-full sm:max-w-sm sm:flex-1 sm:basis-auto">
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
            aria-label="Anmeldepasswort zur Bestätigung" className={inputClass} autoFocus />
        </Field>
        {needsCode && (
          <SecondFactorField value={code} onChange={setCode} allowRecovery={false} className="min-w-0 basis-full sm:max-w-[12rem] sm:flex-1 sm:basis-auto" />
        )}
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>Abbrechen</Button>
        <Button type="submit" variant={variant} disabled={!ready} busy={busy}>{submitLabel}</Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Fortschritt und Ergebnis
// ---------------------------------------------------------------------------

const TONE_BOX = {
  good: "border-emerald-500/30 bg-emerald-500/10 text-emerald-100",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-100",
  bad: "border-red-500/40 bg-red-500/10 text-red-100",
} as const;

function ToneIcon({ tone }: { tone: keyof typeof TONE_BOX }) {
  if (tone === "good") return <CheckCircle2 size={16} className="mt-0.5 flex-none" />;
  if (tone === "bad") return <ShieldAlert size={16} className="mt-0.5 flex-none" />;
  return <XCircle size={16} className="mt-0.5 flex-none" />;
}

function ManualSteps() {
  return (
    <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
      {FAILED_MANUAL_STEPS.map((step) => <li key={step}>{step}</li>)}
    </ol>
  );
}

/**
 * Ein Ergebnis mit Titel, Satz und – bei `failed_manual` – den Schritten auf dem Server. `dataRevert`: der Rückweg sollte
 * die Daten mitnehmen (siehe `outcomeText`).
 */
export function ResultBox({ result, footer, dataRevert = false }: {
  result: Pick<HelperResult, "action" | "from" | "to" | "outcome" | "code">; footer?: ReactNode; dataRevert?: boolean;
}) {
  const tone = outcomeTone(result.outcome);
  return (
    <div role="status" className={`rounded-lg border px-3 py-2 text-sm ${TONE_BOX[tone]}`} data-testid="helper-result" data-outcome={result.outcome}>
      <div className="flex items-start gap-2">
        <ToneIcon tone={tone} />
        <div className="min-w-0">
          <p className="font-medium">{outcomeTitle(result.outcome)}</p>
          <p className="mt-0.5">{outcomeText(result, { dataRevert })}</p>
          {result.outcome === "failed_manual" && <ManualSteps />}
        </div>
      </div>
      {footer && <div className="mt-2 flex flex-wrap justify-end gap-2">{footer}</div>}
    </div>
  );
}

/** So lange bleibt „Bitte von Hand nachsehen“ bzw. „von außen geändert“ ein deutlicher Kasten, auch wenn der Helfer bereit ist. */
export const ALARM_RESULT_S = 24 * 3600;

/**
 * Letzter Vorgang, wenn gerade nichts läuft. `failed_manual` und `external_change` als deutlicher Kasten (mit den
 * Schritten auf dem Server), solange der Helfer nicht bereit ist oder es jünger als `ALARM_RESULT_S` ist. Danach läuft
 * alles wieder (bereit heißt: Nodvard Deck läuft und ist gesund), dann reicht die kurze Zeile.
 */
export function LastResultLine({ result, ready }: { result: HelperResult; ready: boolean }): JSX.Element {
  const alarm = result.outcome === "failed_manual" || result.outcome === "external_change";
  if (alarm && (!ready || Date.now() / 1000 - result.finished_at < ALARM_RESULT_S)) {
    return (
      <div className="mt-3">
        <p className="mb-1 text-xs text-white/45">Letzter Vorgang am {formatUnix(result.finished_at)}:</p>
        <ResultBox result={result} />
      </div>
    );
  }
  return (
    <p className="mt-3 text-xs text-white/50" data-testid="helper-last-result">
      <span className="font-medium text-white/70">Letzter Vorgang ({formatUnix(result.finished_at)}): {outcomeTitle(result.outcome)}.</span> {outcomeText(result)}
    </p>
  );
}

/** Fortschritt eines Vorgangs bis zum Ergebnis (oder bis zur Frist). */
export function FollowPanel({ follow }: { follow: FollowState }): JSX.Element {
  const dismiss = useUpdateHelper((s) => s.dismiss);
  const rollback = follow.action === "rollback";
  const what = rollback ? "Rückweg" : "Update";

  if (follow.phase === "done" && follow.result) {
    const footer = follow.reloading ? (
      <>
        <span className="mr-auto self-center text-xs opacity-80">Die Seite lädt gleich neu …</span>
        <Button onClick={() => browserNavigation.assign(AFTER_SUCCESS_PATH)}><RotateCcw size={14} /> Jetzt neu laden</Button>
      </>
    ) : (
      <Button onClick={dismiss}>Schließen</Button>
    );
    return <ResultBox result={follow.result} footer={footer} dataRevert={follow.dataRevert} />;
  }

  if (follow.phase === "timeout") {
    return (
      <div role="status" className={`rounded-lg border px-3 py-2 text-sm ${TONE_BOX.warn}`} data-testid="helper-timeout">
        <div className="flex items-start gap-2">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <div className="min-w-0">
            <p className="font-medium">Nach {formatDuration(follow.elapsedS)} noch kein Ergebnis</p>
            {follow.watching ? (
              <p className="mt-0.5">
                Der Update-Helfer hat zu diesem {what} noch kein Ergebnis gemeldet; bei einem langsamen Download oder wenn er zurückschalten muss, dauert es länger.
                {follow.running ? ` Gerade läuft Version ${follow.running}.` : ""} Diese Seite fragt alle {formatDuration(updaterTiming.latePollMs / 1000)} weiter
                nach und zeigt das Ergebnis, sobald es da ist. Auf dem Server zeigt „docker compose ps“, was läuft, und „docker compose logs
                updater“, was der Helfer gerade tut.
              </p>
            ) : (
              <p className="mt-0.5">
                Der Update-Helfer arbeitet nicht mehr an diesem {what}, ein Ergebnis dazu liegt aber nicht vor.
                {follow.running ? ` Gerade läuft Version ${follow.running}.` : ""} Sieh auf dem Server nach: „docker compose ps“ zeigt, was läuft,
                „docker compose logs updater“, was der Helfer getan hat.
              </p>
            )}
          </div>
        </div>
        <div className="mt-2 flex justify-end"><Button onClick={dismiss}>Schließen</Button></div>
      </div>
    );
  }

  const stage = follow.phase === "restarting" || follow.phase === "checking" ? Math.max(stageOf(follow.step), stageOf("old_stopped")) : stageOf(follow.step);
  let sentence: string;
  if (follow.phase === "restarting") {
    sentence = "Nodvard Deck wird gerade umgeschaltet und ist kurz nicht erreichbar. Diese Seite fragt weiter nach …";
  } else if (follow.phase === "checking") {
    if (follow.running && follow.running === follow.to) {
      sentence = `Version ${follow.running} antwortet. Der Update-Helfer prüft noch, ob sie gesund bleibt …`;
    } else if (follow.running && follow.running === follow.from) {
      sentence = `Version ${follow.running} läuft wieder. Der Update-Helfer schließt den Vorgang ab …`;
    } else {
      sentence = "Nodvard Deck antwortet wieder. Der Update-Helfer schließt den Vorgang ab …";
    }
  } else if (follow.phase === "working") {
    sentence = stepText(follow.step, follow.action);
  } else {
    sentence = follow.helperAbsent
      ? "Der Update-Helfer antwortet gerade nicht. Der Auftrag wartet höchstens 10 Minuten auf ihn."
      : "Der Auftrag ist gestellt. Der Update-Helfer übernimmt ihn in den nächsten Sekunden …";
  }
  const title = rollback
    ? `Zurück zu Version ${follow.to ?? "…"}`
    : follow.to ? `Update auf Version ${follow.to}` : "Update läuft";
  return (
    <div role="status" aria-live="polite" className="rounded-lg border border-white/10 bg-black/20 px-3 py-3 text-sm" data-testid="helper-progress" data-phase={follow.phase}>
      <p className="flex items-center gap-2 font-medium">
        <Loader2 size={16} className="flex-none animate-spin" /> {title}
        {follow.elapsedS > 0 && <span className="font-normal text-white/40">({formatDuration(follow.elapsedS)})</span>}
      </p>
      <ol className="mt-3 grid grid-cols-2 gap-1.5 text-xs sm:grid-cols-4" aria-label="Abschnitte">
        {STAGES.map((s, i) => {
          const state = i < stage ? "done" : i === stage ? "current" : "open";
          return (
            <li key={s.label} data-state={state} aria-current={state === "current" ? "step" : undefined}
              className={`rounded-md px-2 py-1 ${state === "current" ? "bg-white/[0.12] text-white" : state === "done" ? "text-emerald-300" : "text-white/40"}`}>
              {state === "done" && <span aria-hidden="true">✓ </span>}{s.label}{state === "done" && <span className="sr-only"> (erledigt)</span>}
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-white/80">{sentence}</p>
      <p className="mt-1 text-xs text-white/45">
        Das dauert meist ein paar Minuten. Du kannst die Seite auch schließen: Der Update-Helfer arbeitet ohne sie weiter, das Ergebnis steht
        danach hier. Klappt der Start nicht, schaltet er von selbst zurück.
      </p>
    </div>
  );
}
