/**
 * Beispielseite der Erweiterung hello-world (docs/02-EXTENSION-API.md §4/§5): der PageSpec
 * `component="HelloPage"` (siehe nodvard_deck_ext_hello_world/__init__.py) verweist auf genau
 * diesen Export.
 *
 * Die Seite geht denselben Weg wie die mitgelieferten Erweiterungen und zeigt ihn als
 * Vorlage:
 * - Daten holt `authedFetch` aus dem gemeinsamen Kit (`extensions/_shared/frontend/src/`): es
 *   hängt `/api/v1` an, schickt das Token mit und erneuert es bei einem 401 einmal über den Kern.
 *   Antwortet der Server gar nicht, wirft es `ServerUnavailableError` mit der Standardmeldung.
 * - Fehlertexte einer Antwort liefert `errorText`.
 * - Aussehen aus dem Kit (`Page`, `Card`, `Loading`, `Notice`, `Button`).
 * - Das Laden bricht ab, sobald die Seite verlassen wird.
 *
 * Bewusst ECHTES React (useState/useEffect) über den Import-Map-Shim
 * (window.__nodvardDeck.React), nicht nur ein statischer String -- sonst wäre der ESM-Loader
 * nie wirklich bewiesen. Gebaut mit esbuild über `extensions/hello-world/frontend/build.mjs`:
 * `react`/`react-dom` bleiben extern, das Kit wird in das Bundle gebaut.
 */
import { useEffect, useState } from "react";

import { authedFetch, errorText } from "../../../_shared/frontend/src/api";
import { Button, Card, Loading, Notice, Page } from "../../../_shared/frontend/src/ui";

interface WidgetData {
  data: { title: string; subtitle: string }[];
}

type LoadState =
  | { status: "loading" }
  | { status: "ready"; subtitle: string }
  | { status: "error"; message: string };

export function HelloPage() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  // Zähler statt Funktion: "Neu laden" startet den Effekt samt Abbruch des alten Aufrufs neu.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    (async () => {
      try {
        const res = await authedFetch("/ext/hello-world/widgets/hello", { signal: controller.signal });
        if (!res.ok) throw new Error(await errorText(res));
        const body = (await res.json().catch(() => null)) as WidgetData | null;
        if (!body || !Array.isArray(body.data)) throw new Error("Die Antwort des Servers war nicht lesbar.");
        if (!controller.signal.aborted) setState({ status: "ready", subtitle: body.data[0]?.subtitle ?? "" });
      } catch (err) {
        // Seite verlassen oder neu geladen: das Ergebnis dieses Aufrufs zählt nicht mehr.
        if (controller.signal.aborted) return;
        setState({ status: "error", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => controller.abort();
  }, [attempt]);

  return (
    <Page
      title="Hallo Welt"
      description="Beispielseite aus einem eigenen ESM-Bundle"
      actions={
        <Button onClick={() => setAttempt((n) => n + 1)} disabled={state.status === "loading"}>
          Neu laden
        </Button>
      }
    >
      <Card title="Aus einem echten Bundle">
        <p className="text-sm text-white/70">
          Diese Seite ist nicht Teil des Kern-Frontend-Bundles. Sie wurde per <code>import()</code> von{" "}
          <code>/api/v1/extensions/hello-world/frontend/index.js</code> nachgeladen, React kommt über den
          Import-Map-Shim aus <code>window.__nodvardDeck</code>.
        </p>
      </Card>
      <div className="mt-4">
        {state.status === "loading" && <Loading />}
        {state.status === "error" && <Notice text={state.message} />}
        {state.status === "ready" && (
          <Card title="Daten vom Server">
            <p className="text-sm">{state.subtitle || "Noch keine Daten."}</p>
          </Card>
        )}
      </div>
    </Page>
  );
}
