/**
 * Knopf „Mit Beispieldaten ansehen“: legt Beispiel-Server, -Meldungen und ein Beispiel-Layout an,
 * damit man sieht, wie das Dashboard gefuellt aussieht. Erscheint nur mit dem Recht dafuer
 * (`hosts.write` und `settings.write`) -- sonst rendert die Komponente nichts.
 */
import { Eye, Loader2 } from "lucide-react";

import { canManageDemo, useSeedDemo } from "../lib/demo";
import { useAuthStore } from "../state/auth";

const STYLES = {
  secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
  primary: "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110",
};

export function DemoSeedButton({ small = false, variant = "secondary" }: { small?: boolean; variant?: keyof typeof STYLES }) {
  const can = useAuthStore((s) => s.hasPermission);
  const { run, busy, error } = useSeedDemo();
  if (!canManageDemo(can)) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => void run()}
        disabled={busy}
        data-testid="demo-seed"
        className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${
          small ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm"
        } ${STYLES[variant]}`}
      >
        {busy ? <Loader2 size={small ? 12 : 14} className="animate-spin" /> : <Eye size={small ? 12 : 14} />}
        Mit Beispieldaten ansehen
      </button>
      {error && (
        <p role="alert" className="basis-full text-center text-xs text-red-300">{error}</p>
      )}
    </>
  );
}
