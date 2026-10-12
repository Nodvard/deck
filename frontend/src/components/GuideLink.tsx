import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

import { guideUrl, type GuideSection } from "../lib/docs";

/**
 * Link in die Anleitung (öffnet in neuem Tab). Ohne `section` führt er an den Anfang der Anleitung, sonst an den
 * passenden Abschnitt. Das Symbol zeigt, dass es die Seite verlässt.
 */
export function GuideLink({ section, children, className = "", testId }: {
  section?: GuideSection;
  children?: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <a
      href={guideUrl(section)}
      target="_blank"
      rel="noopener noreferrer"
      data-testid={testId}
      className={`inline-flex items-center gap-1 underline underline-offset-2 hover:text-white ${className}`}
    >
      {children ?? "Anleitung"}
      <ExternalLink size={12} aria-hidden className="flex-none" />
      <span className="sr-only"> (öffnet in neuem Tab)</span>
    </a>
  );
}
