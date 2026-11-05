import { create } from "zustand";

const KEY = "deck.insecureConnection.dismissed";

function readDismissed(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "true";
  } catch {
    return false;
  }
}

export const useConnectionWarningStore = create<{
  dismissed: boolean;
  dismiss: () => void;
  reset: () => void;
}>((set) => ({
  dismissed: readDismissed(),
  dismiss() {
    try {
      sessionStorage.setItem(KEY, "true");
    } catch { /* Ohne Browserspeicher gilt das Ausblenden bis zum Neuladen. */ }
    set({ dismissed: true });
  },
  reset() {
    try {
      sessionStorage.removeItem(KEY);
    } catch { /* Der Zustand im Speicher wird trotzdem zurückgesetzt. */ }
    set({ dismissed: false });
  },
}));
