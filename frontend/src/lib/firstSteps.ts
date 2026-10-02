/**
 * „Erste Schritte“ im Cockpit: die Checkliste, die einen Neuling von der leeren Installation bis
 * zum laufenden Dashboard fuehrt. Alles hier ist abgeleitet aus Daten, die es ohnehin gibt
 * (Server, Erweiterungen, Dashboard-Layout) -- es gibt keine eigene Buchfuehrung, die vom Zustand
 * abweichen koennte. Wo es keine verlaessliche Quelle gibt (Sicherung), steht nur ein Hinweis.
 *
 * Ein Schritt erscheint nur, wenn der Nutzer das Recht hat, ihn zu erledigen, und die Daten dazu
 * geladen werden konnten (kein Raten bei einem Fehler).
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { isSshCredential } from "./hosts";
import { type HostOut } from "./overview";

/** Die Erweiterung fuer Push-Nachrichten. Der Kern weiss sonst nichts ueber einzelne Erweiterungen;
 * hier gibt es (noch) keine allgemeine Kennzeichnung ueber die API. */
export const PUSH_EXTENSION_ID = "ntfy";

export interface ExtensionInfo {
  id: string;
  name: string | null;
  state: string;
  has_settings?: boolean;
  needs_setup?: boolean;
  setup_reasons?: string[];
}

export interface Preferences {
  first_steps_dismissed: boolean;
}

export function useExtensions(enabled = true) {
  return useQuery({ queryKey: ["extensions"], queryFn: () => api.get<ExtensionInfo[]>("/extensions"), enabled });
}

export function usePreferences() {
  return useQuery({ queryKey: ["me", "preferences"], queryFn: () => api.get<Preferences>("/me/preferences"), retry: false });
}

export interface FirstStep {
  id: string;
  title: string;
  text: string;
  done: boolean;
  /** Ziel im Dashboard; fehlt es, gibt es nur den Knopf mit `scrollTo`. */
  to?: string;
  /** Beschriftung des Knopfes, solange der Schritt offen ist. */
  action: string;
  /** Statt eines Links: zu diesem Element auf der Startseite springen. */
  scrollTo?: string;
}

export interface FirstStepsInput {
  can: (permission: string) => boolean;
  /** `undefined` = nicht geladen oder nicht abrufbar: die Schritte dazu entfallen. */
  hosts: HostOut[] | undefined;
  extensions: ExtensionInfo[] | undefined;
  /** Zahl der Widgets, die auf dem Dashboard stehen (nicht ausgeblendet, im Katalog bekannt). */
  placedWidgets: number | undefined;
  /** Zahl der Widgets, die es ueberhaupt gibt (Katalog). */
  catalogWidgets: number | undefined;
}

const HOST_TARGET = "/settings/hosts";
/** Die Serverliste mit schon geöffnetem Formular „Neuen Server anlegen“. */
export const NEW_HOST_TARGET = "/settings/hosts?neu=1";

function name(ext: ExtensionInfo): string {
  return ext.name ?? ext.id;
}

/** Ein Server, den der Nutzer von Hand verwaltet, vor einem automatisch eingelesenen. */
function preferManual(list: HostOut[]): HostOut | undefined {
  return list.find((h) => !h.provider_ext_id) ?? list[0];
}

function listNames(list: ExtensionInfo[]): string {
  const names = list.map(name);
  if (names.length <= 2) return names.join(" und ");
  return `${names.slice(0, 2).join(", ")} und ${names.length - 2} weitere`;
}

export function buildFirstSteps({ can, hosts, extensions, placedWidgets, catalogWidgets }: FirstStepsInput): FirstStep[] {
  const steps: FirstStep[] = [];

  if (can("hosts.write") && can("hosts.read") && hosts) {
    const withAccess = hosts.filter((h) => h.credential && isSshCredential(h.credential));
    steps.push({
      id: "server", title: "Server anlegen", done: hosts.length > 0, to: NEW_HOST_TARGET, action: "Server hinzufügen",
      text: "Trag den ersten Rechner ein, den Nodvard Deck im Blick behalten soll, zum Beispiel den Raspberry Pi.",
    });
    const noAccess = preferManual(hosts.filter((h) => !h.credential || !isSshCredential(h.credential)));
    // Ein Schluessel allein reicht nicht: solange sich Nodvard Deck damit nie angemeldet hat, fehlt vermutlich
    // der Befehl auf dem Server. Dass der Server antwortet, belegt das nicht (der SSH-Port ist offen, mehr nicht).
    const confirmed = withAccess.filter((h) => h.credential!.kind !== "ssh_key" || !!h.login_ok_at);
    const waitingKey = preferManual(withAccess.filter((h) => !confirmed.includes(h)));
    const accessDone = confirmed.length > 0;
    steps.push({
      id: "access", title: "SSH-Zugang hinterlegen", done: accessDone,
      // `?befehl=1`: die Seite klappt den Einrichtungsbefehl gleich auf.
      to: !accessDone && waitingKey ? `${HOST_TARGET}/${waitingKey.id}?befehl=1` : noAccess ? `${HOST_TARGET}/${noAccess.id}` : HOST_TARGET,
      action: !accessDone && waitingKey ? "Befehl ansehen" : "Zugang einrichten",
      text: !accessDone && waitingKey
        ? "Der Schlüssel ist erzeugt, aber noch nicht bestätigt: Führe den Befehl auf dem Server aus und prüfe danach die Verbindung. Die Seite zeigt ihn dir zum Kopieren."
        : "Nodvard Deck meldet sich per SSH auf dem Server an. Mit einem Passwort geht das ohne Befehl; für den vollen Zugang mit Schlüssel führst du einmal einen Befehl auf dem Server aus – die Seite zeigt ihn dir zum Kopieren.",
    });
    const connected = withAccess.some((h) => !!h.login_ok_at);
    const toCheck = preferManual(withAccess);
    steps.push({
      id: "check", title: "Verbindung prüfen", done: connected,
      to: toCheck ? `${HOST_TARGET}/${toCheck.id}` : HOST_TARGET, action: "Verbindung prüfen",
      text: "Ein Klick zeigt, ob die Anmeldung klappt und was dem Server noch fehlt.",
    });
  }

  if (can("extensions.manage") && extensions && extensions.length > 0) {
    const enabled = extensions.filter((e) => e.state === "enabled");
    const needing = enabled.filter((e) => e.needs_setup && e.id !== PUSH_EXTENSION_ID);
    const first = needing[0];
    steps.push({
      id: "modules", title: enabled.length === 0 ? "Module einschalten" : "Module einrichten",
      done: enabled.length > 0 && needing.length === 0,
      to: needing.length === 1 && first?.has_settings ? `/settings/extensions/${first.id}` : "/settings/extensions",
      action: needing.length > 0 ? "Jetzt einrichten" : "Module ansehen",
      text: needing.length > 0
        ? `${listNames(needing)} ${needing.length === 1 ? "braucht" : "brauchen"} noch ein paar Angaben, bevor es losgehen kann.`
        : "Schalte die Module ein, die du brauchst, zum Beispiel für die Auslastung deiner Server, Docker-Dienste oder Updates. Proxmox und Backups brauchst du nur, wenn du Proxmox nutzt.",
    });

    const push = extensions.find((e) => e.id === PUSH_EXTENSION_ID);
    if (push) {
      steps.push({
        id: "push", title: "Push-Nachrichten aufs Handy", done: push.state === "enabled" && !push.needs_setup,
        to: push.state === "enabled" && push.has_settings ? `/settings/extensions/${push.id}` : "/settings/extensions",
        action: push.state === "enabled" ? "Jetzt einrichten" : "Einschalten",
        text: "So meldet sich Nodvard Deck bei dir, wenn etwas nicht stimmt, auch wenn du die Seite nicht offen hast.",
      });
    }
  }

  if (placedWidgets !== undefined && catalogWidgets !== undefined) {
    if (catalogWidgets > 0) {
      steps.push({
        id: "widget", title: "Erstes Widget auf dem Dashboard", done: placedWidgets > 0, scrollTo: "dashboard-widgets",
        action: "Zu den Widgets",
        text: "Widgets zeigen Zahlen und Listen deiner Module auf einen Blick. Über „Widgets verwalten“ legst du fest, welche erscheinen.",
      });
    } else if (can("extensions.manage")) {
      steps.push({
        id: "widget", title: "Erstes Widget auf dem Dashboard", done: false, to: "/settings/extensions",
        action: "Module ansehen", text: "Sobald ein Modul eingeschaltet ist, das Widgets mitbringt, erscheinen sie hier von selbst.",
      });
    }
  }

  return steps;
}
