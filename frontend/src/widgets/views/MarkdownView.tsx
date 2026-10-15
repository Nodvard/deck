import MarkdownIt from "markdown-it";

import { localImageUrl } from "../../lib/branding";
import { resolvePath } from "../template";
import type { MarkdownView as MarkdownViewSpec } from "../types";

/**
 * Markdown als CommonMark+GFM (Listen, Codeblöcke, Zitate, Tabellen). `html: false` (Default) bleibt bewusst: Rohes HTML
 * im Markdown-Text wird escaped statt gerendert (XSS-Schutz, vom Parser selbst durchgesetzt). `linkify: true` erkennt
 * nackte URLs zusätzlich zu `[text](url)`.
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

const defaultImage =
  md.renderer.rules.image ?? ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

/**
 * Bilder nur von diesem Server oder als `data:image/…` (wie Logo und Favicon, `img-src 'self' data: blob:` in der CSP).
 * Ein fremdes Bild wird ein Link mit dem Alternativtext: Die Seite lädt nichts von fremden Adressen, und der Inhalt
 * eines Widgets verrät so niemandem draußen, wer es gerade ansieht.
 */
md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  const src = String(token.attrGet("src") ?? "");
  if (localImageUrl(src)) return defaultImage(tokens, idx, options, env, self);
  const alt = self.renderInlineAsText(token.children ?? [], options, env) || src;
  const href = md.utils.escapeHtml(src);
  return `<a href="${href}" target="_blank" rel="noreferrer">Bild: ${md.utils.escapeHtml(alt)}</a>`;
};

export function MarkdownView({ view, data }: { view: MarkdownViewSpec; data: unknown }) {
  const content = String(resolvePath(data, view.content_field) ?? "");
  // eslint-disable-next-line react/no-danger -- markdown-it mit html:false escaped eingebettetes HTML, siehe Docstring oben.
  return <div className="md-content max-w-none text-sm" dangerouslySetInnerHTML={{ __html: md.render(content) }} />;
}
