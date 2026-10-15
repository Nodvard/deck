// Farben und Radien zeigen auf CSS-Variablen, die das Theme setzt; feste Werte kämen erst mit einem Neubau.
const themed = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (color, shades) => Object.fromEntries(shades.map((s) => [s, themed(`${color}-${s}`)]));

/** @type {import('tailwindcss').Config} */
export default {
  // Die mitgelieferten Extension-Seiten nutzen dieselben Klassen; ohne diesen Eintrag fehlten sie im ausgelieferten CSS.
  // Fremde Extensions liefern eigene Styles mit oder nutzen die Kern-Klassen (.panel, .status-dot, ...).
  content: ["./index.html", "./src/**/*.{ts,tsx}", "../extensions/*/frontend/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: "var(--color-accent)",
        "accent-strong": "var(--color-accent-strong)",
        surface: "var(--color-surface)",
        background: "var(--color-background)",
        foreground: "var(--color-text)",
        // „white“ ist die Schriftfarbe, „black“ ihre Gegenfarbe; im hellen Theme tauschen sie die Rollen.
        white: themed("ink"),
        black: themed("paper"),
        // Immer weiß: Schrift auf einer Fläche in Akzent- oder Statusfarbe.
        "on-accent": "#ffffff",
        // Abdunkeln hinter Dialogen, in jedem Theme dunkel.
        scrim: "rgb(0 0 0 / <alpha-value>)",
        red: scale("red", [100, 200, 300, 400, 500, 950]),
        amber: scale("amber", [50, 100, 200, 300, 400, 500]),
        emerald: scale("emerald", [100, 200, 300, 400, 500]),
        sky: scale("sky", [200, 300, 400, 500]),
      },
      borderRadius: {
        md: "var(--radius-sm)",
        lg: "var(--radius)",
        xl: "var(--radius)",
        "2xl": "var(--radius-lg)",
      },
    },
  },
  plugins: [],
};
