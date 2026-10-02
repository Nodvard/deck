/**
 * Die "reichere Web-Darstellung" aus docs/02-EXTENSION-API.md §4/§5 -- der PageSpec
 * `component="HelloPage"` (siehe nodvard_deck_ext_hello_world/__init__.py) referenziert
 * genau diesen Export. Bewusst ECHTES React (useState/useEffect) ueber den
 * Import-Map-Shim (window.__nodvardDeck.React), nicht nur ein statischer String --
 * sonst waere der ESM-Loader nie wirklich bewiesen, nur eine leere Huelle geladen.
 *
 * Gebaut mit esbuild (docs/02 §5: "kann mit Vite, esbuild oder tsc gebaut werden"),
 * `react`/`react-dom` als external -- siehe extensions/hello-world/frontend/build.mjs.
 */
import { useEffect, useState } from "react";

interface WidgetData {
  data: { title: string; subtitle: string }[];
}

export function HelloPage(): JSX.Element {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [subtitle, setSubtitle] = useState("");

  useEffect(() => {
    let cancelled = false;
    // Die Route verlangt eine Anmeldung: das Token kommt von der Kern-Shell.
    const shell = window.__nodvardDeck ?? window.__lattice;
    const token = shell?.getAccessToken?.();
    fetch("/api/v1/ext/hello-world/widgets/hello", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<WidgetData>;
      })
      .then((body) => {
        if (cancelled) return;
        setSubtitle(body.data[0]?.subtitle ?? "");
        setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-6">
      <h2 className="text-lg font-semibold">Hallo Welt (aus einem echten ESM-Bundle)</h2>
      <p className="mt-2 text-sm opacity-70">
        Diese Seite ist NICHT Teil des Kern-Frontend-Bundles -- sie wurde per{" "}
        <code>import()</code> von <code>/api/v1/extensions/hello-world/frontend/index.js</code>{" "}
        nachgeladen, React kommt über den Import-Map-Shim aus{" "}
        <code>window.__nodvardDeck</code>.
      </p>
      <p className="mt-4 text-sm">
        {state === "loading" && "Lade Widget-Daten …"}
        {state === "error" && "Fehler beim Laden."}
        {state === "ready" && subtitle}
      </p>
    </div>
  );
}
