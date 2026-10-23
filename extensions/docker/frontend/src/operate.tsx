/** Texte zum Modus „Nur bedienen“; das Gegenstück im Backend ist `refused_text` in `operate.py`. */
import { useSettingText } from "../../../_shared/frontend/src/links";

export const OPERATE_CAN = "Nodvard Deck darf dort Container ansehen, ihre Logs lesen und sie starten, stoppen und neu starten, aber nicht mehr.";

/** Der Schritt, der aus einem „Nur bedienen“-Server einen vollen Docker-Server macht; `fullTag` ist die eingestellte Markierung. */
export function operateUpgrade(fullTag: string): string {
  return `gib dem Server unter Einstellungen, Server & Zugänge die Markierung „${fullTag}“: Dann gilt er als voller Docker-Server, und die Einrichtung nimmt den Zugang in die Gruppe docker auf, die praktisch Root-Rechte gibt.`;
}

export const OPERATE_NO_IMAGE_CHECK = "Auf Servern im Modus „Nur bedienen“ prüft Nodvard Deck nicht auf neue Images.";

/** Marke „Nur bedienen“ neben dem Servernamen. */
export function OperateBadge({ hostId }: { hostId: string }): JSX.Element {
  const fullTag = useSettingText("docker", "docker_host_tag", "docker");
  return (
    <span className="rounded bg-white/10 px-1.5 py-0.5 text-[11px] opacity-80" data-testid={`operate-${hostId}`}
      title={`${OPERATE_CAN} Für alles andere ${operateUpgrade(fullTag)}`}>
      Nur bedienen
    </span>
  );
}

/** Statt der Listen, die es auf einem „Nur bedienen“-Server nicht gibt. */
export function OperateNote({ hostId, name }: { hostId: string; name: string }): JSX.Element {
  const fullTag = useSettingText("docker", "docker_host_tag", "docker");
  return (
    <p className="text-sm opacity-80" data-testid={`operate-${hostId}`}>
      Auf {name} ist Docker im Modus „Nur bedienen“ eingerichtet: {OPERATE_CAN} Stacks, Images, Volumes, Netze und Registries
      gibt es dort nicht. Dafür {operateUpgrade(fullTag)}
    </p>
  );
}
