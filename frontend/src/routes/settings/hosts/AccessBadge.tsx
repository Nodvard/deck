/**
 * Das Abzeichen „Zugang“ eines Servers: grün nur, wenn sich Nodvard Deck dort wirklich angemeldet hat. Ein
 * gespeicherter Zugang beweist nichts (der Einrichtungsbefehl kann fehlen, und eine Antwort heißt nur, dass der
 * SSH-Port offen ist); bis zur ersten erfolgreichen Anmeldung bleibt es grau, mit gelbem Hinweis.
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
  // Nur SSH-Zugänge können unbestätigt sein (Einrichtungsbefehl, Passwort); ein Zugriffstoken wird gleich grün.
  if (state === "ok" || !isSshCredential(host.credential)) return <Badge tone="good">{accessLabel(host.credential)}</Badge>;
  return (
    <>
      <Badge>{accessLabel(host.credential)}</Badge>
      <Badge tone="warn">{WARNING[state]}</Badge>
    </>
  );
}
