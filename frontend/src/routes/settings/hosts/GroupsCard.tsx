/**
 * Gruppen: Mit einer Gruppe laesst „Skripte“ ein Skript auf mehreren Servern laufen. Hier
 * anlegen, umbenennen und loeschen (die Server darin bleiben erhalten). Wer in welcher Gruppe ist,
 * stellt man auf der Seite des jeweiligen Servers ein.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, ApiError } from "../../../lib/api";
import { useGroups, type GroupOut } from "../../../lib/hosts";
import { confirmDialog, promptDialog } from "../../../state/dialogs";
import { Button, Card, NoticeLine, inputClass, type Notice } from "../ui";

export function GroupsCard() {
  const queryClient = useQueryClient();
  const groups = useGroups();
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["host-groups"] });
    await queryClient.invalidateQueries({ queryKey: ["hosts", "group"] });
  }

  async function run(action: () => Promise<unknown>, okText?: string) {
    setNotice(null);
    setBusy(true);
    try {
      await action();
      await refresh();
      if (okText) setNotice({ kind: "ok", text: okText });
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    const wanted = name.trim();
    if (!wanted) return;
    await run(async () => {
      await api.post<GroupOut>("/host-groups", { name: wanted });
      setName("");
    });
  }

  async function rename(group: GroupOut) {
    const next = await promptDialog(`Neuer Name für die Gruppe „${group.name}“:`, group.name);
    if (!next || next === group.name) return;
    await run(() => api.patch<GroupOut>(`/host-groups/${group.id}`, { name: next }));
  }

  async function remove(group: GroupOut) {
    const ok = await confirmDialog(
      `Gruppe „${group.name}“ löschen? Die Server darin bleiben erhalten. Skripte, die diese Gruppe als Ziel haben, laufen danach auf keinem Server.`,
      { danger: true, confirmLabel: "Löschen" },
    );
    if (!ok) return;
    await run(() => api.delete(`/host-groups/${group.id}`));
  }

  return (
    <Card title="Gruppen" description="Mit Gruppen lässt „Skripte“ ein Skript auf mehreren Servern laufen. Wer in welcher Gruppe ist, stellst du auf der Seite des Servers ein.">
      <NoticeLine notice={notice} />
      {groups.isError && <NoticeLine notice={{ kind: "error", text: "Die Gruppen konnten nicht geladen werden." }} />}
      {groups.data && groups.data.length === 0 && <p className="mb-4 text-sm text-white/50">Noch keine Gruppen.</p>}
      {groups.data && groups.data.length > 0 && (
        <ul className="mb-4 divide-y divide-white/[0.06] rounded-lg border border-white/[0.08]">
          {groups.data.map((g) => (
            <li key={g.id} className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
              <span className="min-w-0 break-words text-sm font-medium">{g.name}</span>
              <span className="flex flex-wrap gap-2">
                <Button disabled={busy} onClick={() => void rename(g)}>Umbenennen</Button>
                <Button variant="danger" disabled={busy} onClick={() => void remove(g)}>Löschen</Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={(e) => void create(e)} className="flex flex-col gap-2 sm:flex-row">
        <input aria-label="Neue Gruppe" placeholder="Neue Gruppe" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} className={`${inputClass} sm:max-w-xs`} />
        <Button type="submit" disabled={!name.trim()} busy={busy}>Gruppe anlegen</Button>
      </form>
    </Card>
  );
}
