/**
 * Geteilter Branding-Zustand. `main.tsx` lädt ihn einmal beim Boot, vor dem ersten Render der Router-Kinder, damit
 * schon die Login-Seite und der Einrichtungs-Assistent ohne Anmeldung das echte Branding zeigen statt der eingebauten
 * Default-Farben (docs/01-ARCHITECTURE.md).
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
      // Kein Branding-Server erreichbar (z. B. Backend noch nicht oben): die eingebauten CSS-Defaults aus
      // index.css greifen weiter, kein Absturz.
      set({ loaded: true });
    }
  },

  setAndApply(branding) {
    applyBranding(branding);
    set({ branding, loaded: true });
  },
}));
