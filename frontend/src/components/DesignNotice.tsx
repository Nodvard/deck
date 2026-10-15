/**
 * Einmaliger Hinweis nach dem Update auf das neue Aussehen: „Deck hat ein neues Aussehen.“ Wer das alte lieber mag,
 * schaltet mit einem Klick auf das Theme „Bisher“, sonst „Behalten“. Gemerkt wird das beim Benutzer
 * (`PATCH /me/preferences`), nicht im Browser, also auf jedem Gerät nur einmal. Neue Konten bekommen den Hinweis nicht,
 * ein älterer Server ohne das Feld auch nicht.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Palette } from "lucide-react";

import { api } from "../lib/api";
import { type Preferences, usePreferences } from "../lib/firstSteps";
import { applyTheme } from "../lib/theme";

export function DesignNotice() {
  const preferences = usePreferences();
  const queryClient = useQueryClient();
  // Erst eine Antwort von nach dem Anmelden zählt (ein Rest im Zwischenspeicher könnte vom vorigen Konto stammen).
  if (!preferences.isFetchedAfterMount || preferences.data?.design_notice_dismissed !== false) return null;

  async function decide(original: boolean) {
    const change: Partial<Preferences> = original ? { design_notice_dismissed: true, theme: "original" } : { design_notice_dismissed: true };
    if (original) applyTheme("original");
    // Sofort ausblenden; scheitert das Speichern, kommt der Hinweis beim nächsten Laden eben wieder.
    queryClient.setQueryData<Preferences>(["me", "preferences"], (old) => (old ? { ...old, ...change } : old));
    try {
      await api.patch<Preferences>("/me/preferences", change);
    } catch {
      // nichts weiter: das gewählte Aussehen gilt in diesem Browser, gefragt wird beim nächsten Mal neu.
    }
  }

  return (
    <div
      role="status"
      data-testid="design-notice"
      className="flex flex-none flex-col gap-2 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-4 py-2.5 text-sm sm:flex-row sm:items-center sm:gap-3 sm:px-6"
    >
      <Palette size={16} className="hidden flex-none text-[var(--color-accent-text)] sm:block" aria-hidden />
      <p className="min-w-0 flex-1 break-words">
        <strong className="font-semibold">Deck hat ein neues Aussehen.</strong>{" "}
        <span className="text-white/60">Das bisherige findest du jederzeit unter Einstellungen, Darstellung.</span>
      </p>
      <div className="flex flex-none flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void decide(true)}
          className="rounded-lg border border-[var(--color-line-strong)] px-3 py-1.5 text-sm font-medium transition hover:bg-white/[0.06]"
        >
          Zum bisherigen Aussehen
        </button>
        <button
          type="button"
          onClick={() => void decide(false)}
          className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-on-accent transition hover:bg-[var(--color-accent-strong)]"
        >
          Behalten
        </button>
      </div>
    </div>
  );
}
