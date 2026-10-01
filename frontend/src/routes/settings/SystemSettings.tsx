/**
 * System: Einstellungen, die das ganze Dashboard betreffen. Jede Karte ist eine eigene Datei
 * und bringt ihre Daten selbst mit; weitere kommen unten in `CARDS`
 * dazu, ohne dass sich Seite oder Navigation aendern.
 *
 * Jede Karte nennt ihr Recht: „Sicherung“ und „Updates“ brauchen `system.read` (Aendern nur der Owner
 * bzw. die Schalter der Updates mit `settings.write`),
 * „Zeit & Protokoll“ weiter `settings.write` (liest GET /settings). Der Reiter erscheint mit
 * einem der beiden Rechte, gezeigt werden nur die passenden Karten.
 */
import { useEffect, useState, type ComponentType } from "react";

import { api } from "../../lib/api";
import { useAuthStore } from "../../state/auth";
import { BackupCard } from "./BackupCard";
import { RestoreCard } from "./RestoreCard";
import { TimeAndLogCard } from "./TimeAndLogCard";
import { UpdateCopiesCard } from "./UpdateCopiesCard";
import { UpdatesCard } from "./UpdatesCard";
import { NoticeLine, PageHeader, errorText } from "./ui";

interface SettingRow {
  key: string;
  value: unknown;
}

/** Was jede Karte bekommt: die Zeilen von GET /settings (leer ohne `settings.write`). */
export interface SystemCardProps {
  settings: SettingRow[];
}

const CARDS: { id: string; permission: string; needsSettings: boolean; Component: ComponentType<SystemCardProps> }[] = [
  { id: "backup", permission: "system.read", needsSettings: false, Component: BackupCard },
  { id: "restore", permission: "system.read", needsSettings: false, Component: RestoreCard },
  { id: "updates", permission: "system.read", needsSettings: false, Component: UpdatesCard },
  { id: "update-copies", permission: "system.read", needsSettings: false, Component: UpdateCopiesCard },
  { id: "time", permission: "settings.write", needsSettings: true, Component: TimeAndLogCard },
];

export function SystemSettings(): JSX.Element {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  useAuthStore((s) => s.user);
  const cards = CARDS.filter((c) => hasPermission(c.permission));
  const needSettings = cards.some((c) => c.needsSettings);
  const [settings, setSettings] = useState<SettingRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!needSettings) return;
    api.get<SettingRow[]>("/settings")
      .then(setSettings)
      .catch((err: unknown) => setLoadError(errorText(err)));
  }, [needSettings]);

  return (
    <div>
      <PageHeader title="System" description="Sicherung und Wiederherstellung, Updates, Zeitzone, Protokoll und was sonst für das ganze Dashboard gilt." />
      {cards.map(({ id, needsSettings, Component }) => {
        if (!needsSettings) return <Component key={id} settings={[]} />;
        if (loadError) return <NoticeLine key={id} notice={{ kind: "error", text: loadError }} />;
        if (!settings) return <p key={id} className="text-sm text-white/50">Lade …</p>;
        return <Component key={id} settings={settings} />;
      })}
      {cards.length === 0 && <p className="text-sm text-white/50">Für diesen Bereich fehlen dir die Rechte.</p>}
    </div>
  );
}
