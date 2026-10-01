/**
 * Karte „Erste Schritte“ oben im Cockpit: Checkliste mit Haekchen und Direktlink je Schritt.
 * Sie verschwindet von selbst, wenn alles erledigt ist, und laesst sich je Benutzer ausblenden
 * (`GET/PATCH /me/preferences`, gilt also auf jedem Geraet). Die Schritte kommen aus
 * `lib/firstSteps.ts` -- nur solche, fuer die der Nutzer das Recht hat.
 *
 * Solange noch Daten laden, bleibt die Karte weg: ein kurzes Aufblitzen offener Schritte bei
 * jemandem, der laengst fertig ist, waere schlimmer als eine Karte, die einen Moment spaeter kommt.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Check, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useWidgets } from "../lib/catalog";
import { canManageDemo, useDemoStatus } from "../lib/demo";
import { api } from "../lib/api";
import { isHiddenItem, useDashboardLayout } from "../lib/dashboard";
import { buildFirstSteps, type FirstStep, type Preferences, useExtensions, usePreferences } from "../lib/firstSteps";
import { useHosts } from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { DemoSeedButton } from "./DemoSeedButton";
import { ButtonLink } from "./EmptyState";

const stepButton =
  "inline-flex flex-none items-center justify-center rounded-lg px-2.5 py-1 text-xs font-medium transition accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110";

function StepRow({ step }: { step: FirstStep }) {
  return (
    <li data-testid={`first-step-${step.id}`} data-done={step.done} className="flex items-start gap-3 py-3">
      <span
        aria-hidden
        className={`mt-0.5 grid h-5 w-5 flex-none place-items-center rounded-full border ${
          step.done ? "border-emerald-400/60 bg-emerald-500/20 text-emerald-300" : "border-white/25 text-transparent"
        }`}
      >
        <Check size={12} strokeWidth={3} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
        <div className="min-w-0 flex-1">
          <p className={`text-sm font-medium ${step.done ? "text-white/45" : ""}`}>
            {step.title}
            <span className="sr-only">{step.done ? " (erledigt)" : " (offen)"}</span>
          </p>
          {!step.done && <p className="mt-0.5 break-words text-xs text-white/55">{step.text}</p>}
        </div>
        {!step.done && (
          <div className="flex-none">
            {step.to ? (
              <ButtonLink to={step.to} small>{step.action}</ButtonLink>
            ) : (
              <button
                type="button"
                className={stepButton}
                onClick={() => document.getElementById(step.scrollTo ?? "")?.scrollIntoView?.({ behavior: "smooth", block: "start" })}
              >
                {step.action}
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export function FirstStepsCard() {
  const queryClient = useQueryClient();
  const can = useAuthStore((s) => s.hasPermission);
  const canHosts = can("hosts.write") && can("hosts.read");
  const canExtensions = can("extensions.manage");
  const hosts = useHosts(canHosts);
  const extensions = useExtensions(canExtensions);
  const widgets = useWidgets();
  const { layout, isLoading: layoutLoading, error: layoutError } = useDashboardLayout();
  const preferences = usePreferences();
  const demo = useDemoStatus(can("hosts.read"));
  const [saving, setSaving] = useState(false);

  const loading =
    (canHosts && hosts.isLoading) || (canExtensions && extensions.isLoading) || widgets.isLoading || layoutLoading || preferences.isLoading || demo.isLoading;

  const steps = useMemo(() => {
    const catalog = widgets.data;
    const known = new Set((catalog ?? []).map((w) => `${w.ext_id}:${w.id}`));
    const placed = layout && catalog
      ? layout.items.filter((i) => !isHiddenItem(i) && known.has(`${i.ext_id}:${i.widget_id}`)).length
      : undefined;
    return buildFirstSteps({
      can,
      hosts: hosts.data,
      extensions: extensions.data,
      placedWidgets: layoutError ? undefined : placed,
      catalogWidgets: catalog?.length,
    });
  }, [can, hosts.data, extensions.data, widgets.data, layout, layoutError]);

  // Mit Beispieldaten waere die Liste irrefuehrend ("Server angelegt"): das Band oben fuehrt dann zurueck.
  if (loading || demo.data?.active || preferences.data?.first_steps_dismissed || steps.length === 0 || steps.every((s) => s.done)) return null;

  const done = steps.filter((s) => s.done).length;

  async function dismiss() {
    setSaving(true);
    // Sofort ausblenden; scheitert das Speichern, kommt die Karte beim naechsten Laden eben wieder.
    queryClient.setQueryData<Preferences>(["me", "preferences"], { first_steps_dismissed: true });
    try {
      await api.patch<Preferences>("/me/preferences", { first_steps_dismissed: true });
    } catch {
      // nichts weiter: die Karte ist hier ausgeblendet, beim naechsten Mal wird neu gefragt.
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel mx-4 mt-5 p-4 sm:mx-6 sm:p-5" aria-labelledby="first-steps-title" data-testid="first-steps">
      <div className="flex items-start justify-between gap-3">
        <h2 id="first-steps-title" className="text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Erste Schritte</h2>
        <button
          type="button"
          onClick={() => void dismiss()}
          disabled={saving}
          className="-mr-1 -mt-1 flex-none rounded-lg px-2 py-1 text-xs text-white/55 hover:bg-white/[0.06] hover:text-white disabled:opacity-50"
        >
          Ausblenden
        </button>
      </div>
      <p className="-mt-0.5 text-xs text-white/50">So ist Nodvard Deck startklar – ein Schritt nach dem anderen.</p>
      <div className="mt-3 flex items-center gap-3">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10" aria-hidden>
          <div className="accent-gradient h-full rounded-full transition-all" style={{ width: `${(done / steps.length) * 100}%` }} />
        </div>
        <p className="flex-none text-xs tabular-nums text-white/60">{done} von {steps.length} erledigt</p>
      </div>
      <ul className="mt-1 divide-y divide-white/[0.06]">
        {steps.map((s) => <StepRow key={s.id} step={s} />)}
      </ul>
      {canManageDemo(can) && hosts.data?.length === 0 && (
        <div data-testid="first-steps-demo" className="mt-2 flex flex-col gap-2 border-t border-white/[0.06] pt-3 sm:flex-row sm:items-center sm:gap-4">
          <p className="min-w-0 flex-1 text-xs text-white/55">
            Noch keinen Server zur Hand? Schau dir Nodvard Deck erst einmal gefüllt an. Die Beispieldaten löschst du mit einem Klick wieder.
          </p>
          <div className="flex flex-none flex-wrap"><DemoSeedButton small /></div>
        </div>
      )}
      {can("settings.write") && (
        <p data-testid="first-steps-backup" className="mt-2 flex items-start gap-2 border-t border-white/[0.06] pt-3 text-xs text-white/55">
          <ShieldCheck size={14} className="mt-0.5 flex-none text-white/45" />
          <span>
            Tipp zum Schluss: Sichere Nodvard Deck selbst, damit Server, Zugänge und Einstellungen nicht verloren gehen.{" "}
            <Link to="/settings/system" className="underline underline-offset-2 hover:text-white">Zur Sicherung (Einstellungen → System)</Link>
          </span>
        </p>
      )}
    </section>
  );
}
