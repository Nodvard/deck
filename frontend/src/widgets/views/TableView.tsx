import { renderTemplate, resolvePath } from "../template";
import type { TableView as TableViewSpec } from "../types";
import { ActionButton } from "./ActionButton";

const ALIGN_CLASS: Record<"left" | "right" | "center", string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function TableView({ view, data, extId, onAction }: { view: TableViewSpec; data: unknown; extId: string; onAction: () => void }) {
  const rows = Array.isArray(data) ? data : [];

  if (rows.length === 0) {
    return <p className="text-sm opacity-60">{view.empty_text}</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 text-xs uppercase opacity-60">
            {view.columns.map((col) => (
              <th key={col.field} className={`px-2 py-1 font-medium ${ALIGN_CLASS[col.align]}`} style={col.width ? { width: col.width } : undefined}>
                {col.label}
              </th>
            ))}
            {view.row_actions.length > 0 && <th className="px-2 py-1" />}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((row, idx) => (
            <tr key={idx}>
              {view.columns.map((col) => (
                <td key={col.field} className={`px-2 py-1 ${ALIGN_CLASS[col.align]}`}>
                  {col.template ? renderTemplate(col.template, row) : String(resolvePath(row, col.field) ?? "")}
                </td>
              ))}
              {view.row_actions.length > 0 && (
                <td className="flex justify-end gap-2 px-2 py-1">
                  {view.row_actions.map((action) => (
                    <ActionButton key={action.id} action={action} row={row} extId={extId} onDone={onAction} />
                  ))}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
