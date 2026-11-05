import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { expect, it } from "vitest";

it("hält die Import-Map beim Vite-Build außerhalb von Kommentaren", () => {
  const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
  for (const comment of html.matchAll(/<!--[\s\S]*?-->/g)) expect(comment[0]).not.toMatch(/<script\b/i);
  const parsed = new DOMParser().parseFromString(html, "text/html");
  expect(parsed.querySelectorAll('script[type="importmap"]')).toHaveLength(1);
  expect([...parsed.querySelectorAll("script")].map((script) => script.type)).toEqual(["importmap", "module"]);
});

/** Kern und Erweiterungen teilen denselben Vertrag für dynamische Stile. */
it("enthält keine Stilattribute oder Ereignisattribute in HTML und JSX", () => {
  const root = resolve(process.cwd(), "..");
  const dirs = [resolve(root, "frontend/src")];
  for (const extension of readdirSync(resolve(root, "extensions"))) {
    dirs.push(resolve(root, "extensions", extension, "frontend/src"));
  }
  const found = [];
  function inspect(dir) {
    let files;
    try { files = readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const file of files) {
      const name = resolve(dir, file.name);
      if (file.isDirectory()) inspect(name);
      else if (name.endsWith(".tsx") && !name.includes(".test.")) {
        const source = ts.createSourceFile(name, readFileSync(name, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
        function visit(node) {
          if (ts.isJsxAttribute(node) && node.name.getText(source) === "style") found.push(name);
          ts.forEachChild(node, visit);
        }
        visit(source);
      }
    }
  }
  dirs.forEach(inspect);
  expect(found).toEqual([]);
  for (const file of ["index.html", "preview.html", "terminal-test.html"]) {
    const html = readFileSync(resolve(root, "frontend", file), "utf8");
    expect(html).not.toMatch(/\s(?:style|on\w+)\s*=/i);
    expect(html).not.toMatch(/<style\b|https?:\/\/[^"\s]*cdn/i);
    if (file !== "index.html") expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/i);
  }
});
