/**
 * Karte „Wiederherstellen“ (Einstellungen, System): eine Sicherung einspielen. Der Ablauf selbst steht in
 * `RestoreFlow.tsx` (derselbe wie im Einrichtungs-Assistenten). Hier kommen dazu: der Stand vom Server
 * (vorgemerkt? Zwischenstand? letztes Ergebnis?), der alte Stand zum Löschen und die Rechte.
 *
 * Ansehen darf, wer `system.read` hat; einspielen nur der Owner (mit Anmeldepasswort).
 */
import { AlertTriangle, History, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api";
import { formatSize, formatWhen } from "../../lib/backups";
import { deleteReplaced, type RestoreStatus } from "../../lib/restore";
import { useAuthStore } from "../../state/auth";
import { RestoreFlow } from "./RestoreFlow";
import { Badge, Button, Card, Field, NoticeLine, errorText, inputClass, type Notice } from "./ui";

export function RestoreCard(): JSX.Element {
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  const [status, setStatus] = useState<RestoreStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setStatus(await api.get<RestoreStatus>("/system/restore/status"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorText(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const description = "Eine Sicherung einspielen. Das ersetzt alle Daten dieser Installation, auch die Konten.";

  if (!status) {
    return (
      <Card title="Wiederherstellen" description={description}>
        {loadError ? <NoticeLine notice={{ kind: "error", text: loadError }} /> : <p className="text-sm text-white/50">Lade …</p>}
      </Card>
    );
  }

  return (
    <Card title="Wiederherstellen" description={description}>
      <LastResult status={status} />
      {isOwner ? (
        <RestoreFlow
          mode="owner"
          resume={{ staged: status.staged, pending: status.pending }}
          maxUploadBytes={status.limits.max_upload_bytes}
          onDone={() => undefined}
        />
      ) : (
        <p className="text-sm text-white/55">Eine Sicherung einspielen kann nur der Inhaber (Owner) dieser Installation.</p>
      )}
      {isOwner && status.replaced && <ReplacedState replaced={status.replaced} onDeleted={reload} />}
    </Card>
  );
}

function LastResult({ status }: { status: RestoreStatus }) {
  const result = status.result;
  if (!result) return null;
  return (
    <div className="mb-4 flex items-start gap-3 rounded-lg border border-white/10 bg-black/20 px-4 py-3 text-sm">
      <History size={18} className="mt-0.5 flex-none text-white/50" />
      <div className="min-w-0">
        <p>
          Letzte Wiederherstellung: {formatWhen(result.at)}{" "}
          {result.ok ? <Badge tone="good">eingespielt</Badge> : <Badge tone="bad">nicht eingespielt</Badge>}
        </p>
        {result.message && !result.ok && <p className="mt-1 text-xs text-red-300">{result.message}</p>}
        {result.ok && result.actor && <p className="mt-1 text-xs text-white/45">Durch {result.actor}.</p>}
      </div>
    </div>
  );
}

function ReplacedState({ replaced, onDeleted }: { replaced: NonNullable<RestoreStatus["replaced"]>; onDeleted: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function remove() {
    const current = password;
    setPassword("");
    setBusy(true);
    setNotice(null);
    try {
      await deleteReplaced(current);
      setOpen(false);
      await onDeleted();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 border-t border-white/[0.06] pt-4" aria-labelledby="restore-replaced-title">
      <h4 id="restore-replaced-title" className="mb-2 text-sm font-semibold">Stand vor der Wiederherstellung</h4>
      <p className="flex gap-2 text-sm text-white/65">
        <AlertTriangle size={16} className="mt-0.5 flex-none text-amber-300" />
        <span>
          Der alte Stand liegt noch auf dem Server ({formatSize(replaced.size)}, Ordner <code className="break-all">restore/{replaced.name}</code>). Er enthält die alten
          Konten und Schlüssel. Wenn du ihn nicht mehr brauchst, lösche ihn.
          {replaced.expires_at && <> Sonst wird er am {formatWhen(replaced.expires_at)} automatisch gelöscht (30 Tage nach dem Einspielen).</>}
        </span>
      </p>
      <NoticeLine notice={notice} />
      {!open ? (
        <div className="mt-3">
          <Button onClick={() => setOpen(true)}>
            <Trash2 size={14} /> Alten Stand löschen
          </Button>
        </div>
      ) : (
        <form
          className="mt-3 flex flex-wrap items-end gap-2 rounded-lg bg-black/20 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (password) void remove();
          }}
        >
          <Field label="Wirklich löschen? Anmeldepasswort zur Bestätigung" className="min-w-0 basis-full sm:flex-1 sm:basis-auto">
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-label="Anmeldepasswort zum Löschen des alten Stands"
              className={inputClass}
              autoFocus
            />
          </Field>
          <Button variant="ghost" onClick={() => { setOpen(false); setPassword(""); }}>Abbrechen</Button>
          <Button type="submit" variant="danger" busy={busy} disabled={!password}>Löschen</Button>
        </form>
      )}
    </section>
  );
}
