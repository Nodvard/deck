/** Kleine Zugangs-Karte fuer die Server-Seite: wie sich Nodvard Deck anmeldet, „Verbindung pruefen“, Link zur Einrichtung. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { accessState, isSshCredential, loginOutcome, type AccessState, type HostOut } from "../../../lib/hosts";
import { AccessBadge } from "./AccessBadge";
import { ConnectionCheck } from "./ConnectionCheck";

/** Eine Zeile unter der Überschrift, solange der Zugang nicht belegt ist: was als Nächstes zu tun ist. */
const HINT: Partial<Record<AccessState, string>> = {
  unchecked: "Noch nicht geprüft. Ob die Anmeldung klappt, zeigt „Verbindung prüfen“.",
  "never-answered": "Noch keine Verbindung: Der Server hat nie geantwortet. Prüfe zuerst den Zugang.",
  unconfirmed: "Der Server antwortet, aber die Anmeldung ist noch nicht bestätigt. Bei einem Schlüssel fehlt vermutlich noch der Einrichtungsbefehl auf dem Server. „Verbindung prüfen“ zeigt es.",
  "no-answer": "Der Server antwortet gerade nicht. „Verbindung prüfen“ zeigt, woran es liegt.",
};

export function AccessCard({ host }: { host: HostOut }) {
  // Ergebnis der Prüfung, die jemand in dieser Karte gerade gemacht hat (schlägt den Zustand des Servers).
  const [login, setLogin] = useState<"ok" | "fail" | null>(null);
  const hint = host.credential && isSshCredential(host.credential) ? HINT[accessState(host, login)] : undefined;
  return (
    <section id="zugang" className="panel mt-4 scroll-mt-20 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold">Zugang</h2>
          <AccessBadge host={host} login={login} />
        </div>
        <Link to={`/settings/hosts/${host.id}`} className="text-sm text-white/70 underline underline-offset-2 hover:text-white">
          Zugang einrichten
        </Link>
      </div>
      {hint && <p className="mb-3 text-xs text-amber-200/90" data-testid="access-hint">{hint}</p>}
      <ConnectionCheck hostId={host.id} onResult={(result) => setLogin(loginOutcome(result))} />
    </section>
  );
}
