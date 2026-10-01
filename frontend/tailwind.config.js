/** @type {import('tailwindcss').Config} */
export default {
  // Die mitgelieferten Extension-Seiten laufen im selben Dokument und nutzen dieselben
  // Utility-Klassen -- ohne diesen Eintrag fehlten ihre eigenen Klassen (farbige Badges,
  // Abstaende) im ausgelieferten CSS (live gefunden, Startseiten-Auftrag). Fremde, spaeter
  // installierte Extensions liefern eigene Styles mit oder nutzen die Kern-Klassen
  // (.panel, .status-dot, ...; docs/02-EXTENSION-API.md §5).
  content: ["./index.html", "./src/**/*.{ts,tsx}", "../extensions/*/frontend/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Farben zeigen auf CSS-Custom-Properties, die zur Laufzeit aus
      // GET /api/v1/branding gesetzt werden (docs/00-DECISIONS.md D-03: "White-Labeling
      // muss zur Laufzeit funktionieren -- ein Kaeufer aendert Farben in den
      // Einstellungen, nicht durch einen Neubau des Frontends"). Tailwinds eigenes,
      // zur Build-Zeit fixiertes Theming wuerde genau dagegen arbeiten -- deshalb sind
      // dies nur Aliase auf var(--color-*), keine festen Hex-Werte.
      colors: {
        accent: "var(--color-accent)",
        "accent-strong": "var(--color-accent-strong)",
        surface: "var(--color-surface)",
        background: "var(--color-background)",
        foreground: "var(--color-text)",
      },
    },
  },
  plugins: [],
};
