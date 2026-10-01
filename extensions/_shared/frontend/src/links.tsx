/**
 * Hilfen fuer Leerzustaende der Erweiterungsseiten (zusammen mit `EmptyState` aus ui.tsx):
 * ein Knopf zur passenden Einstellungsseite im Kern -- nur mit dem Recht dafuer -- und der
 * Wert einer Einstellung der Erweiterung (z. B. die Server-Markierung) fuer den Hinweistext.
 *
 * Eigene Datei aus demselben Grund wie AuthImage.tsx und location.ts: nur Bundles, die sie
 * wirklich einbinden, aendern sich.
 */
import { useEffect, useState, type ReactNode } from "react";

import { authedFetch } from "./api";
import { buttonClass } from "./ui";
import { deck } from "./deck";

/** Link (im Aussehen eines Knopfes) auf eine Seite des Kerns; ohne das Recht gibt es nichts zu klicken. */
export function SettingsLink({
  to, permission, variant = "primary", children,
}: { to: string; permission: string; variant?: "primary" | "secondary"; children: ReactNode }) {
  if (!deck().hasPermission(permission)) return null;
  return <a href={to} className={buttonClass(variant)}>{children}</a>;
}

/** Ein Text aus den Einstellungen der Erweiterung (`GET /extensions/<id>/settings`), solange er
 * nicht ankommt oder leer ist: `fallback`. Nie ein Fehler -- der Hinweis soll immer lesbar bleiben. */
export function useSettingText(extId: string, key: string, fallback: string): string {
  const [value, setValue] = useState(fallback);
  useEffect(() => {
    let cancelled = false;
    authedFetch(`/extensions/${extId}/settings`)
      .then((res) => (res.ok ? (res.json() as Promise<{ values?: Record<string, unknown> }>) : null))
      .then((body) => {
        const text = body?.values?.[key];
        if (!cancelled && typeof text === "string" && text.trim()) setValue(text.trim());
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [extId, key]);
  return value;
}
