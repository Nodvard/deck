/**
 * Ersetzt `window.confirm()`/`window.prompt()` -- echte Radix-Dialoge statt
 * Browser-nativer Blocking-Prompts (dasselbe Muster wie WidgetPickerDialog.tsx).
 * EIN globaler Zustand statt eines Hooks pro Aufrufer: `ActionButton.tsx` sitzt oft mehrfach gleichzeitig in einer Liste (siehe
 * ListView/TableView), ein Hook pro Instanz haette dort N eigene Dialog-Baeume
 * gemountet, obwohl nie mehr als einer gleichzeitig offen sein kann. `confirmDialog()`/
 * `promptDialog()` sind deshalb freie Funktionen (wie `useAuthStore.getState()...`),
 * aufrufbar aus JEDEM Code, nicht nur aus einer Komponente mit einem eigenen Hook --
 * genau EIN `<GlobalDialogs />` (siehe components/GlobalDialogs.tsx) rendert das
 * tatsaechliche Overlay, einmal in AppShell.tsx gemountet.
 */
import { create } from "zustand";

export interface ConfirmOptions {
  title?: string;
  danger?: boolean;
  confirmLabel?: string;
  cancelLabel?: string;
}

interface PendingConfirm {
  message: string;
  options: ConfirmOptions;
  resolve: (value: boolean) => void;
}

interface PendingPrompt {
  message: string;
  defaultValue: string;
  resolve: (value: string | null) => void;
}

interface DialogsState {
  confirmPending: PendingConfirm | null;
  promptPending: PendingPrompt | null;
  requestConfirm: (message: string, options: ConfirmOptions) => Promise<boolean>;
  requestPrompt: (message: string, defaultValue: string) => Promise<string | null>;
  settleConfirm: (value: boolean) => void;
  settlePrompt: (value: string | null) => void;
}

export const useDialogsStore = create<DialogsState>((set, get) => ({
  confirmPending: null,
  promptPending: null,

  requestConfirm: (message, options) =>
    new Promise<boolean>((resolve) => {
      // Ein evtl. noch offener aelterer Dialog wird stillschweigend als "abgebrochen"
      // aufgeloest -- zwei gleichzeitig angeforderte Bestaetigungen sind ein
      // Programmierfehler des Aufrufers, kein Fall, den die UI abfangen muss.
      get().confirmPending?.resolve(false);
      set({ confirmPending: { message, options, resolve } });
    }),

  requestPrompt: (message, defaultValue) =>
    new Promise<string | null>((resolve) => {
      get().promptPending?.resolve(null);
      set({ promptPending: { message, defaultValue, resolve } });
    }),

  settleConfirm: (value) => {
    get().confirmPending?.resolve(value);
    set({ confirmPending: null });
  },

  settlePrompt: (value) => {
    get().promptPending?.resolve(value);
    set({ promptPending: null });
  },
}));

/** Ersatz fuer `window.confirm(message)` -- `await confirmDialog("...")`. */
export function confirmDialog(message: string, options: ConfirmOptions = {}): Promise<boolean> {
  return useDialogsStore.getState().requestConfirm(message, options);
}

/** Ersatz fuer `window.prompt(message, defaultValue)` -- `await promptDialog("...")`.
 * Gibt `null` bei Abbruch zurueck, sonst den (getrimmten) eingegebenen Text -- ein
 * LEERER Text gilt als Abbruch (`null`), dieselbe Semantik wie `window.prompt()`s
 * eigenes "leer bestaetigt" vs. "Abbrechen" (beide liefern dort einen falsy-artigen
 * Wert, hier vereinheitlicht auf `null`). */
export function promptDialog(message: string, defaultValue = ""): Promise<string | null> {
  return useDialogsStore.getState().requestPrompt(message, defaultValue);
}
