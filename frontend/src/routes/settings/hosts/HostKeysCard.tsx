/**
 * Karte „Neue Server-Schlüssel“ (Einstellungen → Server & Zugänge): Schalter, ob Nodvard Deck den Fingerabdruck eines
 * Servers beim ersten Kontakt still merken darf (`ssh.confirm_new_host_keys`, Recht `settings.write`). Neue
 * Installationen starten mit „an“ (erst bestätigen, bis dahin geht nichts an den Server), bestehende mit „aus“. Ändern
 * darf es nur der Inhaber, mit Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App; andere sehen den Schalter
 * gesperrt.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../../lib/api";
import { useAuthStore } from "../../../state/auth";
import { OWNER_ONLY_HINT, OwnerConfirm, type OwnerProof } from "../OwnerConfirm";
import { Button, Card, NoticeLine, Toggle, type Notice } from "../ui";

export const KEY_CONFIRM_NEW_HOST_KEYS = "ssh.confirm_new_host_keys";

interface SettingRow {
  key: string;
  value: unknown;
}

function HostKeysForm({ settings }: { settings: SettingRow[] }) {
  const queryClient = useQueryClient();
  // Ist NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS gesetzt, zeigt die Karte deren Wert, und eine
  // Änderung meldet der Server als Fehler.
  const stored = settings.find((r) => r.key === KEY_CONFIRM_NEW_HOST_KEYS)?.value;
  const initial = stored === true;
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  const [saved, setSaved] = useState(initial);
  const [confirm, setConfirm] = useState(initial);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function send(proof: OwnerProof) {
    await api.put(`/settings/${KEY_CONFIRM_NEW_HOST_KEYS}`, { value: confirm, ...proof });
  }

  function done() {
    setSaved(confirm);
    setConfirming(false);
    setNotice({ kind: "ok", text: "Gespeichert." });
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
  }

  return (
    <Card
      title="Neue Server-Schlüssel"
      description="Jeder Server zeigt beim ersten Kontakt einen Fingerabdruck. Hier legst du fest, ob Nodvard Deck ihn von selbst merken darf oder ob du ihn erst bestätigen musst."
      footer={isOwner && !confirming ? (
        <Button variant="primary" disabled={confirm === saved} onClick={() => { setNotice(null); setConfirming(true); }}>Speichern</Button>
      ) : undefined}
    >
      <NoticeLine notice={notice} />
      {!isOwner && <p className="mb-3 text-sm text-white/55">{OWNER_ONLY_HINT}</p>}

      <div className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm text-white/80">Neue Server-Schlüssel erst nach meiner Bestätigung merken</span>
          <Toggle checked={confirm} onChange={setConfirm} label="Neue Server-Schlüssel erst nach meiner Bestätigung merken" disabled={!isOwner || confirming} />
        </div>
        <p className="text-xs text-white/40">
          An: Bei einem neuen Server zeigt „Verbindung prüfen“ den Fingerabdruck. Erst wenn du ihn bestätigst, schickt Nodvard Deck
          Passwort oder Schlüssel an den Server. Das schützt davor, dass sich im Netzwerk jemand als dein Server ausgibt. Aus:
          Nodvard Deck merkt sich den Fingerabdruck beim ersten Verbinden von selbst. Bereits gemerkte Schlüssel bleiben, wie sie sind.
          Nach „Schlüssel vergessen“ musst du den neuen Fingerabdruck immer bestätigen, egal wie das hier eingestellt ist.
        </p>
      </div>
      {confirming && <OwnerConfirm send={send} onDone={done} onCancel={() => setConfirming(false)} />}
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
