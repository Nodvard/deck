// Baut das ESM-Bundle fuer die hello-world-Extension (docs/02-EXTENSION-API.md §5:
// "kann mit Vite, esbuild oder tsc gebaut werden"). Nutzt bewusst das schon im
// Kern-Frontend installierte esbuild (frontend/node_modules) statt einer eigenen
// npm-Installation fuer diese eine Demo-Datei -- Extensions mit einem echten
// Frontend-Paket haetten ihr eigenes package.json/node_modules, das ist hier nur der
// minimale Beweis, dass der ESM-Loader/Import-Map-Shim mit einem ECHTEN, gebauten
// Bundle funktioniert, keine Vorlage fuer ein vollstaendiges Extension-Frontend-Setup.
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
    path.join(here, "src", "HelloPage.tsx"),
    "--bundle",
    "--format=esm",
    "--external:react",
    "--external:react-dom",
    "--jsx=automatic",
    "--outfile=" + path.join(here, "dist", "index.js"),
  ],
  { stdio: "inherit", shell: process.platform === "win32" },
);
