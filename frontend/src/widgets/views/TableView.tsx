import { CARD_BODY, CARD_CELL, CARD_CELL_PLAIN, CARD_HEAD, CARD_ROW, CARD_TABLE } from "../../components/cardTable";
import { styleRef } from "../../../../extensions/_shared/frontend/src/csp";
import { renderTemplate, resolvePath } from "../template";
import type { TableView as TableViewSpec } from "../types";
import { ActionButton } from "./ActionButton";

const ALIGN_CLASS: Record<"left" | "right" | "center", string> = {
  left: "text-left",
  right: "text-left sm:text-right",
  center: "text-left sm:text-center",
};

export function TableView({
  view, data, extId, onAction, limit = null,
}: { view: TableViewSpec; data: unknown; extId: string; onAction: () => void; limit?: number | null }) {
  const all = Array.isArray(data) ? data : [];
  // `limit`: so viele Zeilen passen in die Karte, den Rest zählt die Karte als „+ x weitere“ (WidgetCard).
  const rows = limit !== null ? all.slice(0, limit) : all;

  if (rows.length === 0) {
    return <p className="text-sm opacity-60">{view.empty_text}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table role="table" className={`${CARD_TABLE} text-left`}>
        <thead role="rowgroup" className={CARD_HEAD}>
          <tr className="border-b border-white/10 text-xs opacity-60">
            {view.columns.map((col) => (
              <th key={col.field} className={`px-2 py-1 font-medium ${ALIGN_CLASS[col.align]}`} ref={styleRef(col.width ? { width: col.width } : undefined)}>
                {col.label}
              </th>
            ))}
            {view.row_actions.length > 0 && <th className="px-2 py-1" />}
          </tr>
        </thead>
        <tbody role="rowgroup" className={CARD_BODY}>
          {rows.map((row, idx) => (
            <tr role="row" className={CARD_ROW} key={idx} data-fit-row>
              {view.columns.map((col) => (
                <td role="cell" data-label={col.label} key={col.field} className={`${CARD_CELL} min-w-0 py-1 [overflow-wrap:anywhere] sm:px-2 ${ALIGN_CLASS[col.align]}`}>
                  {col.template ? renderTemplate(col.template, row) : String(resolvePath(row, col.field) ?? "")}
                </td>
              ))}
              {view.row_actions.length > 0 && (
                <td role="cell" className={`${CARD_CELL_PLAIN} py-1 sm:px-2`}>
                  <div className="flex flex-wrap gap-2 sm:justify-end">
                    {view.row_actions.map((action) => (
                      <ActionButton key={action.id} action={action} row={row} extId={extId} onDone={onAction} />
                    ))}
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
