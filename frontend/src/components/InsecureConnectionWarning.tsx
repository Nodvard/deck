import { AlertTriangle, X } from "lucide-react";

import { useConnectionWarningStore } from "../state/connectionWarning";
import { GuideLink } from "./GuideLink";

export function InsecureConnectionWarning({ insecure }: { insecure: boolean }) {
  const dismissed = useConnectionWarningStore((s) => s.dismissed);
  const dismiss = useConnectionWarningStore((s) => s.dismiss);
  if (!insecure || dismissed) return null;

  return (
    <div role="alert" className="mb-4 flex items-start gap-3 rounded-lg border border-amber-500/50 bg-amber-500/15 p-3 text-sm text-amber-100">
      <AlertTriangle size={20} aria-hidden className="mt-0.5 flex-none" />
      <div className="min-w-0 flex-1">
        <p>Diese Verbindung ist unverschlüsselt. Im Heimnetz kann dein Anmelde-Token mitgelesen werden.</p>
        <GuideLink section="secureConnection" className="mt-1">Verbindung absichern</GuideLink>
      </div>
      <button type="button" onClick={dismiss} aria-label="Verbindungshinweis für diese Sitzung ausblenden" className="rounded p-1 hover:bg-amber-500/20">
        <X size={18} aria-hidden />
      </button>
    </div>
  );
}
