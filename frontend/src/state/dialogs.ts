/**
 * Ersetzt `window.confirm()`/`window.prompt()` durch Radix-Dialoge. Ein globaler Zustand statt eines Hooks pro
 * Aufrufer (`ActionButton` sitzt oft mehrfach in einer Liste, nie ist mehr als ein Dialog offen):
 * `confirmDialog()`/`promptDialog()` sind freie Funktionen, ein `<GlobalDialogs />` in AppShell rendert das Overlay.
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

/** Passwort und, wenn Zwei-Faktor an ist, der Code aus der App; so wie die API sie im Body erwartet. */
export interface IdentityConfirmation {
  current_password: string;
  totp_code?: string;
}

interface PendingIdentity {
  message: string;
  twoFactor: boolean;
  resolve: (value: IdentityConfirmation | null) => void;
}

interface DialogsState {
  confirmPending: PendingConfirm | null;
  promptPending: PendingPrompt | null;
  identityPending: PendingIdentity | null;
  requestConfirm: (message: string, options: ConfirmOptions) => Promise<boolean>;
  requestPrompt: (message: string, defaultValue: string) => Promise<string | null>;
  requestIdentity: (message: string, twoFactor: boolean) => Promise<IdentityConfirmation | null>;
  settleConfirm: (value: boolean) => void;
  settlePrompt: (value: string | null) => void;
  settleIdentity: (value: IdentityConfirmation | null) => void;
}

export const useDialogsStore = create<DialogsState>((set, get) => ({
  confirmPending: null,
  promptPending: null,
  identityPending: null,

  requestConfirm: (message, options) =>
    new Promise<boolean>((resolve) => {
      // Ein evtl. noch offener älterer Dialog wird stillschweigend als "abgebrochen" aufgelöst: Zwei gleichzeitig
      // angeforderte Bestätigungen sind ein Programmierfehler des Aufrufers, kein Fall, den die UI abfangen muss.
      get().confirmPending?.resolve(false);
      set({ confirmPending: { message, options, resolve } });
    }),

  requestPrompt: (message, defaultValue) =>
    new Promise<string | null>((resolve) => {
      get().promptPending?.resolve(null);
      set({ promptPending: { message, defaultValue, resolve } });
    }),

  requestIdentity: (message, twoFactor) =>
    new Promise<IdentityConfirmation | null>((resolve) => {
      get().identityPending?.resolve(null);
      set({ identityPending: { message, twoFactor, resolve } });
    }),

  settleConfirm: (value) => {
    get().confirmPending?.resolve(value);
    set({ confirmPending: null });
  },

  settlePrompt: (value) => {
    get().promptPending?.resolve(value);
    set({ promptPending: null });
  },

  settleIdentity: (value) => {
    get().identityPending?.resolve(value);
    set({ identityPending: null });
  },
}));

/** Ersatz für `window.confirm(message)`: `await confirmDialog("...")`. */
export function confirmDialog(message: string, options: ConfirmOptions = {}): Promise<boolean> {
  return useDialogsStore.getState().requestConfirm(message, options);
}

/**
 * Ersatz für `window.prompt(message, defaultValue)`. Gibt `null` bei Abbruch zurück, sonst den getrimmten Text; ein
 * leerer Text gilt als Abbruch (`null`), wie bei `window.prompt()`.
 */
export function promptDialog(message: string, defaultValue = ""): Promise<string | null> {
  return useDialogsStore.getState().requestPrompt(message, defaultValue);
}

/**
 * Fragt das eigene Passwort und, mit `twoFactor`, den Code aus der App ab, etwa bevor gespeicherte Zugangsdaten ersetzt
 * oder gelöscht werden. `null` bei Abbruch. Die Werte gehen nur in den nächsten Aufruf, gemerkt wird nichts.
 */
export function confirmIdentity(message: string, twoFactor = false): Promise<IdentityConfirmation | null> {
  return useDialogsStore.getState().requestIdentity(message, twoFactor);
}
