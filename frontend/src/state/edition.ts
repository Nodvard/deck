/**
 * Welche Ausgabe läuft, Nodvard Deck oder Nodvard Dock (`GET /api/v1/edition`, `backend/src/nodvard_deck/edition.py`).
 * `main.tsx` lädt sie einmal beim Start, ohne Anmeldung; bis dahin und ohne Antwort gilt Nodvard Deck. Wer danach einen
 * Weg festlegt, etwa der Assistent, wartet mit `loadedEdition()` auf die Antwort.
 */
import { create } from "zustand";

export interface Edition {
  id: string;
  product_name: string;
  /** Dienst in der mitgelieferten Compose-Datei, für `docker compose logs <service>`. */
  service: string;
  /** Offizielles Image ohne Tag. */
  image: string;
  /** `false`: Die Module stehen mit dem Image fest, der Assistent schaltet sie ein, statt zu fragen. */
  choose_modules: boolean;
  /** Update-Helfer und `scripts/deploy_pi.sh` gelten für diese Ausgabe. */
  repo_tools: boolean;
}

export const DECK_EDITION: Edition = {
  id: "deck",
  product_name: "Nodvard Deck",
  service: "nodvard-deck",
  image: "ghcr.io/nodvard/deck",
  choose_modules: true,
  repo_tools: true,
};

interface EditionState {
  edition: Edition;
  /** Der Server hat geantwortet; vorher ist `edition` nur die Vorgabe. */
  loaded: boolean;
  load: () => Promise<void>;
}

export const EDITION_WAIT_MS = 10_000;

let pending: Promise<void> | null = null;

export const useEditionStore = create<EditionState>((set) => ({
  edition: DECK_EDITION,
  loaded: false,

  load() {
    if (pending) return pending;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), EDITION_WAIT_MS);
    pending = (async () => {
      try {
        const res = await fetch("/api/v1/edition", { signal: controller.signal });
        if (res.ok) set({ edition: { ...DECK_EDITION, ...((await res.json()) as Partial<Edition>) }, loaded: true });
      } catch {
        // Ohne Antwort bleibt Nodvard Deck die Vorgabe.
      }
    })().finally(() => {
      clearTimeout(timer);
      pending = null;
    });
    return pending;
  },
}));

/**
 * Die Ausgabe, sobald der Server geantwortet hat; ist die Abfrage beim Start gescheitert, fragt sie noch einmal.
 * Ohne Antwort innerhalb von `EDITION_WAIT_MS` gibt sie `null` zurück.
 */
export async function loadedEdition(): Promise<Edition | null> {
  if (!useEditionStore.getState().loaded) {
    await useEditionStore.getState().load();
  }
  const { edition, loaded } = useEditionStore.getState();
  return loaded ? edition : null;
}

export function useEdition(): Edition {
  return useEditionStore((s) => s.edition);
}

export function productText(text: string): string {
  if (!text || useEditionStore.getState().edition.choose_modules) return text;
  return text.replace(/Nodvard Deck/g, "Nodvard Dock").replace(/\bDeck\b/g, "Dock");
}
