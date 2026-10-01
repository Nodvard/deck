/**
 * Die feste Symbol-Auswahl fuer eigene App-Kacheln ("+ App hinzufuegen"): lucide-Namen, die das Frontend
 * ohnehin bundelt -- keine frei waehlbaren Bild-Adressen (die haetten den Browser jedes Nutzers beim Anzeigen
 * der Startseite zu einem fremden Server gefuehrt, siehe backend/src/nodvard_deck/services/custom_apps.py).
 *
 * `APP_ICON_NAMES` muss mit `APP_ICONS` im Backend uebereinstimmen (dort prueft die API die Eingabe); ein Test
 * (backend/tests/test_custom_apps_icons.py) haelt beide Listen deckungsgleich. Neue Namen deshalb an BEIDEN
 * Stellen eintragen -- und hier unten Bild und deutsche Bezeichnung.
 */
import {
  Activity,
  Bell,
  Bot,
  Box,
  BookOpen,
  Calendar,
  Camera,
  Cctv,
  ChartBar,
  Cloud,
  Code,
  Container,
  Cpu,
  Database,
  FileText,
  Film,
  Folder,
  Gamepad2,
  GitBranch,
  Globe,
  HardDrive,
  House,
  Image as ImageIcon,
  KeyRound,
  Laptop,
  Lightbulb,
  Link as LinkIcon,
  Lock,
  Mail,
  type LucideIcon,
  Monitor,
  Music,
  Network,
  Package,
  Plug,
  Printer,
  Router,
  Rss,
  Server,
  Settings,
  ShieldCheck,
  Smartphone,
  Star,
  Terminal,
  Thermometer,
  Tv,
  Users,
  Video,
  Wifi,
  Wrench,
  Zap,
} from "lucide-react";

export const APP_ICON_NAMES = [
  "globe", "link", "router", "network", "wifi", "shield-check", "lock", "key-round", "server", "hard-drive",
  "database", "cloud", "cpu", "monitor", "laptop", "smartphone", "tv", "printer", "camera", "cctv",
  "video", "film", "music", "gamepad-2", "book-open", "file-text", "folder", "image", "mail", "calendar",
  "activity", "chart-bar", "thermometer", "lightbulb", "house", "zap", "git-branch", "terminal", "code", "box",
  "container", "package", "wrench", "settings", "bell", "rss", "users", "plug", "bot", "star",
] as const;

export type AppIconName = (typeof APP_ICON_NAMES)[number];

const APP_ICONS: Record<AppIconName, LucideIcon> = {
  "globe": Globe,
  "link": LinkIcon,
  "router": Router,
  "network": Network,
  "wifi": Wifi,
  "shield-check": ShieldCheck,
  "lock": Lock,
  "key-round": KeyRound,
  "server": Server,
  "hard-drive": HardDrive,
  "database": Database,
  "cloud": Cloud,
  "cpu": Cpu,
  "monitor": Monitor,
  "laptop": Laptop,
  "smartphone": Smartphone,
  "tv": Tv,
  "printer": Printer,
  "camera": Camera,
  "cctv": Cctv,
  "video": Video,
  "film": Film,
  "music": Music,
  "gamepad-2": Gamepad2,
  "book-open": BookOpen,
  "file-text": FileText,
  "folder": Folder,
  "image": ImageIcon,
  "mail": Mail,
  "calendar": Calendar,
  "activity": Activity,
  "chart-bar": ChartBar,
  "thermometer": Thermometer,
  "lightbulb": Lightbulb,
  "house": House,
  "zap": Zap,
  "git-branch": GitBranch,
  "terminal": Terminal,
  "code": Code,
  "box": Box,
  "container": Container,
  "package": Package,
  "wrench": Wrench,
  "settings": Settings,
  "bell": Bell,
  "rss": Rss,
  "users": Users,
  "plug": Plug,
  "bot": Bot,
  "star": Star,
};

/** Deutsche Bezeichnung (Hinweis am Symbol und fuer Screenreader). */
export const APP_ICON_LABELS: Record<AppIconName, string> = {
  "globe": "Globus", "link": "Link", "router": "Router", "network": "Netzwerk", "wifi": "WLAN",
  "shield-check": "Schutzschild", "lock": "Schloss", "key-round": "Schlüssel", "server": "Server",
  "hard-drive": "Festplatte", "database": "Datenbank", "cloud": "Cloud", "cpu": "Prozessor",
  "monitor": "Bildschirm", "laptop": "Laptop", "smartphone": "Handy", "tv": "Fernseher", "printer": "Drucker",
  "camera": "Kamera", "cctv": "Überwachungskamera", "video": "Video", "film": "Film", "music": "Musik",
  "gamepad-2": "Spiele", "book-open": "Buch", "file-text": "Dokument", "folder": "Ordner", "image": "Bild",
  "mail": "E-Mail", "calendar": "Kalender", "activity": "Aktivität", "chart-bar": "Diagramm",
  "thermometer": "Thermometer", "lightbulb": "Glühbirne", "house": "Haus", "zap": "Blitz",
  "git-branch": "Verzweigung", "terminal": "Terminal", "code": "Code", "box": "Kiste", "container": "Container",
  "package": "Paket", "wrench": "Werkzeug", "settings": "Einstellungen", "bell": "Glocke", "rss": "Feed",
  "users": "Benutzer", "plug": "Stecker", "bot": "Roboter", "star": "Stern",
};

export function isAppIconName(value: string | null | undefined): value is AppIconName {
  return !!value && (APP_ICON_NAMES as readonly string[]).includes(value);
}

/** Das Bild zu einem Namen aus der Auswahl; `null` fuer alles andere (Emoji, unbekannt, leer). */
export function appIconComponent(name: string | null | undefined): LucideIcon | null {
  return isAppIconName(name) ? APP_ICONS[name] : null;
}
