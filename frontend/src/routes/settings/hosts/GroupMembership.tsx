/** Gruppen eines Servers: Haken setzen = Server in die Gruppe aufnehmen, Haken weg = herausnehmen. */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../../../lib/api";
import { useGroupMembers, useGroups } from "../../../lib/hosts";
import { NoticeLine, type Notice } from "../ui";

export function GroupMembership({ hostId }: { hostId: string }) {
  const queryClient = useQueryClient();
  const groups = useGroups();
  const { members } = useGroupMembers((groups.data ?? []).map((g) => g.id));
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function toggle(groupId: string, inGroup: boolean) {
    setBusy(groupId);
    setNotice(null);
    try {
      if (inGroup) await api.delete(`/host-groups/${groupId}/members/${hostId}`);
      else await api.post(`/host-groups/${groupId}/members/${hostId}`);
      await queryClient.invalidateQueries({ queryKey: ["hosts", "group", groupId] });
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  if (!groups.data) return <p className="text-sm text-white/50">Lade Gruppen …</p>;
  if (groups.data.length === 0) {
    return (
      <p className="text-sm text-white/55">
        Noch keine Gruppen. Lege sie unter <Link to="/settings/hosts" className="underline underline-offset-2">Server &amp; Zugänge</Link> an.
      </p>
    );
  }
  return (
    <div>
      <NoticeLine notice={notice} />
      <ul className="space-y-2">
        {groups.data.map((g) => {
          const inGroup = (members[g.id] ?? []).includes(hostId);
          return (
            <li key={g.id}>
              <label className="flex cursor-pointer items-center gap-2.5 text-sm">
                <input
                  type="checkbox" className="h-4 w-4 accent-[var(--color-accent)]" checked={inGroup} disabled={busy === g.id}
                  onChange={() => void toggle(g.id, inGroup)}
                />
                {g.name}
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
