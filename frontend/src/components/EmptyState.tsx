/**
 * Leere Seite mit Hilfe: sagt in einem Satz, was fehlt und was zu tun ist, und bietet den Knopf
 * zum naechsten Schritt an. Gegenstueck zu `EmptyState` im Erweiterungs-UI-Kit
 * (extensions/_shared/frontend/src/ui.tsx) -- gleiche Optik, damit Kern und Module gleich wirken.
 *
 * Der Knopf (`action`) gehoert nur hinein, wenn der Nutzer das Recht fuer das Ziel hat
 * (`hosts.write`, `extensions.manage`, ...) -- sonst nur den Text zeigen.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Icon } from "./Icon";

export function EmptyState({
  icon, title, text, action, compact = false, testId,
}: {
  /** lucide-Name, siehe `Icon`. */
  icon: string;
  title: string;
  text?: ReactNode;
  action?: ReactNode;
  /** Schmale Spalten (Seitenleiste einer Seite): weniger Innenabstand. */
  compact?: boolean;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={`panel flex flex-col items-center text-center ${compact ? "px-4 py-6" : "px-6 py-12"}`}
    >
      <span className="mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.06] text-[var(--color-accent)]">
        <Icon name={icon} size={22} />
      </span>
      <p className="text-sm font-medium">{title}</p>
      {text && <p className="mt-1 max-w-md break-words text-sm text-white/50">{text}</p>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

const LINK_STYLES = {
  primary: "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110",
  secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
};

/** Ein Link im Aussehen eines Knopfes (gleiche Klassen wie `Button` in routes/settings/ui.tsx). */
export function ButtonLink({
  to, children, variant = "primary", small = false,
}: { to: string; children: ReactNode; variant?: keyof typeof LINK_STYLES; small?: boolean }) {
  return (
    <Link
      to={to}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition ${
        small ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm"
      } ${LINK_STYLES[variant]}`}
    >
      {children}
    </Link>
  );
}
