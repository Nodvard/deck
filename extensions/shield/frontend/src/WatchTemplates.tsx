/**
 * Wächter-Vorlagen: Ordner, in denen fremde Dateien landen (Samba- und NFS-Freigaben, Nextcloud-Daten), auf den Servern
 * suchen und mit einem Klick zum Echtzeit-Wächter hinzufügen. Spricht `GET /defender/watch-templates` (nur lesend) und
 * `POST /defender/watch-paths` (Recht `settings.write`), siehe watch_templates.py.
 */
import { useState } from "react";

import { deck } from "../../../_shared/frontend/src/deck";
import { Button, Card } from "../../../_shared/frontend/src/ui";
import { API, call } from "./api";

export interface WatchSuggestion {
  path: string;
  kinds: string[];
  hosts: string[];
  covered_by: string | null;
  /** Liegt in einem „ausgenommenen Ordner“: dort sucht der Wächter nicht. Ältere Antworten ohne das Feld: wie null. */
  excluded_by?: string | null;
}

interface TemplatesOut {
  suggestions: WatchSuggestion[];
  errors: string[];
  watch_paths: string[];
}

export const KIND_LABEL: Record<string, string> = {
  samba: "Samba-Freigabe",
  nfs: "NFS-Freigabe",
  nextcloud: "Nextcloud-Daten",
};

export function WatchTemplates() {
  const [data, setData] = useState<TemplatesOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const canChange = deck().hasPermission("settings.write");

  async function search() {
    setBusy("search");
    setMessage(null);
    try {
      setData(await call<TemplatesOut>(`${API}/watch-templates`));
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  async function add(path: string) {
    setBusy(path);
    setMessage(null);
    try {
      const r = await call<{ added: boolean; covered_by: string | null }>(`${API}/watch-paths`, {
        method: "POST", body: JSON.stringify({ path }),
      });
      setData((d) => d && {
        ...d,
        suggestions: d.suggestions.map((s) => (s.path === path ? { ...s, covered_by: r.covered_by ?? path } : s)),
      });
      setMessage({
        ok: true,
        text: r.added
          ? `${path} ist jetzt im Echtzeit-Wächter. Neue und hineinkopierte Dateien prüft er ab dem nächsten Lauf.`
          : `${path} wird schon überwacht (über ${r.covered_by}).`,
      });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Wächter-Vorlagen"
      description="Ordner, in denen fremde Dateien landen: Samba- und NFS-Freigaben, die Daten einer Nextcloud. Die Suche liest nur."
      className="mb-4"
      actions={<Button small onClick={() => void search()} disabled={busy !== null}>{busy === "search" ? "Suche …" : data ? "Neu suchen" : "Auf den Servern suchen"}</Button>}
    >
      <div className="space-y-2 text-sm" data-testid="watch-templates">
        {message && <p role={message.ok ? "status" : "alert"} className={message.ok ? "text-emerald-300" : "text-red-300"}>{message.text}</p>}
        {data && data.suggestions.length === 0 && (
          <p className="text-white/55">
            Keine Freigaben und keine Nextcloud gefunden. Ohne root bzw. „sudo“ ohne Passwort fehlt auf einem Server meist
            die Nextcloud.
          </p>
        )}
        {data && data.suggestions.length > 0 && (
          <ul className="divide-y divide-white/[0.06]">
            {data.suggestions.map((s) => (
              <li key={s.path} className="flex flex-wrap items-center gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <p className="break-all font-mono text-xs">{s.path}</p>
                  <p className="text-xs text-white/50">
                    {s.kinds.map((k) => KIND_LABEL[k] ?? k).join(", ")} · auf {s.hosts.join(", ")}
                  </p>
                </div>
                {s.covered_by ? (
                  <span className="text-xs text-emerald-300">
                    {s.covered_by === s.path ? "wird überwacht" : `wird überwacht (über ${s.covered_by})`}
                  </span>
                ) : s.excluded_by ? (
                  <span className="text-xs text-amber-300">
                    ausgenommen (über {s.excluded_by}) – dort prüft der Wächter nicht
                  </span>
                ) : canChange ? (
                  <Button small variant="primary" disabled={busy !== null} onClick={() => void add(s.path)}>
                    {busy === s.path ? "Füge hinzu …" : "Zum Wächter hinzufügen"}
                  </Button>
                ) : (
                  <span className="text-xs text-white/45">Hinzufügen braucht das Recht, Einstellungen zu ändern.</span>
                )}
              </li>
            ))}
          </ul>
        )}
        {data && data.errors.length > 0 && (
          <ul className="text-xs text-amber-300">{data.errors.map((e) => <li key={e}>{e}</li>)}</ul>
        )}
        <p className="text-xs text-white/45">
          Der Wächter sieht bei jedem Lauf alle Dateien eines Ordners an. Ein sehr großer Ordner (etwa eine Nextcloud mit
          vielen Tausend Dateien) kostet deshalb jedes Mal etwas Zeit. Entfernen geht unter „Einstellungen ändern“ →
          „Überwachte Ordner“.
        </p>
      </div>
    </Card>
  );
}
