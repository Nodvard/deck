/** Band, solange der Server weitere Live-Verbindungen dieses Kontos abweist (zu viele offene Tabs). */
import { RefreshCw } from "lucide-react";

import { wsClient } from "../lib/ws";
import { useWsLimitMessage } from "../lib/wsLimit";

export function LiveLimitNotice() {
  const message = useWsLimitMessage();
  if (message === null) return null;
  return (
    <div
      role="status"
      data-testid="live-limit-notice"
      className="flex flex-none flex-col gap-2 border-b border-amber-400/25 bg-amber-500/15 px-4 py-2.5 text-sm text-amber-100 sm:flex-row sm:items-center sm:gap-3 sm:px-6"
    >
      <p className="min-w-0 flex-1 break-words">
        <strong className="font-semibold">Live-Anzeige pausiert.</strong>{" "}
        <span className="text-amber-100/80">{message} Danach hier erneut verbinden.</span>
      </p>
      <button
        type="button"
        onClick={() => wsClient.retry()}
        className="inline-flex w-fit flex-none items-center gap-1.5 rounded-md border border-amber-300/40 px-2.5 py-1 text-xs font-medium hover:bg-amber-400/15"
      >
        <RefreshCw size={13} aria-hidden />
        Erneut verbinden
      </button>
    </div>
  );
}
