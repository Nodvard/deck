import type { CSSProperties, Ref, RefCallback } from "react";

export function cspNonce(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content ?? "";
}

export function createStyle(): HTMLStyleElement {
  const element = document.createElement("style");
  element.nonce = cspNonce();
  return element;
}

const UNITLESS = new Set([
  "animationIterationCount", "aspectRatio", "columnCount", "fillOpacity", "flex", "flexGrow", "flexShrink",
  "fontWeight", "gridArea", "gridColumn", "gridColumnEnd", "gridColumnStart", "gridRow", "gridRowEnd",
  "gridRowStart", "lineHeight", "opacity", "order", "orphans", "scale", "strokeDasharray", "strokeDashoffset",
  "strokeMiterlimit", "strokeOpacity", "strokeWidth", "tabSize", "widows", "zIndex", "zoom",
]);

/** Dynamische Werte bleiben in einem freigegebenen Stylesheet, auch beim erneuten Rendern. */
export function styleRef<T extends HTMLElement | SVGElement>(values: CSSProperties | undefined, ref?: Ref<T>): RefCallback<T> {
  let style: HTMLStyleElement | undefined;
  return (node) => {
    style?.remove();
    style = undefined;
    if (node && values) {
      const id = Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-");
      node.setAttribute("data-deck-style", id);
      style = createStyle();
      document.head.appendChild(style);
      const sheet = style.sheet!;
      const selector = `${node === document.documentElement ? ":root" : ""}[data-deck-style="${id}"]`;
      sheet.insertRule(`${selector} {}`);
      const declaration = (sheet.cssRules[0] as CSSStyleRule).style;
      for (const [key, value] of Object.entries(values)) {
        if (value == null || typeof value === "boolean") continue;
        const name = key.startsWith("--") ? key : key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`).replace(/^ms-/, "-ms-");
        const unit = typeof value === "number" && value !== 0 && !key.startsWith("--") && !UNITLESS.has(key) ? "px" : "";
        declaration.setProperty(name, `${value}${unit}`);
      }
    }
    if (typeof ref === "function") ref(node);
    else if (ref) (ref as { current: T | null }).current = node;
  };
}

/** xterm und Dialoge erzeugen Stylesheets auch nach dem ersten Rendern. */
export function installStyleNonce(): void {
  const nonce = cspNonce();
  if (!nonce) return;
  const create = document.createElement.bind(document);
  document.createElement = ((tag: string, options?: ElementCreationOptions) => {
    const element = create(tag, options);
    if (tag.toLowerCase() === "style") element.setAttribute("nonce", nonce);
    return element;
  }) as typeof document.createElement;
}
