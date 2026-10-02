/**
 * Band ganz oben, solange Beispieldaten da sind: „Du siehst Beispieldaten.“ mit dem Knopf, sie mit
 * einem Klick (nach Rueckfrage) wieder zu loeschen. Wer Server sehen darf, sieht das Band; den Knopf
 * gibt es nur mit dem Recht dazu (`hosts.write` und `settings.write`). Auf dem Handy rutscht der Knopf
 * unter den Text.
 */
import { FlaskConical, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";

import { canManageDemo, useDemoStatus, useRemoveDemo } from "../lib/demo";
import { useAuthStore } from "../state/auth";

export function DemoBanner() {
  const can = useAuthStore((s) => s.hasPermission);
  const status = useDemoStatus(can("hosts.read"));
  const { run, busy, error } = useRemoveDemo();
  const data = status.data;
  if (!data?.active) return null;
  return (
    <div
      role="status"
      data-testid="demo-banner"
      className="flex flex-none flex-col gap-2 border-b border-amber-400/25 bg-amber-500/15 px-4 py-2.5 text-sm text-amber-100 sm:flex-row sm:items-center sm:gap-3 sm:px-6"
    >
      <FlaskConical size={16} className="hidden flex-none text-amber-300 sm:block" aria-hidden />
      <p className="min-w-0 flex-1 break-words">
        <strong className="font-semibold">Du siehst Beispieldaten.</strong>{" "}
        <span className="text-amber-100/80">
          Alles hier ist ausgedacht
          {canManageDemo(can) ? "." : " – ein Administrator kann sie löschen."}
        </span>{" "}
        <span className="text-amber-100/80" data-testid="demo-modules-hint">
          Wie viel davon zu sehen ist, hängt von den eingeschalteten Modulen ab
          {can("extensions.manage") ? (
            <>
              {" "}
              (<Link to="/settings/extensions" className="underline underline-offset-2 hover:text-amber-50">Module ansehen</Link>).
            </>
          ) : "."}
        </span>
        {error && <span role="alert" className="mt-1 block text-red-200">{error}</span>}
      </p>
      {canManageDemo(can) && (
        <button
          type="button"
          onClick={() => void run(data)}
          disabled={busy}
          className="inline-flex flex-none items-center justify-center gap-1.5 self-start rounded-lg border border-amber-300/40 bg-amber-400/15 px-3 py-1.5 text-sm font-medium text-amber-50 transition hover:bg-amber-400/25 disabled:cursor-not-allowed disabled:opacity-60 sm:self-auto"
        >
          {busy && <Loader2 size={14} className="animate-spin" />}
          Beispieldaten löschen
        </button>
      )}
    </div>
  );
}
