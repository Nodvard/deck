import { useQueryClient } from "@tanstack/react-query";
import { LogOut, Menu, Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { CommandPalette, useCommandPaletteHotkey } from "../components/CommandPalette";
import { DemoBanner } from "../components/DemoBanner";
import { GlobalDialogs } from "../components/GlobalDialogs";
import { Icon } from "../components/Icon";
import { NotificationBell } from "../components/NotificationBell";
import { useUnreadNotifications } from "./NotificationsPage";
import { api } from "../lib/api";
import { useCapabilities, usePages } from "../lib/catalog";
import { useChangelogNotice } from "../lib/changelog";
import { setDeckTimezone } from "../lib/deckTimezone";
import { useProactiveTokenRefresh } from "../lib/tokenRefresh";
import { useWsSubscription, wsClient } from "../lib/ws";
import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";
import type { PageOut } from "../widgets/types";

/** Gruppiert PageOut[] nach nav_section (docs/02-EXTENSION-API.md §5), sortiert nach nav_order.
 * Abschnitte mit nur EINER Seite wandern gesammelt unter "Werkzeuge" -- sonst stuende
 * z. B. "DOKUMENTE" als Ueberschrift ueber genau einem Eintrag "Dokumente". */
export function groupPagesBySection(pages: PageOut[]) {
  const sorted = [...pages].filter((p) => p.show_in_nav !== false).sort((a, b) => a.nav_order - b.nav_order);
  const bySection = new Map<string, PageOut[]>();
  for (const page of sorted) {
    const key = page.nav_section ?? "";
    const list = bySection.get(key) ?? [];
    list.push(page);
    bySection.set(key, list);
  }
  const sections = new Map<string, PageOut[]>();
  const singles: PageOut[] = [];
  for (const [section, list] of bySection) {
    if (section && list.length === 1) singles.push(list[0]);
    else sections.set(section, list);
  }
  if (singles.length === 1) sections.set(singles[0].nav_section ?? "", singles);
  else if (singles.length > 1) sections.set("Werkzeuge", singles);
  return sections;
}

function navClass({ isActive }: { isActive: boolean }) {
  return `group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
    isActive ? "nav-active font-medium text-white" : "text-white/70 hover:bg-white/5 hover:text-white"
  }`;
}

function NavItem({ to, icon, label, end, badge }: { to: string; icon: string; label: string; end?: boolean; badge?: number }) {
  return (
    <NavLink to={to} end={end} className={navClass}>
      <Icon name={icon} className="opacity-80 group-hover:opacity-100" />
      <span className="flex-1 truncate">{label}</span>
      {badge !== undefined && badge > 0 && (
        <span className="accent-gradient rounded-full px-1.5 text-[10px] font-semibold text-white" aria-label={`${badge} ungelesen`}>
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </NavLink>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <span className="hidden text-xs tabular-nums text-white/60 md:inline">
      {now.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" })} ·{" "}
      {now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

export function AppShell() {
  // main.tsx laedt das schon beim Boot (vor dem Login) -- hier nur noch lesen, kein
  // zweiter Fetch (siehe state/branding.ts).
  const branding = useBrandingStore((s) => s.branding);
  const { data: pages } = usePages();
  const { data: capabilities } = useCapabilities();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  // Terminal-Seite: eine Shell verlangt dieselbe Berechtigung wie die
  // Terminal-API (`hosts.execute`) -- ohne sie fuehrt der Menuepunkt ins Leere.
  const canUseTerminal = useAuthStore((s) => s.hasPermission("hosts.execute"));
  const canReadNotifications = useAuthStore((s) => s.hasPermission("notifications.read"));
  const unreadNotifications = useUnreadNotifications(canReadNotifications);
  // Kleine Versionsnummer unten in der Seitenleiste (und damit im Handy-Menü); ein Klick
  // öffnet das Änderungsprotokoll, "Neu" erscheint, solange man den Stand noch nicht kennt.
  const { version: appVersion, isNew: changelogIsNew } = useChangelogNotice();
  const queryClient = useQueryClient();
  const [paletteOpen, setPaletteOpen] = useState(false);
  useCommandPaletteHotkey(setPaletteOpen);

  // Handy/Tablet: die Seitenleiste ist ein ausklappbares Menue (unter 1024 px).
  // Bei jeder Navigation (auch Tippen auf den Eintrag der schon offenen Seite: gleiche
  // Adresse, aber neuer `location.key`) und mit Esc klappt es wieder zu.
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.key]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  // services/extensions.py publiziert "extension.enabled"/"extension.disabled" ueber
  // den bestehenden "events"-Kanal -- Navigation UND Dashboard-Widget-Katalog
  // muessen live nachziehen, ohne dass ein Nutzer neu laden muss (Widgets
  // erscheinen/verschwinden beim Aktivieren/Deaktivieren einer Extension).
  useWsSubscription("events", (payload) => {
    const name = (payload as { name?: string }).name;
    if (typeof name === "string" && name.startsWith("extension.")) {
      void queryClient.invalidateQueries({ queryKey: ["pages"] });
      void queryClient.invalidateQueries({ queryKey: ["widgets"] });
    }
    // Die Erreichbarkeitspruefung (`host.status_changed`) meldet, wenn ein Server ausfaellt oder wieder da ist:
    // Server-Liste, Server-Seite und Cockpit ziehen sofort nach, ohne auf den 30-Sekunden-Abruf zu warten.
    if (name === "host.status_changed") {
      void queryClient.invalidateQueries({ queryKey: ["hosts"] });
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
    }
  });

  // Erweiterungen, Upload/Download und Export erneuern bei 401 nicht selbst
  // -- das Token wird im sichtbaren Tab kurz vor Ablauf erneuert (lib/tokenRefresh.ts).
  useProactiveTokenRefresh();

  // Zeitzone des Dashboards fuer die Zeitplan-Waehler (lib/deckTimezone.ts). GET /me darf
  // jeder Angemeldete, auch ohne `settings.write`; scheitert es, rechnen die Waehler in der
  // Zeit des Geraets wie bisher.
  useEffect(() => {
    api.get<{ timezone?: string }>("/me")
      .then((me) => setDeckTimezone(me.timezone || null))
      .catch(() => undefined);
  }, []);

  // Eine WS-Verbindung fuer die gesamte Shell-Lebensdauer (Multiplex-Hub) --
  // einzelne Widgets abonnieren nur noch Kanaele, siehe lib/ws.ts.
  useEffect(() => {
    wsClient.ensureConnected();
    return () => wsClient.disconnect();
  }, []);

  const sections = groupPagesBySection(pages ?? []);
  const productName = branding?.product_name ?? "Nodvard Deck";
  // Ein leerer Anzeigename ("") liesse Kreis und Namen leer -- deshalb || statt ??.
  const userName = user?.display_name || user?.username || "";
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

  return (
    <div className="flex h-screen bg-[var(--color-background)] text-[var(--color-text)]">
      {menuOpen && (
        <div className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden" onClick={() => setMenuOpen(false)} aria-hidden="true" data-testid="menu-backdrop" />
      )}
      <aside
        id="hauptmenue"
        className={`fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-none flex-col border-r border-white/[0.06] bg-[var(--color-background)] shadow-2xl shadow-black/60 transition-transform duration-200 lg:static lg:z-auto lg:w-60 lg:translate-x-0 lg:bg-black/20 lg:shadow-none ${
          menuOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <button
          type="button"
          onClick={() => setMenuOpen(false)}
          className="absolute right-3 top-5 rounded-md p-1.5 text-white/60 hover:bg-white/10 hover:text-white lg:hidden"
          aria-label="Menü schließen"
        >
          <X size={18} />
        </button>
        <Link to="/" className="flex items-center gap-3 pb-4 pl-4 pr-12 pt-5 lg:pr-4">
          {branding?.logo_url ? (
            <img src={branding.logo_url} alt="" className="h-9 w-9 rounded-xl object-contain" />
          ) : (
            <span className="accent-gradient grid h-9 w-9 place-items-center rounded-xl text-base font-bold text-white shadow-lg shadow-black/40">
              {productName.slice(0, 1).toUpperCase()}
            </span>
          )}
          <span className="min-w-0">
            <span className="block truncate text-[15px] font-semibold leading-tight">{productName}</span>
            <span className="block text-[11px] leading-tight text-white/50">Homelab-Cockpit</span>
          </span>
        </Link>
        {capabilities?.feature_flags.demo_mode && (
          <span className="mx-4 mb-2 inline-block w-fit rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-300">
            Demo-Modus
          </span>
        )}
        <nav className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 pb-4" aria-label="Hauptnavigation">
          <div className="flex flex-col gap-0.5">
            <NavItem to="/" end icon="layout-dashboard" label="Übersicht" />
            <NavItem to="/files" icon="folder-open" label="Dateien" />
            {canUseTerminal && <NavItem to="/terminal" icon="terminal" label="Terminal" />}
            {canReadNotifications && <NavItem to="/notifications" icon="bell" label="Meldungen" badge={unreadNotifications} />}
            <NavItem to="/actions" icon="shield-check" label="Aktionen" />
          </div>
          {[...sections.entries()].map(([section, sectionPages]) => (
            <div key={section || "_"}>
              {section && <p className="mb-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-white/40">{section}</p>}
              <div className="flex flex-col gap-0.5">
                {sectionPages.map((page) => (
                  <NavItem key={`${page.ext_id}:${page.id}`} to={`/ext/${page.ext_id}${page.path}`} icon={page.icon ?? "box"} label={page.title} />
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className="border-t border-white/[0.06] p-3">
          <NavItem to="/settings" icon="settings" label="Einstellungen" />
          <div className="mt-2 flex items-center gap-2.5 rounded-lg px-2.5 py-2">
            <span className="grid h-8 w-8 flex-none place-items-center rounded-full bg-white/10 text-xs font-semibold uppercase">
              {userName.slice(0, 2).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1 truncate text-sm">{userName}</span>
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded-md p-1.5 text-white/50 hover:bg-white/10 hover:text-white"
              aria-label="Abmelden"
              title="Abmelden"
            >
              <LogOut size={15} />
            </button>
          </div>
          {appVersion && (
            <Link
              to="/settings/about"
              title="Änderungsprotokoll öffnen"
              className="mt-1 flex w-fit items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] text-white/35 hover:bg-white/5 hover:text-white/70"
            >
              v{appVersion}
              {changelogIsNew && (
                <>
                  {" "}
                  <span data-testid="changelog-neu" className="accent-gradient rounded-full px-1.5 text-[10px] font-semibold text-white">
                    Neu
                  </span>
                </>
              )}
            </Link>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 flex-none items-center gap-2 border-b border-white/[0.06] px-3 sm:gap-3 sm:px-6">
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            className="rounded-lg p-2 text-white/70 hover:bg-white/5 hover:text-white lg:hidden"
            aria-label="Menü öffnen"
            aria-controls="hauptmenue"
            aria-expanded={menuOpen}
          >
            <Menu size={20} />
          </button>
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-white/50 hover:border-white/20 hover:text-white/80 sm:max-w-md"
          >
            <Search size={15} className="flex-none" />
            <span className="flex-1 truncate text-left">Suchen oder springen …</span>
            <kbd className="hidden rounded border border-white/15 px-1.5 text-[10px] sm:inline">{isMac ? "⌘" : "Strg"} K</kbd>
          </button>
          <div className="ml-auto flex flex-none items-center gap-2 sm:gap-4">
            <Clock />
            {canReadNotifications && (
              <NotificationBell unread={unreadNotifications} />
            )}
          </div>
        </header>
        <DemoBanner />
        <main className="min-h-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <GlobalDialogs />
    </div>
  );
}
