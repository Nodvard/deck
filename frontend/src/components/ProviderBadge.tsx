import { providerBadgeText, providerNote, type HostProvider } from "../lib/hostProvider";

/** Dezentes Kennzeichen an einem Server, den ein Modul eingelesen hat, das nicht mehr läuft. Läuft das Modul (oder
 * ist der Server von Hand angelegt), steht nichts da. */
export function ProviderBadge({ provider, testId }: { provider: HostProvider | undefined; testId?: string }) {
  if (!provider?.problem) return null;
  return (
    <span
      data-testid={testId}
      title={providerNote(provider)}
      className="inline-flex flex-none items-center rounded border border-amber-400/25 bg-amber-400/[0.07] px-1.5 py-0.5 text-[10px] font-medium leading-none text-amber-200/90"
    >
      {providerBadgeText(provider)}
    </span>
  );
}
