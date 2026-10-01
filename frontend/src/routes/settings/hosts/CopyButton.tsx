/** Kopierknopf mit Rueckmeldung („Kopiert“); geht auch ohne sicheren Kontext (lib/clipboard.ts). */
import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { copyText } from "../../../lib/clipboard";

export function CopyButton({
  text, label = "Kopieren", onFailed, className = "",
}: {
  text: string;
  label?: string;
  /** Wird gerufen, wenn das Kopieren nicht ging (der Aufrufer markiert dann den Text). */
  onFailed?: () => void;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    const ok = await copyText(text);
    if (!ok) {
      onFailed?.();
      return;
    }
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-medium text-white hover:bg-white/[0.12] ${className}`}
    >
      {copied ? <Check size={14} className="text-emerald-300" /> : <Copy size={14} />}
      {copied ? "Kopiert" : label}
    </button>
  );
}
