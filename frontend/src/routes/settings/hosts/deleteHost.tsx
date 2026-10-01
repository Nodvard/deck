/**
 * Server loeschen -- gemeinsam fuer die Seite „Server & Zugaenge“ und die Server-Seite. Wer
 * loescht, tippt zur Sicherheit den Kurznamen ein; nur bei exakt demselben Namen geht die
 * Anfrage raus. Gelöscht werden auch SSH-Zugang und gemerkter Server-Schluessel.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "../../../lib/api";
import type { HostOut } from "../../../lib/hosts";
import { promptDialog } from "../../../state/dialogs";
import { Button } from "../ui";

export type DeleteOutcome = { status: "deleted" } | { status: "cancelled" } | { status: "mismatch" } | { status: "error"; text: string };

export function deletePrompt(host: HostOut): string {
  const extra = host.provider_ext_id
    ? " Dieser Server wurde automatisch eingelesen und taucht beim nächsten Abgleich wieder auf – dann ohne SSH-Zugang."
    : "";
  return (
    `Zum Löschen den Kurznamen „${host.name}“ eintippen. Gelöscht werden auch SSH-Zugang und gemerkter Server-Schlüssel. ` +
    "Verläufe in Nodvard Shield bleiben mit dem alten Namen stehen; Skripte, die nur auf diesem Server laufen sollten, laufen danach nirgends." +
    extra
  );
}

export async function deleteHost(host: HostOut): Promise<DeleteOutcome> {
  const typed = await promptDialog(deletePrompt(host));
  if (typed === null) return { status: "cancelled" };
  if (typed !== host.name) return { status: "mismatch" };
  try {
    await api.delete(`/hosts/${host.id}`);
    return { status: "deleted" };
  } catch (err) {
    // z. B. 409 „Auf diesem Server laeuft gerade eine Aktion.“
    return { status: "error", text: err instanceof ApiError ? err.message : String(err) };
  }
}

/** Knopf „Server löschen“; meldet das Ergebnis an den Aufrufer (der weiterleitet oder einen Text zeigt). */
export function DeleteHostButton({
  host, label = "Server löschen", onDeleted, onMessage,
}: {
  host: HostOut;
  label?: string;
  onDeleted: () => void;
  onMessage: (message: { kind: "ok" | "error"; text: string }) => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      const outcome = await deleteHost(host);
      if (outcome.status === "mismatch") {
        onMessage({ kind: "error", text: `Der Kurzname stimmte nicht mit „${host.name}“ überein – nichts gelöscht.` });
      } else if (outcome.status === "error") {
        onMessage({ kind: "error", text: outcome.text });
      } else if (outcome.status === "deleted") {
        // Die Abfragen des geloeschten Servers gar nicht erst neu laden (sie waeren 404).
        queryClient.removeQueries({ queryKey: ["hosts", host.id] });
        await queryClient.invalidateQueries({ queryKey: ["hosts"] });
        await queryClient.invalidateQueries({ queryKey: ["overview"] });
        onDeleted();
      }
    } finally {
      setBusy(false);
    }
  }

  return <Button variant="danger" busy={busy} onClick={() => void run()}>{label}</Button>;
}
