/** Ein Image per Name von seiner Registry holen; läuft über die Freigabe. */
import { useState } from "react";

import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage } from "../../../_shared/frontend/src/loadError";
import { Button } from "../../../_shared/frontend/src/ui";

import { hostAction } from "./hostAction";

// Dieselbe Namensregel wie IMAGE_REF_RE im Backend, das verbindlich prüft.
const NAME = "[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*";
const LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?";
const DOMAIN = `${LABEL}(?:\\.${LABEL})*(?::[0-9]{1,5})?`;
const IMAGE_RE = new RegExp(`^(?:${DOMAIN}/)?${NAME}(?:/${NAME})*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[a-f0-9]{64})?$`);
const MAX_LENGTH = 255;

export function validImage(image: string): boolean {
  return image.length <= MAX_LENGTH && IMAGE_RE.test(image);
}

/** Ohne Tag und ohne Digest holt Docker `latest`; ein Port im Registry-Namen ist kein Tag. */
export function withoutTag(image: string): boolean {
  if (image.includes("@")) return false;
  return !(image.split("/").pop() ?? "").includes(":");
}

/** `onDone` bekommt bei einem Vorschlag die Kennung der Aktion, damit die Liste nach der Freigabe neu lädt. */
export function ImagePull({ hostId, onDone }: { hostId: string; onDone: (message: string, waitingFor?: string) => void }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [open, setOpen] = useState(false);
  const [image, setImage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = validImage(image);

  // Bei einem Fehler bleibt das Formular mit dem Namen offen; `onDone` lädt die Liste neu und baut es dabei ab.
  async function pull() {
    setBusy(true);
    setError(null);
    try {
      const result = await hostAction(hostId, "docker.image_pull", "Image über die Docker-Seite holen.", unmountSignal(), { image });
      if (result.failed) {
        setError(result.text);
        return;
      }
      setOpen(false);
      setImage("");
      onDone(`Image holen: ${result.text}`, result.waiting ? result.actionId : undefined);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) return <div className="mb-2"><Button small onClick={() => setOpen(true)}>Image holen</Button></div>;
  return (
    <div className="mb-3 flex flex-col gap-2 bg-black/20 p-2 text-xs panel" data-testid="image-pull">
      <label className="flex flex-col gap-1">
        <span>Name des Images mit Tag</span>
        <input aria-label="Name des Images" value={image} maxLength={MAX_LENGTH} onChange={(e) => setImage(e.target.value.trim())}
          placeholder="zum Beispiel nginx:1.27 oder ghcr.io/besitzer/app:1.0"
          className="rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]" />
      </label>
      {image && !valid && (
        <p className="text-amber-300">
          So heißt kein Image. Erlaubt sind Kleinbuchstaben, Ziffern, „.“, „_“, „-“ und „/“, davor auf Wunsch die Registry und
          dahinter „:“ mit dem Tag, etwa ghcr.io/besitzer/app:1.0.
        </p>
      )}
      {valid && withoutTag(image) && (
        <p className="text-amber-300">
          Ohne Tag holt Docker „latest“, also den Stand, der gerade bei der Registry liegt. Für Stacks besser eine feste Version
          wie nginx:1.27.
        </p>
      )}
      <p className="opacity-70">
        Private Images holt Docker mit der Anmeldung unter „Registries“. Das Holen kann ein paar Minuten dauern und braucht Platz auf dem
        Server; die Seite darf dabei offen bleiben oder verlassen werden. Laufende Container ändern sich nicht.
      </p>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <div className="flex flex-wrap gap-1.5">
        <Button small variant="primary" disabled={busy || !valid} onClick={() => void pull()}>{busy ? "…" : "Holen"}</Button>
        <Button small disabled={busy} onClick={() => setOpen(false)}>Abbrechen</Button>
      </div>
    </div>
  );
}
