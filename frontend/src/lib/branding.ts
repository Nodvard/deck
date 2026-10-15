/**
 * Branding zur Laufzeit aus `GET /api/v1/branding`: Name, Logo und eine eigene Akzentfarbe, ohne Neubau des Frontends.
 * Die Feldnamen spiegeln `backend/src/nodvard_deck/branding.py::Branding`.
 */

import { syncThemeColor } from "./theme";

export interface BrandingColors {
  accent: string;
  accent_strong: string;
  background: string;
  surface: string;
  text: string;
}

export interface Branding {
  product_name: string;
  short_name: string;
  logo_url: string | null;
  favicon_url: string | null;
  login_subtitle: string | null;
  support_url: string | null;
  colors: BrandingColors;
}

/** Werkseinstellung der Akzentfarbe: steht sie im Branding, gilt die Farbe des Themes. */
export const DEFAULT_ACCENT = "#0ea5e9";

const ACCENT_VARS = ["--color-accent", "--color-accent-strong", "--color-accent-text"];

/** Werte der Akzent-Variablen für eine eigene Farbe; die kräftige Stufe (Hover, Gedrückt) ist 15 % dunkler. */
export function accentVars(accent: string): Record<string, string> {
  return {
    "--color-accent": accent,
    "--color-accent-strong": `color-mix(in srgb, ${accent} 85%, black)`,
    "--color-accent-text": accent,
  };
}

/** Eine eigene Akzentfarbe aus dem Branding, sonst `null`; Hintergrund, Flächen und Text kommen vom Theme. */
export function brandAccent(branding: Branding): string | null {
  const accent = branding.colors.accent.trim().toLowerCase();
  return accent && accent !== DEFAULT_ACCENT ? accent : null;
}

// Wie `is_local_image_url` im Backend (`branding.py`): Logo und Favicon nur von diesem Server oder als `data:image/…`
// (die CSP meldet andere Bilder, `img-src 'self' data: blob:`, und soll sie später sperren). Ein alter Wert mit fremder
// Adresse bleibt gespeichert, wird aber nicht mehr eingesetzt.
const OWN_PATH = /^\/[A-Za-z0-9._~!$&'()*+,;=:@%\/?#-]*$/;

/** Die Adresse, wenn das Bild von diesem Server oder als `data:image/…` kommt, sonst `null`. */
export function localImageUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  for (const ch of url) {
    const code = ch.codePointAt(0) ?? 0;
    if (code < 0x21 || code === 0x7f) return null;
  }
  if (url.slice(0, 5).toLowerCase() === "data:") return url.slice(5, 11).toLowerCase() === "image/" ? url : null;
  return OWN_PATH.test(url) && !url.startsWith("//") ? url : null;
}

const DEFAULT_FAVICON = "/favicon.ico";
let faviconStamp = 0;

/** Nach einem Upload: Das Favicon hat dieselbe Adresse wie vorher, der Browser lädt es nur mit neuer Adresse neu. */
export function markFaviconChanged(now: number = Date.now()): void {
  faviconStamp = now;
}

export async function fetchBranding(): Promise<Branding> {
  const res = await fetch("/api/v1/branding");
  if (!res.ok) {
    throw new Error(`GET /api/v1/branding fehlgeschlagen: ${res.status}`);
  }
  return (await res.json()) as Branding;
}

/** Schreibt Branding in DOM/CSS, beim Start und nach jedem Speichern, ohne Neuladen. */
export function applyBranding(branding: Branding): void {
  const root = document.documentElement;
  const accent = brandAccent(branding);
  const values = accent ? accentVars(accent) : {};
  for (const name of ACCENT_VARS) {
    if (accent) root.style.setProperty(name, values[name]);
    else root.style.removeProperty(name);
  }

  document.title = branding.product_name;
  syncThemeColor();

  const link = document.getElementById("favicon-link") as HTMLLinkElement | null;
  if (link) {
    const favicon = localImageUrl(branding.favicon_url);
    const href = favicon && faviconStamp && !favicon.startsWith("data:") && !favicon.includes("?")
      ? `${favicon}?v=${faviconStamp}`
      : favicon ?? DEFAULT_FAVICON;
    if (link.getAttribute("href") !== href) link.setAttribute("href", href);
  }
}

/** Rechteinhaber und Name der Software. Beides bleibt auch bei eigenem Branding (Produktname, Logo) stehen:
 * das Branding aendert, wie die Oberflaeche heisst, nicht, wem die Software gehoert und wie sie heisst. */
export const COPYRIGHT_HOLDER = "Nico Benks";
export const SOFTWARE_NAME = "Nodvard Deck";
const COPYRIGHT_FIRST_YEAR = 2026;

/** Zeile „© 2026 Nico Benks · Nodvard Deck“ (ab 2027 „© 2026–2027 ...“) fuer die Anmeldeseite. */
export function copyrightLine(now: Date = new Date()): string {
  const year = now.getFullYear();
  const years = year > COPYRIGHT_FIRST_YEAR ? `${COPYRIGHT_FIRST_YEAR}–${year}` : `${COPYRIGHT_FIRST_YEAR}`;
  return `© ${years} ${COPYRIGHT_HOLDER} · ${SOFTWARE_NAME}`;
}

/** Erklärt den „Untertitel“: derselbe Wert (`short_name`) ist auch der Name der App auf dem Handy (Web-App-Manifest). */
export const SHORT_NAME_HINT =
  "Kleine Zeile unter dem Namen und Name der App auf dem Handy-Startbildschirm, also kurz halten. Leer: der Anfang des Produktnamens.";
