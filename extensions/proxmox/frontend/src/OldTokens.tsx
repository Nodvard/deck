/**
 * Alte Tokens von Nodvard Deck in Proxmox (Benutzer nodvard@pve und nodvard-backup@pve): angelegt beim Verbinden mit
 * Passwort, benutzt von keiner Verbindung mehr. Nodvard Deck zeigt sie mit dem Befehl zum Löschen, löscht sie aber
 * nicht selbst – dafür bräuchte sein Zugang in Proxmox das Recht „User.Modify“, und damit könnte er sich alle Rechte
 * des Benutzers geben (password_connect.py, `old_tokens_of`).
 */
import { useEffect, useState } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { Button } from "../../../_shared/frontend/src/ui";

export interface OldToken {
  token_id: string;
  /** nodvard@pve oder nodvard-backup@pve */
  user?: string;
  name: string;
  privsep: boolean;
  command: string;
}

interface OldTokensOut {
  users?: string[];
  tokens: OldToken[];
  privsep_off: string[];
  backups_checked: boolean;
}

function canCopy(): boolean {
  return typeof navigator !== "undefined" && Boolean(navigator.clipboard?.writeText) && Boolean(window.isSecureContext);
}

export function OldTokenList({ tokens }: { tokens: OldToken[] }) {
  const [copied, setCopied] = useState(false);
  if (tokens.length === 0) return null;
  const commands = tokens.map((t) => t.command).join("\n");

  async function copy() {
    try {
      await navigator.clipboard.writeText(commands);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="space-y-1.5 text-xs" data-testid="old-tokens">
      <p className="text-white/70">
        Diese Tokens hat Nodvard Deck früher angelegt, benutzt werden sie hier nicht mehr:{" "}
        {tokens.map((t) => t.token_id).join(", ")}. Braucht sie nichts anderes mehr (etwa ein zweites Nodvard Deck),
        lösch sie in Proxmox unter Rechenzentrum → Rechte → API-Token oder auf dem Server mit:
      </p>
      <pre className="select-all overflow-x-auto rounded bg-black/30 px-2 py-1.5 font-mono" aria-label="Befehle zum Löschen">{commands}</pre>
      {canCopy() && (
        <Button small variant="ghost" onClick={() => void copy()}>{copied ? "Kopiert" : "Befehle kopieren"}</Button>
      )}
      <p className="text-white/45">
        Nodvard Deck löscht sie nicht selbst: Dafür bräuchte sein Zugang in Proxmox das Recht, Benutzer zu ändern – und
        damit könnte er sich alle Rechte seines Benutzers geben.
      </p>
    </div>
  );
}

/** Lädt die alten Tokens einer Verbindung (gelesen mit ihrem gespeicherten Token, ohne Passwort). */
export function OldTokens({ connection }: { connection: string }) {
  const [data, setData] = useState<OldTokensOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setData(null);
    setError(null);
    authedFetch(`/ext/proxmox/connections/${encodeURIComponent(connection)}/old-tokens`)
      .then(async (res) => {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        if (!res.ok) throw new Error(errorFromBody(body, res.status));
        if (active) setData(body as unknown as OldTokensOut);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, [connection]);

  return (
    <section className="mb-3 space-y-1.5 rounded-lg border border-white/10 p-3" aria-label={`Alte Tokens – ${connection}`}>
      <h4 className="text-xs font-medium">Alte Tokens – {connection}</h4>
      {!data && !error && <p className="text-xs opacity-60">Lade …</p>}
      {error && <p role="alert" className="text-xs text-red-300">Nicht lesbar: {error}</p>}
      {data && data.tokens.length === 0 && (
        <p className="text-xs opacity-70">In Proxmox liegen keine alten Tokens von Nodvard Deck.</p>
      )}
      {data && <OldTokenList tokens={data.tokens} />}
      {data && !data.backups_checked && (
        <p className="text-xs text-white/45">
          Proxmox-Backups ist aus – dessen Tokens (nodvard-backup@pve) zählen deshalb nie als alt.
        </p>
      )}
      {data && data.privsep_off.length > 0 && (
        <p className="text-xs text-amber-300">
          Ohne Privilegientrennung (darf alles, was sein Benutzer darf): {data.privsep_off.join(", ")}. Brauchst du ihn
          nicht, lösch ihn in Proxmox.
        </p>
      )}
    </section>
  );
}
