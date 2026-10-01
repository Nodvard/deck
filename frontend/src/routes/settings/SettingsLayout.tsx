/**
 * Einstellungen mit Seitennavigation. Jeder Bereich ist eine eigene Unterseite
 * (/settings/<bereich>); Bereiche ohne passende Berechtigung werden nicht angezeigt.
 */
import { Blocks, Bot, HardDrive, History, Info, Palette, Server, UserCircle, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuthStore } from "../../state/auth";

interface Section {
  path: string;
  label: string;
  description: string;
  icon: LucideIcon;
  /** Eine Berechtigung oder eine Liste, von der eine reicht. */
  permission?: string | string[];
}

export const SECTIONS: Section[] = [
  { path: "account", label: "Mein Konto", description: "Profil, Passwort, Zwei-Faktor", icon: UserCircle },
  { path: "appearance", label: "Aussehen", description: "Logo, Farben, Anmeldeseite", icon: Palette, permission: "branding.write" },
  { path: "users", label: "Benutzer", description: "Konten und Rollen", icon: Users, permission: "users.read" },
  { path: "hosts", label: "Server & Zugänge", description: "Server anlegen, SSH-Zugang, Prüfung", icon: Server, permission: "hosts.write" },
  { path: "automation", label: "Automatik & Sicherheit", description: "Freigaben, Sperrliste, Wartung", icon: Bot, permission: "settings.write" },
  // `system.read` zeigt die Sicherung, `settings.write` die Karte „Zeit & Protokoll“ -- eines reicht für den Reiter.
  { path: "system", label: "System", description: "Sicherung, Zeit, Protokoll", icon: HardDrive, permission: ["system.read", "settings.write"] },
  { path: "extensions", label: "Erweiterungen", description: "Module ein- und ausschalten", icon: Blocks, permission: "extensions.manage" },
  { path: "audit", label: "Protokoll", description: "Wer hat wann was getan", icon: History, permission: "audit.read" },
  // Für jeden angemeldeten Nutzer, ohne Berechtigung: Version und Änderungsprotokoll.
  { path: "about", label: "Über Nodvard Deck", description: "Version und Änderungsprotokoll", icon: Info },
];

export function useVisibleSections(): Section[] {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  // `user` abonnieren, damit sich die Liste nach dem Laden der Rechte aktualisiert.
  useAuthStore((s) => s.user);
  return SECTIONS.filter((s) => {
    if (!s.permission) return true;
    const wanted = Array.isArray(s.permission) ? s.permission : [s.permission];
    return wanted.some((p) => hasPermission(p));
  });
}

export function SettingsLayout(): JSX.Element {
  const sections = useVisibleSections();
  const location = useLocation();

  if (location.pathname.replace(/\/$/, "") === "/settings") {
    return <Navigate to="/settings/account" replace />;
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6 lg:flex-row">
      <nav aria-label="Einstellungen" className="lg:w-60 lg:flex-none">
        <h1 className="mb-3 px-2 text-xs font-semibold uppercase tracking-wider text-white/40">Einstellungen</h1>
        <ul className="flex gap-1 overflow-x-auto lg:flex-col">
          {sections.map(({ path, label, description, icon: Icon }) => (
            <li key={path}>
              <NavLink
                to={`/settings/${path}`}
                className={({ isActive }) =>
                  `flex items-start gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition ${
                    isActive ? "bg-white/[0.08] text-white" : "text-white/60 hover:bg-white/[0.04] hover:text-white"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon size={17} className={`mt-0.5 flex-none ${isActive ? "text-[var(--color-accent)]" : ""}`} />
                    <span>
                      <span className="block font-medium">{label}</span>
                      <span className="hidden text-xs text-white/40 lg:block">{description}</span>
                    </span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  );
}
