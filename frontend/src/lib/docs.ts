/**
 * Adressen der Anleitung im öffentlichen Quellcode. Alle Links der Oberfläche zur Doku laufen hierüber, damit sich
 * die Adresse an einer Stelle ändern lässt. Verlinkt wird nur die deutsche Doku (docs/11-ERST-EINRICHTUNG.md).
 */

/** Der öffentliche Quellcode. */
export const REPO_URL = "https://github.com/nodvard/deck";

const BLOB_BASE = `${REPO_URL}/blob/main/`;

/** Adresse einer Datei im Quellcode, optional mit Anker auf eine Überschrift (`repoFile("deploy/README.md", "update-helfer")`). */
export function repoFile(path: string, anchor?: string): string {
  return `${BLOB_BASE}${path}${anchor ? `#${anchor}` : ""}`;
}

/** Die Anleitung „Erst-Einrichtung – Schritt für Schritt“ (Deutsch). */
export const GUIDE_PATH = "docs/11-ERST-EINRICHTUNG.md";

/**
 * Anker der Überschriften der Anleitung, so wie GitHub sie aus der Überschrift macht: klein geschrieben, Satzzeichen
 * weg, Leerzeichen zu „-“, Umlaute bleiben. `docsGuide.test.ts` prüft jeden Anker gegen die Datei; wird eine
 * Überschrift dort umbenannt, schlägt er fehl und sagt, welcher Link ins Leere ginge.
 */
export const GUIDE_SECTIONS = {
  /** 1. Container starten */
  start: "1-container-starten",
  /** 2. Einrichtungsassistent (/setup) */
  assistant: "2-einrichtungsassistent-setup",
  /** 4. Erweiterungen einschalten */
  modules: "4-erweiterungen-einschalten",
  /** 5. Server und SSH-Zugang */
  servers: "5-server-und-ssh-zugang",
  /** 6. Root-Rechte ohne Passwort und Gruppe docker */
  rootRights: "6-root-rechte-ohne-passwort-und-gruppe-docker",
  /** 7. Proxmox verbinden */
  proxmox: "7-proxmox-verbinden",
  /** 8. Push-Nachrichten (ntfy) */
  ntfy: "8-push-nachrichten-ntfy",
  /** 9. Automatik und Wartungsfenster */
  automation: "9-automatik-und-wartungsfenster",
  /** 10.1 In der Oberfläche (empfohlen) */
  backup: "101-in-der-oberfläche-empfohlen",
  /** 10.2 Wiederherstellen (Sicherung einspielen) */
  restore: "102-wiederherstellen-sicherung-einspielen",
  /** 10.5 Neue Version finden und einspielen */
  updates: "105-neue-version-finden-und-einspielen",
  /** 11. Wenn etwas hakt */
  troubleshooting: "11-wenn-etwas-hakt",
  /** 12. Ausgesperrt? Notfall-Befehle */
  locked: "12-ausgesperrt-notfall-befehle",
} as const;

export type GuideSection = keyof typeof GUIDE_SECTIONS;

/** Adresse der Anleitung, ohne Angabe ganz oben, sonst direkt am passenden Abschnitt. */
export function guideUrl(section?: GuideSection): string {
  return repoFile(GUIDE_PATH, section ? GUIDE_SECTIONS[section] : undefined);
}

/** Zu welchem Abschnitt der Anleitung die Einstellungen einer Erweiterung gehören (Kennung des Moduls). Module ohne
 * eigenen Abschnitt fehlen hier. */
export const EXTENSION_GUIDE: Record<string, GuideSection> = {
  proxmox: "proxmox",
  ntfy: "ntfy",
};
