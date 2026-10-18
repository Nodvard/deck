/**
 * Beispielseite von hello-world (docs/02-EXTENSION-API.md §4/§5) und Vorlage für eigene Seiten: Daten über `authedFetch`
 * (Token, Erneuerung bei 401, `ServerUnavailableError`), Fehlertexte über `errorText`, Aussehen aus dem Kit, Abbruch beim
 * Verlassen der Seite; echtes React über den Import-Map-Shim, damit der ESM-Loader wirklich geprüft wird.
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
  // Zähler statt Funktion: „Neu laden“ startet den Effekt samt Abbruch des alten Aufrufs neu.
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
