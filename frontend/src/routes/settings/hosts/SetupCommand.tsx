/**
 * Der Einrichtungsbefehl: EIN Befehl, den man auf dem Server einfuegt. Er legt (falls noetig)
 * den Benutzer an, traegt den oeffentlichen Schluessel ein und richtet auf Wunsch die Gruppe
 * `docker` und sudo ohne Passwort ein. Was davon noetig ist, melden die Erweiterungen
 * (`GET /hosts/{id}/requirements`); die Haken sind danach vorbelegt.
 *
 * Der Befehl enthaelt nur den OEFFENTLICHEN Schluessel -- der private verlaesst den Tresor nie.
 */
import { useMemo, useRef, useState } from "react";

import { COPY_FAILED_TEXT } from "../../../lib/clipboard";
import { useRequirements, useSetup, type CredentialOut, type HostOut } from "../../../lib/hosts";
import { errorText } from "../ui";
import { CopyButton } from "./CopyButton";

const CHECKBOX = "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]";

export function SetupCommand({ host, credential }: { host: HostOut; credential: CredentialOut }) {
  const requirements = useRequirements(host.id);
  const isRoot = credential.username === "root";

  // Was die Erweiterungen auf dem Server brauchen.
  const { rootReasons, groups } = useMemo(() => {
    const reasons: string[] = [];
    const byGroup = new Map<string, string[]>();
    for (const r of requirements.data ?? []) {
      if (r.needs_root) {
        const reason = r.root_reason ?? r.label;
        if (!reasons.includes(reason)) reasons.push(reason);
      }
      if (r.unix_group) byGroup.set(r.unix_group, [...(byGroup.get(r.unix_group) ?? []), r.label]);
    }
    return { rootReasons: reasons, groups: [...byGroup.entries()] };
  }, [requirements.data]);

  // Vorbelegung: alles an, was eine Erweiterung verlangt. `null` = noch nicht angefasst.
  const [sudoChoice, setSudoChoice] = useState<boolean | null>(null);
  const [groupsOff, setGroupsOff] = useState<string[]>([]);
  const sudo = !isRoot && (sudoChoice ?? rootReasons.length > 0);
  const chosenGroups = isRoot ? [] : groups.map(([g]) => g).filter((g) => !groupsOff.includes(g));

  const ready = requirements.isSuccess || requirements.isError;
  const setup = useSetup(host.id, credential.id, { sudo, groups: chosenGroups, enabled: ready });

  const area = useRef<HTMLTextAreaElement>(null);
  const [copyFailed, setCopyFailed] = useState(false);

  const powerful = sudo || chosenGroups.length > 0;
  return (
    <div className="space-y-3" data-testid="setup-command">
      <h4 className="text-sm font-semibold">Diesen Befehl auf dem Server ausführen</h4>

      {!isRoot && (groups.length > 0 || requirements.data) && (
        <div className="space-y-2">
          <label className="flex cursor-pointer items-start gap-2.5 text-sm">
            <input type="checkbox" className={CHECKBOX} checked={sudo} onChange={(e) => setSudoChoice(e.target.checked)} />
            <span>
              Root-Rechte ohne Passwort
              {rootReasons.length > 0 && <span className="text-white/50"> (nötig für: {rootReasons.join(", ")})</span>}
            </span>
          </label>
          {groups.map(([group, labels]) => (
            <label key={group} className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input
                type="checkbox" className={CHECKBOX} checked={!groupsOff.includes(group)}
                onChange={(e) => setGroupsOff(e.target.checked ? groupsOff.filter((g) => g !== group) : [...groupsOff, group])}
              />
              <span>
                Zur Gruppe „{group}“ hinzufügen <span className="text-white/50">(nötig für: {labels.join(", ")})</span>
              </span>
            </label>
          ))}
          {powerful && (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              Das ist praktisch root: Wer in der Gruppe docker ist oder sudo ohne Passwort darf, kann auf dem Server alles tun.
            </p>
          )}
          <p className="text-xs text-white/45">
            Eine enger begrenzte sudo-Regel funktioniert mit Nodvard Deck nicht, weil es root-Befehle über „sudo sh -c“ startet.
            Darum bekommt nur dieser eine Benutzer die Rechte, und er kann sich nur mit dem Schlüssel anmelden.
          </p>
        </div>
      )}

      {!ready || setup.isLoading || setup.isFetching ? (
        <p className="text-sm text-white/50">Befehl wird erstellt …</p>
      ) : setup.isError ? (
        <p role="alert" className="text-sm text-red-300">{errorText(setup.error)}</p>
      ) : setup.data ? (
        <>
          <textarea
            ref={area} readOnly rows={4} aria-label="Einrichtungsbefehl" value={setup.data.one_liner}
            onFocus={(e) => e.currentTarget.select()}
            className="w-full resize-y rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs break-all text-white/90 outline-none focus:border-[var(--color-accent)]"
          />
          <div className="flex flex-wrap items-center gap-3">
            <CopyButton
              text={setup.data.one_liner}
              onFailed={() => { setCopyFailed(true); area.current?.focus(); area.current?.select(); }}
            />
            {copyFailed && <p role="alert" className="text-xs text-amber-200">{COPY_FAILED_TEXT}</p>}
          </div>
          <details className="rounded-lg border border-white/[0.08] px-3 py-2">
            <summary className="cursor-pointer text-sm text-white/70">Was macht der Befehl?</summary>
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-white/70">{setup.data.script}</pre>
          </details>
          {setup.data.notes.filter((n) => !n.includes("praktisch")).map((n) => (
            <p key={n} className="text-xs text-white/55">{n}</p>
          ))}
          <p className="text-xs text-white/55">
            Auf dem Server anmelden (per ssh, am Bildschirm oder über die Konsole deiner VM-Verwaltung), Befehl einfügen, Enter. Danach hier „Verbindung prüfen“.
          </p>
        </>
      ) : null}
    </div>
  );
}
