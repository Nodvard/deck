/**
 * Assistent, letzter Schritt: Abschluss, Hinweis auf „Erste Schritte“ im Cockpit und der
 * Notfall-Befehl für den Fall, dass man sich ausgesperrt hat (nodvard_deck.admin).
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../lib/api";
import { Button } from "../settings/ui";
import { StepNav } from "./StepNav";

export const codeBlockClass = "block select-all overflow-x-auto rounded bg-black/40 px-2 py-1.5 font-mono text-xs";

export function FinishStep({ onBack, onFinish }: { onBack: () => void; onFinish: () => void }) {
  /** `null` = unbekannt (dann kein Hinweis auf die Zwei-Faktor-Anmeldung). */
  const [totp, setTotp] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<{ totp_enabled?: boolean }>("/me")
      .then((me) => alive && setTotp(Boolean(me.totp_enabled)))
      .catch(() => undefined);
    return () => { alive = false; };
  }, []);

  return (
    <div className="flex flex-col gap-4" data-testid="setup-access">
      <div>
        <h2 className="text-sm font-semibold">Fertig – Nodvard Deck ist startklar</h2>
        <p className="mt-1 text-sm text-white/70">
          Im Cockpit zeigt dir die Karte „Erste Schritte“ als Nächstes, was noch fehlt: einen Server anlegen, seinen Zugang hinterlegen und
          die Module einrichten, die noch Angaben brauchen.
        </p>
        {totp === false && (
          <p className="mt-2 text-sm text-white/70">
            Die Zwei-Faktor-Anmeldung hast du übersprungen. Du kannst sie jederzeit nachholen – dann bekommst du auch Wiederherstellungs-Codes
            (Rettungscodes), mit denen du wieder hineinkommst, wenn dein Handy weg ist.{" "}
            <Link to="/settings/account" replace className="text-[var(--color-accent)] underline-offset-2 hover:underline">
              Zwei-Faktor jetzt einrichten (Mein Konto)
            </Link>
          </p>
        )}
      </div>
      <div className="rounded border border-white/10 p-3 text-xs">
        <p className="font-medium">Trotzdem ausgesperrt? Der Notfall-Befehl</p>
        <p className="mt-1 opacity-70">
          Auf dem Server, im Ordner mit der Compose-Datei, setzt dieser Befehl ein neues Passwort (der Benutzername steht statt
          <code> &lt;benutzername&gt;</code>):
        </p>
        <code className={`${codeBlockClass} mt-1`}>docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password &lt;benutzername&gt;</code>
        <p className="mt-1 opacity-70">Er steht auch auf der Anmeldeseite unter „Passwort vergessen?“.</p>
      </div>
      <StepNav onBack={onBack}>
        <Button variant="primary" onClick={onFinish}>Weiter zum Dashboard</Button>
      </StepNav>
    </div>
  );
}
