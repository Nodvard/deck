/**
 * Gemerkte Server-Schluessel: Nodvard Deck merkt sich den Fingerabdruck, den ein Server beim
 * ersten Mal zeigt (je nach Einstellung nach Bestaetigung unter „Verbindung pruefen“), und lehnt
 * danach jeden anderen ab. Nach einer Neuinstallation des Servers muss man den alten bewusst
 * vergessen -- mit Rueckfrage; den neuen Fingerabdruck muss man dann immer bestaetigen.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../../../lib/api";
import { formatDate, refreshHosts, useKnownKeys, type HostOut } from "../../../lib/hosts";
import { confirmDialog } from "../../../state/dialogs";
import { Button, NoticeLine, type Notice } from "../ui";

export function KnownKeys({ host }: { host: HostOut }) {
  const queryClient = useQueryClient();
  const keys = useKnownKeys(host.id);
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function forget(keyType: string) {
    const ok = await confirmDialog(
      `Gemerkten Schlüssel (${keyType}) von „${host.display_name}“ vergessen? Nur machen, wenn der Server neu installiert wurde. Bis du unter „Verbindung prüfen“ den neuen Fingerabdruck bestätigst, verbindet sich Nodvard Deck nicht mehr mit dem Server.`,
      { danger: true, confirmLabel: "Vergessen" },
    );
    if (!ok) return;
    setNotice(null);
    setBusy(keyType);
    try {
      await api.delete(`/hosts/${host.id}/known-hosts/${encodeURIComponent(keyType)}`);
      await refreshHosts(queryClient);
      setNotice({ kind: "ok", text: "Schlüssel vergessen. Drück jetzt „Verbindung prüfen“ und bestätige den neuen Fingerabdruck – vorher verbindet sich Nodvard Deck nicht mehr mit dem Server." });
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div id="server-schluessel">
      <NoticeLine notice={notice} />
      {keys.isError && <NoticeLine notice={{ kind: "error", text: "Die gemerkten Schlüssel konnten nicht geladen werden." }} />}
      {keys.data && keys.data.length === 0 && (
        <p className="text-sm text-white/50">Noch kein Schlüssel gemerkt. Unter „Verbindung prüfen“ siehst du den Fingerabdruck und kannst ihn bestätigen.</p>
      )}
      {keys.data && keys.data.length > 0 && (
        <ul className="divide-y divide-white/[0.06] rounded-lg border border-white/[0.08]">
          {keys.data.map((k) => (
            <li key={k.key_type} className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 text-sm">
                <p className="font-medium">{k.key_type}</p>
                <p className="break-all font-mono text-xs text-white/70">{k.fingerprint}</p>
                <p className="mt-0.5 text-xs text-white/45">
                  gemerkt am {formatDate(k.first_seen_at)} von {k.accepted_by_label ?? "automatisch"}
                </p>
              </div>
              <Button variant="danger" busy={busy === k.key_type} onClick={() => void forget(k.key_type)}>Vergessen</Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
