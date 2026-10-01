/**
 * Die tatsaechliche UI zu state/dialogs.ts -- genau EINMAL in AppShell.tsx gemountet,
 * siehe dortigen Docstring fuer die Begruendung (Singleton statt Hook pro Aufrufer).
 *
 * Bewusst zwei getrennte `Dialog.Root`s (Confirm/Prompt) statt eines gemeinsamen --
 * beide koennen unabhaengig offen sein (in der Praxis nie gleichzeitig, aber nichts
 * erzwingt das), und die JSX bleibt so einfacher als mit einer Union aus zwei
 * Payload-Formen in einem Root.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { useState, type FormEvent } from "react";

import { useDialogsStore } from "../state/dialogs";

export function GlobalDialogs() {
  const confirmPending = useDialogsStore((s) => s.confirmPending);
  const settleConfirm = useDialogsStore((s) => s.settleConfirm);
  const promptPending = useDialogsStore((s) => s.promptPending);
  const settlePrompt = useDialogsStore((s) => s.settlePrompt);

  return (
    <>
      <Dialog.Root open={confirmPending !== null} onOpenChange={(open) => { if (!open) settleConfirm(false); }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 shadow-xl">
            <Dialog.Title className="mb-2 text-base font-semibold">
              {confirmPending?.options.title ?? "Bestätigen"}
            </Dialog.Title>
            <Dialog.Description className="mb-4 text-sm opacity-80">
              {confirmPending?.message}
            </Dialog.Description>
            <div className="flex justify-end gap-2">
              <Dialog.Close asChild>
                <button type="button" className="rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20">
                  {confirmPending?.options.cancelLabel ?? "Abbrechen"}
                </button>
              </Dialog.Close>
              <button
                type="button"
                onClick={() => settleConfirm(true)}
                className={`rounded px-3 py-1.5 text-sm ${
                  confirmPending?.options.danger
                    ? "bg-red-500/90 text-white hover:bg-red-500"
                    : "bg-[var(--color-accent)] text-[var(--color-background)]"
                }`}
              >
                {confirmPending?.options.confirmLabel ?? "Bestätigen"}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <PromptDialogContent
        // Wechselt der Schluessel (neue Anfrage), montiert React die Komponente
        // komplett neu -- ihr `useState(defaultValue)` startet dadurch automatisch
        // wieder frisch, ohne einen manuellen Abgleich in einem Effekt/Render-Zweig.
        key={promptPending ? `${promptPending.message}:${promptPending.defaultValue}` : "closed"}
        open={promptPending !== null}
        message={promptPending?.message ?? ""}
        defaultValue={promptPending?.defaultValue ?? ""}
        onCancel={() => settlePrompt(null)}
        onSubmit={(value) => settlePrompt(value.trim() || null)}
      />
    </>
  );
}

function PromptDialogContent({
  open, message, defaultValue, onCancel, onSubmit,
}: {
  open: boolean; message: string; defaultValue: string;
  onCancel: () => void; onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(defaultValue);

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit(value);
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next) onCancel(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 shadow-xl">
          <Dialog.Title className="mb-3 text-base font-semibold">{message}</Dialog.Title>
          <form onSubmit={submit}>
            <input
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onFocus={(e) => e.target.select()}
              className="mb-4 w-full rounded bg-white/10 px-2 py-1.5 text-sm"
            />
            <div className="flex justify-end gap-2">
              <Dialog.Close asChild>
                <button type="button" className="rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20">
                  Abbrechen
                </button>
              </Dialog.Close>
              <button type="submit" className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm text-[var(--color-background)]">
                OK
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
