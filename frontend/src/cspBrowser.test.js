// @vitest-environment node
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import { expect, test } from "vitest";

test("der CSP-Browsercheck ist mit Chromium im Testablauf hinterlegt", () => {
  const read = (file) => readFileSync(new URL(file, import.meta.url), "utf8");
  const pkg = JSON.parse(read("../package.json"));
  const lock = JSON.parse(read("../package-lock.json"));
  const version = pkg.devDependencies.playwright;
  expect(version).toMatch(/^\d+\.\d+\.\d+$/);
  expect(lock.packages[""].devDependencies.playwright).toBe(version);
  expect(lock.packages["node_modules/playwright"].version).toBe(version);
  expect(lock.packages["node_modules/playwright"].dependencies["playwright-core"]).toBe(version);
  expect(lock.packages["node_modules/playwright-core"].version).toBe(version);
  expect(pkg.scripts["test:csp"]).toBe("node ../scripts/test_csp_browser.mjs");
  expect(pkg.scripts["test:csp:install"]).toBe("playwright install chromium");

  const steps = parse(read("../../.github/workflows/ci.yml")).jobs.frontend.steps;
  const commands = steps.filter((step) => step["working-directory"] === "frontend").map((step) => step.run);
  const install = commands.indexOf("npm run test:csp:install -- --with-deps");
  expect(commands.indexOf("npm ci")).toBeGreaterThanOrEqual(0);
  expect(install).toBeGreaterThan(commands.indexOf("npm ci"));
  expect(commands.indexOf("npm run test:csp")).toBeGreaterThan(install);
});
