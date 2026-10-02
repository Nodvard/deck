/**
 * Karte „Neue Server-Schlüssel“ (Einstellungen -> Server & Zugänge): Schalter, ob Nodvard Deck den
 * Fingerabdruck eines Servers beim ersten Kontakt still merken darf (`ssh.confirm_new_host_keys`,
 * GET/PUT /settings/{key}, Recht `settings.write`). An: der Fingerabdruck muss unter „Verbindung prüfen“
 * bestätigt werden, bis dahin wird nichts an den Server geschickt. Neue Installationen starten mit „an“,
 * bestehende mit „aus“. Ist die Umgebungsvariable NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS gesetzt,
 * zeigt die Karte deren Wert, und eine Änderung meldet der Server als Fehler. Ohne das Recht gibt es die Karte nicht.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../../lib/api";
import { useAuthStore } from "../../../state/auth";
import { Button, Card, NoticeLine, Toggle, errorText, type Notice } from "../ui";

export const KEY_CONFIRM_NEW_HOST_KEYS = "ssh.confirm_new_host_keys";

interface SettingRow {
  key: string;
  value: unknown;
}

function HostKeysForm({ settings }: { settings: SettingRow[] }) {
  const queryClient = useQueryClient();
  const stored = settings.find((r) => r.key === KEY_CONFIRM_NEW_HOST_KEYS)?.value;
  const initial = stored === true;
  const [saved, setSaved] = useState(initial);
  const [confirm, setConfirm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      await api.put(`/settings/${KEY_CONFIRM_NEW_HOST_KEYS}`, { value: confirm });
      setSaved(confirm);
      setNotice({ kind: "ok", text: "Gespeichert." });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Neue Server-Schlüssel"
      description="Jeder Server zeigt beim ersten Kontakt einen Fingerabdruck. Hier legst du fest, ob Nodvard Deck ihn von selbst merken darf oder ob du ihn erst bestätigen musst."
      footer={<Button variant="primary" busy={busy} disabled={confirm === saved} onClick={() => void save()}>Speichern</Button>}
    >
      <NoticeLine notice={notice} />

      <div className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm text-white/80">Neue Server-Schlüssel erst nach meiner Bestätigung merken</span>
          <Toggle checked={confirm} onChange={setConfirm} label="Neue Server-Schlüssel erst nach meiner Bestätigung merken" disabled={busy} />
        </div>
        <p className="text-xs text-white/40">
          An: Bei einem neuen Server zeigt „Verbindung prüfen“ den Fingerabdruck. Erst wenn du ihn bestätigst, schickt Nodvard Deck
          Passwort oder Schlüssel an den Server. Das schützt davor, dass sich im Netzwerk jemand als dein Server ausgibt. Aus:
          Nodvard Deck merkt sich den Fingerabdruck beim ersten Verbinden von selbst. Bereits gemerkte Schlüssel bleiben, wie sie sind.
          Nach „Schlüssel vergessen“ musst du den neuen Fingerabdruck immer bestätigen, egal wie das hier eingestellt ist.
        </p>
      </div>
    </Card>
  );
}

export function HostKeysCard() {
  const allowed = useAuthStore((s) => s.hasPermission)("settings.write");
  useAuthStore((s) => s.user);
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<SettingRow[]>("/settings"),
    enabled: allowed,
    retry: false,
  });

  if (!allowed) return null;
  if (settings.isError) {
    return (
      <Card title="Neue Server-Schlüssel">
        <NoticeLine notice={{ kind: "error", text: "Die Einstellung konnte nicht geladen werden." }} />
      </Card>
    );
  }
  if (!settings.data) return null;
  return <HostKeysForm settings={settings.data} />;
}
