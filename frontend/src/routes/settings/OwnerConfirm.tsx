/**
 * Bestätigung des Inhabers für Einstellungen, die nur er ändern darf (Selbstständigkeit, neue Server-Schlüssel): sein
 * Passwort und, wenn Zwei-Faktor an ist, der Code aus der App. Das Codefeld steht gleich da, wenn bekannt ist, dass
 * Zwei-Faktor an ist (`useTwoFactorEnabled`), sonst nach der Rückfrage des Servers (`totp_missing`). Fehlt nur der Code
 * oder stimmt er nicht, bleibt das Passwort stehen; ein Code gilt nur einmal und wird nach jedem Versuch geleert.
 */
import { useState } from "react";

import { SecondFactorField, isTotpMissing, isTotpRejected, useTwoFactorEnabled } from "./SecondFactorField";
import { Button, Field, NoticeLine, errorText, inputClass, type Notice } from "./ui";

export interface OwnerProof {
  current_password: string;
  totp_code: string;
}

export const OWNER_ONLY_HINT = "Das ändert nur der Inhaber, mit seinem Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App.";

export function OwnerConfirm({
  send, onDone, onCancel, submitLabel = "Bestätigen und speichern",
}: {
  send: (proof: OwnerProof) => Promise<void>;
  onDone: () => void;
  onCancel: () => void;
  submitLabel?: string;
}): JSX.Element {
  const twoFactor = useTwoFactorEnabled();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [askedForCode, setAskedForCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const needsCode = askedForCode || twoFactor === true;
  const ready = !!password && (!needsCode || !!code.trim()) && !busy;

  async function submit() {
    const typed = password;
    const totp = code;
    setPassword("");
    setCode("");
    setBusy(true);
    setNotice(null);
    try {
      await send({ current_password: typed, totp_code: totp });
      onDone();
    } catch (err) {
      if (isTotpMissing(err) || isTotpRejected(err)) {
        // Das Passwort stimmte (der Server prüft es zuerst); es fehlt nur ein gültiger Code aus der App.
        setAskedForCode(true);
        setPassword(typed);
      }
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="mt-4 space-y-3 rounded-lg bg-black/20 p-3"
      aria-label="Änderung bestätigen"
      onSubmit={(e) => { e.preventDefault(); if (ready) void submit(); }}
    >
      <p className="text-sm text-white/70">Bitte bestätige die Änderung mit deinem Passwort.</p>
      <NoticeLine notice={notice} />
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Dein Anmeldepasswort zur Bestätigung" className="min-w-0 basis-full sm:max-w-sm sm:flex-1 sm:basis-auto">
          <input
            type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
            aria-label="Anmeldepasswort zur Bestätigung" className={inputClass} autoFocus
          />
        </Field>
        {needsCode && (
          <SecondFactorField value={code} onChange={setCode} allowRecovery={false} className="min-w-0 basis-full sm:max-w-[12rem] sm:flex-1 sm:basis-auto" />
        )}
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>Abbrechen</Button>
        <Button type="submit" variant="primary" disabled={!ready} busy={busy}>{submitLabel}</Button>
      </div>
    </form>
  );
}
