// src/HelloPage.tsx
import { useEffect, useState } from "react";
import { jsx, jsxs } from "react/jsx-runtime";
function HelloPage() {
  const [state, setState] = useState("loading");
  const [subtitle, setSubtitle] = useState("");
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/ext/hello-world/widgets/hello").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then((body) => {
      if (cancelled) return;
      setSubtitle(body.data[0]?.subtitle ?? "");
      setState("ready");
    }).catch(() => {
      if (!cancelled) setState("error");
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return /* @__PURE__ */ jsxs("div", { className: "rounded-lg border border-white/10 bg-[var(--color-surface)] p-6", children: [
    /* @__PURE__ */ jsx("h2", { className: "text-lg font-semibold", children: "Hallo Welt (aus einem echten ESM-Bundle)" }),
    /* @__PURE__ */ jsxs("p", { className: "mt-2 text-sm opacity-70", children: [
      "Diese Seite ist NICHT Teil des Kern-Frontend-Bundles -- sie wurde per",
      " ",
      /* @__PURE__ */ jsx("code", { children: "import()" }),
      " von ",
      /* @__PURE__ */ jsx("code", { children: "/api/v1/extensions/hello-world/frontend/index.js" }),
      " ",
      "nachgeladen, React kommt \xFCber den Import-Map-Shim aus",
      " ",
      /* @__PURE__ */ jsx("code", { children: "window.__nodvardDeck" }),
      "."
    ] }),
    /* @__PURE__ */ jsxs("p", { className: "mt-4 text-sm", children: [
      state === "loading" && "Lade Widget-Daten \u2026",
      state === "error" && "Fehler beim Laden.",
      state === "ready" && subtitle
    ] })
  ] });
}
export {
  HelloPage
};
