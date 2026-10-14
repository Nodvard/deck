/**
 * Kennzeichen „Beta“ für Module, die im Manifest `stage = "beta"` tragen (`GET /extensions`, `GET /pages`).
 * Ruhig gehalten (grau, kein Warnton): Das Modul funktioniert, ist nur noch nicht so lange im Einsatz wie die anderen.
 * Der Satz zur Erklärung steht einmal hier und wird überall gleich gezeigt (Tooltip, Fußzeile der Listen, Seitenkopf).
 */

/** Was „Beta“ heißt, in einem Satz. */
export const BETA_HINT = "Funktioniert, ist aber noch nicht so gründlich erprobt wie der Rest. Rückmeldungen helfen.";

/**
 * Nur der Wert `beta` kennzeichnet; fehlt die Angabe (älteres Backend) oder ist sie
 * unbekannt, gilt das Modul als fertig.
 */
export function isBeta(stage: string | null | undefined): boolean {
  return stage === "beta";
}

/**
 * Kleines Abzeichen „Beta“ neben dem Namen eines Moduls. Der Tooltip erklärt es (`tooltip`), Hilfsprogramme
 * lesen den Satz mit (`spoken`). Beides aus, wo der Satz ohnehin daneben steht; im Menü nur der Tooltip, damit
 * der Name des Links kurz bleibt.
 */
export function BetaBadge({ className = "", tooltip = true, spoken = true }: { className?: string; tooltip?: boolean; spoken?: boolean }) {
  return (
    <span
      data-testid="beta-badge"
      title={tooltip ? BETA_HINT : undefined}
      className={`inline-flex flex-none items-center rounded-md bg-white/[0.08] px-1.5 py-0.5 text-[11px] font-medium text-white/70 ${className}`}
    >
      Beta
      {spoken && <span className="sr-only"> – {BETA_HINT}</span>}
    </span>
  );
}

/** Der Satz zum Abzeichen, einmal unter einer Liste von Modulen (Assistent, Einstellungen). */
export function BetaExplainer() {
  return (
    <p data-testid="beta-explainer" className="text-xs text-white/50">
      <span className="font-medium text-white/65">Beta:</span> {BETA_HINT}
    </p>
  );
}

/** Schmale Zeile über der Seite eines Beta-Moduls, damit das Kennzeichen auch dort steht, wo man das Modul benutzt. */
export function BetaPageNotice() {
  return (
    <div data-testid="beta-page-notice" className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-2 px-4 pt-3 text-xs text-white/55 sm:px-6">
      <BetaBadge tooltip={false} spoken={false} />
      <span>{BETA_HINT}</span>
    </div>
  );
}
