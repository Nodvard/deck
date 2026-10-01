/**
 * Karte „Kopien vor Updates“ (Einstellungen, System): die letzten Kopien der Datenbank, die Nodvard Deck vor einer
 * Migration angelegt hat (`GET /system/info`, Feld `pre_update_copies`, Recht `system.read`). Klein gehalten: nur
 * zeigen, kein Knopf -- eingespielt wird die Kopie beim Start (`nodvard_deck.boot`) oder über die Notseite.
 *
 * Fehlt das Feld (älterer Server) oder scheitert die Abfrage, zeigt die Karte gar nichts: die Angabe ist eine
 * Zugabe, kein Grund für eine Fehlermeldung.
 */
import { DatabaseBackup } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import { formatSize, formatWhen } from "../../lib/backups";
import { Card } from "./ui";

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

function versions(copy: PreUpdateCopy): string {
  const from = copy.from_version ? `Version ${copy.from_version}` : "früherer Version";
  return copy.to_version ? `Update von ${from} auf ${copy.to_version}` : `Update von ${from}`;
}

export function UpdateCopiesCard(): JSX.Element | null {
  const [copies, setCopies] = useState<PreUpdateCopy[] | null>(null);
  const [database, setDatabase] = useState<string | null>(null);

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

  if (copies === null) return null;
  // Nur bei SQLite (der Standard-Datenbank) legt Nodvard Deck Kopien an; die Karte „Updates“ sagt dasselbe.
  if (database !== null && database !== "sqlite") {
    return (
      <Card title="Kopien vor Updates" description={`Bei dieser Datenbank (${database}) legt Nodvard Deck vor einem Update keine Kopie an.`}>
        <p className="text-sm text-white/55">Bitte sichere die Datenbank vor einem Update selbst.</p>
      </Card>
    );
  }
  return (
    <Card title="Kopien vor Updates" description="Vor jedem Update legt Nodvard Deck eine Kopie der Datenbank an. Die letzten drei bleiben liegen.">
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
