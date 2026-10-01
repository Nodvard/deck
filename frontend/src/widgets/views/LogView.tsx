import { useEffect, useRef } from "react";

import { resolvePath } from "../template";
import type { LogView as LogViewSpec } from "../types";

export function LogView({ view, data }: { view: LogViewSpec; data: unknown }) {
  const raw = resolvePath(data, view.lines_field);
  const lines = (Array.isArray(raw) ? raw : []).slice(-view.max_lines);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (view.follow && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines.length, view.follow]);

  return (
    <div ref={containerRef} className="max-h-56 overflow-y-auto rounded bg-black/40 p-2 font-mono text-xs leading-relaxed">
      {lines.length === 0 ? (
        <p className="opacity-50">Keine Zeilen</p>
      ) : (
        lines.map((line, idx) => (
          <p key={idx} className="whitespace-pre-wrap">
            {String(line)}
          </p>
        ))
      )}
    </div>
  );
}
