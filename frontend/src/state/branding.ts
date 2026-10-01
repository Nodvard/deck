/**
 * Geteilter Branding-Zustand.
 *
 * Vorher rief `AppShell.tsx` `fetchBranding()`/`applyBranding()` selbst auf, beim
 * Mount NACH dem Login (docs/01-ARCHITECTURE.md §6 verlangt aber ausdruecklich, dass
 * schon die Login-Seite unauthentifiziert brandet). `main.tsx` laedt jetzt EINMAL beim
 * Boot, vor dem ersten Render der Router-Kinder -- Login- UND der neue
 * Erstinbetriebnahme-Assistent (SetupPage) sehen dadurch von Anfang an das echte
 * Branding statt der eingebauten Default-Farben.
 */
import { create } from "zustand";

import { applyBranding, fetchBranding, type Branding } from "../lib/branding";

interface BrandingState {
  branding: Branding | null;
  loaded: boolean;
  load: () => Promise<void>;
  setAndApply: (branding: Branding) => void;
}

export const useBrandingStore = create<BrandingState>((set) => ({
  branding: null,
  loaded: false,

  async load() {
    try {
      const branding = await fetchBranding();
      applyBranding(branding);
      set({ branding, loaded: true });
    } catch {
      // Kein Branding-Server erreichbar (z. B. Backend noch nicht oben) -- die
      // eingebauten CSS-Defaults aus index.css greifen weiter, kein Absturz.
      set({ loaded: true });
    }
  },

  setAndApply(branding) {
    applyBranding(branding);
    set({ branding, loaded: true });
  },
}));
