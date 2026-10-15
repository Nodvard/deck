import type { QueryClient } from "@tanstack/react-query";

import { useAuthStore } from "../state/auth";

/**
 * Leert den Zwischenspeicher der Abfragen, sobald das angemeldete Konto wechselt: Abmelden, abgelaufene Sitzung
 * oder ein anderes Konto im selben Tab. Sonst sähe das neue Konto kurz die Daten des alten, und sein Theme käme
 * aus den Einstellungen des Vorgängers. Eine Token-Erneuerung für dasselbe Konto lässt alles stehen.
 */
export function clearQueriesOnAccountChange(queryClient: QueryClient): () => void {
  let account = useAuthStore.getState().user?.id ?? null;
  return useAuthStore.subscribe((state) => {
    const next = state.user?.id ?? null;
    if (next === account) return;
    const hadAccount = account !== null;
    account = next;
    if (hadAccount) queryClient.clear();
  });
}
