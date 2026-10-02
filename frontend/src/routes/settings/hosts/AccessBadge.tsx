/**
 * Das Abzeichen „Zugang“ eines Servers (Server-Seite und Serverliste): gruen nur, wenn sich
 * Nodvard Deck dort wirklich angemeldet hat. Ein gespeicherter Zugang allein beweist nichts -- bei
 * einem Schluessel kann der Einrichtungsbefehl auf dem Server noch fehlen, und dass der Server
 * antwortet, heisst nur, dass sein SSH-Port offen ist. Bis zur ersten erfolgreichen Anmeldung bleibt
 * das Abzeichen deshalb grau und bekommt einen gelben Hinweis daneben.
 */
import { accessLabel, accessState, isSshCredential, type AccessState, type HostOut } from "../../../lib/hosts";
import { Badge } from "../ui";

const WARNING: Record<Exclude<AccessState, "ok">, string> = {
  "login-failed": "Anmeldung klappt nicht",
  "never-answered": "noch keine Verbindung",
  "no-answer": "keine Antwort",
  unconfirmed: "Anmeldung noch nicht bestätigt",
  unchecked: "noch nicht geprüft",
};

export function AccessBadge({ host, login = null }: { host: HostOut; /** Ergebnis der letzten Prüfung in dieser Ansicht. */ login?: "ok" | "fail" | null }) {
  if (!host.credential) return <Badge tone="warn">Kein Zugang</Badge>;
  const state = accessState(host, login);
  // Nur SSH-Zugaenge haben diese Vorgeschichte (Einrichtungsbefehl, Passwort); ein Zugriffstoken bleibt, wie es war.
  if (state === "ok" || !isSshCredential(host.credential)) return <Badge tone="good">{accessLabel(host.credential)}</Badge>;
  return (
    <>
      <Badge>{accessLabel(host.credential)}</Badge>
      <Badge tone="warn">{WARNING[state]}</Badge>
    </>
  );
}
