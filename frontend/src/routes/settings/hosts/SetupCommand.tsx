/**
 * Der Einrichtungsbefehl, den man auf dem Server einfügt: Benutzer anlegen, öffentlichen Schlüssel eintragen (der
 * private verlässt den Tresor nie), auf Wunsch Gruppe `docker` und sudo ohne Passwort. Die Erweiterungen belegen die
 * Haken vor. Reichen die Hilfsskripte der Module, erlaubt sudo nur sie; die volle Regel gibt es dann unter „Erweitert“.
 * „Mit Passwort einrichten“ führt denselben Befehl selbst aus (`PasswordSetup`).
 */
import { useMemo, useRef, useState } from "react";

import { GuideLink } from "../../../components/GuideLink";
import { COPY_FAILED_TEXT } from "../../../lib/clipboard";
import { useRequirements, useSetup, type ConnectionCheck as CheckResult, type CredentialOut, type HostOut } from "../../../lib/hosts";
import { errorText } from "../ui";
import { CopyButton } from "./CopyButton";
import { PasswordSetup } from "./PasswordSetup";

const CHECKBOX = "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]";

type Way = "command" | "password";

const WAY_LABEL: Record<Way, string> = {
  command: "Befehl selbst einfügen",
  password: "Mit Passwort einrichten (Nodvard Deck führt den Befehl aus)",
};

export function SetupCommand({
  host, credential, onChecked,
}: {
  host: HostOut;
  credential: CredentialOut;
  /** Ergebnis der Prüfung nach „Mit Passwort einrichten“. */
  onChecked?: (result: CheckResult) => void;
}) {
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
  const [fullChoice, setFullChoice] = useState(false);
  const [groupsOff, setGroupsOff] = useState<string[]>([]);
  const sudo = !isRoot && (sudoChoice ?? rootReasons.length > 0);
  const chosenGroups = isRoot ? [] : groups.map(([g]) => g).filter((g) => !groupsOff.includes(g));

  const ready = requirements.isSuccess || requirements.isError;
  const setup = useSetup(host.id, credential.id, { sudo, fullSudo: fullChoice, groups: chosenGroups, enabled: ready });
  // Welche Regel kommt, steht erst mit dem Befehl fest; vorher keinen der Hinweise dazu zeigen.
  const ruleKnown = sudo && setup.data !== undefined;
  // Braucht hier kein Modul root, richtet der Befehl trotz Haken kein sudo ein; nur „Volle Rechte“ ändert das.
  const noRootNeeded = ruleKnown && setup.data?.sudo === false;
  // Ein älterer Server ohne die Angabe richtet die volle Regel ein.
  const fullRule = ruleKnown && !noRootNeeded && (setup.data?.full_sudo ?? true);
  const offersHelpers = (setup.data?.helpers ?? []).length > 0;
  // Ein Modul braucht root ohne Hilfsskript: Dann gehört die volle Regel dazu, auch ohne Häkchen.
  const fullForced = sudo && !fullChoice && setup.data?.full_sudo === true;

  const area = useRef<HTMLTextAreaElement>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const [way, setWay] = useState<Way>("command");
  const loading = !ready || setup.isLoading || setup.isFetching;

  // Solange die Regel noch nicht feststeht, bleibt die Warnung stehen.
  const powerful = (sudo && !ruleKnown) || fullRule || chosenGroups.length > 0;
  return (
    <div className="space-y-3" data-testid="setup-command">
      <h4 className="text-sm font-semibold">Server für diesen Schlüssel einrichten</h4>

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
          {ruleKnown && (noRootNeeded ? (
            <p className="text-xs text-white/45" data-testid="setup-sudo-note">
              Kein Modul braucht auf diesem Server root. Der Befehl richtet darum kein sudo ein; brauchst du sudo für
              eigene Befehle über Nodvard Deck, kreuze unter „Erweitert“ „Volle Rechte“ an.
            </p>
          ) : fullRule ? (
            <p className="text-xs text-white/45" data-testid="setup-sudo-note">
              Nodvard Deck startet einige root-Befehle über „sudo sh -c“, darum braucht es die volle sudo-Regel.
              Deshalb bekommt nur dieser eine Benutzer die Rechte, und er kann sich nur mit dem Schlüssel anmelden.
            </p>
          ) : (
            <p className="text-xs text-white/45" data-testid="setup-sudo-note">
              Kein volles root: sudo erlaubt nur die Hilfsskripte der Module, jedes mit einer eigenen Regel. Was ein
              Skript darf, steht unter „Was macht der Befehl?“.
            </p>
          ))}
          {sudo && (fullChoice || offersHelpers || noRootNeeded) && (
            <details className="text-sm" data-testid="setup-advanced">
              <summary className="cursor-pointer text-xs text-white/55">Erweitert</summary>
              <label className="mt-2 flex cursor-pointer items-start gap-2.5">
                <input
                  type="checkbox" className={CHECKBOX} checked={fullChoice || fullForced} disabled={fullForced}
                  onChange={(e) => setFullChoice(e.target.checked)}
                />
                <span>
                  Volle Rechte: sudo ohne Passwort für alles
                  <span className="text-white/50">
                    {fullForced
                      ? " (nötig, weil ein Modul root braucht und noch kein Hilfsskript hat)"
                      : " (nur nötig, wenn du über Nodvard Deck eigene Befehle mit sudo startest)"}
                  </span>
                </span>
              </label>
            </details>
          )}
        </div>
      )}

      <fieldset className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-4">
        <legend className="sr-only">Wie willst du einrichten?</legend>
        {(["command", "password"] as Way[]).map((w) => (
          <label key={w} className="flex cursor-pointer items-start gap-2 text-sm">
            <input type="radio" name={`einrichten-${credential.id}`} className={CHECKBOX} checked={way === w} onChange={() => setWay(w)} />
            <span>{WAY_LABEL[w]}</span>
          </label>
        ))}
      </fieldset>

      {setup.isError ? (
        <p role="alert" className="text-sm text-red-300">{errorText(setup.error)}</p>
      ) : (
        <>
          {way === "password" ? (
            // Bleibt stehen, während der Befehl nach einem Haken neu geladen wird (sonst wären die Eingaben weg). Die
            // Haken gelten hier genauso; das Backend baut daraus denselben Befehl wie oben.
            <PasswordSetup
              host={host} credential={credential} sudo={sudo} fullSudo={fullChoice} groups={chosenGroups} ready={ready}
              knownNotes={setup.data?.notes ?? []} onChecked={onChecked}
            />
          ) : loading ? (
            <p className="text-sm text-white/50">Befehl wird erstellt …</p>
          ) : setup.data ? (
            <>
              <p className="text-sm text-white/70" data-testid="setup-howto">
                So geht’s: Auf dem Server anmelden (per ssh, am Bildschirm oder über die Konsole deiner VM-Verwaltung), den Befehl einfügen, Enter.
                Danach hier „Verbindung prüfen“.
              </p>
              <p className="text-xs text-white/55" data-testid="setup-guide">
                Wofür die Rechte nötig sind und was sie erlauben: <GuideLink section="rootRights">Anleitung, Abschnitt „Root-Rechte ohne Passwort und Gruppe docker“</GuideLink>
              </p>
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
            </>
          ) : null}
          {!loading && setup.data && (
            <>
              <details className="rounded-lg border border-white/[0.08] px-3 py-2">
                <summary className="cursor-pointer text-sm text-white/70">Was macht der Befehl?</summary>
                <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-white/70">{setup.data.script}</pre>
                {(setup.data.helpers ?? []).map((h) => (
                  <details key={h.ext_id} className="mt-2" data-testid="setup-helper">
                    <summary className="cursor-pointer text-xs text-white/60">
                      Hilfsskript für {h.module} (Version {h.version}), kommt nach {h.path}
                    </summary>
                    <p className="mt-1 break-all font-mono text-[11px] text-white/45">sha256 {h.sha256}</p>
                    <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-white/70">{h.text}</pre>
                  </details>
                ))}
              </details>
              {/* Hinweise zu root/sudo stehen schon oben bei den Haken: nicht doppelt zeigen. */}
              {setup.data.notes.filter((n) => !["praktisch", "sudo sh -c", "kein volles root", "kein sudo ein"].some((part) => n.includes(part))).map((n) => (
                <p key={n} className="text-xs text-white/55">{n}</p>
              ))}
            </>
          )}
        </>
      )}
    </div>
  );
}
