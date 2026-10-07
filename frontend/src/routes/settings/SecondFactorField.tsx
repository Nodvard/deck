/**
 * Eingabe für den zweiten Faktor, wenn eine Aktion außer dem Passwort einen Code verlangt.
 *
 * - Mit `allowRecovery` (Zwei-Faktor abschalten, neue Wiederherstellungs-Codes): ein Feld für beides. Sechs Ziffern
 *   sind der Code aus der App, alles andere ein Wiederherstellungs-Code – das erkennt der Server selbst, wie bei der
 *   Anmeldung.
 * - Ohne (Sicherung herunterladen oder einspielen): nur der Code aus der App. Wiederherstellungs-Codes sind für den
 *   Notfall bei der Anmeldung da.
 */
import { useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";
import { Field, inputClass } from "./ui";

export const SECOND_FACTOR_LABEL = "Code aus der App oder Wiederherstellungs-Code";
export const TOTP_LABEL = "Zwei-Faktor-Code";

function errorCode(err: unknown, status: number): unknown {
  if (!(err instanceof ApiError) || err.status !== status) return undefined;
  const body = err.detail;
  return typeof body === "object" && body !== null ? (body as { code?: unknown }).code : undefined;
}

/** Der Server will zusätzlich den Code (403 mit `code: "totp_missing"`): Zwei-Faktor ist für dieses Konto an. */
export function isTotpMissing(err: unknown): boolean {
  return errorCode(err, 403) === "totp_missing";
}

/**
 * Der Code stimmt nicht oder wurde schon benutzt (400 mit `code: "totp_wrong"` bzw. `"totp_used"`). Das Passwort
 * davor stimmte (der Server prüft es zuerst): nur der Code muss neu eingegeben werden.
 */
export function isTotpRejected(err: unknown): boolean {
  const code = errorCode(err, 400);
  return code === "totp_wrong" || code === "totp_used";
}

/**
 * Ist für das angemeldete Konto die Zwei-Faktor-Anmeldung an (`GET /me`, `totp_enabled`)? Dann zeigt die Oberfläche
 * das Code-Feld gleich und nicht erst nach der ersten Rückfrage des Servers. `null`, solange es nicht feststeht (Antwort
 * noch unterwegs, Abfrage gescheitert, `enabled` aus): Dann bleibt die Rückfrage (`totp_missing`) der Weg – sie gilt
 * immer, auch wenn Zwei-Faktor gerade in einem anderen Fenster eingeschaltet wurde.
 */
export function useTwoFactorEnabled(enabled = true): boolean | null {
  const [value, setValue] = useState<boolean | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    api
      .get<{ totp_enabled?: boolean }>("/me")
      .then((me) => {
        if (alive) setValue(me.totp_enabled === true);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [enabled]);
  return enabled ? value : null;
}

export function SecondFactorField({
  value, onChange, allowRecovery = true, className = "",
}: {
  value: string;
  onChange: (value: string) => void;
  allowRecovery?: boolean;
  className?: string;
}): JSX.Element {
  return (
    <Field
      label={allowRecovery ? SECOND_FACTOR_LABEL : TOTP_LABEL}
      hint={
        allowRecovery
          ? "Die sechs Ziffern aus deiner Authenticator-App. Handy nicht zur Hand? Dann geht auch einer deiner Wiederherstellungs-Codes, er ist danach verbraucht."
          : "Die sechs Ziffern aus deiner Authenticator-App. Jeder Code gilt nur einmal."
      }
      className={className}
    >
      <input
        autoComplete="one-time-code"
        autoCapitalize={allowRecovery ? "characters" : undefined}
        inputMode={allowRecovery ? undefined : "numeric"}
        spellCheck={false}
        placeholder={allowRecovery ? "123456 oder XXXXX-XXXXX" : "123456"}
        maxLength={allowRecovery ? 32 : 8}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`${inputClass} font-mono`}
      />
    </Field>
  );
}
