import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useState, type ComponentType } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";

import { api } from "../lib/api";
import { usePages } from "../lib/catalog";
import { dispatchDeckEvent, findDeck } from "../lib/deckGlobal";
import type { ExtensionInfo } from "../lib/firstSteps";
import { useAuthStore } from "../state/auth";
import { Button, errorText } from "./settings/ui";

/** Die Seite gibt es nicht. Steckt dahinter ein ausgeschaltetes Modul (alter Link, Lesezeichen), sagt die Seite das und
 * schaltet es auf Knopfdruck ein (wer das darf), statt „nicht gefunden oder keine Berechtigung“ zu raten. */
function PageMissing({ extId }: { extId: string | undefined }) {
  const queryClient = useQueryClient();
  const canManage = useAuthStore((s) => s.hasPermission("extensions.manage"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switchedOn, setSwitchedOn] = useState(false);
  const { data: ext } = useQuery({
    queryKey: ["extensions", "one", extId],
    queryFn: () => api.get<ExtensionInfo>(`/extensions/${encodeURIComponent(extId ?? "")}`),
    enabled: Boolean(extId),
    retry: false,
  });
  const name = ext?.name ?? extId;

  async function enable() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ state?: string; last_error?: string | null } | undefined>(`/extensions/${encodeURIComponent(extId ?? "")}/enable`);
      // Das Backend antwortet auch dann mit 200, wenn das Modul beim Laden abstürzt: dann nicht „eingeschaltet“ melden.
      if (result?.state && result.state !== "enabled") {
        setError(`„${name}“ ließ sich nicht einschalten${result.last_error ? `: ${result.last_error}` : "."}`);
      } else {
        setSwitchedOn(true);
      }
      for (const key of ["extensions", "pages", "widgets", "capabilities"]) void queryClient.invalidateQueries({ queryKey: [key] });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (switchedOn && ext?.state === "enabled") {
    return <p className="p-6 text-sm" data-testid="module-switched-on" role="status">Das Modul „{name}“ ist eingeschaltet. Die Seite wird geladen …</p>;
  }
  if (ext?.state === "disabled") {
    return (
      <div className="p-6" data-testid="module-off">
        <p className="text-sm">Das Modul „{name}“ ist ausgeschaltet, darum gibt es diese Seite gerade nicht.</p>
        <p className="mt-1 text-sm opacity-70">
          {canManage ? "Du kannst es gleich hier einschalten. Ausschalten geht jederzeit wieder unter Einstellungen → Erweiterungen." : "Ein Administrator kann es einschalten."}
        </p>
        {canManage && <div className="mt-3"><Button variant="primary" busy={busy} onClick={() => void enable()}>Einschalten</Button></div>}
        {error && <p role="alert" className="mt-2 text-sm text-red-300">{error}</p>}
      </div>
    );
  }
  if (ext && ext.state !== "enabled") {
    return (
      <div className="p-6" data-testid="module-broken">
        <p className="text-sm">Das Modul „{name}“ läuft gerade nicht. Auf der Erweiterungen-Seite in den Einstellungen steht, woran es liegt.</p>
        {/* Ein gescheitertes Einschalten von eben: der Grund bleibt stehen, auch wenn der neue Zustand schon da ist. */}
        {error && <p role="alert" className="mt-2 text-sm text-red-300">{error}</p>}
      </div>
    );
  }
  return (
    <p className="p-6 text-sm text-red-400">
      Seite nicht gefunden oder keine Berechtigung.{" "}
      <Link to="/" className="underline underline-offset-2">Zur Übersicht</Link>
    </p>
  );
}

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
  // Alte Adresse einer umbenannten Erweiterung (Lesezeichen, ntfy-Link, gespeicherte Meldung): die Seite gibt es
  // unter der heutigen Kennung. Eine Seite unter der Kennung aus der Adresse geht vor.
  const renamedPage = page
    ? undefined
    : pages?.find((p) => extId !== undefined && p.legacy_ext_ids?.includes(extId) && `/ext/${extId}${p.path}` === location.pathname);

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
  // Ersetzend, mit unveränderter Abfrage und unverändertem Anker (`?tab=…#…`): Zurück führt nicht auf die alte Adresse.
  if (renamedPage) return <Navigate replace to={{ pathname: `/ext/${renamedPage.ext_id}${renamedPage.path}`, search: location.search, hash: location.hash }} />;
  if (!page) return <PageMissing extId={extId} />;
  if (error) return <p className="p-6 text-sm text-red-400">Fehler im Modul: {error}</p>;
  if (!Component) return <p className="p-6 text-sm opacity-60">Lade Modul …</p>;
  // Neu aufbauen, wenn sich nur die Query aendert (Server-Seite -> `?host=`): fuer Seiten, die
  // ihre Query nur beim Mount lesen. Die mitgelieferten folgen ihr zusaetzlich ueber das
  // Ereignis oben, auch bei derselben Adresse (docs/02-EXTENSION-API.md, Host-Werkzeuge).
  return <Component key={location.search} />;
}
