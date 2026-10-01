/**
 * Karte „Updates“ (Einstellungen, System): ob es eine neuere Version gibt und wie man sie einspielt.
 *
 * - `GET /system/updates` (Recht `system.read`) liefert den letzten bekannten Stand, ohne Anfrage ins Netz.
 * - „Jetzt suchen“: `POST /system/updates/check`, höchstens einmal pro Minute (sonst 429 mit Hinweis).
 * - Schalter „täglich suchen“ und Kanal (`system.update_check.enabled`/`.channel`) nur mit `settings.write`.
 *   Solange gesucht oder gespeichert wird, sind Schalter, Kanal und Knopf gesperrt: Sonst überschriebe die
 *   Antwort der einen Anfrage das Ergebnis der anderen.
 * - Reiter mit Anleitungen je Umgebung. Eingespielt wird nicht von hier: das macht die Umgebung selbst
 *   (Compose, Portainer …). Vor einem Umbau der Datenbank legt Nodvard Deck beim Start eine Kopie an (nur bei
 *   SQLite, der Standard-Datenbank). `GET /system/info` (freiwillig, ohne Fehlermeldung) liefert dazu die Datenbank
 *   und die Kopien vor Updates; daraus kommt nach einem Update die Version von vorher für den Rückweg.
 */
import { ArrowUpCircle, CheckCircle2, ExternalLink, Info, RefreshCw, WifiOff } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { api } from "../../lib/api";
import { formatWhen } from "../../lib/backups";
import { useAuthStore } from "../../state/auth";
import type { PreUpdateCopy } from "./UpdateCopiesCard";
import { Button, Card, NoticeLine, Toggle, errorText, inputClass, type Notice } from "./ui";

export interface UpdateStatus {
  current: string;
  latest: string | null;
  latest_digest?: string | null;
  available: boolean;
  channel: "stable" | "beta";
  enabled: boolean;
  checked_at: string | null;
  attempted_at?: string | null;
  source: "live" | "cache" | "offline";
  error?: string | null;
  official_image: boolean;
  image?: string | null;
  official_image_name: string;
  helper: boolean;
  release_notes_url?: string | null;
}

/** Was die Karte aus `GET /system/info` nutzt (alles freiwillig). */
interface SystemInfo {
  database?: string;
  pre_update_copies?: PreUpdateCopy[];
}

export type GuideId = "compose" | "desktop" | "portainer" | "synology" | "unraid" | "deploy-pi";

interface Guide {
  id: GuideId;
  label: string;
  steps: ReactNode[];
  /** Kurzer Hinweis unter den Schritten (z. B. anderer Dateiname). */
  note?: ReactNode;
  back: ReactNode;
}

export interface GuideOptions {
  /** Die Version, die eingespielt werden soll -- nur bei einem echten Update (`available`), sonst `null`
   * (dann steht ein Platzhalter da: nie eine Anleitung zurück auf eine ältere Version). */
  target: string | null;
  /** Die Version von vor dem Update, wenn sie sicher bekannt ist (Kopie vor dem Update hin zur laufenden Version). */
  previous?: string | null;
}

/** Die Installation mit dem fertigen Image legt die Datei als `compose.yml` ab (docs/11 1.1); Compose findet sie dann selbst. */
const compose = (command: string) => `docker compose ${command}`;

function Cmd({ children }: { children: ReactNode }) {
  return <code className="rounded bg-black/30 px-1.5 py-0.5 text-[12px] text-white/85">{children}</code>;
}

/** `0.6.0-rc1` -> `0.6` (die Reihe, die release.yml als Tag mit veröffentlicht). */
function seriesOf(version: string): string {
  return version.split("-")[0].split(".").slice(0, 2).join(".");
}

export function isPrerelease(version: string | null | undefined): boolean {
  return !!version && version.includes("-");
}

export function guides(image: string, current: string, { target, previous = null }: GuideOptions): Guide[] {
  const next = target ?? "<neue Version>";
  const pinned = (field: ReactNode) =>
    target !== null && isPrerelease(target) ? (
      <>
        Vorabversionen gibt es nicht unter <Cmd>:latest</Cmd>: Trag bei {field} genau <Cmd>{`${image}:${target}`}</Cmd> ein. Ist die fertige
        Version da, kannst du wieder <Cmd>:latest</Cmd> nehmen.
      </>
    ) : (
      <>
        Steht bei {field} eine feste Version (z. B. <Cmd>{`:${current}`}</Cmd>) oder eine Reihe wie <Cmd>{`:${seriesOf(current)}`}</Cmd> statt{" "}
        <Cmd>:latest</Cmd>, trag dort zuerst die neue ein: <Cmd>{`${image}:${next}`}</Cmd>.
      </>
    );
  const imageField = <Cmd>image:</Cmd>;
  const fileHint = (
    <>
      Im Ordner mit der Datei <Cmd>compose.yml</Cmd> ausführen (unter Linux ggf. <Cmd>sudo</Cmd> davor). Heißt deine Datei anders (z. B.{" "}
      <Cmd>compose.standalone.yml</Cmd> aus einer älteren Anleitung), häng <Cmd>-f</Cmd> und den Dateinamen an: <Cmd>docker compose -f compose.standalone.yml …</Cmd>.
    </>
  );
  // Rückweg: Vor dem Update ist die laufende Version die, zu der man zurückwill -- also jetzt notieren. Danach läuft
  // schon die neue; dann nur eine sicher bekannte Vorversion nennen, sonst neutral beschreiben.
  const keep =
    target !== null ? (
      <>
        Notier dir vor dem Update die jetzige Version: <Cmd>{`${image}:${current}`}</Cmd>.{" "}
      </>
    ) : null;
  const previousRef: ReactNode =
    target !== null ? (
      <>diese Version wieder</>
    ) : previous ? (
      <>
        die Version von vor dem Update (<Cmd>{`${image}:${previous}`}</Cmd>)
      </>
    ) : (
      <>die Version, die vor dem Update lief (jetzt läuft {current}),</>
    );
  const composeBack = (
    <>
      {keep}In der Compose-Datei bei <Cmd>image:</Cmd> {previousRef} eintragen und <Cmd>{compose("up -d")}</Cmd> ausführen.
    </>
  );
  return [
    {
      id: "compose",
      label: "Docker Compose",
      steps: [
        <>Im Ordner mit der Compose-Datei ein Terminal öffnen. {pinned(imageField)}</>,
        <>Neue Version holen: <Cmd>{compose("pull")}</Cmd></>,
        <>Neu starten: <Cmd>{compose("up -d")}</Cmd> – nach etwa einer Minute ist das Dashboard wieder da.</>,
      ],
      note: fileHint,
      back: composeBack,
    },
    {
      id: "desktop",
      label: "Docker Desktop",
      steps: [
        <>Docker Desktop öffnen und unten rechts „Terminal“ wählen (oder PowerShell bzw. das Terminal deines Rechners).</>,
        <>In den Ordner mit der Compose-Datei wechseln. {pinned(imageField)}</>,
        <>Nacheinander <Cmd>{compose("pull")}</Cmd> und <Cmd>{compose("up -d")}</Cmd> ausführen. Unter „Containers“ läuft danach die neue Version.</>,
      ],
      note: fileHint,
      back: composeBack,
    },
    {
      id: "portainer",
      label: "Portainer",
      steps: [
        <>Links „Stacks“ öffnen und den Stack mit Nodvard Deck anklicken.</>,
        <>Oben „Editor“ wählen. {pinned(imageField)}</>,
        <>Unten „Update the stack“ klicken, im Fenster „Re-pull image“ einschalten und bestätigen.</>,
      ],
      back: (
        <>
          {keep}Im „Editor“ bei <Cmd>image:</Cmd> {previousRef} eintragen und erneut „Update the stack“ klicken.
        </>
      ),
    },
    {
      id: "synology",
      label: "Synology",
      steps: [
        <>Container Manager öffnen, links „Image“ wählen.</>,
        <>Steht bei <Cmd>{image}</Cmd> „Update verfügbar“, darauf klicken und „Aktualisieren“ bestätigen. Der Container startet danach mit der neuen Version.</>,
        <>Läuft Nodvard Deck als „Projekt“: unter „Projekt“ das Projekt stoppen und über „Aktion“ → „Erstellen“ neu anlegen; dabei wird die neue Version geladen. {pinned(imageField)}</>,
      ],
      back: (
        <>
          {keep}Im Projekt (bzw. in den Einstellungen des Containers) als Image {previousRef} eintragen und das Projekt neu erstellen.
        </>
      ),
    },
    {
      id: "unraid",
      label: "Unraid",
      steps: [
        <>Oben den Reiter „Docker“ öffnen.</>,
        <>Unten „Check for Updates“ klicken.</>,
        <>Bei Nodvard Deck erscheint „apply update“ – anklicken und mit „Apply update“ bestätigen.</>,
      ],
      note: pinned(<>„Repository“ (Container bearbeiten: „Edit“, danach „Apply“)</>),
      back: (
        <>
          {keep}Den Container bearbeiten („Edit“), bei „Repository“ {previousRef} eintragen und „Apply“ klicken.
        </>
      ),
    },
    {
      id: "deploy-pi",
      label: "deploy_pi.sh",
      steps: [
        <>Für ein selbst gebautes Image, das vom PC auf einen Raspberry Pi geht. Docker Desktop muss laufen.</>,
        <>Im Repository den neuen Stand holen: <Cmd>git pull</Cmd> (oder genau diese Version: <Cmd>git fetch --tags</Cmd> und <Cmd>{`git checkout v${next}`}</Cmd>).</>,
        <><Cmd>scripts/deploy_pi.sh</Cmd> ausführen. Das Skript baut, liefert aus, prüft und schaltet bei einem Fehler selbst zurück.</>,
      ],
      back: (
        <>
          Auf dem Pi im Deploy-Ordner <Cmd>bash pi_switch.sh rollback</Cmd> ausführen – das holt das vorige Image zurück.
        </>
      ),
    },
  ];
}

const CHANNEL_LABELS: Record<UpdateStatus["channel"], string> = {
  stable: "Nur fertige Versionen",
  beta: "Auch Vorabversionen (Beta)",
};

/**
 * Die Version von vor dem Update, wenn eine Kopie vor dem Update genau zur laufenden Version führte (neueste zuerst).
 * Kopien von älteren Versionen halten `from == to` fest (selbst gebaut, oder eine Vorabversion mit derselben
 * Versionsnummer): Das ist keine Vorversion, dann gilt sie als unbekannt.
 */
function previousVersion(info: SystemInfo | null, current: string): string | null {
  const copy = info?.pre_update_copies?.find((c) => c.to_version === current);
  return copy?.from_version && copy.from_version !== current ? copy.from_version : null;
}

export function UpdatesCard(): JSX.Element {
  const canWrite = useAuthStore((s) => s.hasPermission("settings.write"));
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [tab, setTab] = useState<GuideId | null>(null);

  useEffect(() => {
    let alive = true;
    api.get<UpdateStatus>("/system/updates")
      .then((s) => alive && setStatus(s))
      .catch((err: unknown) => alive && setLoadError(errorText(err)));
    // Nur eine Zugabe für die Anleitungen (Datenbank, Version von vorher): scheitert das, bleibt es still.
    api.get<SystemInfo>("/system/info")
      .then((i) => alive && setInfo(i))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  async function checkNow() {
    setBusy(true);
    setNotice(null);
    try {
      setStatus(await api.post<UpdateStatus>("/system/updates/check"));
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function save(key: "enabled" | "channel", value: boolean | string) {
    if (!status) return;
    setSaving(true);
    setNotice(null);
    try {
      await api.put(`/settings/system.update_check.${key}`, { value });
      if (key === "channel") {
        // Der Kanal ändert, welche Version als neueste gilt: Stand neu laden (ohne Netz).
        setStatus(await api.get<UpdateStatus>("/system/updates"));
      } else {
        setStatus((s) => (s ? { ...s, enabled: value as boolean } : s));
      }
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setSaving(false);
    }
  }

  if (!status) {
    return (
      <Card title="Updates">
        {loadError ? <NoticeLine notice={{ kind: "error", text: loadError }} /> : <p className="text-sm text-white/50">Lade …</p>}
      </Card>
    );
  }

  // Nur ein echtes Update kommt in die Anleitungen; ist die laufende Version neuer, gibt es keine Anleitung zurück.
  const target = status.available ? status.latest : null;
  const all = guides(status.official_image_name, status.current, {
    target,
    previous: target === null ? previousVersion(info, status.current) : null,
  });
  const activeId = tab ?? (status.official_image ? "compose" : "deploy-pi");
  const active = all.find((g) => g.id === activeId) ?? all[0];
  // Ohne Angabe gilt die Standard-Datenbank SQLite; nur bei ihr gibt es die Kopie vor dem Update.
  const sqlite = !info?.database || info.database === "sqlite";
  const nothingFound = !status.latest && !!status.checked_at;
  const locked = busy || saving;

  return (
    <Card
      title="Updates"
      description="Ob es eine neuere Version von Nodvard Deck gibt und wie du sie einspielst."
      footer={
        <Button variant="primary" busy={busy} disabled={saving} onClick={() => void checkNow()}>
          <RefreshCw size={14} /> Jetzt suchen
        </Button>
      }
    >
      <NoticeLine notice={notice} />

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-white/55">Installiert</dt>
        <dd>Version {status.current}</dd>
        <dt className="text-white/55">Neueste Version</dt>
        <dd>{status.latest ? `Version ${status.latest}` : nothingFound ? "keine gefunden" : "noch nicht geprüft"}</dd>
        <dt className="text-white/55">Zuletzt geprüft</dt>
        <dd>{formatWhen(status.checked_at)}</dd>
      </dl>

      {status.available && status.latest && (
        <div role="status" className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-100">
          <ArrowUpCircle size={16} className="flex-none" />
          <span className="font-medium">
            Update verfügbar: Version {status.latest}
            {isPrerelease(status.latest) ? " (Vorabversion)" : ""}
          </span>
          {status.release_notes_url && (
            <a href={status.release_notes_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 underline underline-offset-2">
              Was ist neu? <ExternalLink size={12} />
            </a>
          )}
        </div>
      )}
      {!status.available && status.latest && !status.error && (
        <p className="mt-4 flex items-center gap-2 text-sm text-white/70">
          <CheckCircle2 size={16} className="flex-none text-emerald-300" /> Du bist auf dem neuesten Stand.
        </p>
      )}
      {nothingFound && !status.error && (
        <p className="mt-4 flex items-center gap-2 text-sm text-white/70">
          <Info size={16} className="flex-none text-white/50" />
          {status.channel === "stable"
            ? "Es gibt noch keine fertige Version zum Herunterladen."
            : "Es gibt noch keine Version zum Herunterladen."}
        </p>
      )}
      {status.error && (
        <p className="mt-4 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          <WifiOff size={16} className="mt-0.5 flex-none" />
          <span>
            {status.error} {status.checked_at ? "Angezeigt ist der letzte bekannte Stand." : "Bitte später noch einmal versuchen."}
          </span>
        </p>
      )}

      {!status.official_image && (
        <p className="mt-4 text-xs text-white/55">
          Dieses Dashboard läuft mit einem selbst gebauten Image. Aktualisiere es auf demselben Weg, auf dem du es gebaut hast – auf einem Raspberry
          Pi z. B. mit <Cmd>scripts/deploy_pi.sh</Cmd>. Die anderen Anleitungen gelten für das fertige Image <Cmd>{status.official_image_name}</Cmd>.
        </p>
      )}

      <div className="mt-5 border-t border-white/[0.06] pt-4">
        {canWrite ? (
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <Toggle
                checked={status.enabled}
                disabled={locked}
                label="Täglich automatisch nach Updates suchen"
                onChange={(v) => void save("enabled", v)}
              />
              <div className="text-sm">
                <p>Täglich automatisch nach Updates suchen</p>
                <p className="mt-0.5 text-xs text-white/45">
                  Dazu fragt Nodvard Deck einmal am Tag bei GitHub (ghcr.io, dort liegen die Images) nach, welche Versionen es gibt. GitHub sieht dabei
                  die IP-Adresse deines Anschlusses und den Zeitpunkt; sonst wird nichts übertragen, auch nicht die installierte Version. Wenn du das
                  nicht möchtest, schalte die Suche hier ab. „Jetzt suchen“ geht auch dann, sieht GitHub aber ebenso.
                </p>
              </div>
            </div>
            <label className="block max-w-xs text-sm">
              <span className="mb-1.5 block text-white/70">Welche Versionen</span>
              <select
                className={inputClass}
                value={status.channel}
                disabled={locked}
                aria-label="Welche Versionen"
                onChange={(e) => void save("channel", e.target.value)}
              >
                {(Object.keys(CHANNEL_LABELS) as UpdateStatus["channel"][]).map((c) => (
                  <option key={c} value={c}>{CHANNEL_LABELS[c]}</option>
                ))}
              </select>
            </label>
          </div>
        ) : (
          <p className="text-xs text-white/45">
            Tägliche Suche: {status.enabled ? "an" : "aus"} · {CHANNEL_LABELS[status.channel]}. Bei jeder Suche sieht GitHub (ghcr.io) die
            IP-Adresse und den Zeitpunkt, sonst nichts. Abschalten kann sie, wer Einstellungen ändern darf.
          </p>
        )}
      </div>

      <div className="mt-5 border-t border-white/[0.06] pt-4">
        <h4 className="mb-2 text-sm font-semibold">So spielst du ein Update ein</h4>
        <div role="tablist" aria-label="Anleitung für" className="mb-3 flex flex-wrap gap-1">
          {all.map((g) => (
            <button
              key={g.id}
              type="button"
              role="tab"
              id={`update-guide-${g.id}`}
              aria-selected={g.id === active.id}
              aria-controls="update-guide-panel"
              onClick={() => setTab(g.id)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                g.id === active.id ? "bg-white/[0.14] text-white" : "text-white/60 hover:bg-white/[0.06] hover:text-white"
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="update-guide-panel" aria-labelledby={`update-guide-${active.id}`} className="text-sm">
          <ol className="list-decimal space-y-1.5 pl-5 text-white/80">
            {active.steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
          {active.note && <p className="mt-2 text-xs text-white/55">{active.note}</p>}
          {sqlite ? (
            <p className="mt-3 text-xs text-white/55">
              Vorher wird automatisch eine Kopie der Datenbank angelegt: Muss das Update die Datenbank umbauen, sichert Nodvard Deck sie beim Start
              zuerst (siehe „Kopien vor Updates“).
            </p>
          ) : (
            <p className="mt-3 text-xs text-white/55">
              Bei dieser Datenbank ({info?.database}) legt Nodvard Deck vor einem Update keine Kopie an. Bitte sichere sie vorher selbst.
            </p>
          )}
          <p className="mt-2 text-xs text-white/55">
            <span className="font-medium text-white/75">Zurück zur Vorversion: </span>
            {active.back}{" "}
            {sqlite ? (
              <>
                Hat die neue Version noch nie richtig gestartet, spielt die alte die Kopie von selbst wieder ein. Lief die neue Version schon und hat
                sie dabei die Datenbank umgebaut, zeigt die alte eine Notseite: Dort mit dem Notfallcode (steht im Protokoll des Containers) „Stand vor
                dem Update wiederherstellen“ wählen – was seit dem Update geändert wurde, geht dabei verloren. Beides klappt erst ab einer Version,
                die diese Kopien schon anlegt. Oder wieder auf die neue Version wechseln.
              </>
            ) : (
              <>Hat die neue Version die Datenbank schon umgebaut, startet die alte nicht (Notseite); zurück geht es dann nur mit deiner eigenen Sicherung.</>
            )}
          </p>
        </div>
      </div>
    </Card>
  );
}
