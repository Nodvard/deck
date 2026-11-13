import { describe, expect, it } from "vitest";
import ts from "typescript";

const sources = import.meta.glob([
  "../preview/fixtures.ts",
  "../routes/ActionsPage.tsx",
  "../routes/FilesPage.test.tsx",
  "../routes/DashboardPage.test.tsx",
  "../routes/HostPage.tsx",
  "../routes/AppShell.tsx",
  "../main.tsx",
  "../routes/FilesPage.tsx",
  "../routes/settings/BackupCard.tsx",
  "../components/AppTile.tsx",
  "../routes/ActionsPage.test.tsx",
  "../routes/HostHistory.tsx",
  "../routes/TerminalPage.test.tsx",
  "../state/auth.test.ts",
  "../widgets/WidgetRenderers.test.tsx",
  "../widgets/views/ActionButton.test.tsx",
  "../widgets/views/ActionButton.tsx",
  "../components/AppsSection.tsx",
  "../components/CommandPalette.tsx",
  "../lib/api.test.ts",
  "../routes/RequireAuth.test.tsx",
  "../routes/UsersPage.test.tsx",
  "../components/AppDialog.tsx",
  "../components/NotificationBell.tsx",
  "../routes/Cockpit.tsx",
  "../routes/ConsolePage.test.tsx",
  "../routes/HostPage.test.tsx",
  "../routes/LoginPage.tsx",
  "../widgets/template.test.ts",
  "../routes/settings/hosts/AccessPanel.tsx",
  "../lib/firstSteps.ts",
  "../routes/ConsolePage.tsx",
  "../components/FirstStepsCard.tsx",
  "../lib/keyboardLayouts.test.ts",
  "../lib/tokenRefresh.test.tsx",
  "../routes/settings/RestoreFlow.tsx",
  "../routes/settings/UpdatesCard.tsx",
  "../components/TimeSeriesChart.tsx",
  "../lib/apps.actions.test.tsx",
  "../routes/HostPageConfirm.test.tsx",
  "../routes/LoginPage.test.tsx",
  "../routes/WidgetPickerDialog.tsx",
  "../routes/settings/hosts/PasswordSetup.tsx",
  "../components/FolderPickerDialog.tsx",
  "../components/GlobalDialogs.tsx",
  "../routes/UsersPage.tsx",
  "../routes/settings/ui.tsx",
  "../lib/actions.ts",
  "../routes/SetupPage.tsx",
  "../state/auth.ts",
  "../routes/RequireAuth.tsx",
  "../routes/TerminalPage.tsx",
  "../routes/SetupPage.test.tsx",
], { eager: true, query: "?raw", import: "default" }) as Record<string, string>;

// Vitest ersetzt CSS-Importe; den Quelltext direkt lesen.
const nodeFs = "node:" + "fs";
const { readFileSync } = await import(/* @vite-ignore */ nodeFs) as { readFileSync: (path: URL, encoding: "utf8") => string };
const readSource = (relative: string) => readFileSync(new URL(relative, import.meta.url), "utf8");
sources["../styles/index.css"] = readSource("../styles/index.css");

// Interne Kennungen und Suchaliase gehören nicht zur deutschen Prosa.
const internal = /^[a-z0-9/#?&=._-]+$/;
const spelling = /\b(?:fuer|ueber|waehrend|zurueck|oeffne(?:n|t)|loesche(?:n|t)|schliesse(?:n|t)|hinzufuegen|bestaetigt|waehlen|naechste[nr]?|laeuft|koenn(?:en|te)|waere|haelt|haengt|laedt|laesst|pruefen|aendert|aeltere[nr]?|zusaetzlich|geoeffnet|ungueltig|fuenf|ausserhalb)\b/i;
const history = /Live gefunden|live gefunden|Bisher gab es|frühere Pragmatik|wie bis 0\.8|ging bisher|gingen bisher/;
const prose = (text: string) => text.replace(/`[^`]*`/g, "");

function texts(file: string, source: string): { comments: string[]; labels: string[] } {
  if (file.endsWith(".css")) return { comments: source.match(/\/\*[\s\S]*?\*\//g) ?? [], labels: [] };
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const comments = new Set<string>();
  const labels: string[] = [];
  const visit = (node: ts.Node) => {
    for (const range of [...(ts.getLeadingCommentRanges(source, node.pos) ?? []), ...(ts.getTrailingCommentRanges(source, node.end) ?? [])]) {
      comments.add(source.slice(range.pos, range.end));
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node) || ts.isJsxText(node)) {
      const text = node.getText(ast).replace(/^["'`]|["'`]$/g, "");
      if (!internal.test(text) && !file.endsWith("/CommandPalette.tsx")) labels.push(text);
    }
    ts.forEachChild(node, visit);
  };
  visit(ast);
  return { comments: [...comments], labels };
}

describe("Deutsche Texte der bereinigten Kern-Dateien", () => {
  for (const [file, source] of Object.entries(sources)) {
    it(file, () => {
      expect(source.trim().length).toBeGreaterThan(0);
      expect(source).not.toMatch(/^(?:<{7}|={7}|>{7})(?: .*)?$/m);
      const { comments, labels } = texts(file, source);
      for (const comment of comments) {
        expect(prose(comment)).not.toMatch(spelling);
        expect(comment).not.toMatch(history);
      }
      for (const label of labels) {
        expect(label).not.toMatch(spelling);
        expect(label).not.toMatch(/\s[–—]\s/);
      }
      const header = source.match(/^\/\*\*[\s\S]*?\*\//)?.[0];
      if (header) expect(header.split("\n").length).toBeLessThanOrEqual(3);
      if (/\/(FilesPage|FolderPickerDialog)\.tsx$/.test(file)) expect(source).not.toMatch(/\u{1f517}|\u{1f4c1}/u);
      if (file.endsWith("/preview/fixtures.ts")) expect(source).not.toMatch(/\bKI\b/);
    });
  }
});
