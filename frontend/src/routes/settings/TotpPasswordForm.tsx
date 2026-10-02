/**
 * Passwortabfrage vor dem Einrichten der Zwei-Faktor-Anmeldung. Der Server verlangt das aktuelle
 * Passwort (wie beim Abschalten), damit eine fremde, offen gebliebene Sitzung die Anmeldung nicht
 * mit einer eigenen App einschalten und dich aussperren kann. Gemeinsam genutzt von Mein Konto
 * und dem Einrichtungsassistenten.
 */
import { useState } from "react";

import { Button, Field, inputClass } from "./ui";

export function TotpPasswordForm({
  busy, onSubmit, onCancel, cancelLabel = "Abbrechen",
}: {
  busy: boolean;
  onSubmit: (password: string) => void;
  onCancel: () => void;
  cancelLabel?: string;
}): JSX.Element {
  const [password, setPassword] = useState("");

  return (
    <form
      className="mt-5 flex max-w-md flex-wrap items-end gap-2"
      data-testid="totp-password"
      onSubmit={(e) => {
        e.preventDefault();
        if (password && !busy) onSubmit(password);
      }}
    >
      <Field label="Passwort zur Bestätigung" hint="Zur Sicherheit wird dein aktuelles Passwort verlangt." className="flex-1">
        <input
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={inputClass}
        />
      </Field>
      <Button type="submit" variant="primary" busy={busy} disabled={!password}>Weiter</Button>
      <Button variant="ghost" onClick={onCancel}>{cancelLabel}</Button>
    </form>
  );
}
