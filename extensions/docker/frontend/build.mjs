// Baut das ESM-Bundle für das Docker-Modul, nach demselben Muster wie extensions/proxmox/frontend/build.mjs:
// kein eigenes package.json, esbuild und Pakete wie yaml kommen aus frontend/node_modules.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..", "..");
const nodeModules = path.join(repoRoot, "frontend", "node_modules");
const esbuildBin = path.join(nodeModules, ".bin", process.platform === "win32" ? "esbuild.cmd" : "esbuild");

execFileSync(
  esbuildBin,
  [
    path.join(here, "src", "ContainersPage.tsx"),
    "--bundle",
    // Sonst landet bei einem verlinkten node_modules der echte Pfad in den Herkunftskommentaren des Bundles.
    "--preserve-symlinks",
    "--format=esm",
    "--external:react",
    "--external:react-dom",
    "--jsx=automatic",
    "--outfile=" + path.join(here, "dist", "index.js"),
  ],
  { stdio: "inherit", shell: process.platform === "win32", env: { ...process.env, NODE_PATH: nodeModules } },
);
