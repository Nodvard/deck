/**
 * Testhilfe (nur fuer *.test.tsx, kein Bundle bindet sie ein): Erweiterungsseiten, die der
 * Adresszeile folgen (location.ts), in zwei Aufbauten pruefen.
 *
 * - `navigateTo`: ein Link ohne Kern-Shell -- neuer Verlaufseintrag plus die Ereignisse, die
 *   ExtensionPage nach jeder Navigation feuert.
 * - `renderInShell`: die echte Kette Router (Browser-Verlauf) -> ExtensionPage (Kern) -> Seite,
 *   darueber Links wie aus Menue, Server-Seite oder Benachrichtigung. Die Testdatei ersetzt dafuer
 *   selbst per `vi.mock` den Seitenkatalog (`frontend/src/lib/catalog`, `usePages`) und das Bundle
 *   (`/api/v1/extensions/<id>/frontend/index.js?v=dev`) durch die Seite aus den Quellen --
 *   `vi.mock` wirkt nur in der Datei, in der es steht. Vorbild: SocPage.navigation.test.tsx.
 */
import { act, render } from "@testing-library/react";
import { Link, RouterProvider, createBrowserRouter } from "react-router-dom";

import { dispatchDeckEvent } from "../../../../frontend/src/lib/deckGlobal";
import { ExtensionPage } from "../../../../frontend/src/routes/ExtensionPage";

export function navigateTo(url: string): void {
  act(() => {
    window.history.pushState(null, "", url);
    // Wie die Kern-Shell: beide Namen (das Kit hoert auf genau einen).
    dispatchDeckEvent("navigate");
  });
}

/** `links`: Beschriftung -> Ziel. */
export function renderInShell(links: Record<string, string>): void {
  const menu = (
    <nav>
      {Object.entries(links).map(([label, to]) => (
        <Link key={label} to={to}>{label}</Link>
      ))}
    </nav>
  );
  const router = createBrowserRouter([{ path: "/ext/:extId/*", element: <>{menu}<ExtensionPage /></> }]);
  render(<RouterProvider router={router} />);
}
