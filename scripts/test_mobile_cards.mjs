import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const cwd = fileURLToPath(new URL("../frontend", import.meta.url));
const suites = [
  ["vitest.config.ts", [
    "src/routes/Cockpit.test.tsx", "src/routes/UsersPage.test.tsx",
    "src/routes/settings/AuditSettingsLabels.test.tsx", "src/routes/settings/ExtensionsSettingsGroups.test.tsx",
    "src/components/FolderPickerDialog.test.tsx", "src/components/DialogWidths.test.js", "src/widgets/views/TableView.test.tsx",
  ]],
  ["vitest.extensions.config.ts", [
    "backups/BackupsPage", "docker/Registries", "documents/DocumentsPage", "inventory/InventoryPage",
    "proxmox/ProxmoxNodePage", "shield/UpdatesTab", "synology/SynologyPage", "system/SystemPage",
  ].map(entry => {
    const [module, name] = entry.split("/");
    return `../extensions/${module}/frontend/src/${name}.test.tsx`;
  })],
];
for (const [config, files] of suites) {
  const result = spawnSync(process.execPath, ["node_modules/vitest/vitest.mjs", "run", "--configLoader", "runner", "--config", config, "--maxWorkers", "1", "-t", "390 px", ...files], {
    cwd, stdio: "inherit", env: { ...process.env, DECK_MOBILE_BROWSER: "1" },
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
