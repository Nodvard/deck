/**
 * Update-Helfer (Einstellungen → System, Karten „Updates“ und „Kopien vor Updates“): Zustand lesen, Update und Rückweg
 * anfordern und einem Vorgang bis zum Ergebnis folgen. Server: `GET /system/updates/helper`,
 * `POST /system/updates/apply`, `POST /system/updates/rollback` (docs/04-API.md, Abschnitt System; Backend
 * `services/update_helper.py`).
 *
 * Ein Vorgang wird in zwei Phasen verfolgt:
 *  1. Solange das Dashboard antwortet: `GET /system/updates/helper` (Schritt des Helfers, `busy.step`).
 *  2. Antwortet es nicht mehr (der Helfer schaltet den Container um): `/api/v1/health`, bis wieder eine Version gesund
 *     antwortet (die neue oder, nach einem Rückbau, wieder die alte), dann zurück zu 1., wo das Ergebnis steht.
 * Nach 30 Minuten ohne Ergebnis sagt die Anzeige das (Zeitüberschreitung). Der Helfer selbst darf länger brauchen: bis zu
 * 30 Minuten für den Download, danach 15 für den Start und bei einem Fehler noch einmal 15 für den Rückbau. Solange er an
 * diesem Auftrag arbeitet (oder nicht antwortet), fragt die Anzeige deshalb danach seltener weiter nach
 * (`updaterTiming.latePollMs`) und zeigt das Ergebnis aus `last_result`, sobald es da ist. Ein Vorgang, dem die Karte
 * beim Laden wieder folgt (Seite neu geladen, „Schließen“), bekommt die Frist ab da.
 * Nach einem gelungenen Update oder Rückweg lädt die Seite neu, wie nach dem Einspielen einer Sicherung
 * (`browserNavigation` aus lib/restore.ts): Es läuft ja eine andere Version.
 *
 * Zustand und Vorgang liegen in einem kleinen Store, damit beide Karten dasselbe sehen. Passwort und Code laufen nie
 * hier durch, nur als Argument der Aufrufe (wie in lib/restore.ts).
 */
import { create } from "zustand";

import { useAuthStore } from "../state/auth";
import { ApiError, api } from "./api";
import { browserNavigation, waitForRestart, type Health } from "./restore";

export type HelperAction = "update" | "rollback";
export type HelperOutcome = "applied" | "reverted" | "rolled_back" | "refused" | "aborted" | "failed_manual" | "external_change";

export interface HelperTarget {
  current_version: string | null;
  floating_tag: string | null;
  pinned: boolean;
}

export interface HelperBusy {
  id: string;
  action: HelperAction;
  step: string;
  since: number;
}

export interface HelperPrevious {
  version: string;
  until: number;
  /** `true`: die Daten gehen mit zurück (alles seit `data_since` geht verloren), `false`: sie bleiben, `null`: unklar. */
  data_revert?: boolean | null;
  data_since?: number | null;
}

export interface HelperResult {
  id: string;
  action: HelperAction;
  from: string | null;
  to: string | null;
  outcome: HelperOutcome;
  code: string | null;
  finished_at: number;
}

export interface HelperPending {
  id: string;
  action: HelperAction;
  to: string | null;
  at: number;
}

export interface HelperView {
  present: boolean;
  reason: "missing" | "stale" | "unsafe" | "proto" | "invalid" | null;
  ready: boolean;
  ready_reason: string | null;
  state: "idle" | "busy" | "unsafe" | "error" | null;
  helper_version: string | null;
  heartbeat_at: number | null;
  target: HelperTarget | null;
  busy: HelperBusy | null;
  previous: HelperPrevious | null;
  last_result: HelperResult | null;
  pending: HelperPending | null;
}

export interface UpdateRequested {
  request_id: string;
  action: HelperAction;
  from: string | null;
  to: string;
  data_revert?: boolean;
}

/**
 * Abfragetakt, Frist, Takt nach der Frist und Pause vor dem Neuladen; als Objekt, damit Tests und Vorschau sie verkürzen
 * können.
 */
export const updaterTiming = { pollMs: 3000, timeoutMs: 30 * 60 * 1000, latePollMs: 30_000, reloadDelayMs: 3000 };

/** Wohin die Seite nach einem gelungenen Update bzw. Rückweg neu lädt. */
export const AFTER_SUCCESS_PATH = "/settings/system";

export function requestUpdate(version: string, password: string, totpCode: string): Promise<UpdateRequested> {
  const totp = totpCode.trim();
  return api.post<UpdateRequested>("/system/updates/apply", { version, current_password: password, ...(totp ? { totp_code: totp } : {}) });
}

export function requestRollback(password: string, acceptDataLoss: boolean, totpCode: string): Promise<UpdateRequested> {
  const totp = totpCode.trim();
  return api.post<UpdateRequested>("/system/updates/rollback", {
    current_password: password, accept_data_loss: acceptDataLoss, ...(totp ? { totp_code: totp } : {}),
  });
}

/** Fester Code einer Ablehnung (`{detail, code}`), sonst `undefined`. */
export function errorCode(err: unknown): string | undefined {
  if (!(err instanceof ApiError) || typeof err.detail !== "object" || err.detail === null) return undefined;
  const code = (err.detail as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

/** Grund des Helfers zu einer Ablehnung (`helper_reason`), sonst `undefined`. */
export function helperReason(err: unknown): string | undefined {
  if (!(err instanceof ApiError) || typeof err.detail !== "object" || err.detail === null) return undefined;
  const reason = (err.detail as { helper_reason?: unknown }).helper_reason;
  return typeof reason === "string" ? reason : undefined;
}

/** Kann `floating_tag` (`latest` oder `X.Y`) überhaupt auf `version` zeigen? Wie `tag_fits_version` im Helfer. */
export function tagFitsVersion(tag: string | null | undefined, version: string): boolean {
  if (!tag) return false;
  if (tag === "latest") return true;
  const [major, minor] = version.split(".");
  return tag === `${major}.${minor}`;
}

// ---------------------------------------------------------------------------
// Einem Vorgang folgen
// ---------------------------------------------------------------------------

export type FollowPhase = "waiting" | "working" | "restarting" | "checking" | "done" | "timeout";

export interface FollowState {
  requestId: string;
  action: HelperAction;
  from: string | null;
  to: string | null;
  dataRevert: boolean;
  /** Beginn in Millisekunden (Uhr des Browsers). */
  startedAt: number;
  /** Ab hier gilt die Zeitüberschreitung (Millisekunden). */
  deadline: number;
  phase: FollowPhase;
  /** Letzter bekannter Schritt des Helfers (`busy.step`). */
  step: string | null;
  /** Der Helfer antwortet gerade nicht (`present: false`), der Auftrag wartet. */
  helperAbsent: boolean;
  /** Version, die nach dem Umschalten gesund antwortet (`/api/v1/health`). */
  running: string | null;
  elapsedS: number;
  result: HelperResult | null;
  /** Gleich wird die Seite neu geladen (nach `applied` bzw. `reverted`). */
  reloading: boolean;
  /** Nach der Frist: Die Anzeige fragt weiter nach, weil der Helfer noch an diesem Auftrag arbeitet oder nicht antwortet. */
  watching: boolean;
}

export interface FollowInit {
  requestId: string;
  action: HelperAction;
  from?: string | null;
  to?: string | null;
  dataRevert?: boolean;
  startedAt?: number;
  /** Ohne Angabe: `startedAt` plus `updaterTiming.timeoutMs`. */
  deadline?: number;
}

interface UpdateHelperState {
  view: HelperView | null;
  loaded: boolean;
  loadError: string | null;
  follow: FollowState | null;
  /** Zustand neu lesen. Läuft gerade ein Vorgang (auch einer von vorher, `busy`/`pending`), wird ihm gefolgt. */
  load: () => Promise<HelperView | null>;
  startFollow: (init: FollowInit) => void;
  /** Ergebnis bzw. Zeitüberschreitung schließen und den Zustand neu lesen. */
  dismiss: () => void;
}

const DOWN = new Set([502, 503, 504]);
let running: AbortController | null = null;
let loading: Promise<HelperView | null> | null = null;
let reloadTimer: ReturnType<typeof setTimeout> | null = null;

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
  });
}

function mine(view: HelperView, requestId: string): HelperResult | null {
  return view.last_result?.id === requestId ? view.last_result : null;
}

/**
 * Schritte, bei denen schon umgeschaltet ist. Antwortet danach wieder eine Version (Phase 2), bleibt `busy.step` stehen,
 * solange der Helfer sie prüft oder zurückbaut (der Rückbau ändert den Schritt nicht).
 */
const SWITCHED_STEPS = new Set(["old_stopped", "started"]);

export const useUpdateHelper = create<UpdateHelperState>((set, get) => {
  async function followLoop(signal: AbortSignal): Promise<void> {
    const start = get().follow;
    if (!start) return;
    const deadline = start.deadline;
    const update = (patch: Partial<FollowState>) => {
      const current = get().follow;
      if (signal.aborted || !current || current.requestId !== start.requestId) return;
      set({ follow: { ...current, ...patch, elapsedS: Math.max(0, Math.round((Date.now() - current.startedAt) / 1000)) } });
    };
    const finish = (result: HelperResult) => {
      const success = result.outcome === "applied" || result.outcome === "reverted";
      update({ phase: "done", result, reloading: success });
      if (success) {
        // Es läuft jetzt eine andere Version: neu laden, damit auch die Oberfläche passt (wie nach einer Wiederherstellung).
        reloadTimer = setTimeout(() => browserNavigation.assign(AFTER_SUCCESS_PATH), updaterTiming.reloadDelayMs);
      }
    };
    // Abgemeldet (etwa nach einem Rückweg mit Daten, die alte Datenbank kennt die Anmeldung nicht): Die Anmeldeseite
    // übernimmt. Nichts Halbes zurücklassen: Nach der Anmeldung liest die Karte den Zustand neu, das Ergebnis steht dann
    // als letzter Vorgang da.
    const signedOut = (): boolean => {
      if (useAuthStore.getState().status !== "anonymous") return false;
      set({ follow: null, loaded: false });
      return true;
    };

    while (!signal.aborted && Date.now() < deadline) {
      let view: HelperView | null = null;
      try {
        view = await api.get<HelperView>("/system/updates/helper");
      } catch (err) {
        if (signal.aborted) return;
        if (err instanceof ApiError && DOWN.has(err.status)) {
          // Phase 2: das Dashboard wird gerade umgeschaltet. Warten, bis wieder eine Version gesund antwortet.
          update({ phase: "restarting" });
          let seen: Health | null = null;
          const back = await waitForRestart(null, {
            signal,
            timeoutMs: Math.max(0, deadline - Date.now()),
            onTick: () => update({}),
            ready: (health) => { seen = health; return true; },
          });
          if (back) update({ phase: "checking", running: (seen as Health | null)?.version ?? null });
          continue;
        }
        if (signedOut()) return;
      }
      if (view) {
        set({ view });
        const result = mine(view, start.requestId);
        if (result) {
          finish(result);
          return;
        }
        const busy = view.busy?.id === start.requestId ? view.busy : null;
        const current = get().follow;
        if (busy) {
          // Nach dem Umschalten antwortet schon wieder eine Version: Der Satz dazu („Version … läuft wieder …“) bleibt
          // stehen, statt wieder „Die neue Version startet …“ zu zeigen.
          const settled = current?.phase === "checking" && SWITCHED_STEPS.has(busy.step);
          update(settled ? { step: busy.step, helperAbsent: false } : { phase: "working", step: busy.step, helperAbsent: false });
        } else if (current && current.step === null && current.phase === "waiting") update({ helperAbsent: !view.present });
        else update({ phase: "checking", helperAbsent: !view.present });
      }
      await sleep(updaterTiming.pollMs, signal);
    }
    // Frist vorbei. Solange der Helfer an diesem Auftrag arbeitet (oder gerade nicht antwortet), seltener weiter nachfragen
    // und das Ergebnis zeigen, sobald es da ist; arbeitet er nicht mehr daran und gibt es kein Ergebnis, aufhören.
    let first = true;
    while (!signal.aborted) {
      let view: HelperView | null = null;
      try {
        view = await api.get<HelperView>("/system/updates/helper");
      } catch {
        if (signal.aborted || signedOut()) return;
      }
      if (view) {
        set({ view });
        const result = mine(view, start.requestId);
        if (result) {
          finish(result);
          return;
        }
      }
      const open = !view || !view.present || view.busy?.id === start.requestId || view.pending?.id === start.requestId;
      update(first ? { phase: "timeout", watching: open } : { watching: open });
      first = false;
      if (!open) return;
      await sleep(updaterTiming.latePollMs, signal);
    }
  }

  return {
    view: null,
    loaded: false,
    loadError: null,
    follow: null,

    load: () => {
      if (loading) return loading;
      loading = (async () => {
        try {
          const view = await api.get<HelperView>("/system/updates/helper");
          set({ view, loaded: true, loadError: null });
          const open = view.busy ?? view.pending;
          if (open && !get().follow) {
            // Ein Vorgang läuft schon (Seite neu geladen, oder ein anderer Owner hat ihn gestartet): ihm folgen.
            get().startFollow({
              requestId: open.id,
              action: open.action,
              to: view.pending?.id === open.id ? view.pending.to : null,
              startedAt: ("since" in open ? open.since : open.at) * 1000,
              // Die Frist ab jetzt: sonst stünde nach „Schließen“ einer Zeitüberschreitung gleich wieder dieselbe da.
              deadline: Date.now() + updaterTiming.timeoutMs,
            });
          }
          return view;
        } catch (err) {
          set({ loaded: true, loadError: err instanceof Error ? err.message : String(err) });
          return null;
        } finally {
          loading = null;
        }
      })();
      return loading;
    },

    startFollow: (init) => {
      running?.abort();
      const controller = new AbortController();
      running = controller;
      const startedAt = init.startedAt ?? Date.now();
      set({
        follow: {
          requestId: init.requestId,
          action: init.action,
          from: init.from ?? null,
          to: init.to ?? null,
          dataRevert: init.dataRevert ?? false,
          startedAt,
          deadline: init.deadline ?? startedAt + updaterTiming.timeoutMs,
          phase: "waiting",
          step: null,
          helperAbsent: false,
          running: null,
          elapsedS: 0,
          result: null,
          reloading: false,
          watching: false,
        },
      });
      void followLoop(controller.signal).finally(() => {
        if (running === controller) running = null;
      });
    },

    dismiss: () => {
      running?.abort();
      running = null;
      if (reloadTimer) clearTimeout(reloadTimer);
      reloadTimer = null;
      set({ follow: null });
      void get().load();
    },
  };
});

/** Nur für Tests: alles auf Anfang. */
export function resetUpdateHelper(): void {
  running?.abort();
  running = null;
  loading = null;
  if (reloadTimer) clearTimeout(reloadTimer);
  reloadTimer = null;
  useUpdateHelper.setState({ view: null, loaded: false, loadError: null, follow: null });
}
