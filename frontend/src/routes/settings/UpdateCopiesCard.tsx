/**
 * Karte „Kopien vor Updates“ (Einstellungen, System): die letzten Kopien der Datenbank, die Nodvard Deck vor einer
 * Migration angelegt hat (`GET /system/info`, Feld `pre_update_copies`, Recht `system.read`). Die Kopien selbst nur
 * zeigen -- eingespielt wird eine Kopie beim Start (`nodvard_deck.boot`) oder über die Notseite.
 *
 * Dazu der Rückweg über den Update-Helfer (`previous` in `GET /system/updates/helper`): „Zurück zu <Version>“, solange
 * er gilt (7 Tage nach einem Update), nur für den Owner, mit Passwort und ggf. Code. Hat die laufende Version beim
 * Start die Datenbank umgebaut (`previous.data_revert`), gehen die Daten mit zurück: Dann warnt die Karte mit dem
 * Zeitpunkt, seit dem alles verloren geht, und verlangt das Häkchen (`accept_data_loss`). Ist das unklar
 * (`data_revert: null`), gibt es keinen Knopf, der Server lehnte ihn ohnehin ab.
 *
 * Fehlen Kopien und Rückweg (älterer Server, Abfrage gescheitert), zeigt die Karte gar nichts: die Angaben sind eine
 * Zugabe, kein Grund für eine Fehlermeldung.
 */
import { AlertTriangle, DatabaseBackup, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import { formatSize, formatWhen } from "../../lib/backups";
import { requestRollback, useUpdateHelper, type HelperPrevious, type HelperView } from "../../lib/updater";
import { useAuthStore } from "../../state/auth";
import { ConfirmForm, FollowPanel, formatUnix, useHelperView } from "./UpdateHelper";
import { Button, Card } from "./ui";

export interface PreUpdateCopy {
  name: string;
  created_at: string | null;
  from_version: string | null;
  to_version: string | null;
  size: number;
}

interface SystemInfo {
  database?: string;
  pre_update_copies?: PreUpdateCopy[];
}

/** Beschriftung einer Kopie. Bei gleicher Version vorher/nachher war es kein Versionswechsel, sondern ein Umbau der
 * Datenbank (neue Migration ohne neue Versionsnummer); fehlt eine Angabe, steht nichts Erfundenes da. */
function versions(copy: PreUpdateCopy): string {
  const from = copy.from_version?.trim() || null;
  const to = copy.to_version?.trim() || null;
  if (from && to && from === to) return `Datenbank-Umbau in ${to}`;
  if (!from && !to) return "Datenbank-Umbau (Version nicht bekannt)";
  const fromText = from ? `Version ${from}` : "früherer Version";
  return to ? `Update von ${fromText} auf ${to}` : `Update von ${fromText}`;
}

/** Der Rückweg gilt noch (der Server blendet einen abgelaufenen schon aus; hier nur die Uhr dazwischen). */
function validPrevious(view: HelperView | null): HelperPrevious | null {
  const previous = view?.present ? view.previous : null;
  return previous && previous.until * 1000 > Date.now() ? previous : null;
}

function RollbackSection({ view, previous }: { view: HelperView; previous: HelperPrevious }) {
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  const startFollow = useUpdateHelper((s) => s.startFollow);
  const [confirming, setConfirming] = useState(false);
  const since = previous.data_since ? formatUnix(previous.data_since) : null;
  const lost = since ? `Änderungen seit ${since}` : "Änderungen seit dem Update";
  const unclear = previous.data_revert === null;
  const canRequest = isOwner && view.ready && !unclear;
  return (
    <section className="mb-4 rounded-lg border border-white/10 bg-black/15 p-3" aria-labelledby="rollback-title" data-testid="rollback-section">
      <h4 id="rollback-title" className="flex items-center gap-2 text-sm font-semibold">
        <Undo2 size={15} className="flex-none text-white/60" /> {unclear ? "Rückweg" : "Zurück"} zu Version {previous.version}
      </h4>
      {!unclear && (
        <p className="mt-1 text-xs text-white/55">
          Der Update-Helfer kann bis {formatUnix(previous.until)} auf die Version vor dem letzten Update zurückschalten.
        </p>
      )}
      {previous.data_revert === true && (
        <p className="mt-2 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100" data-testid="rollback-data-warning">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <span>
            {lost} gehen verloren: Diese Version hat beim Start die Datenbank umgebaut, deshalb kommt die Kopie von davor zurück. Den
            ersetzten Stand hebt Nodvard Deck 30 Tage im Datenordner unter <code className="whitespace-nowrap">restore/replaced-…</code> auf.
          </span>
        </p>
      )}
      {previous.data_revert === false && (
        <p className="mt-2 text-xs text-white/55">Deine Daten bleiben dabei, wie sie sind: Diese Version hat die Datenbank nicht umgebaut.</p>
      )}
      {unclear && (
        <p className="mt-2 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <span data-testid="rollback-unclear">
            Ob deine Daten beim Rückweg mit zurückmüssen, lässt sich nicht sicher sagen (meist hat diese Version beim Start die Datenbank
            umgebaut, und die passende Kopie von davor fehlt). Per Knopf geht es deshalb nicht zurück. Bleib am besten bei dieser Version;
            zurück ginge es nur mit einer eigenen Sicherung von vor dem Update.
          </span>
        </p>
      )}
      {!unclear && !isOwner && <p className="mt-2 text-xs text-white/45">Zurückschalten kann nur der Inhaber dieser Installation (Owner).</p>}
      {!unclear && isOwner && !view.ready && (
        <p className="mt-2 text-xs text-white/45">Zurück geht es, sobald der Update-Helfer bereit ist (siehe „Updates“).</p>
      )}
      {canRequest && !confirming && (
        <div className="mt-3">
          <Button variant="danger" onClick={() => setConfirming(true)}><Undo2 size={14} /> Zurück zu Version {previous.version}</Button>
        </div>
      )}
      {canRequest && confirming && (
        <ConfirmForm
          title="Rückweg bestätigen"
          submitLabel={`Zurück zu Version ${previous.version}`}
          variant="danger"
          consent={previous.data_revert ? `Ich weiß: ${lost} gehen verloren.` : null}
          lateConsent="Ich weiß: Änderungen seit dem Update gehen verloren."
          send={(password, code, consent) => requestRollback(password, consent, code)}
          onSent={(requested) => {
            setConfirming(false);
            startFollow({
              requestId: requested.request_id, action: "rollback", from: requested.from, to: requested.to,
              dataRevert: requested.data_revert ?? false,
            });
          }}
          onCancel={() => setConfirming(false)}
        >
          <p className="text-xs text-white/60">
            Nodvard Deck ist dabei ein paar Minuten nicht erreichbar. Danach läuft Version {previous.version}; einen weiteren Rückweg gibt es von dort nicht.
          </p>
        </ConfirmForm>
      )}
    </section>
  );
}

export function UpdateCopiesCard(): JSX.Element | null {
  const [copies, setCopies] = useState<PreUpdateCopy[] | null>(null);
  const [database, setDatabase] = useState<string | null>(null);
  const { view, follow } = useHelperView();
  const previous = follow ? null : validPrevious(view);
  const rollbackRunning = follow?.action === "rollback" ? follow : null;

  useEffect(() => {
    let alive = true;
    api.get<SystemInfo>("/system/info")
      .then((info) => {
        if (!alive) return;
        if (typeof info.database === "string") setDatabase(info.database);
        if (Array.isArray(info.pre_update_copies)) setCopies(info.pre_update_copies);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const helperPart = rollbackRunning ? (
    <div className="mb-4"><FollowPanel follow={rollbackRunning} /></div>
  ) : previous && view ? (
    <RollbackSection view={view} previous={previous} />
  ) : null;
  if (copies === null) {
    return helperPart ? <Card title="Kopien vor Updates">{helperPart}</Card> : null;
  }
  // Nur bei SQLite (der Standard-Datenbank) legt Nodvard Deck Kopien an; die Karte „Updates“ sagt dasselbe.
  if (database !== null && database !== "sqlite") {
    return (
      <Card title="Kopien vor Updates" description={`Bei dieser Datenbank (${database}) legt Nodvard Deck vor einem Update keine Kopie an.`}>
        {helperPart}
        <p className="text-sm text-white/55">Bitte sichere die Datenbank vor einem Update selbst.</p>
      </Card>
    );
  }
  return (
    <Card title="Kopien vor Updates" description="Vor jedem Update legt Nodvard Deck eine Kopie der Datenbank an. Die letzten drei bleiben liegen.">
      {helperPart}
      {copies.length === 0 ? (
        <p className="text-sm text-white/55">Noch keine Kopie. Sie entsteht beim nächsten Update, das die Datenbank umbaut.</p>
      ) : (
        <ul className="divide-y divide-white/[0.06]" aria-label="Kopien vor Updates">
          {copies.map((copy) => (
            <li key={copy.name} className="flex items-start gap-3 py-2 text-sm">
              <DatabaseBackup size={16} className="mt-0.5 flex-none text-white/45" />
              <div className="min-w-0">
                <p>{versions(copy)}</p>
                <p className="text-xs text-white/45">
                  {formatWhen(copy.created_at)} · {formatSize(copy.size)} · <code className="break-all">backups/vor-update/{copy.name}</code>
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-white/45">
        Gehst du nach einem misslungenen Update auf die alte Version zurück, spielt Nodvard Deck die Kopie von selbst wieder ein, solange die neue
        Version nie erfolgreich gestartet ist. Sonst hilft die Notseite (der Notfallcode steht im Protokoll des Containers). Beides klappt erst ab
        einer Version, die diese Kopien schon anlegt.
      </p>
    </Card>
  );
}
