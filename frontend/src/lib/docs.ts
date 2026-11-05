import { useEditionStore } from "../state/edition";

/**
 * Adressen der Anleitung im öffentlichen Quellcode. Alle Links der Oberfläche zur Doku laufen hierüber, damit sich
 * die Adresse an einer Stelle ändern lässt. Verlinkt wird nur die deutsche Doku (docs/11-ERST-EINRICHTUNG.md).
 */

/** Der öffentliche Quellcode. */
export const REPO_URL = "https://github.com/nodvard/deck";

const BLOB_BASE = `${REPO_URL}/blob/main/`;

/**
 * Adresse einer Datei im Quellcode, optional mit Anker auf eine Überschrift
 * (`repoFile("deploy/README.md", "update-helfer")`).
 */
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
  secureConnection: "verbindung-absichern",
  /** 1. Schnellstart */
  start: "1-schnellstart",
  /** 2. Einrichtungsassistent (/setup) */
  assistant: "2-einrichtungsassistent-setup",
  /** 4. Erweiterungen einschalten */
  modules: "4-erweiterungen-einschalten",
  /** 3. Den ersten Server verbinden */
  servers: "3-den-ersten-server-verbinden",
  /** F. Root-Rechte ohne Passwort und Gruppe docker (Anhang) */
  rootRights: "f-root-rechte-ohne-passwort-und-gruppe-docker",
  /** 4.2 Proxmox verbinden */
  proxmox: "42-proxmox-verbinden",
  /** 5. Push-Nachrichten (ntfy) */
  ntfy: "5-push-nachrichten-ntfy",
  /** H. Automatik und Wartungsfenster (Anhang) */
  automation: "h-automatik-und-wartungsfenster",
  /** 6.1 In der Oberfläche (empfohlen) */
  backup: "61-in-der-oberfläche-empfohlen",
  /** 6.2 Wiederherstellen (Sicherung einspielen) */
  restore: "62-wiederherstellen-sicherung-einspielen",
  /** I.1 Neue Version finden und einspielen (Anhang) */
  updates: "i1-neue-version-finden-und-einspielen",
  /** 7. Typische Stolpersteine */
  troubleshooting: "7-typische-stolpersteine",
  /** 7.3 Ausgesperrt? Notfall-Befehle */
  locked: "73-ausgesperrt-notfall-befehle",
} as const;

export type GuideSection = keyof typeof GUIDE_SECTIONS;

/** Adresse der Anleitung, ohne Angabe ganz oben, sonst direkt am passenden Abschnitt. */
export function guideUrl(section?: GuideSection): string {
  if (!useEditionStore.getState().edition.choose_modules) {
    const anchors: Partial<Record<GuideSection, string>> = {
      servers: "3-server-verbinden-auch-den-eigenen", assistant: "2-einrichten",
      backup: "sichern-und-entfernen", restore: "sichern-und-entfernen", updates: "1-starten",
      rootRights: "sicherheit-und-datenschutz-auf-einen-blick",
    };
    return repoFile("deploy/nodvard-dock/README.md", section ? anchors[section] : undefined);
  }
  return repoFile(GUIDE_PATH, section ? GUIDE_SECTIONS[section] : undefined);
}

/** Zu welchem Abschnitt der Anleitung die Einstellungen einer Erweiterung gehören (Kennung des Moduls). Module ohne
 * eigenen Abschnitt fehlen hier. */
export const EXTENSION_GUIDE: Record<string, GuideSection> = {
  proxmox: "proxmox",
  ntfy: "ntfy",
};
