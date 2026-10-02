/**
 * Freundliche Seiten statt der englischen Entwickler-Fehlerseite von React Router
 * ("Unexpected Application Error! 404 Not Found ... Hey developer"):
 *
 * - `NotFoundPage`: unbekannte Adresse (alter Merkzettel, Tippfehler, Link aus einer
 *   Push-Nachricht nach einem Update).
 * - `RouteErrorPage`: ein Fehler beim Aufbauen einer Seite. Innerhalb der Oberflaeche bleibt das
 *   Menue stehen (`fullScreen` = false); ganz aussen, wenn schon die Oberflaeche selbst
 *   scheitert, fuellt die Seite den Bildschirm.
 */
import { isRouteErrorResponse, useRouteError } from "react-router-dom";

import { ButtonLink, EmptyState } from "../components/EmptyState";

const reloadClass =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-medium text-white transition hover:bg-white/[0.12]";

/** Browser-Meldungen, wenn eine nachgeladene Datei fehlt (nach einem Update des Servers). */
const CHUNK_ERROR = /dynamically imported module|Importing a module script failed|error loading dynamically|Unable to preload CSS/i;

function NotFoundCard() {
  return (
    <EmptyState
      icon="layout-dashboard"
      title="Seite nicht gefunden"
      text="Diese Adresse gibt es nicht (mehr). Vielleicht ist der Link veraltet oder es hat sich ein Tippfehler eingeschlichen."
      action={<ButtonLink to="/">Zur Übersicht</ButtonLink>}
    />
  );
}

export function NotFoundPage() {
  return (
    <div className="p-6" data-testid="not-found">
      <NotFoundCard />
    </div>
  );
}

/** Kurze Beschreibung des Fehlers fuer „Technische Einzelheiten“ (kann englisch sein, ist nur zum Weitergeben). */
function technicalDetail(error: unknown): string | null {
  if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`.trim();
  if (error instanceof Error) return error.message || error.name;
  if (typeof error === "string" && error) return error;
  return null;
}

export function RouteErrorPage({ fullScreen = false }: { fullScreen?: boolean }) {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const outdated = error instanceof Error && CHUNK_ERROR.test(error.message);
  const detail = technicalDetail(error);

  let content;
  if (notFound) {
    content = <NotFoundCard />;
  } else {
    content = (
      <EmptyState
        icon="shield-alert"
        title={outdated ? "Die Seite konnte nicht geladen werden" : "Hier ist etwas schiefgegangen"}
        text={
          outdated
            ? "Wahrscheinlich wurde Nodvard Deck gerade aktualisiert. Lade die Seite neu, dann klappt es meistens."
            : "Die Seite konnte nicht angezeigt werden. Lade sie neu oder gehe zurück zur Übersicht. Wenn es bleibt, hilft ein Blick ins Protokoll des Servers."
        }
        action={
          <>
            <button type="button" onClick={() => window.location.reload()} className={reloadClass}>
              Seite neu laden
            </button>
            <ButtonLink to="/">Zur Übersicht</ButtonLink>
          </>
        }
      />
    );
  }

  const body = (
    <div role={notFound ? undefined : "alert"} data-testid={notFound ? "not-found" : "route-error"}>
      {content}
      {!notFound && detail && (
        <details className="mt-3 text-xs text-white/50">
          <summary className="cursor-pointer select-none">Technische Einzelheiten</summary>
          <p className="mt-1 break-words font-mono">{detail}</p>
        </details>
      )}
    </div>
  );

  if (fullScreen) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)] px-4 py-6 text-white">
        <div className="w-full max-w-md">{body}</div>
      </div>
    );
  }
  return <div className="p-6">{body}</div>;
}
