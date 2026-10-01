// Baut das ESM-Bundle fuer die system-Extension -- identisches Muster wie
// extensions/proxmox/frontend/build.mjs (siehe dort fuer die volle Begruendung).
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..", "..");
const esbuildBin = path.join(
  repoRoot, "frontend", "node_modules", ".bin", process.platform === "win32" ? "esbuild.cmd" : "esbuild",
);

execFileSync(
  esbuildBin,
  [
    path.join(here, "src", "SystemPage.tsx"),
    "--bundle",
    "--format=esm",
    "--external:react",
    "--external:react-dom",
    "--jsx=automatic",
    "--outfile=" + path.join(here, "dist", "index.js"),
  ],
  { stdio: "inherit", shell: process.platform === "win32" },
);
