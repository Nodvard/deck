/** Einmalige Erinnerung nach dem Verschieben der Zwei-Faktor-Einrichtung, je Konto auf allen Geräten. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import type { Preferences } from "../lib/firstSteps";
import { useAuthStore } from "../state/auth";
import { Button, NoticeLine } from "../routes/settings/ui";

interface ReminderState {
  preferences: Preferences;
  enabled: boolean | null;
}

export function TotpReminder() {
  const userId = useAuthStore((s) => s.user?.id);
  return userId ? <AccountReminder key={userId} userId={userId} /> : null;
}

function AccountReminder({ userId }: { userId: string }) {
  const queryClient = useQueryClient();
  const queryKey = ["me", "totp-reminder", userId];
  const reminder = useQuery({
    queryKey,
    queryFn: async (): Promise<ReminderState> => {
      const preferences = await api.get<Preferences>("/me/preferences");
      if (preferences.totp_setup_skipped !== true || preferences.totp_reminder_dismissed !== false) {
        return { preferences, enabled: null };
      }
      const me = await api.get<{ id: string; totp_enabled?: boolean }>("/me");
      return { preferences, enabled: me.id === userId && typeof me.totp_enabled === "boolean" ? me.totp_enabled : null };
    },
    retry: false,
    refetchOnMount: "always",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!reminder.isFetchedAfterMount || !reminder.isSuccess || reminder.data.enabled !== false
    || reminder.data.preferences.totp_setup_skipped !== true || reminder.data.preferences.totp_reminder_dismissed !== false) return null;

  async function dismiss() {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const preferences = await api.patch<Preferences>("/me/preferences", { totp_reminder_dismissed: true });
      // Eine späte Antwort des vorigen Kontos darf den gemeinsamen Zwischenspeicher nicht überschreiben.
      if (useAuthStore.getState().user?.id !== userId) return;
      queryClient.setQueryData<ReminderState>(queryKey, (old) => old ? { ...old, preferences } : old);
      queryClient.setQueryData<Preferences>(["me", "preferences"], preferences);
    } catch {
      setError("Die Erinnerung konnte nicht ausgeblendet werden. Bitte versuche es noch einmal.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel mx-4 mt-5 p-4 sm:mx-6 sm:p-5" aria-labelledby="totp-reminder-title" data-testid="totp-reminder">
      <div className="flex flex-wrap items-start gap-3">
        <ShieldCheck size={18} className="mt-0.5 flex-none text-[var(--color-accent-text)]" aria-hidden />
        <div className="min-w-0 flex-1 basis-48">
          <h2 id="totp-reminder-title" className="text-sm font-semibold">Zwei-Faktor-Anmeldung noch einrichten</h2>
          <p className="mt-1 text-sm text-white/70">
            Du hast die Einrichtung übersprungen. Schütze dein Konto zusätzlich mit einem Code aus deiner App.{" "}
            <Link to="/settings/account" className="text-[var(--color-accent-text)] underline underline-offset-2 hover:text-white">
              Einstellungen → Mein Konto
            </Link>
          </p>
          <p className="mt-1 text-xs text-white/50">Diese Erinnerung bleibt, bis Zwei-Faktor aktiv ist oder du sie bewusst ausblendest.</p>
        </div>
        <Button variant="ghost" busy={saving} onClick={() => void dismiss()}>Erinnerung ausblenden</Button>
      </div>
      {error && <div className="mt-3"><NoticeLine notice={{ kind: "error", text: error }} /></div>}
    </section>
  );
}
