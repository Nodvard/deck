/**
 * Zwei-Faktor einrichten: QR-Code zum Abfotografieren, der Schluessel als Text fuer den Fall, dass
 * das Scannen nicht klappt, und das Feld fuer den ersten Code aus der App. Gemeinsam genutzt von
 * Mein Konto (`TotpCard`) und dem Einrichtungsassistenten. Der QR-Code entsteht im Browser
 * (components/QrCode.tsx), nichts geht an einen fremden Dienst.
 *
 * Die Texte sind bewusst ohne Anrede formuliert (die Oberfläche duzt sonst überall).
 */
import { useState } from "react";

import { QrCode } from "../../components/QrCode";
import { Button, inputClass } from "./ui";

export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
}

export function TotpEnroll({
  setup, busy, onConfirm, onCancel, cancelLabel = "Abbrechen",
}: {
  setup: TotpSetup;
  busy: boolean;
  /** Bekommt den Code ohne Leerzeichen (genau 6 Stellen). */
  onConfirm: (code: string) => void;
  onCancel: () => void;
  cancelLabel?: string;
}): JSX.Element {
  const [code, setCode] = useState("");
  const digits = code.replace(/\s/g, "");
  const secretGroups = setup.secret.match(/.{1,4}/g)?.join(" ");

  return (
    <div className="mt-5 rounded-lg border border-white/10 bg-black/20 p-4" data-testid="totp-enroll">
      <ol className="list-decimal space-y-5 pl-5 text-sm text-white/80">
        <li>
          Authenticator-App auf dem Handy öffnen (z. B. Google Authenticator, Microsoft Authenticator, 2FAS oder Aegis) und diesen
          QR-Code scannen:
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
            <QrCode value={setup.otpauth_uri} label="QR-Code für die Authenticator-App" size={176} />
            <p className="min-w-0 flex-1 basis-40 text-xs text-white/50">
              Der QR-Code wird in diesem Browser erzeugt und nirgends hingeschickt.
              <br />
              <a href={setup.otpauth_uri} className="mt-1 inline-block underline-offset-2 hover:text-white hover:underline">
                Am Handy geöffnet? Direkt in der App öffnen
              </a>
            </p>
          </div>
        </li>
        <li>
          Klappt das Scannen nicht, ein Konto von Hand hinzufügen und diesen Schlüssel eintippen:
          <code data-testid="totp-secret" className="mt-2 block select-all break-words rounded-md bg-black/40 px-3 py-2 font-mono text-base tracking-wider text-white">
            {secretGroups}
          </code>
        </li>
        <li>
          Den sechsstelligen Code eingeben, den die App jetzt anzeigt:
          <div className="mt-2 flex max-w-xs gap-2">
            <input
              aria-label="Bestätigungscode"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={7}
              placeholder="123 456"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && digits.length === 6 && !busy) onConfirm(digits); }}
              className={`${inputClass} font-mono tracking-[0.3em]`}
            />
            <Button variant="primary" busy={busy} disabled={digits.length !== 6} onClick={() => onConfirm(digits)}>
              Bestätigen
            </Button>
          </div>
        </li>
      </ol>
      <div className="mt-3 text-right">
        <Button variant="ghost" onClick={onCancel}>{cancelLabel}</Button>
      </div>
    </div>
  );
}
