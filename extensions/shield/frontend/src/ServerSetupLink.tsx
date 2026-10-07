/** Link zur Seite „Server & Zugänge“ (Einstellungen), nur für Nutzer, die dort etwas tun dürfen. */
import { deck } from "../../../_shared/frontend/src/deck";

export function ServerSetupLink({ className = "" }: { className?: string }) {
  if (!deck().hasPermission("hosts.write")) return null;
  return (
    <a href="/settings/hosts" className={`underline underline-offset-2 ${className}`}>
      Zugang einrichten
    </a>
  );
}
