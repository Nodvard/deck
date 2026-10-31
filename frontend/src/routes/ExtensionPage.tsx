import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useState, type ComponentType } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";

import { BetaPageNotice, isBeta } from "../components/BetaBadge";
import { api } from "../lib/api";
import { usePages } from "../lib/catalog";
import { dispatchDeckEvent, findDeck } from "../lib/deckGlobal";
import { extensionErrorText } from "../lib/extensionError";
import { useExtensionLabel } from "../lib/extensionNames";
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
        setError(`„${name}“ ließ sich nicht einschalten${result.last_error ? `: ${extensionErrorText(result.last_error)}` : "."}`);
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

/** Das Programm einer Seite ließ sich nicht laden (nach einem Update oft nur ein alter Stand im Browser) oder hat
 * keine passende Seite mitgebracht. Der Satz sagt, was man tun kann; die technische Meldung steht klein darunter. */
function ModuleLoadError({ extId, message }: { extId: string; message: string }) {
  const label = useExtensionLabel();
  return (
    <div className="p-6" data-testid="module-load-error">
      <p role="alert" className="text-sm text-red-300">Das Modul „{label(extId)}“ ließ sich nicht laden.</p>
      <p className="mt-1 text-sm opacity-70">
        Lade die Seite einmal neu. Hilft das nicht, schau unter{" "}
        <Link to="/settings/extensions" className="underline underline-offset-2">Einstellungen → Erweiterungen</Link>{" "}
        nach, ob dort ein Fehler zum Modul steht.
      </p>
      <div className="mt-3"><Button onClick={() => window.location.reload()}>Seite neu laden</Button></div>
      <p className="mt-3 break-words text-xs opacity-50">Technische Meldung: {message}</p>
    </div>
  );
}

/**
 * ESM-Loader (docs/02-EXTENSION-API.md): lädt `/api/v1/extensions/<ext_id>/frontend/index.js` per `import()` und
 * rendert den in `PageSpec.component` benannten Export. `@vite-ignore`, weil das Ziel erst zur Laufzeit feststeht und
 * das Bundle der Extension gehört, nicht dem Kern-Build.
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

  // Der Router navigiert per pushState, das meldet der Browser bei niemandem: Seiten, die ihrer Query folgen, erfahren
  // jede Navigation über dieses Ereignis, auch bei gleicher Adresse (`location.key` ändert sich). Kindeffekte laufen
  // davor, ein frisch eingehängter Zuhörer hört es.
  useEffect(() => {
    dispatchDeckEvent("navigate");
  }, [location.key]);

  // Das gilt auch für Zurück/Vor: Die Seiten sollen dann nicht zusätzlich selbst auf popstate hören (location.ts),
  // sonst fragen sie doppelt ab. Layout-Effekt, damit die Marke vor den Effekten der Seite steht.
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
  if (error) return <ModuleLoadError extId={page.ext_id} message={error} />;
  if (!Component) return <p className="p-6 text-sm opacity-60">Lade Modul …</p>;
  // Neu aufbauen, wenn sich nur die Query ändert (Server-Seite -> `?host=`), für Seiten, die ihre Query nur beim Mount
  // lesen. Die mitgelieferten folgen ihr zusätzlich über das Ereignis oben, auch bei derselben Adresse.
  const content = <Component key={location.search} />;
  // Beta-Module: eine schmale Zeile darüber (die Seite des Moduls bringt ihren eigenen Kopf mit und bleibt unberührt).
  return isBeta(page.stage) ? <><BetaPageNotice />{content}</> : content;
}
