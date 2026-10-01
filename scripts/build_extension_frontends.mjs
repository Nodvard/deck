#!/usr/bin/env node
/**
 * Baut ALLE Extension-Frontend-Bundles auf einmal -- ersetzt den bisher manuellen
 * `node build.mjs`-Lauf pro Extension.
 *
 * Zwei Modi:
 *   node scripts/build_extension_frontends.mjs            baut alle Bundles neu
 *   node scripts/build_extension_frontends.mjs --check     prueft nur, ob das
 *     committete `dist/index.js` einem frischen Build entspricht, OHNE das
 *     Arbeitsverzeichnis zu veraendern (Exit-Code != 0 + Liste der Abweichungen,
 *     wenn nicht). Das deckt den eigentlichen Fehlerfall ab: eine Aenderung
 *     erfordert einen manuellen Rebuild, sonst dient der Live-Demo ein veraltetes
 *     Bundle. `--check` ist die CI-/Pre-Deploy-Gegenprobe dazu, analog
 *     `scripts/check_core_purity.py`.
 */
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const extensionsDir = path.join(repoRoot, "extensions");
const checkOnly = process.argv.includes("--check");

function findBuildScripts() {
  if (!existsSync(extensionsDir)) return [];
  return readdirSync(extensionsDir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => ({
      extName: e.name,
      buildScript: path.join(extensionsDir, e.name, "frontend", "build.mjs"),
      distFile: path.join(extensionsDir, e.name, "frontend", "dist", "index.js"),
    }))
    .filter((e) => existsSync(e.buildScript));
}

/**
 * cwd MUSS das jeweilige `frontend/`-Verzeichnis sein, nicht das Repo-Root --
 * esbuild betten den Eintragspfad in einen Quellkommentar relativ zum
 * aufrufenden Arbeitsverzeichnis ein (`// src/Foo.tsx` vs. `// extensions/.../
 * src/Foo.tsx`), sonst weicht jeder frische Build spurios vom manuell mit
 * `cd frontend && node build.mjs` erzeugten Bundle ab.
 */
function runBuild(buildScript) {
  execFileSync(process.execPath, [buildScript], { cwd: path.dirname(buildScript), stdio: "pipe" });
}

function buildAll(targets) {
  const failed = [];
  for (const { extName, buildScript } of targets) {
    process.stdout.write(`-- ${extName} --\n`);
    try {
      runBuild(buildScript);
    } catch (err) {
      process.stderr.write(`FEHLER beim Bauen von "${extName}":\n${err.stderr?.toString() ?? err.message}\n`);
      failed.push(extName);
    }
  }
  return failed;
}

/** Baut jede Extension in eine WEGWERF-Kopie ihres dist/-Ordners (nie das
 * committete Bundle ueberschreiben) und vergleicht den Inhalt. */
function checkAll(targets) {
  const failed = [];
  const stale = [];
  for (const { extName, buildScript, distFile } of targets) {
    const before = existsSync(distFile) ? readFileSync(distFile) : null;
    const backupDir = mkdtempSync(path.join(tmpdir(), "nodvard-deck-ext-bundle-check-"));
    const backupFile = path.join(backupDir, "index.js");
    if (before !== null) writeFileSync(backupFile, before);

    try {
      runBuild(buildScript);
    } catch (err) {
      process.stderr.write(`FEHLER beim Bauen von "${extName}":\n${err.stderr?.toString() ?? err.message}\n`);
      failed.push(extName);
      if (before !== null) copyFileSync(backupFile, distFile);
      rmSync(backupDir, { recursive: true, force: true });
      continue;
    }

    const after = existsSync(distFile) ? readFileSync(distFile) : null;
    // `core.autocrlf=true` (dieses Repo) laesst den ausgecheckten Stand mit CRLF auf
    // der Platte liegen, waehrend esbuild (plattformunabhaengig) immer LF schreibt --
    // ohne Normalisierung waere JEDES committete Bundle "veraltet", nur weil es zuletzt
    // ueber `git checkout` statt einen frischen Build auf die Platte kam.
    const normalize = (buf) => (buf === null ? null : buf.toString("utf-8").replace(/\r\n/g, "\n"));
    const matches = before !== null && after !== null && normalize(before) === normalize(after);

    // Committetes Bundle NIE stehen lassen, wie es der frische Build hinterlassen
    // hat -- --check darf das Arbeitsverzeichnis nicht veraendern.
    if (before !== null) copyFileSync(backupFile, distFile);
    else if (after !== null) rmSync(distFile, { force: true });
    rmSync(backupDir, { recursive: true, force: true });

    if (before === null) {
      process.stderr.write(`FEHLT: "extensions/${extName}/frontend/dist/index.js" ist nicht committet.\n`);
      stale.push(extName);
    } else if (!matches) {
      process.stderr.write(
        `VERALTET: "extensions/${extName}/frontend/dist/index.js" weicht von einem frischen Build ab -- ` +
          `"node extensions/${extName}/frontend/build.mjs" laufen lassen und das Ergebnis committen.\n`,
      );
      stale.push(extName);
    } else {
      process.stdout.write(`OK: ${extName}\n`);
    }
  }
  return { failed, stale };
}

const targets = findBuildScripts();
if (targets.length === 0) {
  console.log("Keine Extension-Frontend-Bundles gefunden.");
  process.exit(0);
}

if (!checkOnly) {
  const failed = buildAll(targets);
  if (failed.length > 0) {
    console.error(`\n${failed.length} von ${targets.length} Extension-Bundle(s) fehlgeschlagen: ${failed.join(", ")}`);
    process.exit(1);
  }
  console.log(`\nOK: alle ${targets.length} Extension-Bundles gebaut.`);
  process.exit(0);
}

const { failed, stale } = checkAll(targets);
if (failed.length > 0 || stale.length > 0) {
  const parts = [];
  if (failed.length > 0) parts.push(`${failed.length} Build-Fehler (${failed.join(", ")})`);
  if (stale.length > 0) parts.push(`${stale.length} veraltet/fehlend (${stale.join(", ")})`);
  console.error(`\nFEHLGESCHLAGEN: ${parts.join("; ")}`);
  process.exit(1);
}
console.log(`\nOK: alle ${targets.length} Extension-Bundles aktuell.`);
