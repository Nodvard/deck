import { useEffect, useLayoutEffect, useState, type ComponentType } from "react";
import { useLocation, useParams } from "react-router-dom";

import { usePages } from "../lib/catalog";
import { dispatchDeckEvent, findDeck } from "../lib/deckGlobal";

/**
 * ESM-Loader (docs/02-EXTENSION-API.md §5): laedt `/api/v1/extensions/<ext_id>/
 * frontend/index.js` per `import()` und rendert den in `PageSpec.component`
 * benannten Export. `@vite-ignore`, weil das Ziel erst zur Laufzeit feststeht --
 * Vite kann diesen Import nicht statisch analysieren/bundlen, was hier gewollt ist
 * (das Bundle gehoert der Extension, nicht dem Kern-Build).
 */
export function ExtensionPage() {
  const { extId } = useParams();
  const location = useLocation();
  const { data: pages, isLoading } = usePages();
  const [Component, setComponent] = useState<ComponentType | null>(null);
  const [error, setError] = useState<string | null>(null);

  const page = pages?.find((p) => p.ext_id === extId && `/ext/${p.ext_id}${p.path}` === location.pathname);

  useEffect(() => {
    if (!page) return;
    let cancelled = false;
    setComponent(null);
    setError(null);

    const build = typeof __BUILD_ID__ !== "undefined" ? __BUILD_ID__ : "dev";
    const url = `/api/v1/extensions/${page.ext_id}/frontend/index.js?v=${build}`;
    import(/* @vite-ignore */ url)
      .then((mod: Record<string, unknown>) => {
        if (cancelled) return;
        const exported = mod[page.component];
        if (typeof exported !== "function") {
          throw new Error(`Export "${page.component}" fehlt im Bundle von "${page.ext_id}".`);
        }
        setComponent(() => exported as ComponentType);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
    };
  }, [page?.ext_id, page?.component]);

  // Der Router navigiert per pushState -- das meldet der Browser bei niemandem. Seiten, die ihrer
  // Query folgen (Reiter, Server-Filter), erfahren von jeder Navigation ueber dieses Ereignis, auch
  // wenn sich nur der Verlaufsschluessel aendert (gleiche Adresse, die Seite hat ihre per
  // replaceState geaendert). Kindeffekte laufen vor diesem: ein frisch eingehaengter Zuhoerer hoert es.
  // Gefeuert wird unter beiden Namen (`nodvard-deck:navigate` und das alte `lattice:navigate`, damit
  // auch Erweiterungen mit aelterem UI-Kit es hoeren); das Kit hoert immer nur auf einen
  // (extensions/_shared/frontend/src/location.ts), nichts reagiert doppelt.
  useEffect(() => {
    dispatchDeckEvent("navigate");
  }, [location.key]);

  // Das gilt auch fuer Zurueck/Vor: die Seiten sollen dann nicht zusaetzlich selbst auf popstate
  // hoeren (location.ts), sonst fragen sie doppelt ab. Layout-Effekt, damit die Marke vor den
  // Effekten der Seite steht.
  useLayoutEffect(() => {
    const shell = findDeck();
    if (!shell) return;
    shell.navigateEvents = true;
    return () => {
      shell.navigateEvents = false;
    };
  }, []);

  if (isLoading) return <p className="p-6 text-sm opacity-60">Lade …</p>;
  if (!page) return <p className="p-6 text-sm text-red-400">Seite nicht gefunden oder keine Berechtigung.</p>;
  if (error) return <p className="p-6 text-sm text-red-400">Extension-Fehler: {error}</p>;
  if (!Component) return <p className="p-6 text-sm opacity-60">Lade Extension-Bundle …</p>;
  // Neu aufbauen, wenn sich nur die Query aendert (Server-Seite -> `?host=`): fuer Seiten, die
  // ihre Query nur beim Mount lesen. Die mitgelieferten folgen ihr zusaetzlich ueber das
  // Ereignis oben, auch bei derselben Adresse (docs/02-EXTENSION-API.md, Host-Werkzeuge).
  return <Component key={location.search} />;
}
