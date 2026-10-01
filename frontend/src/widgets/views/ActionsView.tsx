import type { ActionsView as ActionsViewSpec } from "../types";
import { ActionButton } from "./ActionButton";

export function ActionsView({ view, data, extId, onAction }: { view: ActionsViewSpec; data: unknown; extId: string; onAction: () => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {view.actions.map((action) => (
        <ActionButton key={action.id} action={action} row={data} extId={extId} onDone={onAction} />
      ))}
    </div>
  );
}
