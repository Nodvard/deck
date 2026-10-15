/** Eigenes CSS der Admins als eigene Datei vom Server, nach allen anderen Stilen geladen, damit es gewinnt. */

export const CUSTOM_CSS_URL = "/api/v1/branding/custom.css";
const LINK_ID = "custom-css";
/** Gilt für diesen Tab, damit der Notausgang auch nach dem Weiterklicken hält. */
const SAFE_MODE_KEY = "nodvard-deck.ohne-css";

/** `?ohne-css=1` lädt die Seite ohne eigenes CSS, falls es die Oberfläche unbenutzbar macht. */
export function customCssDisabled(search = window.location.search, storage?: Storage): boolean {
  let store: Storage | null = null;
  try {
    store = storage ?? window.sessionStorage;
  } catch {
    store = null;
  }
  if (new URLSearchParams(search).get("ohne-css") === "1") {
    try {
      store?.setItem(SAFE_MODE_KEY, "1");
    } catch {
      // Ohne Speicher gilt der Notausgang nur für diese eine Seite.
    }
    return true;
  }
  try {
    return store?.getItem(SAFE_MODE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Hängt das eigene CSS ein oder lädt es neu; `version` umgeht nach dem Speichern den Browser-Cache. */
export function loadCustomCss(version?: number): void {
  if (customCssDisabled()) return;
  let link = document.getElementById(LINK_ID) as HTMLLinkElement | null;
  if (!link) {
    link = document.createElement("link");
    link.id = LINK_ID;
    link.rel = "stylesheet";
    document.head.appendChild(link);
  }
  link.href = version ? `${CUSTOM_CSS_URL}?v=${version}` : CUSTOM_CSS_URL;
}

/** Hebt den Notausgang für diesen Tab wieder auf (auch `?ohne-css=1` in der Adresse) und lädt das eigene CSS. */
export function enableCustomCss(storage?: Storage): void {
  try {
    (storage ?? window.sessionStorage).removeItem(SAFE_MODE_KEY);
  } catch {
    // Ohne Speicher war der Notausgang ohnehin nur in der Adresse.
  }
  const url = new URL(window.location.href);
  if (url.searchParams.has("ohne-css")) {
    url.searchParams.delete("ohne-css");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }
  loadCustomCss(Date.now());
}
