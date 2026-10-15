/** Vier wählbare Themes; das gewählte steht als `data-theme` am <html>, die Farben setzt `styles/index.css`. */

export const THEMES = [
  { id: "classic", label: "Klassisch hell", hint: "Hell und sachlich, wie ein Admin-Panel.", swatch: ["#eef0f3", "#ffffff", "#2f6fd1"] },
  { id: "simple", label: "Schlicht dunkel", hint: "Ruhig und dunkel. Standard.", swatch: ["#1a1b1e", "#25262b", "#4c6ef5"] },
  { id: "pro", label: "Profi dunkel", hint: "Kontrastreich, mit orangem Akzent.", swatch: ["#111217", "#181b1f", "#f5732a"] },
  { id: "original", label: "Bisher", hint: "Das Aussehen bis 0.8: dunkelblau mit Verlauf.", swatch: ["#0b1220", "#111a2b", "#0ea5e9"] },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

export const DEFAULT_THEME: ThemeId = "simple";

/** Merkt die Wahl im Browser, damit schon die Anmeldeseite im richtigen Theme erscheint. */
export const THEME_STORAGE_KEY = "nodvard-deck.theme";

export function isTheme(value: unknown): value is ThemeId {
  return THEMES.some((t) => t.id === value);
}

export function storedTheme(storage?: Storage): ThemeId {
  try {
    const value = (storage ?? window.localStorage).getItem(THEME_STORAGE_KEY);
    return isTheme(value) ? value : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function currentTheme(): ThemeId {
  const value = document.documentElement.dataset.theme;
  return isTheme(value) ? value : DEFAULT_THEME;
}

export function applyTheme(theme: ThemeId, storage?: Storage): void {
  document.documentElement.dataset.theme = theme;
  try {
    (storage ?? window.localStorage).setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Privates Fenster oder gesperrter Speicher: das Theme gilt dann nur bis zum Neuladen.
  }
  syncThemeColor();
}

/** Statusleiste der Handy-App in der Hintergrundfarbe des Themes. */
export function syncThemeColor(): void {
  const background = getComputedStyle(document.documentElement).getPropertyValue("--color-background").trim();
  if (background) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", background);
}
