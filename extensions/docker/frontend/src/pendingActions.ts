/** Vorschläge, die auf die Freigabe warten; ist eine Aktion entschieden und fertig, fällt ihr Schlüssel weg. */
import { useEffect, useRef, useState } from "react";

import { ACTION_POLL_INTERVAL_MS, type ActionState } from "../../../_shared/frontend/src/actions";
import { authedFetch } from "../../../_shared/frontend/src/api";

const OPEN = new Set(["proposed", "approved", "executing"]);

export interface PendingActions {
  keys: ReadonlySet<string>;
  add: (key: string, actionId: string | undefined) => void;
}

export function usePendingActions(onSettled: (completed: ReadonlyMap<string, ActionState | null>) => void): PendingActions {
  const [pending, setPending] = useState<ReadonlyMap<string, string | undefined>>(new Map());
  const settled = useRef(onSettled);
  settled.current = onSettled;

  useEffect(() => {
    const watched = [...pending].filter((entry): entry is [string, string] => Boolean(entry[1]));
    if (watched.length === 0) return;
    const controller = new AbortController();
    let asking = false;
    const timer = setInterval(() => {
      if (asking || document.hidden) return;
      asking = true;
      void (async () => {
        const done = new Map<string, ActionState | null>();
        for (const [key, id] of watched) {
          try {
            const res = await authedFetch(`/actions/${encodeURIComponent(id)}`, { signal: controller.signal });
            const body = res.ok ? ((await res.json()) as ActionState) : null;
            if (res.status === 404 || (body && !OPEN.has(body.status ?? ""))) done.set(key, body);
          } catch {
            // Server gerade nicht erreichbar oder Seite verlassen: beim nächsten Takt wieder.
          }
        }
        asking = false;
        if (done.size === 0 || controller.signal.aborted) return;
        setPending((map) => {
          const next = new Map(map);
          for (const key of done.keys()) next.delete(key);
          return next;
        });
        settled.current(done);
      })();
    }, ACTION_POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [pending]);

  return {
    keys: new Set(pending.keys()),
    add: (key, actionId) => setPending((map) => new Map(map).set(key, actionId)),
  };
}
