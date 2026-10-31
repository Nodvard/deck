/**
 * Testhilfe (nur für *.test.tsx, in keinem Bundle): Erweiterungsseiten, die der Adresszeile folgen (location.ts), in
 * zwei Aufbauten prüfen.
 */
import { act, render } from "@testing-library/react";
import { Link, RouterProvider, createBrowserRouter } from "react-router-dom";

import { dispatchDeckEvent } from "../../../../frontend/src/lib/deckGlobal";
import { ExtensionPage } from "../../../../frontend/src/routes/ExtensionPage";

/** Ein Link ohne Kern-Shell: neuer Verlaufseintrag plus die Ereignisse, die ExtensionPage nach jeder Navigation feuert. */
export function navigateTo(url: string): void {
  act(() => {
    window.history.pushState(null, "", url);
    dispatchDeckEvent("navigate");
  });
}

/**
 * Die echte Kette Router -> ExtensionPage -> Seite, darüber Links (`links`: Beschriftung -> Ziel). Die Testdatei ersetzt
 * per `vi.mock` selbst Seitenkatalog (`frontend/src/lib/catalog`) und Bundle durch die Seite aus den Quellen, denn
 * `vi.mock` wirkt nur in seiner Datei. Vorbild: SocPage.navigation.test.tsx.
 */
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
