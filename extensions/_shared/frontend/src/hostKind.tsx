/**
 * Art eines Servers für Gruppen und Plaketten, gemeinsam für Kern-Seiten und Erweiterungen: physischer Server (auch ein
 * Hypervisor-Knoten), VM oder LXC, bei Gästen mit dem Host, auf dem sie laufen („LXC auf pve1“). Die Daten kommen aus
 * `HostOut` (`category`, `parent_label`, `power_state`); ältere Server ohne `category` werden über `kind` eingeordnet.
 * Dieselben Wörter nutzt das Widget „Server-Zustand“ (`kind_label` in der Erweiterung `system`).
 */
import type { ReactNode } from "react";

export type HostCategory = "server" | "vm" | "lxc";

export interface KindSource {
  kind?: string | null;
  category?: string | null;
  parent_label?: string | null;
  power_state?: string | null;
  /** Der Anbieter kennt den Server nicht mehr (etwa in Proxmox gelöscht). */
  gone_since?: string | null;
}

export const CATEGORY_ORDER: readonly HostCategory[] = ["server", "vm", "lxc"];

export const CATEGORY_TITLE: Record<HostCategory, string> = {
  server: "Physische Server",
  vm: "Virtuelle Maschinen",
  lxc: "LXC",
};

/** Kurzform für Filter-Chips. */
export const CATEGORY_SHORT: Record<HostCategory, string> = { server: "Physisch", vm: "VMs", lxc: "LXC" };

const WORD: Record<HostCategory, string> = { server: "Server", vm: "VM", lxc: "LXC" };

export function hostCategory(host: KindSource): HostCategory {
  const value = host.category ?? host.kind;
  return value === "vm" || value === "lxc" ? value : "server";
}

/** „Knoten“, „Server“, „VM“ oder „LXC“, bei Gästen mit dem Host dahinter: „LXC auf pve1“. */
export function kindLabel(host: KindSource): string {
  const category = hostCategory(host);
  if (category === "server") return host.kind === "hypervisor" || host.kind === "node" ? "Knoten" : WORD.server;
  return host.parent_label ? `${WORD[category]} auf ${host.parent_label}` : WORD[category];
}

/** Beim Anbieter nicht mehr vorhanden: grau, keine Störung; die Einstellungen bieten „Entfernen“ an. */
export function isGone(host: KindSource): boolean {
  return Boolean(host.gone_since);
}

/** Ausgeschaltet laut Anbieter (gestoppt oder pausiert): grau, „aus“, keine Störung. */
export function isSwitchedOff(host: KindSource): boolean {
  return host.power_state === "stopped" || host.power_state === "paused";
}

export interface HostGroup<T> {
  category: HostCategory;
  title: string;
  hosts: T[];
}

/** Gruppen in fester Reihenfolge (physisch, VMs, LXC); leere fallen weg, die Reihenfolge innerhalb bleibt. */
export function groupHosts<T extends KindSource>(hosts: readonly T[]): HostGroup<T>[] {
  return CATEGORY_ORDER
    .map((category) => ({ category, title: CATEGORY_TITLE[category], hosts: hosts.filter((h) => hostCategory(h) === category) }))
    .filter((group) => group.hosts.length > 0);
}

/** „4 VMs · 5 LXC“ (leer ohne Gäste). */
export function guestSummary(hosts: readonly KindSource[]): string {
  const vms = hosts.filter((h) => hostCategory(h) === "vm").length;
  const lxc = hosts.filter((h) => hostCategory(h) === "lxc").length;
  return [vms ? `${vms} ${vms === 1 ? "VM" : "VMs"}` : "", lxc ? `${lxc} LXC` : ""].filter(Boolean).join(" · ");
}

// Symbole nach lucide (ISC-Lizenz), hier eingebettet, damit die Erweiterungs-Bundles keine Symbol-Bibliothek brauchen.
const ICON_PATHS: Record<HostCategory, ReactNode> = {
  server: (
    <>
      <rect x="2" y="2" width="20" height="8" rx="2" />
      <rect x="2" y="14" width="20" height="8" rx="2" />
      <path d="M6 6h.01M6 18h.01" />
    </>
  ),
  vm: (
    <>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </>
  ),
  lxc: (
    <>
      <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
      <path d="m3.3 7 8.7 5 8.7-5M12 22V12" />
    </>
  ),
};

export function KindIcon({ category, size = 12 }: { category: HostCategory; size?: number }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="flex-none"
    >
      {ICON_PATHS[category]}
    </svg>
  );
}

/** Plakette mit Symbol: „Server“, „Knoten“, „VM auf pve1“, „LXC auf pve1“. */
export function KindBadge({ host, className = "" }: { host: KindSource; className?: string }) {
  const label = kindLabel(host);
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded bg-white/[0.07] px-1.5 py-0.5 text-[11px] text-white/60 ${className}`}
      data-testid="kind-badge"
    >
      <KindIcon category={hostCategory(host)} />
      <span className="truncate">{label}</span>
    </span>
  );
}

/** Überschrift einer Gruppe mit Anzahl. */
export function GroupHeading({ group, className = "" }: { group: HostGroup<unknown>; className?: string }) {
  return (
    <h3 className={`flex items-center gap-1.5 text-xs font-medium text-white/55 ${className}`} data-testid={`group-${group.category}`}>
      <KindIcon category={group.category} />
      {group.title}
      <span className="text-white/35">{group.hosts.length}</span>
    </h3>
  );
}
