import { useEffect, useRef, useState } from "react";

import { Button } from "../../../_shared/frontend/src/ui";

import type { StackOut } from "./Stacks";

export function StackStopDialog({ stack, onClose, onStop }: {
  stack: StackOut; onClose: () => void; onStop: (verb: "stop" | "down") => void;
}): JSX.Element {
  const ref = useRef<HTMLDialogElement>(null);
  const [verb, setVerb] = useState<"stop" | "down">(stack.running > 0 ? "stop" : "down");

  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => { dialog.close(); trigger?.focus(); };
  }, []);

  return (
    <dialog ref={ref} aria-label={`Stack „${stack.project}“ stoppen`}
      className="m-auto w-[28rem] max-w-[calc(100vw-2rem)] rounded-xl border border-white/10 bg-[var(--color-surface)] p-4 text-sm text-white shadow-xl backdrop:bg-black/60"
      onCancel={(e) => { e.preventDefault(); onClose(); }}>
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); onStop(verb); }}>
        <h3 className="break-words font-medium">Stack „{stack.project}“ stoppen</h3>
        <label className="flex items-center gap-2">
          <input type="radio" name="stack-stop" checked={verb === "stop"} disabled={stack.running === 0} onChange={() => setVerb("stop")} />
          Nur anhalten
        </label>
        <p className="text-xs opacity-70">Die Container bleiben bestehen. Die Dienste sind bis zum nächsten Anwenden nicht erreichbar.</p>
        <label className="flex items-center gap-2">
          <input type="radio" name="stack-stop" checked={verb === "down"} onChange={() => setVerb("down")} />
          Container entfernen
        </label>
        <p className="text-xs opacity-70">
          Container und Netze werden entfernt. Was nur im Container lag und nicht in einem Volume oder Ordner, geht verloren.
          Die Volumes bleiben. Anwenden legt die Container neu an.
        </p>
        <div className="flex justify-end gap-2">
          <Button small onClick={onClose}>Abbrechen</Button>
          <Button small variant="danger" type="submit">Stoppen</Button>
        </div>
      </form>
    </dialog>
  );
}
