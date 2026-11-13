import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../../lib/api";
import { Button, NoticeLine, errorText, type Notice } from "../ui";

interface RetryState {
  failures: number;
  retry_at: number;
}

export function SshRetryNotice({ hostId, onRetry }: { hostId: string; onRetry: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const state = useQuery({
    queryKey: ["ssh-retry", hostId],
    queryFn: () => api.get<RetryState>(`/hosts/${hostId}/ssh-retry`),
    refetchInterval: 30_000,
    retry: false,
  });

  async function retry() {
    setBusy(true);
    setNotice(null);
    try {
      await api.post(`/hosts/${hostId}/ssh-retry`);
      await onRetry();
      await state.refetch();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  if (!state.data?.failures) return null;
  const until = state.data.retry_at * 1000;
  return (
    <div className="mb-3 rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-sm">
      <p className="font-semibold">Zugang abgelehnt</p>
      <p className="mt-1">Mögliche Ursachen:</p>
      <ul className="list-inside list-disc text-white/70">
        <li>Schlüssel fehlt oder passt nicht, oder das Passwort ist falsch.</li>
        <li>Die Anmeldung verlangt einen zweiten Faktor (2FA).</li>
        <li>fail2ban sperrt den Deck-Server.</li>
        <li>Duo verlangt eine Bestätigung.</li>
      </ul>
      {until > Date.now() && (
        <p className="my-2">Automatische Anmeldungen pausieren bis {new Date(until).toLocaleString("de-DE")}.</p>
      )}
      <Button busy={busy} onClick={() => void retry()}>Jetzt erneut prüfen</Button>
      <NoticeLine notice={notice} />
    </div>
  );
}
