/**
 * Icons nach Namen. `PageSpec.icon`/`WidgetSpec.icon` der
 * Extensions sind seit jeher lucide-Namen in kebab-case ("server", "hard-drive") --
 * hier auf die Komponenten abgebildet. Bewusst eine feste Auswahl statt aller ~1500
 * Icons: jedes importierte Icon landet im Bundle. Unbekannte Namen -> neutrales Icon.
 */
import {
  Activity,
  Bell,
  Boxes,
  Box,
  Cpu,
  DatabaseBackup,
  FileText,
  FolderOpen,
  Gamepad2,
  HardDrive,
  LayoutDashboard,
  LayoutGrid,
  type LucideIcon,
  Monitor,
  Package,
  Server,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Siren,
  Sparkles,
  SquareTerminal,
  Tag,
  Users,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  activity: Activity,
  bell: Bell,
  boxes: Boxes,
  cpu: Cpu,
  "database-backup": DatabaseBackup,
  "file-text": FileText,
  folder: FolderOpen,
  "folder-open": FolderOpen,
  "gamepad-2": Gamepad2,
  "hard-drive": HardDrive,
  "layout-dashboard": LayoutDashboard,
  "layout-grid": LayoutGrid,
  monitor: Monitor,
  package: Package,
  server: Server,
  settings: Settings,
  "shield-alert": ShieldAlert,
  "shield-check": ShieldCheck,
  siren: Siren,
  sparkles: Sparkles,
  terminal: SquareTerminal,
  "terminal-square": SquareTerminal,
  "square-terminal": SquareTerminal,
  tag: Tag,
  users: Users,
};

export function iconFor(name: string | null | undefined): LucideIcon {
  return (name && ICONS[name]) || Box;
}

export function Icon({ name, className, size = 16 }: { name: string | null | undefined; className?: string; size?: number }) {
  const Component = iconFor(name);
  return <Component className={className} size={size} strokeWidth={1.8} aria-hidden />;
}
