// ../../_shared/frontend/src/ui.tsx
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
function Page({ title, description, actions, children }) {
  return /* @__PURE__ */ jsxs("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs("div", { className: "mb-6 flex flex-wrap items-end justify-between gap-3", children: [
      /* @__PURE__ */ jsxs("div", { children: [
        /* @__PURE__ */ jsx("h2", { className: "text-xl font-semibold tracking-tight", children: title }),
        description && /* @__PURE__ */ jsx("p", { className: "mt-1 text-sm text-white/55", children: description })
      ] }),
      actions && /* @__PURE__ */ jsx("div", { className: "flex flex-wrap items-center gap-2", children: actions })
    ] }),
    children
  ] });
}
function Card({ title, description, actions, children, className = "", padded = true }) {
  return /* @__PURE__ */ jsxs("section", { className: `panel overflow-hidden ${className}`, children: [
    (title || actions) && /* @__PURE__ */ jsxs("header", { className: "flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-5 py-3.5", children: [
      /* @__PURE__ */ jsxs("div", { children: [
        title && /* @__PURE__ */ jsx("h3", { className: "text-sm font-semibold", children: title }),
        description && /* @__PURE__ */ jsx("p", { className: "mt-0.5 text-xs text-white/50", children: description })
      ] }),
      actions && /* @__PURE__ */ jsx("div", { className: "flex items-center gap-2", children: actions })
    ] }),
    /* @__PURE__ */ jsx("div", { className: padded ? "px-5 py-4" : "", children })
  ] });
}
var BUTTON_STYLES = {
  primary: "accent-gradient text-on-accent hover:bg-accent-strong",
  secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
  danger: "border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25",
  ghost: "text-white/60 hover:bg-white/[0.06] hover:text-white"
};
var buttonClass = (variant = "secondary", small = false) => `inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${small ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm"} ${BUTTON_STYLES[variant]}`;

// src/NetworkPage.tsx
import { jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
var MOVED = [
  { title: "Pi-hole", text: "Anfragen, Blockierung, aktivste Ger\xE4te und Sperrlisten.", href: "/ext/pihole/pihole", settings: "/settings/extensions/pihole" },
  {
    title: "Nginx Proxy Manager",
    text: "Proxy-Hosts, Zertifikate und der Adress-Check zu den Containern.",
    href: "/ext/npm/npm",
    settings: "/settings/extensions/npm"
  }
];
function NetworkPage() {
  return /* @__PURE__ */ jsxs2(Page, { title: "Netzwerk", description: "Pi-hole und Nginx Proxy Manager sind jetzt eigene Module. Einstellungen, Kacheln und Zugangsdaten sind mit umgezogen.", children: [
    /* @__PURE__ */ jsx2("div", { "data-testid": "moved", className: "grid gap-4 md:grid-cols-2", children: MOVED.map((m) => /* @__PURE__ */ jsxs2(Card, { title: m.title, children: [
      /* @__PURE__ */ jsx2("p", { className: "mb-3 text-sm text-white/60", children: m.text }),
      /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap gap-2", children: [
        /* @__PURE__ */ jsxs2("a", { href: m.href, className: buttonClass("primary", true), children: [
          m.title,
          " \xF6ffnen"
        ] }),
        /* @__PURE__ */ jsx2("a", { href: m.settings, className: buttonClass("ghost", true), children: "Einstellungen" })
      ] })
    ] }, m.title)) }),
    /* @__PURE__ */ jsx2("p", { className: "mt-4 text-sm text-white/45", children: "Ist ein Modul aus, l\xE4sst es sich unter Einstellungen \u2192 Erweiterungen einschalten. \u201ENetzwerk\u201C selbst braucht es nicht mehr." })
  ] });
}
export {
  NetworkPage
};
