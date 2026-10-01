import MarkdownIt from "markdown-it";

import { resolvePath } from "../template";
import type { MarkdownView as MarkdownViewSpec } from "../types";

/**
 * Ersetzt die vorherige
 * handgerollte Teilmenge (nur Ueberschriften/fett/kursiv/Inline-Code/Links/Absaetze,
 * siehe git-Historie) durch echtes CommonMark+GFM -- Listen, Codebloecke, Zitate,
 * Tabellen. `html: false` (Default) bleibt bewusst so: rohes HTML im Markdown-Text
 * wird escaped statt gerendert, dieselbe XSS-Haltung wie die vorherige
 * escapeHtml-zuerst-Strategie, jetzt vom Parser selbst statt von Hand durchgesetzt.
 * `linkify: true` erkennt nackte URLs zusaetzlich zu `[text](url)`.
 */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true });

const defaultLinkOpen =
  md.renderer.rules.link_open ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noreferrer");
  return defaultLinkOpen(tokens, idx, options, env, self);
};

export function MarkdownView({ view, data }: { view: MarkdownViewSpec; data: unknown }) {
  const content = String(resolvePath(data, view.content_field) ?? "");
  // eslint-disable-next-line react/no-danger -- markdown-it mit html:false escaped eingebettetes HTML, siehe Docstring oben.
  return <div className="md-content max-w-none text-sm" dangerouslySetInnerHTML={{ __html: md.render(content) }} />;
}
