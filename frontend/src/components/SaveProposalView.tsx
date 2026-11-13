import { useState } from "react";

import { api, ApiError } from "../lib/api";

export const SAVE_ACTION_TYPE = "docker.stack_save";

interface Proposal {
  content: string;
  diff: string;
}

/** Der volle Inhalt und der Unterschied einer Compose-Änderung; die Schnittstelle prüft, wer das sehen darf. */
export function SaveProposalView({ hostId, project, actionId }: { hostId: string; project: string; actionId: string }): JSX.Element {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      const path = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/save-proposals/${encodeURIComponent(actionId)}`;
      setProposal(await api.get<Proposal>(path));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Der Inhalt ließ sich nicht laden. Bitte erneut versuchen.");
    } finally {
      setBusy(false);
    }
  }

  if (proposal === null) {
    return (
      <div className="mt-1">
        <button type="button" disabled={busy} onClick={() => void load()} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40">
          Änderung ansehen
        </button>
        {error && <p role="alert" className="mt-1 max-w-xs break-words text-xs text-red-300">{error}</p>}
      </div>
    );
  }
  return (
    <div className="mt-1 space-y-1" data-testid={`proposal-${actionId}`}>
      <p className="text-xs opacity-70">Unterschied zur Datei auf dem Server:</p>
      <pre className="max-h-60 max-w-md overflow-auto whitespace-pre-wrap break-all rounded bg-black/30 p-1.5 font-mono text-xs">{proposal.diff || "Kein Unterschied."}</pre>
      <details>
        <summary className="cursor-pointer text-xs opacity-70">Ganze neue Datei</summary>
        <pre className="max-h-60 max-w-md overflow-auto whitespace-pre-wrap break-all rounded bg-black/30 p-1.5 font-mono text-xs">{proposal.content}</pre>
      </details>
    </div>
  );
}
