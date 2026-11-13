import type { Plugin } from "vite";

const ORIGINAL = '_addStyle(t,e){t.setAttribute("style",`${t.getAttribute("style")||""}${e};`)}';
const PATCHED = '_addStyle(t,e){t.style.cssText=`${t.getAttribute("style")||""}${e};`}';

/** xterm.js 6.0.0 (MIT) setzt Truecolor und Kontrastfarben über Stilattribute; CSSOM erhält sie unter der strikten CSP. */
// Quelle: https://github.com/xtermjs/xterm.js/blob/6.0.0/src/browser/renderer/dom/DomRendererRowFactory.ts
export function xtermCsp(): Plugin {
  return {
    name: "deck-xterm-csp",
    enforce: "pre",
    transform(code, id) {
      const filename = id.split("?")[0].replace(/\\/g, "/");
      if (!/\/node_modules\/@xterm\/xterm\/lib\/xterm\.mjs$/.test(filename)) return null;
      if (code.split(ORIGINAL).length !== 2) {
        throw new Error("xterm hat sich geändert: Die CSP-Korrektur für Terminalfarben muss geprüft werden.");
      }
      return { code: code.replace(ORIGINAL, PATCHED), map: null };
    },
  };
}
