/**
 * Befehlspalette: Strg+K / ⌘K
 * springt ueberall hin -- jede Seite (Kern und Extensions), jeder Host (Konsole,
 * Terminal), jede App mit Adresse. Eine Suche statt Menue-Klickerei.
 *
 * Nur Navigation, keine Aktion mit Nebenwirkung: Starten/Stoppen bleibt auf den Seiten
 * mit Rueckfrage und Gate.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, ExternalLink, Monitor, Search, Server, SquareTerminal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { usePages } from "../lib/catalog";
import { hasConsole, useHosts, useOverview, useTerminalHosts } from "../lib/overview";
import { SECTIONS as SETTINGS_SECTIONS } from "../routes/settings/SettingsLayout";
import { useAuthStore } from "../state/auth";
import { Icon } from "./Icon";

export interface PaletteItem {
  id: string;
  group: "Seiten" | "Server" | "Apps";
  title: string;
  subtitle?: string;
  icon: JSX.Element;
  keywords: string;
  run: () => void;
}

/** `permission` wie im Menue (AppShell.tsx): ohne Recht fehlt die Seite auch hier. */
const CORE_PAGES: { path: string; title: string; icon: string; keywords: string; permission?: string | string[] }[] = [
  { path: "/", title: "Übersicht", icon: "layout-dashboard", keywords: "start dashboard home cockpit" },
  { path: "/files", title: "Dateien", icon: "folder-open", keywords: "dateimanager explorer nextcloud sftp" },
  { path: "/terminal", title: "Terminal", icon: "terminal", keywords: "ssh shell konsole", permission: "hosts.execute" },
  { path: "/notifications", title: "Meldungen", icon: "bell", keywords: "benachrichtigungen warnungen alerts", permission: "notifications.read" },
  { path: "/actions", title: "Aktionen & Freigaben", icon: "shield-check", keywords: "gate vorschlaege freigabe approve" },
];

/** Suchwoerter je Einstellungsbereich. Titel und noetiges Recht kommen aus SettingsLayout
 * (SECTIONS), damit Palette und Einstellungs-Menue dieselben Bereiche zeigen. */
const SETTINGS_KEYWORDS: Record<string, string> = {
  account: "settings profil passwort 2fa totp konto",
  appearance: "settings branding logo farben design anmeldeseite",
  users: "settings nutzer rollen benutzerverwaltung accounts",
  hosts: "settings server hinzufügen zugang zugänge ssh schlüssel rechner hosts raspberry nas verbindung prüfen",
  automation: "settings autonomie freigabe sperrliste wartung",
  extensions: "settings module extensions plugins",
  audit: "settings audit log verlauf",
  system: "settings sicherung datensicherung wiederherstellen zeitzone aufbewahrung",
  about: "settings version changelog änderungsprotokoll neuigkeiten release über nodvard deck lattice",
};

/** Alle Suchbegriffe muessen vorkommen (Reihenfolge egal) -- "prox kno" findet "Proxmox-Knoten". */
export function matches(item: Pick<PaletteItem, "title" | "subtitle" | "keywords">, query: string): boolean {
  const haystack = `${item.title} ${item.subtitle ?? ""} ${item.keywords}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => haystack.includes(token));
}

function usePaletteItems(open: boolean): PaletteItem[] {
  const navigate = useNavigate();
  const { data: pages } = usePages();
  const hasPermission = useAuthStore((s) => s.hasPermission);
  // `user` abonnieren, damit die Liste nach dem Laden der Rechte neu gebaut wird.
  const user = useAuthStore((s) => s.user);
  const canReadHosts = useAuthStore((s) => s.hasPermission("hosts.read"));
  const canExecute = useAuthStore((s) => s.hasPermission("hosts.execute"));
  const { data: hosts } = useHosts(open && canReadHosts);
  const { data: overview } = useOverview(open && canReadHosts);
  const { data: terminalHosts } = useTerminalHosts(open && canExecute);

  return useMemo(() => {
    const items: PaletteItem[] = [];
    const allowed = (permission?: string | string[]) =>
      !permission || (Array.isArray(permission) ? permission : [permission]).some((p) => hasPermission(p));
    const settingsPages = SETTINGS_SECTIONS.map((section) => ({
      path: `/settings/${section.path}`, title: `Einstellungen: ${section.label}`, icon: "settings",
      keywords: SETTINGS_KEYWORDS[section.path] ?? "settings", permission: section.permission,
    }));
    for (const page of [...CORE_PAGES, ...settingsPages]) {
      if (!allowed(page.permission)) continue;
      items.push({
        id: `page:${page.path}`, group: "Seiten", title: page.title, keywords: page.keywords,
        icon: <Icon name={page.icon} />, run: () => navigate(page.path),
      });
    }
    for (const page of pages ?? []) {
      items.push({
        id: `ext:${page.ext_id}:${page.id}`, group: "Seiten", title: page.title, subtitle: page.nav_section ?? undefined,
        keywords: `${page.ext_id} ${page.path}`, icon: <Icon name={page.icon} />,
        run: () => navigate(`/ext/${page.ext_id}${page.path}`),
      });
    }
    const terminalIds = new Set(terminalHosts ?? []);
    for (const host of hosts ?? []) {
      items.push({
        id: `host:${host.id}`, group: "Server", title: host.display_name, subtitle: host.address,
        keywords: `${host.name} ${host.kind ?? ""} server seite einstellungen werkzeuge`, icon: <Server size={16} strokeWidth={1.8} />,
        run: () => navigate(`/hosts/${host.id}`),
      });
      if (hasConsole(host)) {
        items.push({
          id: `console:${host.id}`, group: "Server", title: `${host.display_name}: Konsole`, subtitle: host.address,
          keywords: `${host.name} ${host.kind ?? ""} bildschirm vnc`, icon: <Monitor size={16} strokeWidth={1.8} />,
          run: () => navigate(`/console/${host.id}`),
        });
      }
      if (terminalIds.has(host.id)) {
        items.push({
          id: `terminal:${host.id}`, group: "Server", title: `${host.display_name}: Terminal`, subtitle: host.address,
          keywords: `${host.name} ssh shell`, icon: <SquareTerminal size={16} strokeWidth={1.8} />,
          run: () => navigate(`/terminal?host=${encodeURIComponent(host.id)}`),
        });
      }
    }
    for (const service of overview?.services ?? []) {
      if (!service.url) continue;
      const url = service.url;
      items.push({
        id: `app:${service.id}`, group: "Apps", title: service.name, subtitle: service.host ?? undefined,
        keywords: `${service.image ?? ""} app oeffnen`, icon: <ExternalLink size={16} strokeWidth={1.8} />,
        run: () => window.open(url, "_blank", "noopener,noreferrer"),
      });
    }
    return items;
  }, [pages, hosts, overview, terminalHosts, navigate, hasPermission, user]);
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const items = usePaletteItems(open);
  const listRef = useRef<HTMLDivElement>(null);

  const results = useMemo(() => {
    const filtered = query.trim() ? items.filter((i) => matches(i, query)) : items.filter((i) => i.group === "Seiten");
    return filtered.slice(0, 40);
  }, [items, query]);

  useEffect(() => setActive(0), [query, open]);
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function choose(item: PaletteItem | undefined) {
    if (!item) return;
    onOpenChange(false);
    item.run();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((a) => Math.min(a + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(results[active]);
    }
  }

  let lastGroup: string | null = null;
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className="panel fixed left-1/2 top-[12vh] z-50 w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 overflow-hidden p-0"
          aria-describedby={undefined}
          onKeyDown={onKeyDown}
        >
          <Dialog.Title className="sr-only">Suchen und springen</Dialog.Title>
          <div className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
            <Search size={18} className="opacity-60" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Seite, Server oder App suchen …"
              aria-label="Suchen"
              className="w-full bg-transparent text-sm outline-none placeholder:opacity-50"
            />
            <kbd className="rounded border border-white/15 px-1.5 py-0.5 text-[10px] opacity-60">Esc</kbd>
          </div>
          <div ref={listRef} className="max-h-[55vh] overflow-y-auto p-2" role="listbox" aria-label="Treffer">
            {results.length === 0 && <p className="px-3 py-6 text-center text-sm opacity-60">Nichts gefunden.</p>}
            {results.map((item, index) => {
              const header = item.group !== lastGroup ? item.group : null;
              lastGroup = item.group;
              return (
                <div key={item.id}>
                  {header && <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider opacity-50">{header}</p>}
                  <button
                    type="button"
                    role="option"
                    aria-selected={index === active}
                    data-index={index}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => choose(item)}
                    className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${index === active ? "nav-active" : "hover:bg-white/5"}`}
                  >
                    <span className="opacity-80">{item.icon}</span>
                    <span className="min-w-0 flex-1 break-words">{item.title}</span>
                    {item.subtitle && <span className="break-words text-xs opacity-50">{item.subtitle}</span>}
                    {index === active && <ArrowRight size={14} className="opacity-60" />}
                  </button>
                </div>
              );
            })}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Strg+K / ⌘K oeffnet die Palette von ueberall. */
export function useCommandPaletteHotkey(setOpen: (open: boolean) => void) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);
}
