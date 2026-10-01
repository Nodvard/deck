/**
 * Über Nodvard Deck: welche Version läuft, und was in jeder Version passiert ist
 * (Änderungsprotokoll). Die Daten liegen als kleine Dateien im Backend
 * (backend/src/nodvard_deck/changelog/), jeder PR trägt seine Einträge selbst ein.
 */
import { ChevronRight } from "lucide-react";
import { useEffect } from "react";

import {
  KIND_LABELS,
  KIND_ORDER,
  changelogFingerprint,
  formatDate,
  frontendBuildDate,
  markChangelogSeen,
  useChangelog,
  type ChangeKind,
  type ChangelogEntry,
  type ChangelogVersion,
} from "../../lib/changelog";
import { NOVNC_SOURCE_URL, NOVNC_VERSION, THIRD_PARTY_LICENSES_IMAGE_PATH, THIRD_PARTY_LICENSES_URL } from "../../lib/thirdParty";
import { Badge, Card, NoticeLine, PageHeader, errorText } from "./ui";

const KIND_TONE: Record<ChangeKind, "good" | "accent" | "warn" | "bad"> = {
  neu: "good",
  verbessert: "accent",
  behoben: "warn",
  sicherheit: "bad",
};

/** Einträge nach Art gruppiert (feste Reihenfolge), leere Gruppen entfallen. */
function EntryGroups({ entries }: { entries: ChangelogEntry[] }) {
  return (
    <div className="space-y-4">
      {KIND_ORDER.map((kind) => {
        const items = entries.filter((entry) => entry.kind === kind);
        if (items.length === 0) return null;
        return (
          <section key={kind} aria-label={KIND_LABELS[kind]}>
            <h4>
              <Badge tone={KIND_TONE[kind]}>{KIND_LABELS[kind]}</Badge>
            </h4>
            <ul className="mt-2 space-y-2">
              {items.map((entry, index) => (
                <li key={index} className="break-words text-sm leading-relaxed text-white/80">
                  {entry.text}
                  {entry.prs.length > 0 && (
                    <span className="ml-1.5 whitespace-nowrap text-[11px] text-white/35">{entry.prs.map((n) => `#${n}`).join(" ")}</span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/** Eine Version als Karte; nur die neueste ist aufgeklappt, die älteren per Klick. */
function VersionCard({ release, running, open }: { release: ChangelogVersion; running: boolean; open: boolean }) {
  return (
    <details open={open} className="panel group mb-5 overflow-hidden">
      <summary className="flex cursor-pointer list-none items-start gap-3 px-5 py-4 [&::-webkit-details-marker]:hidden">
        <ChevronRight size={16} className="mt-0.5 flex-none text-white/40 transition-transform group-open:rotate-90" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">
            Version {release.version} · {formatDate(release.date)}
            {running && (
              <span className="ml-2 align-middle">
                <Badge tone="accent">Läuft gerade</Badge>
              </span>
            )}
          </h3>
          {release.title && <p className="mt-0.5 text-xs text-white/50">{release.title}</p>}
        </div>
        <span className="hidden flex-none text-xs text-white/35 sm:inline">{release.entries.length} Änderungen</span>
      </summary>
      <div className="border-t border-white/[0.06] px-5 py-4">
        <EntryGroups entries={release.entries} />
      </div>
    </details>
  );
}

export function AboutSettings(): JSX.Element {
  const { data, error, isPending } = useChangelog();

  // Wer die Seite öffnet, kennt den Stand -- die Neu-Markierung in der Seitenleiste verschwindet.
  useEffect(() => {
    if (data && typeof data.current === "string") markChangelogSeen(changelogFingerprint(data));
  }, [data]);

  const built = frontendBuildDate();

  return (
    <>
      <PageHeader title="Über Nodvard Deck" description="Welche Version läuft, was sich in den Versionen geändert hat, dazu Lizenz und Marken." />

      {error && <NoticeLine notice={{ kind: "error", text: `Das Änderungsprotokoll konnte nicht geladen werden: ${errorText(error)}` }} />}
      {isPending && !error && <p className="text-sm text-white/50">Wird geladen …</p>}

      {data && (
        <>
          <Card title="Diese Installation">
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
              <dt className="text-white/50">Version</dt>
              <dd className="font-medium">{data.current}</dd>
              {data.build && (
                <>
                  <dt className="text-white/50">Build</dt>
                  <dd className="break-all">{data.build}</dd>
                </>
              )}
              {built && (
                <>
                  <dt className="text-white/50">Oberfläche gebaut</dt>
                  <dd>{built.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}</dd>
                </>
              )}
            </dl>
          </Card>

          {data.unreleased.length > 0 && (
            <Card title="Noch ohne Versionsnummer" description="Schon eingebaut, aber noch in keiner Version enthalten.">
              <EntryGroups entries={data.unreleased} />
            </Card>
          )}

          {data.versions.map((release, index) => (
            <VersionCard key={release.version} release={release} running={release.version === data.current} open={index === 0} />
          ))}
          {data.versions.length === 0 && <p className="text-sm text-white/50">Es gibt noch keine Einträge im Änderungsprotokoll.</p>}
        </>
      )}

      <Card title="Lizenz und Marken">
        <p className="text-sm leading-relaxed text-white/80">
          Nodvard Deck steht unter der PolyForm Noncommercial License 1.0.0: für private und andere nicht-kommerzielle Zwecke frei, kommerzielle Nutzung nur mit gesonderter Erlaubnis (Anfragen: kontakt@nodvard.com). Quellcode:{" "}
          <a href="https://github.com/nodvard/deck" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-white">
            github.com/nodvard/deck
          </a>
        </p>
        <p className="mt-3 text-sm leading-relaxed text-white/80">
          Die mitgelieferte Fremdsoftware und ihre Lizenzen findest du in der Datei{" "}
          <a href={THIRD_PARTY_LICENSES_URL} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-white">
            THIRD_PARTY_LICENSES
          </a>{" "}
          im Quellcode, im Image unter <code className="break-all text-white/90">{THIRD_PARTY_LICENSES_IMAGE_PATH}</code>.
        </p>
        <p className="mt-3 text-sm leading-relaxed text-white/80">
          Die grafische Konsole nutzt noVNC {NOVNC_VERSION} (unverändert, Mozilla Public License 2.0). Den Quellcode genau dieser
          Version findest du unter{" "}
          <a href={NOVNC_SOURCE_URL} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-white">
            github.com/novnc/noVNC
          </a>
          .
        </p>
        <p className="mt-3 text-xs leading-relaxed text-white/50">
          Proxmox, Docker, Portainer, Synology, Unraid, Nextcloud, Pi-hole, Nginx Proxy Manager, ntfy, Ollama, ClamAV, Lynis, Fail2ban,
          Raspberry Pi und andere genannte Namen sind Marken ihrer jeweiligen Inhaber. Nodvard Deck steht in keiner Verbindung zu ihnen.
        </p>
      </Card>
    </>
  );
}
