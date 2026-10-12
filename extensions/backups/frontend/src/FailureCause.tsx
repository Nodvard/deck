/**
 * Warum ist ein Backup fehlgeschlagen? Spricht `GET /ext/backups/jobs/{job_ref}/failure` (failure_text.py im Backend):
 * zwei feste Sätze („Was ist passiert?“, „Was kannst du tun?“) und darunter die Zeile von Proxmox. Ohne `upid` der
 * neueste fehlgeschlagene Lauf, sonst genau dieser.
 */
import { useEffect, useState } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";

export interface FailureCauseOut {
  upid: string;
  node: string;
  started_at: number | null;
  finished_at: number | null;
  status: string | null;
  log_readable: boolean;
  key: string;
  what: string;
  todo: string;
  detail: string | null;
}

export function FailureCause({ jobRef, upid = null }: { jobRef: string; upid?: string | null }) {
  const [cause, setCause] = useState<FailureCauseOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setCause(null);
    setError(null);
    const query = upid ? `?upid=${encodeURIComponent(upid)}` : "";
    authedFetch(`/ext/backups/jobs/${encodeURIComponent(jobRef)}/failure${query}`)
      .then(async (res) => {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        if (!res.ok) throw new Error(errorFromBody(body, res.status));
        if (active) setCause(body as unknown as FailureCauseOut);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, [jobRef, upid]);

  return (
    <div className="mb-2 space-y-1 rounded border border-red-500/30 bg-red-500/5 p-2 text-xs" data-testid="failure-cause">
      {!cause && !error && <p className="opacity-60">Lese, warum der Lauf fehlgeschlagen ist …</p>}
      {error && <p role="alert" className="text-red-300">Grund nicht lesbar: {error}</p>}
      {cause && (
        <>
          <p><span className="font-medium">Was ist passiert?</span> {cause.what}</p>
          <p><span className="font-medium">Was kannst du tun?</span> {cause.todo}</p>
          {cause.detail && (
            <p className="break-all font-mono opacity-70" data-testid="failure-detail">Proxmox meldet: {cause.detail}</p>
          )}
          {!cause.log_readable && (
            <p className="opacity-60">
              Das Protokoll des Laufs ließ sich nicht lesen (meist fehlt dem Zugang das Recht „Sys.Audit“, oder Proxmox hat
              das alte Protokoll schon gelöscht). Die Erklärung stützt sich nur auf das Ergebnis des Laufs.
            </p>
          )}
        </>
      )}
    </div>
  );
}
