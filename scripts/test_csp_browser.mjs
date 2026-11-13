import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Baut die vorhandene Vorschau und prüft sie unter der tatsächlich konfigurierten CSP. */
export async function testCspBrowser(page) {
  const { build } = await import("../frontend/node_modules/vite/dist/node/index.js");
  const scratch = path.join(root, "work");
  mkdirSync(scratch, { recursive: true });
  const directory = mkdtempSync(path.join(scratch, "csp-test-"));
  const previous = process.cwd();
  let server;
  const errors = [];
  const onConsole = (message) => {
    if (/content.security.policy|violates.*directive|refused to (apply|load|execute|connect)/i.test(message.text())) errors.push(message.text());
  };
  const onPageError = (error) => errors.push(error.message);
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  try {
    process.chdir(path.join(root, "frontend"));
    await build({
      configFile: path.join(root, "frontend/vite.preview.config.ts"), configLoader: "runner", logLevel: "error",
      build: { outDir: directory, rollupOptions: { input: path.join(root, "frontend/preview.html") } },
    });
    process.chdir(previous);
    const source = readFileSync(path.join(root, "backend/src/nodvard_deck/main.py"), "utf8");
    const template = [...source.match(/\nSPA_CSP = \(([\s\S]*?)\n\)/)[1].matchAll(/"([^"]*)"/g)].map((match) => match[1]).join("");
    assert(!template.includes("unsafe-inline") && !template.includes("*"));
    server = createServer((request, response) => {
      const url = new URL(request.url, "http://localhost");
      const filename = path.resolve(directory, "." + url.pathname);
      if (!filename.startsWith(directory + path.sep)) { response.writeHead(404).end(); return; }
      try {
        let content = readFileSync(filename);
        const type = path.extname(filename);
        response.setHeader("Content-Type", ({ ".html": "text/html", ".js": "text/javascript", ".css": "text/css" })[type] ?? "application/octet-stream");
        if (type === ".html") {
          const nonce = randomBytes(32).toString("base64url");
          content = Buffer.from(content.toString().replace("<head>", `<head><meta name="csp-nonce" content="${nonce}">`));
          response.setHeader("Content-Security-Policy", template.replace("__DECK_CSP_NONCE__", nonce).replace("__DECK_CSP_SOCKET__", `ws://127.0.0.1:${server.address().port}`) + "; script-src 'self'; frame-ancestors 'none'");
          response.setHeader("Cache-Control", "no-store");
        }
        response.end(content);
      } catch { response.writeHead(404).end(); }
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    await page.addInitScript(() => {
      window.cspViolations = [];
      document.addEventListener("securitypolicyviolation", (event) => window.cspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`));
    });
    const results = [];
    for (const [route, text] of [
      ["/", "Widgets"], ["/apps", "Apps"], ["/settings/appearance", "Darstellung"],
      ["/ext/docker/matrix", "Docker"], ["/ext/shield/soc", "Nodvard Shield"],
      ["/terminal", "Terminal"], ["/ext/documents/documents", "Dokumente"],
    ]) {
      const response = await page.goto(`http://127.0.0.1:${server.address().port}/preview.html?path=${encodeURIComponent(route)}`);
      assert.equal(response.status(), 200);
      await page.getByRole("heading", { name: text, exact: true }).first().waitFor();
      await page.waitForFunction(() => !document.body.textContent.includes("Lade Extension-Seite"));
      if (route === "/apps") {
        await page.getByRole("button", { name: "App hinzufügen", exact: true }).click();
        await page.getByRole("dialog", { name: "App hinzufügen" }).waitFor();
        await page.waitForFunction(() => getComputedStyle(document.body).overflow === "hidden");
        await page.keyboard.press("Escape");
        await page.getByRole("dialog").waitFor({ state: "hidden" });
        await page.waitForFunction(() => getComputedStyle(document.body).overflow !== "hidden");
      }
      if (route === "/terminal") {
        await page.evaluate(() => {
          const fetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            if (typeof input === "string" && input.endsWith("/terminal/sessions") && init?.method === "POST") {
              return Promise.resolve(new Response(JSON.stringify({ session_id: "csp-test", ws_url: "/csp-terminal" }), {
                headers: { "Content-Type": "application/json" },
              }));
            }
            return fetch(input, init);
          };
          const Socket = window.WebSocket;
          window.WebSocket = class extends Socket {
            constructor(...args) {
              super(...args);
              window.cspTerminalSocket = this;
            }
          };
        });
        await page.getByRole("button", { name: "Terminal öffnen", exact: true }).first().click();
        await page.locator(".xterm-screen").waitFor();
        await page.waitForFunction(() => typeof window.cspTerminalSocket?.onmessage === "function");
        await page.evaluate(() => {
          const data = new TextEncoder().encode("\x1b[38;2;18;152;186m\x1b[48;2;101;67;33mCSP-TRUECOLOR\x1b[0m\r\n").buffer;
          window.cspTerminalSocket.onmessage(new MessageEvent("message", { data }));
        });
        const output = page.locator(".xterm-rows span").filter({ hasText: "CSP-TRUECOLOR" });
        await output.waitFor();
        assert.deepEqual(await output.evaluate((span) => {
          const style = getComputedStyle(span);
          return [style.color, style.backgroundColor];
        }), ["rgb(18, 152, 186)", "rgb(101, 67, 33)"], "Terminalfarben unter der erzwungenen CSP");
      }
      assert.deepEqual(await page.evaluate(() => window.cspViolations), [], route);
      assert.deepEqual(errors, [], route);
      assert(await page.evaluate(() => [...document.querySelectorAll("style")].every((style) => style.nonce === document.querySelector('meta[name="csp-nonce"]').content)), route);
      results.push(route);
    }
    await page.evaluate(() => {
      const element = document.createElement("div");
      element.id = "csp-probe";
      document.body.append(element);
      document.addEventListener("securitypolicyviolation", (event) => {
        window.cspProbeDirective = event.effectiveDirective;
      }, { once: true });
      element.setAttribute("style", "position: fixed");
    });
    await page.waitForFunction(() => window.cspProbeDirective === "style-src-attr");
    assert.equal(await page.locator("#csp-probe").evaluate((element) => getComputedStyle(element).position),
      "static", "Fremde Stilattribute bleiben gesperrt");
    return results;
  } finally {
    process.chdir(previous);
    page.off("console", onConsole);
    page.off("pageerror", onPageError);
    if (server) await new Promise((resolve) => server.close(resolve));
    assert(directory.startsWith(scratch + path.sep + "csp-test-"));
    rmSync(directory, { recursive: true, force: true });
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { chromium } = createRequire(path.join(root, "frontend/package.json"))("playwright");
  const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
  try { console.log(await testCspBrowser(await browser.newPage())); }
  finally { await browser.close(); }
}
