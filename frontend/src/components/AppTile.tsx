/**
 * Kacheln im Bereich „Apps“ des Cockpits: `AppTile` fuer einen erkannten Dienst (Container, wie bisher) und
 * `CustomAppTile` fuer eine von Hand angelegte App („+ App hinzufuegen“). Beide sehen gleich aus; die eigene hat
 * dazu Symbol/Farbe nach Wahl und -- nur mit Schreibrecht -- ein kleines Menue (Bearbeiten, Verschieben, Loeschen).
 *
 * Links oeffnen mit `rel="noopener noreferrer"`. Eine eigene App zeigt nie einen Zustand: der Server fragt ihre
 * Adresse nicht ab (kein „laeuft/laeuft nicht“), sie ist nur ein Link.
 */
import { ArrowDown, ArrowUp, ExternalLink, MoreVertical, Pencil, Trash2 } from "lucide-react";
import { type KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";

import { appIconComponent } from "../lib/appIcons";
import { safeHref, urlHost } from "../lib/apps";
import { type AppTileOut, hueFor, type ServiceOut } from "../lib/overview";

/** Anfangsbuchstaben fuer eine Kachel ohne Symbol. */
function initials(name: string): string {
  return name.replace(/[^\p{L}\p{N}]/gu, "").slice(0, 2) || "?";
}

const AVATAR = "grid h-10 w-10 flex-none place-items-center rounded-xl text-sm font-bold uppercase text-white shadow-md shadow-black/30";

export function AppTile({ service }: { service: ServiceOut }) {
  const hue = hueFor(service.name);
  const running = service.state === "running";
  const inner = (
    <>
      <span className={AVATAR} style={{ background: `linear-gradient(135deg, hsl(${hue} 70% 52%), hsl(${(hue + 40) % 360} 70% 38%))` }}>
        {service.name.replace(/[^a-z0-9]/gi, "").slice(0, 2) || "?"}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block break-words text-sm font-medium">{service.name}</span>
        <span className="flex items-center gap-1.5 text-[11px] text-white/45">
          <span className={`status-dot ${running ? "online" : "stopped"}`} style={{ width: "0.4rem", height: "0.4rem" }} />
          <span className="min-w-0 break-words">{service.host ?? "–"}</span>
        </span>
      </span>
      {service.url && <ExternalLink size={13} className="flex-none text-white/30 group-hover:text-white/70" />}
    </>
  );
  const className = `panel group flex items-center gap-3 px-3 py-2.5 ${service.url ? "panel-hover" : "opacity-60"}`;
  return service.url ? (
    <a href={service.url} target="_blank" rel="noopener noreferrer" className={className} data-testid={`app-${service.id}`}>
      {inner}
    </a>
  ) : (
    <div className={className} data-testid={`app-${service.id}`} title="Keine Web-Oberfläche erkannt">
      {inner}
    </div>
  );
}

/** Farbe der Kachel: die gewaehlte (nur ein echter `#rrggbb`-Wert kommt in den Stil) oder eine nach dem Namen. */
function avatarBackground(app: Pick<AppTileOut, "name" | "color">): string {
  if (app.color && /^#[0-9a-f]{6}$/i.test(app.color)) {
    return `linear-gradient(135deg, ${app.color}, color-mix(in srgb, ${app.color} 55%, black))`;
  }
  const hue = hueFor(app.name);
  return `linear-gradient(135deg, hsl(${hue} 70% 52%), hsl(${(hue + 40) % 360} 70% 38%))`;
}

export function AppAvatar({ app }: { app: Pick<AppTileOut, "name" | "icon" | "color"> }) {
  const Component = appIconComponent(app.icon);
  return (
    <span className={AVATAR} style={{ background: avatarBackground(app) }} data-testid="app-avatar">
      {Component ? (
        <Component size={18} strokeWidth={1.8} aria-hidden />
      ) : app.icon ? (
        <span className="text-lg normal-case leading-none" aria-hidden>{app.icon}</span>
      ) : (
        initials(app.name)
      )}
    </span>
  );
}

export interface TileMenuActions {
  onEdit: () => void;
  onDelete: () => void;
  onMove: (direction: "up" | "down") => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}

const ITEM = "flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-white/[0.08] focus:bg-white/[0.08] focus:outline-none disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent";

/** Kleines Menue an der Kachel. Schliesst per Escape, Klick daneben und nach jeder Auswahl. `open` liegt bei der
 * Kachel, damit sie waehrend das Menue offen ist ueber ihren Nachbarn liegt (sonst ragen deren Knoepfe hinein). */
function TileMenu({ name, actions, open, setOpen }: { name: string; actions: TileMenuActions; open: boolean; setOpen: (open: boolean) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent | TouchEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
      document.removeEventListener("keydown", key);
    };
  }, [open, setOpen]);

  // Beim Oeffnen landet der Fokus auf dem ersten Eintrag; mit Pfeil hoch/runter geht es durch die Eintraege.
  useEffect(() => {
    if (open) root.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
  }, [open]);

  function moveFocus(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)'));
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "ArrowDown" ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
    items[next]?.focus();
  }

  // Der Fokus geht zuerst auf den Menue-Knopf: der fokussierte Eintrag verschwindet mit dem Menue, und der Fokus
  // landete sonst auf <body> (wer mit der Tastatur arbeitet, muesste von oben neu tabben).
  const choose = (run: () => void) => () => {
    button.current?.focus();
    setOpen(false);
    run();
  };

  return (
    <div ref={root} className="relative flex-none">
      <button
        ref={button}
        type="button"
        aria-label={`Menü für ${name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="grid h-8 w-8 place-items-center rounded-lg text-white/55 hover:bg-white/10 hover:text-white"
      >
        <MoreVertical size={16} aria-hidden />
      </button>
      {open && (
        <div
          role="menu"
          aria-label={`Aktionen für ${name}`}
          onKeyDown={moveFocus}
          className="absolute right-0 top-full z-30 mt-1 w-48 overflow-hidden rounded-lg border border-white/10 bg-[var(--color-surface)] py-1 shadow-xl shadow-black/50"
        >
          <button type="button" role="menuitem" className={ITEM} onClick={choose(actions.onEdit)}>
            <Pencil size={14} aria-hidden /> Bearbeiten
          </button>
          <button type="button" role="menuitem" className={ITEM} disabled={!actions.canMoveUp} onClick={choose(() => actions.onMove("up"))}>
            <ArrowUp size={14} aria-hidden /> Nach vorn
          </button>
          <button type="button" role="menuitem" className={ITEM} disabled={!actions.canMoveDown} onClick={choose(() => actions.onMove("down"))}>
            <ArrowDown size={14} aria-hidden /> Nach hinten
          </button>
          <button type="button" role="menuitem" className={`${ITEM} text-red-300`} onClick={choose(actions.onDelete)}>
            <Trash2 size={14} aria-hidden /> Löschen
          </button>
        </div>
      )}
    </div>
  );
}

/** Eine eigene App. `actions` nur mit Schreibrecht (`apps.write`) -- ohne gibt es kein Menue. */
export function CustomAppTile({ app, actions }: { app: AppTileOut; actions?: TileMenuActions }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const href = safeHref(app.url);
  const subtitle = app.host ?? urlHost(href);
  const inner = (
    <>
      <AppAvatar app={app} />
      <span className="min-w-0 flex-1">
        <span className="block break-words text-sm font-medium">{app.name}</span>
        {subtitle && <span className="block break-words text-[11px] text-white/45">{subtitle}</span>}
      </span>
      {href && <ExternalLink size={13} className="flex-none text-white/30 group-hover:text-white/70" aria-hidden />}
    </>
  );
  return (
    <div
      className={`panel group flex items-center gap-1 pr-1.5 ${href ? "panel-hover" : "opacity-60"} ${menuOpen ? "relative z-30" : ""}`}
      data-testid={`app-custom-${app.id}`}
    >
      {href ? (
        <a
          href={href}
          target={app.open_in_new_tab ? "_blank" : undefined}
          rel="noopener noreferrer"
          className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-3 pr-1"
        >
          {inner}
        </a>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-3 pr-1" title="Diese Adresse ist nicht gültig – bitte bearbeite die App.">
          {inner}
        </div>
      )}
      {actions && <TileMenu name={app.name} actions={actions} open={menuOpen} setOpen={setMenuOpen} />}
    </div>
  );
}
