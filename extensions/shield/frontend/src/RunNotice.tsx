/** Hinweis oben auf einem Reiter: Ergebnis einer Aktion, mit Links zur nächsten Stelle. */
import { Icon } from "../../../_shared/frontend/src/ui";
import type { ActionResult } from "./api";

/** Neben „ok“/„error“ gibt es „warn“ (erledigt, aber mit Einschränkung oder noch offen) und „info“ (läuft gerade). */
export type PageNotice = ActionResult;

const NOTICE_STYLE: Record<PageNotice["kind"], string> = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
  error: "border-red-500/30 bg-red-500/10 text-red-200",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-200",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-200",
};

export function RunNotice({ notice, onClose }: { notice: PageNotice; onClose?: () => void }) {
  return (
    <div role={notice.kind === "error" ? "alert" : "status"} className={`mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${NOTICE_STYLE[notice.kind]}`}>
      <span className="min-w-0 break-words">
        {notice.text}
        {notice.links?.map((l) => (
          <a key={l.href} href={l.href} className="ml-2 whitespace-nowrap underline underline-offset-2 hover:text-white">{l.label}</a>
        ))}
      </span>
      {onClose && (
        <button type="button" aria-label="Hinweis schließen" onClick={onClose} className="flex-none opacity-60 hover:opacity-100"><Icon name="x" size={14} /></button>
      )}
    </div>
  );
}

/** Die Hinweise mehrerer Aktionen untereinander, jeder für sich zu schließen. */
export function RunNotices({ notices, onClose }: { notices: ReadonlyArray<readonly [string, PageNotice]>; onClose: (key: string) => void }) {
  return (
    <>
      {notices.map(([key, notice]) => <RunNotice key={key} notice={notice} onClose={() => onClose(key)} />)}
    </>
  );
}
