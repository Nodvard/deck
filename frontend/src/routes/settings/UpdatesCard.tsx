/**
 * Karte „Updates“ (Einstellungen, System): ob es eine neuere Version gibt und wie man sie einspielt. Die Suche läuft
 * über `/system/updates` (höchstens einmal pro Minute), der Knopf „Jetzt aktualisieren“ über den Update-Helfer
 * (`applyBlock`), für alle ohne Helfer gibt es Anleitungen je Umgebung.
 */
import { ArrowUpCircle, CheckCircle2, ExternalLink, Info, RefreshCw, ShieldAlert, WifiOff } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api } from "../../lib/api";
import { formatWhen } from "../../lib/backups";
import { requestUpdate, tagFitsVersion, useUpdateHelper, type HelperView } from "../../lib/updater";
import { useAuthStore } from "../../state/auth";
import { DECK_EDITION, useEdition } from "../../state/edition";
import type { PreUpdateCopy } from "./UpdateCopiesCard";
import { OWNER_ONLY_HINT, OwnerConfirm, type OwnerProof } from "./OwnerConfirm";
import { ConfirmForm, FollowPanel, HelperStatusLine, LastResultLine, formatUnix, useHelperView } from "./UpdateHelper";
import { Button, Card, NoticeLine, Toggle, errorText, inputClass, type Notice } from "./ui";
import { HELPER_CODE_TEXTS } from "./updaterTexts";

export interface UpdateStatus {
  current: string;
  latest: string | null;
  latest_digest?: string | null;
  available: boolean;
  /** Der Inhaber hat zugestimmt, dass Deck Sicherheitsupdates selbst einspielt (wirkt erst mit signierten Anforderungen). */
  auto_security?: boolean;
  /** Arten bis `latest`: `sicherheit`, `behoben`, `verbessert`, `neu`, `dokumentation`.
   * In Image-Annotationen wird Dokumentation für ältere Deck-Versionen als `verbessert` veröffentlicht. */
  kinds?: string[];
  /** `false`: Für mindestens eine dieser Versionen sind die Arten unbekannt. */
  kinds_complete?: boolean;
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
  /** Zu `latest` liegt eine Freigabe in gültiger Form am Release; ohne sie gibt es kein Update per Knopf. */
  approved?: boolean;
  approved_key?: string | null;
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
  /** Die Version, die eingespielt werden soll: nur bei einem echten Update (`available`), sonst `null`
   * (dann steht ein Platzhalter da: nie eine Anleitung zurück auf eine ältere Version). */
  target: string | null;
  /** Die Version von vor dem Update, wenn sie sicher bekannt ist (Kopie vor dem Update hin zur laufenden Version). */
  previous?: string | null;
  /** Name der laufenden Ausgabe, wie ihn Portainer, Synology und Unraid zeigen. */
  product?: string;
  /** Mit der Anleitung für `scripts/deploy_pi.sh` (nur Nodvard Deck). */
  deployPi?: boolean;
}

/** Die Installation mit dem fertigen Image legt die Datei als `compose.yml` ab; Compose findet sie dann selbst. */
const compose = (command: string) => `docker compose ${command}`;

function Cmd({ children }: { children: ReactNode }) {
  return <code className="rounded bg-black/30 px-1.5 py-0.5 text-[12px] text-white/85">{children}</code>;
}

const KIND_LABELS: [kind: string, label: string][] = [
  ["sicherheit", "Sicherheitsupdate"],
  ["behoben", "Fehlerbehebungen"],
  ["neu", "Neue Funktionen"],
  ["verbessert", "Verbesserungen"],
  ["dokumentation", "Dokumentation"],
];

/** Was das Update bringt; fehlt etwas, sagt die Zeile das, statt das Update harmlos aussehen zu lassen. */
function UpdateKinds({ kinds = [], complete = false }: { kinds?: string[]; complete?: boolean }) {
  const shown = KIND_LABELS.filter(([kind]) => kinds.includes(kind));
  if (shown.length === 0 && complete) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      {shown.map(([kind, label]) =>
        kind === "sicherheit" ? (
          <span key={kind} className="inline-flex items-center gap-1 rounded-full border border-red-500/40 bg-red-500/15 px-2 py-0.5 font-medium text-red-100">
            <ShieldAlert size={12} /> {label}
          </span>
        ) : (
          <span key={kind} className="rounded-full border border-white/15 bg-white/[0.06] px-2 py-0.5 text-white/80">{label}</span>
        ),
      )}
      {!complete && (
        <span className="text-white/60">
          {shown.length === 0
            ? "Was dieses Update ändert, ist nicht bekannt. Es kann auch Sicherheitsupdates enthalten; schau unter „Was ist neu?“ nach."
            : "Nicht für jede Version bis dahin ist bekannt, was sie ändert; schau unter „Was ist neu?“ nach."}
        </span>
      )}
    </div>
  );
}

export const KEY_AUTO_SECURITY = "system.update_check.auto_security";

/** Zustimmung zum Einspielen ohne Klick. Sie wird schon gespeichert, wirkt aber erst mit signierten Anforderungen. */
function AutoSecuritySwitch({
  enabled,
  locked,
  confirmLocked,
  onSaved,
}: {
  enabled: boolean;
  locked: boolean;
  confirmLocked: boolean;
  onSaved: (value: boolean) => void;
}) {
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  const [wanted, setWanted] = useState<boolean | null>(null);

  async function send(proof: OwnerProof) {
    await api.put(`/settings/${KEY_AUTO_SECURITY}`, { value: wanted, ...proof });
  }

  return (
    <div>
      <div className="flex items-start gap-3">
        <Toggle
          checked={wanted ?? enabled}
          disabled={!isOwner || wanted !== null || locked}
          label="Sicherheitsupdates selbst einspielen"
          onChange={setWanted}
        />
        <div className="text-sm">
          <p>Sicherheitsupdates selbst einspielen</p>
          <p className="mt-0.5 text-xs text-white/45">
            Noch nicht in dieser Version: Bis jetzt spielt Nodvard Deck kein Update von selbst ein. Findet die Update-Suche (die tägliche
            oben oder „Jetzt suchen“) ein Sicherheitsupdate, meldet Deck das rot, mit eingerichtetem Meldungskanal auch als Push. Mit
            Version 1.0 spielt es dann, wenn du hier zustimmst, Sicherheitsupdates derselben Reihe (etwa 1.0.1 auf 1.0.2) selbst ein, nur
            im Kanal „Nur fertige Versionen“ und nur mit Update-Helfer.
          </p>
          {enabled && wanted === null && (
            <p className="mt-1 text-xs text-amber-200/80">Zugestimmt. Bis Version 1.0 bleibt es beim Melden, einspielen musst du noch selbst.</p>
          )}
          {!isOwner && <p className="mt-1 text-xs text-white/55">{OWNER_ONLY_HINT}</p>}
        </div>
      </div>
      {wanted !== null && (
        <OwnerConfirm
          disabled={confirmLocked}
          send={send}
          onDone={() => {
            onSaved(wanted);
            setWanted(null);
          }}
          onCancel={() => setWanted(null)}
        />
      )}
    </div>
  );
}

/** `0.6.0-rc1` -> `0.6` (die Reihe, die release.yml als Tag mit veröffentlicht). */
function seriesOf(version: string): string {
  return version.split("-")[0].split(".").slice(0, 2).join(".");
}

export function isPrerelease(version: string | null | undefined): boolean {
  return !!version && version.includes("-");
}

export function guides(
  image: string,
  current: string,
  { target, previous = null, product = "Nodvard Deck", deployPi = true }: GuideOptions,
): Guide[] {
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
  const all: Guide[] = [
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
        <>Links „Stacks“ öffnen und den Stack mit {product} anklicken.</>,
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
        <>Läuft {product} als „Projekt“: unter „Projekt“ das Projekt stoppen und über „Aktion“ → „Erstellen“ neu anlegen; dabei wird die neue Version geladen. {pinned(imageField)}</>,
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
        <>Bei {product} erscheint „apply update“ – anklicken und mit „Apply update“ bestätigen.</>,
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
  return deployPi ? all : all.filter((guide) => guide.id !== "deploy-pi");
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

/**
 * Gibt es den Knopf „Jetzt aktualisieren“? `{ ok: true }` oder der Grund dagegen: als Satz nur, wo er weiterhilft
 * (`why: null`: es gibt nichts zu tun oder die Zeile zum Helfer sagt schon alles).
 */
export function applyBlock(status: UpdateStatus, view: HelperView | null, isOwner: boolean): { ok: true } | { ok: false; why: string | null } {
  const target = status.available ? status.latest : null;
  if (!target || !view?.present || !view.ready || !view.target) return { ok: false, why: null };
  if (!status.official_image) return { ok: false, why: null }; // sagt der Absatz zum selbst gebauten Image
  if (isPrerelease(target)) {
    return { ok: false, why: "Vorabversionen spielt der Update-Helfer nicht ein. Nimm dafür die Anleitung unten." };
  }
  if (view.target.pinned || !view.target.floating_tag) return { ok: false, why: HELPER_CODE_TEXTS.pinned_version };
  if (!tagFitsVersion(view.target.floating_tag, target)) {
    const [major, minor] = target.split(".");
    return {
      ok: false,
      why: `In der Compose-Datei steht die Reihe „:${view.target.floating_tag}“, Version ${target} kommt darüber nicht. Trag „:latest“ (oder „:${major}.${minor}“) ein und führ „docker compose up -d“ aus.`,
    };
  }
  if (!isOwner) return { ok: false, why: "Per Knopf aktualisieren kann nur der Inhaber dieser Installation (Owner)." };
  if (view.request_options?.includes("digest") && !status.latest_digest) {
    return { ok: false, why: "Bei der letzten Update-Suche kam keine Prüfsumme für diese Version an. Such erneut nach Updates." };
  }
  if (!status.approved) {
    return {
      ok: false,
      why: `Für Version ${target} liegt noch keine gültige Freigabe vor, oder sie ließ sich bei der Suche nicht abrufen. Such später erneut nach Updates; von Hand geht es schon mit der Anleitung unten.`,
    };
  }
  const blockedUntil = blockedAfterRollback(view, target);
  if (blockedUntil !== null) {
    return {
      ok: false,
      why: `Von Version ${target} bist du vor Kurzem zurückgegangen. Der Update-Helfer spielt sie deshalb erst ab ${formatUnix(blockedUntil)} wieder ein.`,
    };
  }
  return { ok: true };
}

/** Die Kennung des Schlüssels in Vierergruppen, so liest sie sich leichter. */
export function formatKeyId(keyId: string): string {
  return keyId.match(/.{1,4}/g)?.join(" ") ?? keyId;
}

/** Wer die Freigabe prüft: der Helfer selbst (`v: 2`) oder, bei einem älteren Helfer, noch niemand. */
function SignatureLine({ status, view }: { status: UpdateStatus; view: HelperView }) {
  if (view.request_versions?.includes(2)) {
    const key = status.approved_key ? ` (Schlüssel ${formatKeyId(status.approved_key)})` : "";
    return (
      <p className="text-xs text-white/55" data-testid="helper-signed">
        Freigabe liegt vor{key}. Ob sie echt ist, prüft der Update-Helfer, bevor er etwas einspielt.
      </p>
    );
  }
  return (
    <p className="text-xs text-amber-200/80" data-testid="helper-unsigned">
      Dein Update-Helfer prüft die Freigabe noch nicht selbst. Hol seine aktuelle Version (Anleitung: deploy/README.md,
      Abschnitt „Update-Helfer“), dann prüft er vor jedem Update die Signatur.
    </p>
  );
}

/** So lange sperrt der Helfer nach einem Rückweg die verlassene Version (`BLOCK_AFTER_ROLLBACK_S` im Helfer). */
export const BLOCK_AFTER_ROLLBACK_S = 24 * 3600;

/**
 * Bis wann (Unix-Zeit) der Helfer `target` sperrt, wenn der letzte Vorgang genau der Rückweg von dieser Version war;
 * sonst `null`. So kostet der Versuch kein Passwort und keinen Code, die der Helfer danach ohnehin ablehnte.
 */
function blockedAfterRollback(view: HelperView, target: string): number | null {
  const last = view.last_result;
  if (!last || last.action !== "rollback" || last.outcome !== "reverted" || last.from !== target) return null;
  const until = last.finished_at + BLOCK_AFTER_ROLLBACK_S;
  return until > Date.now() / 1000 ? until : null;
}

/** Der Teil der Karte zum Update-Helfer: Zustand, Knopf mit Bestätigung, Fortschritt, letztes Ergebnis. */
function HelperSection({ status, sqlite, database }: { status: UpdateStatus; sqlite: boolean; database?: string }) {
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  const { view, follow } = useHelperView();
  const startFollow = useUpdateHelper((s) => s.startFollow);
  const [confirming, setConfirming] = useState(false);
  // Folgt die Karte einem Vorgang (auch einem aus einem anderen Tab), ist die Bestätigung erledigt.
  useEffect(() => {
    if (follow) setConfirming(false);
  }, [follow]);
  if (!view) return null;
  if (follow && follow.action === "update") {
    return (
      <div className="mt-4" data-testid="helper-section">
        <FollowPanel follow={follow} />
      </div>
    );
  }
  // Selbst gebautes Image: Der Helfer arbeitet nur mit dem offiziellen, die Karte lädt nicht ein, ihn einzurichten.
  if (!status.official_image && !view.present && (view.reason ?? "missing") === "missing") return null;
  const block = follow ? ({ ok: false, why: null } as const) : applyBlock(status, view, isOwner);
  const target = status.latest ?? "";
  return (
    <div className="mt-4 space-y-2" data-testid="helper-section">
      <HelperStatusLine view={view} />
      {block.ok && !confirming && (
        <Button variant="primary" onClick={() => setConfirming(true)}>
          <ArrowUpCircle size={14} /> Jetzt aktualisieren auf {target}
        </Button>
      )}
      {block.ok && <SignatureLine status={status} view={view} />}
      {!block.ok && block.why && <p className="text-xs text-white/55" data-testid="helper-why-not">{block.why}</p>}
      {block.ok && confirming && (
        <ConfirmForm
          title={`Auf Version ${target} aktualisieren`}
          submitLabel="Jetzt aktualisieren"
          variant="primary"
          send={(password, code) => requestUpdate(target, password, code)}
          onSent={(requested) => {
            setConfirming(false);
            startFollow({ requestId: requested.request_id, action: "update", from: requested.from, to: requested.to });
          }}
          onCancel={() => setConfirming(false)}
        >
          <ul className="list-disc space-y-1 pl-5 text-xs text-white/60">
            <li>Der Update-Helfer lädt die neue Version und schaltet um. Nodvard Deck ist dabei ein paar Minuten nicht erreichbar.</li>
            {sqlite ? (
              <li>Vorher wird automatisch eine Kopie der Datenbank angelegt, falls die neue Version sie umbauen muss (siehe „Kopien vor Updates“).</li>
            ) : (
              <li>Bei deiner Datenbank ({database}) legt Nodvard Deck vorher keine Kopie an. Sichere sie vorher selbst.</li>
            )}
            <li>Startet die neue Version nicht richtig, schaltet der Helfer von selbst auf die bisherige zurück.</li>
            <li>Nach einem gelungenen Update kannst du 7 Tage lang per Knopf zurück (Karte „Kopien vor Updates“).</li>
          </ul>
        </ConfirmForm>
      )}
      {!follow && view.last_result && <LastResultLine result={view.last_result} ready={view.present && view.ready} />}
    </div>
  );
}

export function UpdatesCard(): JSX.Element {
  const edition = useEdition();
  // Freigaben von github.com holt nur die Suche nach dem Image von Nodvard Deck.
  const asksForGrant = edition.image === DECK_EDITION.image;
  const canWrite = useAuthStore((s) => s.hasPermission("settings.write"));
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const autoSecurityRevision = useRef(0);
  const channelRevision = useRef(0);
  const [notice, setNotice] = useState<Notice>(null);
  const [tab, setTab] = useState<GuideId | null>(null);
  const helperView = useUpdateHelper((s) => s.view);
  const helperFollow = useUpdateHelper((s) => s.follow);
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));

  useEffect(() => {
    let alive = true;
    const revision = autoSecurityRevision.current;
    const channel = channelRevision.current;
    api.get<UpdateStatus>("/system/updates")
      .then((s) => alive && receiveStatus(s, revision, channel))
      .catch((err: unknown) => alive && setLoadError(errorText(err)));
    // Nur eine Zugabe für die Anleitungen (Datenbank, Version von vorher): scheitert das, bleibt es still.
    api.get<SystemInfo>("/system/info")
      .then((i) => alive && setInfo(i))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  function receiveStatus(loaded: UpdateStatus, revision: number, channel: number) {
    setStatus((s) => {
      if (channel !== channelRevision.current) return s;
      return revision === autoSecurityRevision.current ? loaded : { ...loaded, auto_security: s?.auto_security };
    });
  }

  async function checkNow() {
    const revision = autoSecurityRevision.current;
    const channel = channelRevision.current;
    setBusy(true);
    setNotice(null);
    try {
      const checked = await api.post<UpdateStatus>("/system/updates/check");
      receiveStatus(checked, revision, channel);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function save(key: "enabled" | "channel", value: boolean | string) {
    if (!status) return;
    const revision = autoSecurityRevision.current;
    const channel = key === "channel" ? ++channelRevision.current : channelRevision.current;
    setSaving(true);
    setNotice(null);
    try {
      await api.put(`/settings/system.update_check.${key}`, { value });
      if (key === "channel") {
        // Der Kanal ändert, welche Version als neueste gilt: Stand neu laden (ohne Netz).
        receiveStatus(await api.get<UpdateStatus>("/system/updates"), revision, channel);
      } else {
        setStatus((s) => (s ? { ...s, enabled: value as boolean } : s));
      }
    } catch (err) {
      if (channel === channelRevision.current) setNotice({ kind: "error", text: errorText(err) });
    } finally {
      if (channel === channelRevision.current) setSaving(false);
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
    product: edition.product_name,
    deployPi: edition.repo_tools,
  });
  const activeId = tab ?? (status.official_image || !edition.repo_tools ? "compose" : "deploy-pi");
  const active = all.find((g) => g.id === activeId) ?? all[0];
  // Ohne Angabe gilt die Standard-Datenbank SQLite; nur bei ihr gibt es die Kopie vor dem Update.
  const sqlite = !info?.database || info.database === "sqlite";
  const nothingFound = !status.latest && !!status.checked_at;
  // Solange gesucht oder gespeichert wird, sind Schalter, Kanal und Knopf gesperrt: Sonst überschriebe die Antwort der
  // einen Anfrage das Ergebnis der anderen.
  const locked = busy || saving;

  return (
    <Card
      title="Updates"
      description={`Ob es eine neuere Version von ${edition.product_name} gibt und wie du sie einspielst.`}
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
        <>
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
          <UpdateKinds kinds={status.kinds} complete={status.kinds_complete} />
        </>
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

      {/* Der Update-Helfer aktualisiert nur das Image von Nodvard Deck. */}
      {edition.repo_tools && <HelperSection status={status} sqlite={!info?.database || info.database === "sqlite"} database={info?.database} />}

      {!status.official_image && (
        <p className="mt-4 text-xs text-white/55">
          {edition.repo_tools ? (
            <>
              Dieses Dashboard läuft mit einem selbst gebauten Image. Aktualisiere es auf demselben Weg, auf dem du es gebaut hast – auf einem Raspberry
              Pi z. B. mit <Cmd>scripts/deploy_pi.sh</Cmd>. Die anderen Anleitungen gelten für das fertige Image <Cmd>{status.official_image_name}</Cmd>.
            </>
          ) : (
            <>
              Dieses Dashboard läuft mit einem selbst gebauten Image. Aktualisiere es auf demselben Weg, auf dem du es gebaut hast. Die Anleitungen
              gelten für das fertige Image <Cmd>{status.official_image_name}</Cmd>.
            </>
          )}
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
                  Dazu fragt {edition.product_name} einmal am Tag bei GitHub (ghcr.io, dort liegen die Images{asksForGrant && ", und github.com für die Freigabe"}) nach, welche Versionen es gibt. GitHub sieht dabei
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
            {edition.repo_tools && (
              <AutoSecuritySwitch
                enabled={status.auto_security === true}
                locked={locked}
                confirmLocked={saving}
                onSaved={(value) => {
                  autoSecurityRevision.current += 1;
                  setStatus((s) => (s ? { ...s, auto_security: value } : s));
                }}
              />
            )}
          </div>
        ) : (
          <p className="text-xs text-white/45">
            Tägliche Suche: {status.enabled ? "an" : "aus"} · {CHANNEL_LABELS[status.channel]}. Bei jeder Suche sieht GitHub (ghcr.io{asksForGrant && ", github.com"}) die
            IP-Adresse und den Zeitpunkt, sonst nichts. Abschalten kann sie, wer Einstellungen ändern darf.
          </p>
        )}
      </div>

      <ManualGuide collapsed={manualGuideFolded(status, helperView, Boolean(helperFollow), isOwner)}>
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
              Vorher wird automatisch eine Kopie der Datenbank angelegt: Muss das Update die Datenbank umbauen, sichert {edition.product_name} sie beim Start
              zuerst (siehe „Kopien vor Updates“).
            </p>
          ) : (
            <p className="mt-3 text-xs text-white/55">
              Bei dieser Datenbank ({info?.database}) legt {edition.product_name} vor einem Update keine Kopie an. Bitte sichere sie vorher selbst.
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
      </ManualGuide>
    </Card>
  );
}

/** Die Anleitung von Hand wird nur eingeklappt, wenn der Helfer das Update übernimmt (Knopf da oder Vorgang läuft). */
export function manualGuideFolded(
  status: UpdateStatus, view: HelperView | null, following: boolean, isOwner: boolean,
): boolean {
  if (!view || !status.official_image) return false;
  return following || view.state === "busy" || applyBlock(status, view, isOwner).ok;
}

/** Anleitung für Updates von Hand, eingeklappt, wenn der Knopf oben der Weg ist. */
function ManualGuide({ collapsed, children }: { collapsed: boolean; children: ReactNode }): JSX.Element {
  if (!collapsed) {
    return (
      <div className="mt-5 border-t border-white/[0.06] pt-4">
        <h4 className="mb-2 text-sm font-semibold">So spielst du ein Update ein</h4>
        {children}
      </div>
    );
  }
  return (
    <details className="mt-5 border-t border-white/[0.06] pt-4" data-testid="manual-update-guide">
      <summary className="mb-2 cursor-pointer text-sm font-semibold">Von Hand aktualisieren (ohne Update-Helfer)</summary>
      {children}
    </details>
  );
}
