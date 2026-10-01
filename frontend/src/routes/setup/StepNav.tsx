import type { ReactNode } from "react";

import { Button } from "../settings/ui";

/** Knopfzeile unter jedem Schritt: links „Zurück“, rechts die Weiter-Knöpfe (umbrechen am Handy). */
export function StepNav({ onBack, children }: { onBack?: () => void; children: ReactNode }) {
  return (
    <div className="mt-6 flex flex-wrap items-center gap-2">
      {onBack && <Button variant="ghost" onClick={onBack}>Zurück</Button>}
      <span className="flex-1" />
      {children}
    </div>
  );
}
