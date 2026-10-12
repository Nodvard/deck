/**
 * Server, die ein Modul eingelesen hat (z. B. Proxmox), und was daraus wird, wenn das Modul ausgeschaltet ist:
 * Die Server bleiben in der Liste stehen, ihr Zustand wird aber nicht mehr nachgeführt. Die Oberfläche sagt das
 * an mehreren Stellen (Cockpit, Server-Seite, Löschen, Modul ausschalten); dieser Text und diese Entscheidung
 * stehen nur hier.
 */
import { findExtension } from "./extensionNames";
import type { ExtensionInfo } from "./firstSteps";
import type { HostOut } from "./overview";

/** `disabled`: ausgeschaltet. `broken`: eingeschaltet, aber beim Laden gescheitert. `missing`: gibt es nicht mehr. */
export type ProviderProblem = "disabled" | "broken" | "missing";

export interface HostProvider {
  /** Anzeigename des Moduls, sonst seine Kennung. */
  name: string;
  /** `null`: das Modul läuft, der Server wird weiter nachgeführt. */
  problem: ProviderProblem | null;
}

/** Das Modul, das den Server eingelesen hat. `undefined`: von Hand angelegt, oder die Modul-Liste ist noch nicht da
 * (dann lieber nichts behaupten). */
export function hostProvider(host: Pick<HostOut, "provider_ext_id">, extensions: readonly ExtensionInfo[] | undefined): HostProvider | undefined {
  const id = host.provider_ext_id;
  if (!id || !extensions) return undefined;
  const ext = findExtension(extensions, id);
  const problem: ProviderProblem | null = !ext ? "missing" : ext.state === "enabled" ? null : ext.state === "disabled" ? "disabled" : "broken";
  return { name: ext?.name ?? id, problem };
}

/** Kurzes Kennzeichen für Karten und Listen. */
export function providerBadgeText(provider: HostProvider): string {
  return provider.problem === "disabled" ? "Modul aus" : provider.problem === "missing" ? "Modul fehlt" : "Modul gestört";
}

/** Was mit dem Modul los ist, als Satzende nach „Das Modul … “. */
function providerStateText(provider: HostProvider): string {
  return provider.problem === "disabled" ? "ist ausgeschaltet" : provider.problem === "missing" ? "ist nicht mehr da" : "läuft gerade nicht";
}

/** Erklärung zum Kennzeichen (Tooltip): wer den Server eingelesen hat und was das jetzt heißt. */
export function providerNote(provider: HostProvider): string {
  return `Eingelesen vom Modul „${provider.name}“. Das Modul ${providerStateText(provider)}, darum wird der Zustand dieses Servers nicht mehr aktualisiert.`;
}

/** Zusatz im Löschen-Dialog, wenn das Modul nicht läuft: Der Server kommt nicht wieder, denn es gleicht nichts mehr ab. */
export function providerDeleteNote(provider: HostProvider): string {
  const stays = provider.problem === "disabled" ? "solange es aus bleibt" : provider.problem === "missing" ? "" : "solange es nicht läuft";
  return `Das Modul „${provider.name}“, das diesen Server eingelesen hat, ${provider.problem === "disabled" ? "ist aus" : providerStateText(provider)}; der Server kommt nicht wieder${stays ? `, ${stays}` : ""}.`;
}
