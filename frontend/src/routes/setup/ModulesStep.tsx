/**
 * Assistent, Schritt „Was willst du nutzen?“: die installierten Module als Kacheln mit Schalter.
 * Ein Klick schaltet das Modul ein oder aus (`POST /extensions/{id}/enable|disable`, dieselben
 * Endpunkte wie Einstellungen -> Erweiterungen). Kein Modul ist Pflicht.
 *
 * Gruppe und Reihenfolge kommen aus dem Manifest der Module (`category`, `sort_order`); der Kern
 * kennt kein Modul beim Namen. Die Angaben, die ein Modul braucht (Adressen, Zugangsdaten), kommen
 * erst danach unter Einstellungen -> Erweiterungen -- die Kachel sagt es.
 */
import { AlertTriangle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Icon } from "../../components/Icon";
import { api } from "../../lib/api";
import { Button, NoticeLine, Toggle, errorText, type Notice } from "../settings/ui";
import { StepNav } from "./StepNav";

interface ExtensionRow {
  id: string;
  state: string;
  name: string | null;
  description: string | null;
  icon: string | null;
  last_error: string | null;
  category?: string | null;
  sort_order?: number;
  /** Nur bei eingeschalteten Modulen: Pflichtangaben fehlen. */
  needs_setup?: boolean;
}

/** Reihenfolge und Überschrift der Gruppen; alles Unbekannte kommt unter „Weitere Module“. */
const GROUPS: { id: string; title: string; hint?: string }[] = [
  { id: "servers", title: "Für deine Server", hint: "Zustand, Zugriff und Verwaltung deiner Rechner und Dienste." },
  { id: "security", title: "Sicherheit" },
  { id: "tools", title: "Werkzeuge" },
  { id: "connections", title: "Verbindungen zu anderen Diensten" },
  { id: "example", title: "Zum Ausprobieren" },
];
const OTHER_GROUP = { id: "other", title: "Weitere Module" };

/** Beispiel-Module für Entwickler (z. B. „Hello World“) gehören nicht in den Assistenten: Wer neu anfängt, würde sich
 * fragen, wozu sie da sind. Unter Einstellungen → Erweiterungen bleiben sie sichtbar. */
export function forEveryone(rows: ExtensionRow[]): ExtensionRow[] {
  return rows.filter((r) => r.category !== "example");
}

/**
 * Kurzfassung der Beschreibung fuer die Kachel: der erste Satz, und steht darin ein Doppelpunkt
 * („Virenschutz fuer alle Server: ClamAV-Scans, ...“), nur das davor. Abkuerzungen wie „z. B.“ oder
 * „d. h.“ (Punkt nach einem Einzelbuchstaben) beenden keinen Satz. Der volle Text bleibt im Tooltip.
 */
export function shortDescription(text: string | null): string {
  if (!text) return "";
  let first = text;
  const ends = /[.!?](?=\s|$)/g;
  for (let m = ends.exec(text); m; m = ends.exec(text)) {
    const letterBefore = /\p{L}/u.test(text.charAt(m.index - 1));
    const singleLetter = m[0] === "." && letterBefore && !/\p{L}/u.test(text.charAt(m.index - 2));
    if (!singleLetter) {
      first = text.slice(0, m.index + 1);
      break;
    }
  }
  const colon = first.indexOf(": ");
  return colon >= 20 ? `${first.slice(0, colon)}.` : first;
}

export function groupModules(rows: ExtensionRow[]): { id: string; title: string; hint?: string; rows: ExtensionRow[] }[] {
  const known = new Set(GROUPS.map((g) => g.id));
  const byName = (a: ExtensionRow, b: ExtensionRow) =>
    (a.sort_order ?? 100) - (b.sort_order ?? 100) || (a.name ?? a.id).localeCompare(b.name ?? b.id);
  return [...GROUPS, OTHER_GROUP]
    .map((g) => ({
      ...g,
      rows: rows.filter((r) => (known.has(r.category ?? "") ? r.category === g.id : g.id === "other")).sort(byName),
    }))
    .filter((g) => g.rows.length > 0);
}

export function ModulesStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const [rows, setRows] = useState<ExtensionRow[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  async function load() {
    try {
      setRows(await api.get<ExtensionRow[]>("/extensions"));
    } catch (err) {
      setRows((old) => old ?? []);
      setNotice({ kind: "error", text: `Die Module konnten nicht geladen werden: ${errorText(err)}` });
    }
  }
  useEffect(() => { void load(); }, []);

  async function toggle(ext: ExtensionRow, enable: boolean) {
    const label = ext.name ?? ext.id;
    setBusyId(ext.id);
    setNotice(null);
    try {
      const result = await api.post<Partial<ExtensionRow> | undefined>(`/extensions/${ext.id}/${enable ? "enable" : "disable"}`);
      // Der Server antwortet auch dann mit 200, wenn das Modul beim Starten scheitert (state "error").
      if (enable && result?.state && result.state !== "enabled") {
        setNotice({ kind: "error", text: `„${label}“ ließ sich nicht einschalten${result.last_error ? `: ${result.last_error}` : "."}` });
      }
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    }
    // Neu laden: erst die Liste weiß, ob das Modul noch Angaben braucht (needs_setup).
    await load();
    setBusyId(null);
  }

  const groups = useMemo(() => groupModules(forEveryone(rows ?? [])), [rows]);
  const enabledCount = (rows ?? []).filter((r) => r.state === "enabled").length;
  const needSetup = (rows ?? []).filter((r) => r.state === "enabled" && r.needs_setup).length;

  return (
    <div data-testid="setup-modules">
      <p className="mb-4 text-sm text-white/70">
        Wähle die Module, die du nutzen möchtest, mit einem Klick auf den Schalter. Kein Modul ist Pflicht, und alles lässt sich später
        unter Einstellungen → Erweiterungen ändern. Ausgeschaltete Module verschwinden aus dem Menü.
      </p>
      <NoticeLine notice={notice} />
      {rows === null ? (
        <p className="text-sm text-white/50">Lade …</p>
      ) : rows.length === 0 ? (
        <p className="rounded-lg border border-white/10 p-3 text-sm text-white/60">Es sind keine Module installiert.</p>
      ) : (
        <div className="space-y-5">
          {groups.map((group) => (
            <section key={group.id} aria-label={group.title}>
              <h2 className="text-xs font-semibold uppercase tracking-wide text-white/45">{group.title}</h2>
              <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                {group.rows.map((ext) => {
                  const enabled = ext.state === "enabled";
                  const label = ext.name ?? ext.id;
                  const unusable = ext.state === "incompatible" || ext.state === "uninstalling";
                  return (
                    <li key={ext.id} data-testid={`module-${ext.id}`}>
                      <label
                        className={`flex h-full cursor-pointer items-start gap-3 rounded-lg border p-3 transition ${
                          enabled
                            ? "border-[color-mix(in_srgb,var(--color-accent)_55%,transparent)] bg-[color-mix(in_srgb,var(--color-accent)_8%,transparent)]"
                            : "border-white/10 bg-black/20 hover:border-white/20"
                        }`}
                      >
                        <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-white/[0.06] text-[var(--color-accent)]">
                          <Icon name={ext.icon} size={17} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium">{label}</span>
                          {ext.description && (
                            <span title={ext.description} className="mt-0.5 block break-words text-xs text-white/55">
                              {shortDescription(ext.description)}
                            </span>
                          )}
                          {enabled && ext.needs_setup && (
                            <span data-testid={`needs-setup-${ext.id}`} className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200/90">
                              <AlertTriangle size={12} className="mt-0.5 flex-none" /> Braucht danach noch Angaben
                            </span>
                          )}
                          {ext.state === "error" && ext.last_error && <span className="mt-1 block text-xs text-red-300">{ext.last_error}</span>}
                          {ext.state === "incompatible" && <span className="mt-1 block text-xs text-amber-200/90">Passt nicht zu dieser Version.</span>}
                        </span>
                        <Toggle
                          checked={enabled}
                          disabled={busyId !== null || unusable}
                          label={`${label} ${enabled ? "ausschalten" : "einschalten"}`}
                          onChange={(v) => void toggle(ext, v)}
                        />
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}

      <p className="mt-5 rounded-lg border border-white/10 bg-black/20 p-3 text-xs text-white/60" data-testid="modules-hint">
        {enabledCount > 0 ? `${enabledCount} ${enabledCount === 1 ? "Modul" : "Module"} eingeschaltet. ` : ""}
        {needSetup > 0
          ? `${needSetup} ${needSetup === 1 ? "braucht" : "brauchen"} danach noch Angaben. `
          : ""}
        Angaben wie Adressen, Zugangsdaten oder Tokens trägst du danach unter Einstellungen → Erweiterungen ein. Die Karte „Erste Schritte“
        im Cockpit erinnert dich daran.
      </p>

      <StepNav onBack={onBack}>
        <Button variant="primary" onClick={onNext}>{enabledCount > 0 ? "Weiter" : "Überspringen"}</Button>
      </StepNav>
    </div>
  );
}
