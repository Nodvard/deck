/**
 * Branding zur Laufzeit -- docs/01-ARCHITECTURE.md §6.
 *
 * Name, Logo und Farben sind ein Konfigurationswert, kein Build-Artefakt: die Werte
 * kommen aus `GET /api/v1/branding` und werden in CSS-Custom-Properties auf `:root`
 * geschrieben. Kein Rebuild, kein Neustart, keine Umgebungsvariable -- ein Kaeufer des
 * generischen Kerns aendert sein Branding in den Einstellungen.
 *
 * Feldnamen und -typen spiegeln exakt backend/src/nodvard_deck/branding.py::Branding.
 */

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

const CSS_VAR_BY_COLOR_KEY: Record<keyof BrandingColors, string> = {
  accent: "--color-accent",
  accent_strong: "--color-accent-strong",
  background: "--color-background",
  surface: "--color-surface",
  text: "--color-text",
};

export async function fetchBranding(): Promise<Branding> {
  const res = await fetch("/api/v1/branding");
  if (!res.ok) {
    throw new Error(`GET /api/v1/branding fehlgeschlagen: ${res.status}`);
  }
  return (await res.json()) as Branding;
}

/**
 * Schreibt Branding in DOM/CSS. Wird einmal beim Start aufgerufen (siehe AppShell.tsx) --
 * spaeter (WP-Settings) erneut nach `PUT /api/v1/branding`, ohne Reload.
 */
export function applyBranding(branding: Branding): void {
  const root = document.documentElement;
  for (const [key, cssVar] of Object.entries(CSS_VAR_BY_COLOR_KEY) as [
    keyof BrandingColors,
    string,
  ][]) {
    root.style.setProperty(cssVar, branding.colors[key]);
  }

  document.title = branding.product_name;
  // Statusleiste der Handy-App in der Hintergrundfarbe des Brandings.
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", branding.colors.background);

  if (branding.favicon_url) {
    const link = document.getElementById("favicon-link") as HTMLLinkElement | null;
    if (link) link.href = branding.favicon_url;
  }
}
