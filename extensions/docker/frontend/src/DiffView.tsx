/** Unterschied zwischen der Datei auf dem Server und dem Entwurf, Zeile für Zeile. */
import { useMemo } from "react";

import { hunks, lineDiff } from "./lineDiff";

const TONE = { "+": "bg-emerald-500/15 text-emerald-200", "-": "bg-red-500/15 text-red-200", " ": "opacity-70" };

export function DiffView({ before, after, open }: { before: string; after: string; open?: boolean }): JSX.Element | null {
  const diff = useMemo(() => lineDiff(before, after), [before, after]);
  if (!diff.added && !diff.removed) return null;
  return (
    <details open={open} data-testid="stack-diff">
      <summary className="cursor-pointer select-none opacity-80">Unterschied zur Datei auf dem Server: +{diff.added} −{diff.removed} Zeilen</summary>
      {diff.lines ? (
        <pre className="mt-1 max-h-72 overflow-auto rounded bg-black/60 p-2 font-mono text-[11px] leading-snug">
          {hunks(diff.lines).map((line, index) => line
            ? <div key={index} className={TONE[line.kind]}>{line.kind} {line.text}</div>
            : <div key={index} className="opacity-40">…</div>)}
        </pre>
      ) : <p className="opacity-70">So viele Änderungen lassen sich hier nicht Zeile für Zeile zeigen.</p>}
    </details>
  );
}
