/**
 * Testdaten fuer die Design-Vorschau -- typische Homelab-Beispieldaten (Hosts, IPs,
 * Auslastung, Container), frei erfunden. Nur fuer preview.html.
 */
const page = (ext_id: string, id: string, path: string, title: string, icon: string, nav_section: string, nav_order: number) => ({
  ext_id, id, path, title, icon, nav_section, nav_order, permissions: [], component: "X", mobile: "widgets", show_in_nav: true,
});

const PAGES = [
  page("proxmox", "nodes", "/nodes", "Proxmox", "server", "Infrastruktur", 10),
  page("backups", "backups", "/backups", "Backups", "database-backup", "Infrastruktur", 20),
  page("service-matrix", "matrix", "/matrix", "Service-Matrix", "layout-grid", "Infrastruktur", 30),
  page("gameserver", "servers", "/gameservers", "Gameserver", "gamepad-2", "Infrastruktur", 40),
  page("nexus-soc", "soc", "/soc", "Nodvard Shield", "shield-alert", "Sicherheit", 20),
  page("scripts", "scripts", "/scripts", "Skripte", "terminal-square", "Automatisierung", 20),
  page("documents", "documents", "/documents", "Dokumente", "file-text", "Dokumente", 10),
  page("inventory", "inventory", "/inventory", "Inventar", "package", "Inventar", 10),
];

type PreviewCredential = { kind: "ssh_key" | "ssh_password"; username: string; port: number } | null;

const host = (
  id: string, display_name: string, address: string, kind: string | null, status: string,
  extra: { name?: string; provider_ext_id?: string | null; os_family?: string; credential?: PreviewCredential } = {},
) => ({
  id, name: extra.name ?? display_name.toLowerCase(), display_name, address, os_family: extra.os_family ?? "linux", kind, tags: [],
  managed_tags: [] as string[],
  credential: extra.credential
    ? { id: `c-${id}`, kind: extra.credential.kind, username: extra.credential.username, port: extra.credential.port }
    : null,
  is_managed: true, enabled: true, status, last_seen_at: null,
  provider_ext_id: extra.provider_ext_id === undefined ? "proxmox" : extra.provider_ext_id, provider_ref: null, created_at: "", updated_at: "",
});

const KEY_ROOT: PreviewCredential = { kind: "ssh_key", username: "root", port: 22 };
const KEY_LATTICE: PreviewCredential = { kind: "ssh_key", username: "lattice", port: 22 };

const HOSTS = [
  host("n-pve2", "pve2", "192.168.2.11", "hypervisor", "up", { credential: KEY_ROOT }),
  host("n-pve1", "pve1", "192.168.2.12", "hypervisor", "up", { credential: KEY_ROOT }),
  host("g-docker", "docker", "192.168.2.21", "vm", "running", { credential: KEY_LATTICE }),
  host("g-monitoring", "monitoring", "192.168.2.22", "vm", "running", { credential: { kind: "ssh_password", username: "admin", port: 22 } }),
  host("g-ki", "ki-server", "192.168.2.43", "vm", "running"),
  host("g-valheim", "game-win", "192.168.2.24", "vm", "running", { os_family: "windows" }),
  host("g-dmini", "docker-lxc", "192.168.2.25", "lxc", "running"),
  host("g-bastion", "bastion", "192.168.2.26", "lxc", "running"),
  host("h-pi", "Raspberry Pi", "192.168.2.31", null, "up", { name: "bastel-pi", provider_ext_id: null, credential: KEY_LATTICE }),
];

const HOST_ID_BY_NAME: Record<string, string> = Object.fromEntries(HOSTS.map((h) => [h.display_name, h.id]));
HOSTS.find((h) => h.id === "g-valheim")!.tags = ["gameserver", "windows"] as never[];
HOSTS.find((h) => h.id === "g-docker")!.tags = ["docker"] as never[];
HOSTS.find((h) => h.id === "h-pi")!.tags = ["docker"] as never[];
for (const h of HOSTS) {
  if (!h.provider_ext_id) continue;
  h.managed_tags = ["proxmox", h.kind ?? "proxmox"].filter((t, i, a) => a.indexOf(t) === i);
  h.tags = [...new Set([...h.managed_tags, ...h.tags])] as never[];
}

/** Werkzeug-Kacheln der Server-Seite, wie sie die Extensions per register_host_tool melden. */
function hostTools(id: string) {
  const h = HOSTS.find((x) => x.id === id);
  if (!h) return undefined;
  if (previewState.start) return []; // frisch eingerichtet: noch keine Module, die Werkzeuge mitbringen
  if (previewState.ohneProxmoxMin) return [];
  const tools: object[] = [
    { ext_id: "scripts", id: "scripts", title: "Skripte", description: "Gespeicherte Skripte für diesen Host ansehen und ausführen", icon: "terminal-square", category: "control", href: `/ext/scripts/scripts?host=${id}`, order: 100 },
  ];
  if (h.kind === "hypervisor") {
    tools.push({ ext_id: "proxmox", id: "node", title: "Hardware, Datenträger & Updates", description: "Auslastung, SMART-Zustand der Platten, Paket-Updates", icon: "hard-drive", category: "monitoring", href: `/ext/proxmox/nodes?host=${id}`, order: 100 });
  }
  if (h.kind === "vm" || h.kind === "lxc") {
    tools.push({ ext_id: "proxmox", id: "tasks", title: "Aufgabenverlauf", description: "Wer hat wann was auf dem Knoten getan", icon: "activity", category: "monitoring", href: `/ext/proxmox/nodes?host=${id}&tasks=1`, order: 200 });
    tools.push({ ext_id: "backups", id: "backups", title: "Backups", description: "Backup-Jobs, letzter Lauf, vorhandene Sicherungen", icon: "database-backup", category: "data", href: `/ext/backups/backups?host=${id}`, order: 100 });
    tools.push({ ext_id: "proxmox", id: "guest", title: "Hardware, Netzwerk & Snapshots", description: "Kerne, RAM, Disks mit Speicherort, Netzwerk, Snapshots", icon: "server", category: "settings", href: `/ext/proxmox/nodes?host=${id}`, order: 100 });
  }
  if ((h.tags as string[]).includes("gameserver") && !previewState.ohneProxmox) {
    tools.push({ ext_id: "gameserver", id: "gameserver", title: "Gameserver", description: "Join-Code, Spieler, Welt-Sicherung, Neustart", icon: "gamepad-2", category: "services", href: `/ext/gameserver/gameservers?host=${id}`, order: 100 });
  }
  if (previewState.ohneProxmox && !previewState.ohneProxmoxMin && h.os_family === "linux") {
    tools.push({ ext_id: "system", id: "system", title: "System-Monitor", description: "Live wie im Task-Manager: Kerne, Temperaturen, Platten, Netz, Prozesse", icon: "cpu", category: "monitoring", href: `/ext/system/system?host=${id}`, order: 50 });
  }
  if ((h.tags as string[]).includes("docker") && !previewState.ohneProxmoxMin) {
    tools.push({ ext_id: "service-matrix", id: "containers", title: "Container", description: "Docker-Container starten, stoppen, Live-Logs", icon: "layout-grid", category: "services", href: `/ext/service-matrix/matrix?host=${id}`, order: 100 });
  }
  return tools;
}

const action = (action_type: string, label: string, default_risk: string, source: "host" | "global", extra: object = {}) => ({
  action_type, label, description: null, icon: null, default_risk, permissions: [], host_bound: true, confirm_text: null,
  command_field: null, params_schema: null, source, ...extra,
});

function hostActions(id: string) {
  const h = HOSTS.find((x) => x.id === id);
  if (!h) return undefined;
  const common = [
    action("shell.exec", "Shell-Befehl ausführen", "high", "global", { permissions: ["hosts.execute"], command_field: "command" }),
    action("container.restart", "Container neu starten", "medium", "global", {
      permissions: ["hosts.execute"],
      params_schema: { type: "object", properties: { container: { type: "string", title: "Container" } }, required: ["container"] },
    }),
  ];
  if (h.kind !== "vm" && h.kind !== "lxc") return common;
  return [
    ...common,
    action("vm.start", "Starten", "low", "host"),
    action("vm.reboot", "Neustarten", "high", "host", { confirm_text: "Die VM wird neugestartet. Fortfahren?" }),
    action("vm.snapshot", "Snapshot erstellen", "medium", "host"),
    action("vm.snapshot_rollback", "Snapshot zurückrollen", "high", "host", {
      params_schema: { type: "object", properties: { snapname: { type: "string" } }, required: ["snapname"] },
    }),
    // Eigenes Formular auf der Proxmox-Seite -- die Server-Seite blendet es aus.
    action("vm.config_set", "Hardware ändern", "medium", "host", {
      params_schema: { type: "object", properties: { changes: { type: "object" } }, required: ["changes"] },
    }),
  ];
}

const GB = 1024 ** 3;
const METRICS: Record<string, object> = {
  "n-pve2": { values: { cpu_percent: 14.2, mem_used_bytes: 11.1 * GB, mem_total_bytes: 13.4 * GB, uptime_s: 38305 }, sampled_at: "" },
  "n-pve1": { values: { cpu_percent: 23.5, mem_used_bytes: 3.3 * GB, mem_total_bytes: 5.6 * GB, uptime_s: 39379 }, sampled_at: "" },
};

/** Letzter Messwert aus dem Verlauf, wie ihn `GET /hosts/metrics/latest` liefert (Cockpit-Karten der Linux-Server). */
function latestOf(cpu: number, memUsedGb: number, memTotalGb: number, diskUsedGb: number, diskTotalGb: number, uptimeS: number, ageS = 8) {
  const pct = (used: number, total: number) => Math.round((1000 * used) / total) / 10;
  return {
    cpu, mem: pct(memUsedGb, memTotalGb), disk: pct(diskUsedGb, diskTotalGb),
    mem_used_bytes: memUsedGb * GB, mem_total_bytes: memTotalGb * GB, disk_used_bytes: diskUsedGb * GB, disk_total_bytes: diskTotalGb * GB,
    uptime_s: uptimeS, at: new Date(Date.now() - ageS * 1000).toISOString(), age_s: ageS, stale: ageS > 120,
  };
}

/** Server -> letzter Wert. Hypervisor-Knoten stehen nie drin (sie werden live gefragt). */
const LATEST: Record<string, ReturnType<typeof latestOf>> = {
  "h-pi": latestOf(6.4, 1.7, 3.7, 22.6, 58.0, 70454),
};

const svc = (host: string, name: string, image: string, port: number | null, state = "running") => ({
  // Wie der echte ServiceCatalog: host_id ist die Host-ID des Dashboards, host der Anzeigename.
  id: `${host}:${name}`, name, host, host_id: HOST_ID_BY_NAME[host] ?? host, state, tone: state === "running" ? "good" : "danger",
  url: port ? `http://192.168.2.${host === "docker" ? 21 : host === "docker-lxc" ? 25 : 31}:${port}` : null, image,
});

const OVERVIEW = {
  services: [
    svc("docker", "nextcloud-app", "nextcloud:29", 8081),
    svc("docker", "grafana", "grafana/grafana:11", 3000),
    svc("docker", "prometheus", "prom/prometheus", 9090),
    svc("docker", "portainer", "portainer/portainer-ce", 9443),
    svc("docker", "cadvisor", "gcr.io/cadvisor/cadvisor", 8082),
    svc("docker", "nextcloud-db", "mariadb:11", null),
    svc("docker", "redis", "redis:7", null),
    svc("docker-lxc", "uptime-kuma", "louislam/uptime-kuma", 3001),
    svc("docker-lxc", "ntfy-relay", "binwiederhier/ntfy", 8090),
    svc("Raspberry Pi", "pihole", "pihole/pihole", 8088),
    svc("Raspberry Pi", "npm-nginx-1", "jc21/nginx-proxy-manager", 81),
    svc("Raspberry Pi", "deploy-nodvard-deck-1", "nodvard-deck:latest", 8080),
    svc("Raspberry Pi", "portainer_agent", "portainer/agent", null),
    svc("docker", "clamav", "clamav/clamav", null, "exited"),
  ],
  services_running: 13,
  backups: { total: 5, ok: 3, failed: 0, running: 0, unknown: 2, failed_names: [], unreachable_names: [] },
  pending_actions: 1,
  unread_notifications: 3,
  attention: [
    { id: "a1", ts: new Date(Date.now() - 42 * 60_000).toISOString(), severity: "warning", title: "Datenträger auf pve1: 28 % Rest", source_ext_id: "proxmox" },
  ],
  errors: [],
  generated_at: Date.now() / 1000,
};

const w = (ext_id: string, id: string, title: string, icon: string, data_endpoint: string, view: object) => ({
  id, ext_id, title, icon, description: null, size: { w: 2, h: 2, min_w: 1, min_h: 1 },
  refresh: { interval_s: null, ws_channel: null }, data_endpoint, view, permissions: [], component: null, default_enabled: true,
});
const listItem = (title: string, subtitle: string | null, badge: object | null, actions: object[] = []) => ({ title, subtitle, icon: null, badge, actions });

/** Alle echten Widgets (Specs wie in den Extensions registriert), Daten wie live. */
const WIDGETS = [
  w("backups", "summary", "Backup-Center", "database-backup", "widgets/summary", {
    kind: "list", empty_text: "Keine Backup-Jobs konfiguriert", max_items: null,
    item: listItem("{{ name }}", "{{ storage }} · {{ connection }}", { text: "{{ last_status_label }}", tone: "{{ tone }}" }, [
      { id: "retry", label: "Erneut versuchen", endpoint: "jobs/{{ job_ref }}/retry", method: "POST", confirm: true, confirm_text: "?", style: "danger", permissions: [], show_if: "{{ job_ref }}", payload: null },
    ]),
  }),
  w("gameserver", "servers", "Gameserver", "gamepad-2", "widgets/servers", {
    kind: "list", empty_text: "Keine Gameserver getaggt", max_items: null,
    item: listItem("{{ name }}", "{{ join_code_display }} · {{ players_display }}", { text: "{{ service_label }}", tone: "{{ tone }}" }, [
      { id: "stop", label: "Stoppen", endpoint: "servers/{{ host_id }}/stop", method: "POST", confirm: true, confirm_text: "?", style: "danger", permissions: [], show_if: "{{ can_stop }}", payload: null },
    ]),
  }),
  w("proxmox", "overview", "Proxmox-Übersicht", "server", "widgets/overview", {
    kind: "status_grid", tile_title: "{{ name }}", tile_subtitle: "{{ kind_label }} · {{ connection }}", tile_tone: "{{ status | tone }}", tile_link: null,
  }),
  w("proxmox", "node-load", "Proxmox-Knoten-Auslastung", "cpu", "widgets/node-load", {
    kind: "gauge", value_field: "value", max_field: null, max_value: 100, label: "CPU (Ø über {{ node_count }} Knoten)",
  }),
  w("proxmox", "disks", "Proxmox-Datenträger", "hard-drive", "widgets/disks", {
    kind: "list", empty_text: "–", max_items: null, item: listItem("{{ node }} · {{ model }}", "{{ summary }}", { text: "{{ badge }}", tone: "{{ tone }}" }),
  }),
  w("proxmox", "updates", "Proxmox-Updates", "package", "widgets/updates", {
    kind: "list", empty_text: "–", max_items: null, item: listItem("{{ node }}", "{{ summary }}", { text: "{{ badge }}", tone: "{{ tone }}" }),
  }),
  w("nexus-soc", "incidents", "Vorfälle", "shield-alert", "widgets/incidents", {
    kind: "list", empty_text: "Keine offenen Vorfälle", max_items: null,
    item: listItem("{{ title }}", "{{ host }}", { text: "{{ status_label }}", tone: "{{ tone }}" }),
  }),
  w("service-matrix", "matrix", "Service-Matrix", "layout-grid", "widgets/matrix", {
    kind: "status_grid", tile_title: "{{ name }}", tile_subtitle: "{{ host }}", tile_tone: "{{ tone }}", tile_link: null,
  }),
];

let layoutItems: unknown[] = WIDGETS.map((widget, i) => ({ widget_id: widget.id, ext_id: widget.ext_id, x: (i * 2) % 12, y: Math.floor(i / 6) * 2, w: 2, h: 2, config: {} }));

const WIDGET_DATA: Record<string, unknown> = {
  "/ext/backups/widgets/summary": [
    { name: "game-win", storage: "kein Backup-Job", connection: "pve2", last_status_label: "kein Backup-Job", tone: "warn", job_ref: "" },
    { name: "monitoring", storage: "backup-pve1", connection: "pve2", last_status_label: "erfolgreich", tone: "good", job_ref: "pve2--a--102" },
    { name: "ki-server", storage: "backup-nas", connection: "pve2", last_status_label: "erfolgreich", tone: "good", job_ref: "pve2--b--120" },
    { name: "docker-lxc", storage: "backup-nas", connection: "pve1", last_status_label: "unbekannt", tone: "neutral", job_ref: "pve1--c--100" },
  ],
  "/ext/gameserver/widgets/servers": [
    { name: "game-win", join_code_display: "Join-Code: 413749", players_display: "1 Spieler online", service_label: "läuft", tone: "good", can_stop: true, host_id: "g-valheim" },
  ],
  "/ext/proxmox/widgets/overview": [
    { name: "pve2", kind_label: "Knoten", connection: "pve2", status: "up" },
    { name: "pve1", kind_label: "Knoten", connection: "pve1", status: "up" },
    { name: "docker", kind_label: "VM", connection: "pve2", status: "running" },
    { name: "monitoring", kind_label: "VM", connection: "pve2", status: "running" },
    { name: "ki-server", kind_label: "VM", connection: "pve2", status: "running" },
    { name: "game-win", kind_label: "VM", connection: "pve2", status: "running" },
    { name: "docker-lxc", kind_label: "LXC", connection: "pve1", status: "running" },
    { name: "bastion", kind_label: "LXC", connection: "pve1", status: "running" },
  ],
  "/ext/proxmox/widgets/node-load": { value: 18.9, node_count: 2 },
  "/ext/proxmox/widgets/disks": [
    { node: "pve2", model: "NVMe SSD 1TB", summary: "NVMe · 1.0 TB · 96 % Restlebensdauer · 38 °C", badge: "gesund", tone: "good" },
    { node: "pve1", model: "SATA SSD 500GB", summary: "SSD · 500 GB · 33 °C", badge: "gesund", tone: "good" },
  ],
  "/ext/proxmox/widgets/updates": [
    { node: "pve2", summary: "Auf dem neuesten Stand", badge: "aktuell", tone: "good" },
    { node: "pve1", summary: "Auf dem neuesten Stand", badge: "aktuell", tone: "good" },
  ],
  "/ext/nexus-soc/widgets/incidents": [
    { title: "Container 'clamav' beendet (Exit 137)", host: "docker", status_label: "Vorschlag", tone: "warn" },
  ],
  "/ext/service-matrix/widgets/matrix": OVERVIEW.services.map((sv) => ({ name: sv.name, host: sv.host, tone: sv.tone })),
};

const GAMESERVER = {
  host_id: "g-valheim", name: "game-win", status: "up", service_label: "läuft", tone: "good", running: true,
  players_online: 1, version: "1.0.12", error: null, can_start: false, can_stop: true, profile: "valheim-windows",
  join_code: "413749", join_code_display: "Join-Code: 413749", players_display: "1 Spieler online", service_state: "running", status_label: "läuft",
  details: {
    service_name: "ValheimServer",
    process: { ram_mb: 1392, cpu_s: 1082, responding: true },
    server: { name: "Valheim", world: "Heimatwelt", port: 2456, crossplay: true, public: true, preset: null, modifiers: ["portals: casual"], has_password: true },
    join_code_age_s: 52197,
    recent_players: [{ name: "Spielerin", last_seen_age_s: 540 }, { name: "Nachbar_Tom", last_seen_age_s: 851756 }, { name: "Gast", last_seen_age_s: null }],
    last_save_age_s: 55,
    world: { name: "Heimatwelt", size: 1876559, age_s: 55 },
    auto_backups: [
      { name: "Heimatwelt_backup_auto-20260924-093504", size: 1876559, age_s: 1855 },
      { name: "Heimatwelt_backup_auto-20260924-000507", size: 1876559, age_s: 36052 },
    ],
    backups: [{ name: "20260920-181500", size: 1850000, age_s: 345600 }],
    log_tail: [
      "09/24/2026 10:05:04: World save (1/5) ZDOs done [23ms]",
      "09/24/2026 10:05:04: World save (5/5) done. Total time [35ms]",
      "09/24/2026 10:05:13:  Connections 1 ZDOS:47618  sent:0 recv:0",
      "09/24/2026 10:07:00: Got character ZDOID from Spielerin : 123:1",
    ],
    paths: { log: "C:/valheim/logs/service-out.log", world: "C:/.../worlds_local", backup: "C:/valheim/backups" },
    error: null,
  },
  config: { profile: "valheim-windows", values: {} },
};

/** Deterministischer Beispiel-Verlauf fuer die Design-Vorschau (kein Zufall, damit
 * Screenshots vergleichbar bleiben): Tagesgang + ein Lastspitzen-Ereignis + eine Luecke. */
function metricsHistory(hostId: string, range: string): object {
  const seconds: Record<string, number> = { "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000 };
  const span = seconds[range] ?? 3600;
  const step = Math.max(30, Math.ceil(span / 480 / 30) * 30);
  const now = 1_790_000_000;
  const timestamps: number[] = [];
  for (let t = now - span + step; t <= now; t += step) timestamps.push(t);
  const n = timestamps.length;
  const wave = (i: number, period: number) => Math.sin((i / n) * Math.PI * 2 * period);
  const gap = (i: number) => i > n * 0.62 && i < n * 0.65;
  const series = (f: (i: number) => number) => timestamps.map((_, i) => (gap(i) ? null : Math.round(f(i) * 100) / 100));
  const spike = (i: number) => (Math.abs(i - n * 0.8) < n * 0.03 ? 1 : 0);
  const node = hostId.startsWith("n-");
  return {
    range, source: node ? "provider" : "lattice", step_s: step, timestamps, max: {},
    series: {
      cpu_percent: series((i) => 12 + 6 * wave(i, 3) + 3 * wave(i, 17) + 55 * spike(i)),
      cpu_iowait_percent: series((i) => 1.5 + wave(i, 5) + 18 * spike(i)),
      load_1: series((i) => 0.8 + 0.3 * wave(i, 4) + 2.5 * spike(i)),
      load_5: series((i) => 0.8 + 0.2 * wave(i, 3) + 1.2 * spike(i)),
      load_15: series((i) => 0.75 + 0.1 * wave(i, 2) + 0.5 * spike(i)),
      mem_used_bytes: series((i) => (2.1 + 0.2 * wave(i, 2) + 0.4 * spike(i)) * GB),
      mem_total_bytes: series(() => 3.7 * GB),
      swap_used_bytes: series((i) => (1.3 + 0.05 * wave(i, 1)) * GB),
      swap_total_bytes: series(() => 2 * GB),
      net_in_bps: series((i) => 180_000 + 90_000 * wave(i, 9) + 14_000_000 * spike(i)),
      net_out_bps: series((i) => 60_000 + 30_000 * wave(i, 7) + 2_000_000 * spike(i)),
      disk_read_bps: series((i) => 40_000 + 20_000 * wave(i, 11)),
      disk_write_bps: series((i) => 300_000 + 150_000 * wave(i, 6) + 60_000_000 * spike(i)),
      disk_busy_percent: series((i) => 3 + 2 * wave(i, 6) + 85 * spike(i)),
      temp_c: series((i) => 48 + 3 * wave(i, 3) + 14 * spike(i)),
      root_used_bytes: series((i) => (76 + (i / n) * 2) * GB),
      root_total_bytes: series(() => 234 * GB),
    },
  };
}

let liveTick = 0;

/** Live-Ansicht der system-Extension (Pi): pro Aufruf leicht veraenderte Werte, damit die
 * pve1-Verlaeufe in der Vorschau wie echt wandern -- deterministisch ueber den Zaehler. */
function previewLive(): object {
  liveTick += 1;
  const w = (k: number) => Math.sin(liveTick / k);
  return {
    complete: true, interval_s: 1.01, uptime_s: 70454 + liveTick * 3,
    cpu: {
      model: "Raspberry Pi 4 Model B Rev 1.5", cores: 4,
      total: { percent: 18 + 9 * w(2), user: 11 + 5 * w(2), system: 5 + 2 * w(3), iowait: 1.5 + w(4), steal: 0 },
      per_core: [0, 1, 2, 3].map((id) => ({ id, percent: Math.max(0, 22 + 18 * Math.sin(liveTick / 2 + id * 1.7)), freq_mhz: id % 2 ? 600 : 1800 })),
      load: [0.58, 1.0, 0.76],
    },
    memory: {
      total: 3885880 * 1024, used: (1600000 + 30000 * w(3)) * 1024, available: 2200000 * 1024, free: 400000 * 1024,
      buffers: 50000 * 1024, cached: 1500000 * 1024, shared: 20000 * 1024, dirty: 100 * 1024,
      swap_total: 2097148 * 1024, swap_used: 262144 * 1024,
    },
    temperatures: [{ source: "thermal", label: "cpu-thermal", celsius: 52.1 + 2 * w(5) }, { source: "rp1_adc", label: "temp1", celsius: 44.3 }],
    fans: [{ label: "pwmfan fan1", rpm: 3120 }],
    disks: [
      { name: "sda", read_bps: 40000 + 30000 * Math.abs(w(2)), write_bps: 300000 + 250000 * Math.abs(w(3)), read_iops: 3, write_iops: 28, busy_percent: 4 + 3 * Math.abs(w(3)) },
      { name: "mmcblk0", read_bps: 0, write_bps: 0, read_iops: 0, write_iops: 0, busy_percent: 0 },
    ],
    network: [
      { name: "eth0", virtual: false, rx_bps: 180000 + 120000 * Math.abs(w(2)), tx_bps: 60000 + 40000 * Math.abs(w(3)), rx_total: 58e9, tx_total: 21e9, errors: 0, drops: 0, speed_mbps: 1000, state: "up" },
      { name: "wlan0", virtual: false, rx_bps: 0, tx_bps: 0, rx_total: 0, tx_total: 0, errors: 0, drops: 0, speed_mbps: null, state: "down" },
      { name: "tailscale0", virtual: true, rx_bps: 800, tx_bps: 600, rx_total: 2e8, tx_total: 1e8, errors: 0, drops: 0, speed_mbps: null, state: "unknown" },
      { name: "docker0", virtual: true, rx_bps: 1200, tx_bps: 900, rx_total: 4e8, tx_total: 3e8, errors: 0, drops: 0, speed_mbps: null, state: "up" },
    ],
    filesystems: [
      { device: "/dev/sda2", fstype: "ext4", mount: "/", size: 234e9, used: 76e9, available: 146e9, percent: 34.2 },
      { device: "/dev/sda1", fstype: "vfat", mount: "/boot/firmware", size: 510e6, used: 94e6, available: 416e6, percent: 18.4 },
    ],
    processes: [
      { pid: 812, name: "python3", user: "lattice", cpu_percent: 6.1 + 3 * Math.abs(w(2)), mem_bytes: 167e6 },
      { pid: 1320, name: "pihole-FTL", user: "pihole", cpu_percent: 1.8, mem_bytes: 101e6 },
      { pid: 2210, name: "node", user: "root", cpu_percent: 1.2, mem_bytes: 110e6 },
      { pid: 905, name: "tailscaled", user: "root", cpu_percent: 0.6, mem_bytes: 48e6 },
      { pid: 3001, name: "clamd", user: "clamav", cpu_percent: 0.1, mem_bytes: 1.02e9 },
      { pid: 1, name: "systemd", user: "root", cpu_percent: 0, mem_bytes: 12e6 },
    ],
    process_count: 214,
    throttled: { raw: "0x50000", flags: ["Unterspannung (seit Start)", "gedrosselt (seit Start)"] },
  };
}

/** Aktionen-Seite, echtes ActionOut-Format (api/v1/actions.py). Nutzer-Vorschlaege tragen
 * die UUID des Nutzers in proposed_by_id; angezeigt wird proposed_by_label. */
function actionRow(id: string, fields: Record<string, unknown>) {
  return {
    id, ext_id: "nexus-soc", action_type: "updates.apply", host_id: "h-pi", payload: {}, risk: "medium", status: "proposed",
    proposed_by_type: "user", proposed_by_id: "5f3c9a1e-7b2d-4c8e-9f10-2a6b4d8c0e13", proposed_by_label: "admin",
    reason: "", gate_decision: {},
    approved_by_user_id: null, approved_by_label: null, approved_at: null, executed_at: null, finished_at: null, result: {}, correlation_id: null,
    idempotency_key: null, expires_at: "2026-09-26T08:00:00Z", created_at: "2026-09-25T20:10:00Z", ...fields,
  };
}
const ACTIONS = [
  actionRow("act-1", { reason: "Sicherheitsupdates einspielen (12 Pakete, davon 3 Sicherheit)" }),
  actionRow("act-2", {
    action_type: "container.restart", host_id: "g-docker", proposed_by_type: "extension", proposed_by_id: "nexus-soc",
    proposed_by_label: "Nodvard Shield", reason: "KI-Container-Wache: „immich_server“ ist abgestürzt (Exit 137)", created_at: "2026-09-25T19:44:00Z",
  }),
  actionRow("act-3", {
    ext_id: "proxmox", action_type: "vm.stop", host_id: "g-valheim", risk: "high", status: "failed", reason: "Wartung",
    approved_by_user_id: "5f3c9a1e-7b2d-4c8e-9f10-2a6b4d8c0e13", approved_by_label: "admin", approved_at: "2026-09-25T18:01:00Z",
    executed_at: "2026-09-25T18:01:00Z", finished_at: "2026-09-25T18:03:00Z",
    result: { success: false, error: "Zeitüberschreitung beim Herunterfahren" }, created_at: "2026-09-25T18:00:00Z",
  }),
];

/** Dateien-Seite, Format wie api/v1/files.py (FileSourceOut, list -> {items, next_cursor}). */
const FILE_CAPS = { write: true, rename: true, remove: true, mkdir: true, search: true, range_read: true, sync_status: false, quota: false, trash: false };
const FILE_SOURCES = [
  { source_id: "ssh-sftp:h-pi", label: "SSH (SFTP): Raspberry Pi", icon: "server", caps: FILE_CAPS },
  { source_id: "ssh-sftp:g-docker", label: "SSH (SFTP): docker", icon: "server", caps: FILE_CAPS },
  { source_id: "nextcloud", label: "Nextcloud", icon: "cloud", caps: FILE_CAPS },
];
const FILE_ENTRIES = [
  { name: "backups", path: "/backups", is_dir: true, size: null, modified_at: "2026-09-25T17:30:00Z", mime: null, sync_status: null, metadata: {} },
  { name: "docker-compose.yml", path: "/docker-compose.yml", is_dir: false, size: 2381, modified_at: "2026-09-21T09:12:00Z", mime: "text/yaml", sync_status: null, metadata: {} },
  { name: "backup.tar", path: "/backup.tar", is_dir: false, size: 48_211_968, modified_at: "2026-09-25T17:28:00Z", mime: "application/x-tar", sync_status: null, metadata: {} },
];

/** Änderungsprotokoll (GET /app/changelog) -- Auszug, wie ihn das Backend liefert. */
const CHANGELOG = {
  current: "0.4.0",
  build: null,
  unreleased: [
    { kind: "verbessert", text: "Die Meldungen-Seite lädt nach dem Löschen wieder richtig.", prs: [47] },
  ],
  versions: [
    {
      version: "0.4.0", date: "2026-09-30", title: "Image-Updates und Feinschliff",
      entries: [
        { kind: "neu", text: "Service-Matrix zeigt in der neuen Spalte „Image-Update“, ob es für einen laufenden Container ein neueres Image gibt.", prs: [45] },
        { kind: "neu", text: "Änderungsprotokoll mit Versionen: Unter Einstellungen, Über Nodvard Deck steht, was in welcher Version passiert ist.", prs: [] },
        { kind: "verbessert", text: "Nach einem Update zeigt die Zusammenfassung, welche Pakete apt zurückgehalten hat.", prs: [36] },
        { kind: "behoben", text: "Ein Passwortwechsel meldet die aktuelle Sitzung nicht mehr ab, wenn ein anderer Tab vorher die Anmeldung erneuert hat.", prs: [40] },
      ],
    },
    {
      version: "0.3.0", date: "2026-09-30", title: "Aktionen im Hintergrund",
      entries: [
        { kind: "neu", text: "Bestätigte Aktionen laufen im Hintergrund und zeigen am Ende das Ergebnis.", prs: [20] },
        { kind: "sicherheit", text: "Als geheim markierte Skript-Parameter stehen nicht mehr im Klartext in Aktionen und Läufen.", prs: [27] },
      ],
    },
  ],
};

/** Was „Verbindung pruefen“ in der Vorschau antwortet: `?scenario=new|changed|error` in der Adresse. */
export const previewState: {
  scenario: "ok" | "new" | "changed" | "error";
  /** `?scenario=empty`: frische Installation ohne Server, Module und Widgets. */
  noHosts: boolean;
  /** `?scenario=start`: erster Server da, aber ohne Zugang; Module brauchen noch Einrichtung. */
  start: boolean;
  firstStepsDismissed: boolean;
  /** `?path=/setup`: Einrichtungsassistent mit frischer Installation (Module aus, 2FA aus). */
  setup: boolean;
  /** Beispieldaten aktiv (`?scenario=demo` oder Klick auf „Mit Beispieldaten ansehen“). */
  demo: boolean;
  /** Wiederherstellen: `?scenario=restore-uploaded|restore-ready|restore-pending|restore-result|restore-replaced` (sonst leer). */
  restore: "" | "uploaded" | "ready" | "pending" | "result" | "replaced";
  /** `?scenario=ohne-proxmox`: nur von Hand angelegte Server mit SSH-Zugang, Proxmox und Backups aus. */
  ohneProxmox: boolean;
  /** `?scenario=ohne-proxmox-min`: wie oben, aber auch System, Service-Matrix & Co. aus (nur Terminal/Dateien). */
  ohneProxmoxMin: boolean;
  /** `?scenario=keine-apps`: Server da, aber weder erkannte Dienste noch eigene Apps (kein Modul fuer Container) -- der Leerzustand von „Apps“. */
  noApps: boolean;
  /** Eigene Apps („+ App hinzufuegen“), die Anlegen/Aendern/Loeschen in der Vorschau und in Tests veraendern; `null` = Ausgangsstand (siehe `customApps()`). */
  customApps: PreviewApp[] | null;
} = { scenario: "ok", noHosts: false, start: false, firstStepsDismissed: false, setup: false, demo: false, restore: "", ohneProxmox: false, ohneProxmoxMin: false, noApps: false, customApps: null };

/** Eine eigene App, wie `GET /apps` sie liefert (ohne die Zeitstempel). */
interface PreviewApp {
  id: string; name: string; url: string; icon: string | null; color: string | null; group: string | null;
  open_in_new_tab: boolean; host_id: string | null; sort_order: number;
}
const previewApp = (id: string, name: string, url: string, icon: string | null, group: string | null, extra: { color?: string; tab?: boolean; host_id?: string } = {}): PreviewApp => ({
  id, name, url, icon, color: extra.color ?? null, group, open_in_new_tab: extra.tab ?? true, host_id: extra.host_id ?? null, sort_order: 0,
});

/** Ausgangsstand eigener Apps: ein typisches Homelab, das keine Service-Matrix fuer Router und Co. hat. */
function initialCustomApps(): PreviewApp[] {
  if (previewState.noHosts || previewState.noApps) return [];
  return [
    previewApp("ca1", "FRITZ!Box", "http://192.168.2.1", "router", "Netzwerk", { color: "#ef4444" }),
    previewApp("ca2", "Pi-hole", "http://192.168.2.31:8088/admin", "shield-check", "Netzwerk", { host_id: "h-pi" }),
    previewApp("ca3", "Synology NAS", "http://192.168.2.50:5000", "hard-drive", "Speicher", { color: "#3b82f6", tab: false }),
    previewApp("ca4", "Home Assistant", "http://192.168.2.60:8123", "🏠", "Smart Home", { color: "#14b8a6" }),
    previewApp("ca5", "Monitoring", "http://192.168.2.22/monitoring", "activity", "Überwachung", { host_id: "g-monitoring" }),
    previewApp("ca6", "Drucker", "http://192.168.2.40", "printer", null),
  ].map((a, i) => ({ ...a, sort_order: i }));
}

/** Die eigenen Apps der Vorschau (legt den Ausgangsstand beim ersten Zugriff an). */
export function customApps(): PreviewApp[] {
  return (previewState.customApps ??= initialCustomApps());
}

const appTile = (a: PreviewApp) => ({
  id: a.id, source: "custom", name: a.name, url: a.url, host: a.host_id ? HOSTS.find((h) => h.id === a.host_id)?.display_name ?? null : null,
  host_id: a.host_id, state: null, tone: null, image: null, icon: a.icon, color: a.color, group: a.group, open_in_new_tab: a.open_in_new_tab, sort_order: a.sort_order,
});

/** `GET /overview` mit dem Feld `apps`: eigene Apps zuerst, danach die erkannten Dienste (wie backend api/v1/overview.py). */
function withApps<T extends { services: Record<string, unknown>[] }>(base: T, custom: ReturnType<typeof appTile>[]) {
  const services = previewState.noApps ? [] : base.services;
  const detected = services.map((sv) => ({
    id: sv.id, source: "detected", name: sv.name, url: sv.url ?? null, host: sv.host ?? null, host_id: sv.host_id ?? null, state: sv.state ?? null,
    tone: sv.tone ?? null, image: sv.image ?? null, icon: null, color: null, group: null, open_in_new_tab: true, sort_order: null,
  }));
  return { ...base, services, services_running: previewState.noApps ? 0 : (base as { services_running?: number }).services_running, apps: [...custom, ...detected] };
}

/** Schreibaufrufe auf `/apps` (Anlegen, Aendern, Loeschen, Reihenfolge) -- wirken auf `customApps()`. */
function appRoutes(p: string, method: string, body: unknown): { handled: boolean; value?: unknown } {
  const list = customApps();
  const fields = (body ?? {}) as Partial<PreviewApp>;
  if (p === "/apps" && method === "GET") return { handled: true, value: list.map(appTile) };
  if (p === "/apps" && method === "POST") {
    const created = previewApp(`ca-new-${list.length + 1}-${Date.now() % 10_000}`, "", "", null, null);
    Object.assign(created, fields, { sort_order: list.reduce((max, a) => Math.max(max, a.sort_order), -1) + 1 });
    list.push(created);
    return { handled: true, value: appTile(created) };
  }
  if (p === "/apps/order" && method === "PUT") {
    const ids = (body as { ids?: string[] } | undefined)?.ids ?? [];
    const rest = list.filter((a) => !ids.includes(a.id));
    const ordered = [...ids.map((id) => list.find((a) => a.id === id)!).filter(Boolean), ...rest];
    ordered.forEach((a, i) => { a.sort_order = i; });
    list.splice(0, list.length, ...ordered);
    return { handled: true, value: list.map(appTile) };
  }
  const one = p.match(/^\/apps\/([^/?]+)$/);
  if (one) {
    const found = list.find((a) => a.id === decodeURIComponent(one[1]));
    if (!found) return { handled: true, value: undefined };
    if (method === "PATCH") {
      Object.assign(found, fields);
      return { handled: true, value: appTile(found) };
    }
    if (method === "DELETE") {
      list.splice(list.indexOf(found), 1);
      return { handled: true, value: {} };
    }
  }
  return { handled: false };
}

/** Die drei Beispiel-Apps der Beispieldaten (core/demo_seed.py: Adressen aus 192.0.2.0/24). */
const DEMO_APPS = [
  { ...appTile(previewApp("da1", "Beispiel-Router", "http://192.0.2.1", "router", "Netzwerk")), sort_order: 0 },
  { ...appTile(previewApp("da2", "Beispiel-NAS", "http://192.0.2.10:5000", "hard-drive", "Speicher", { host_id: "demo-nas" })), host: "Beispiel-NAS", sort_order: 1 },
  { ...appTile(previewApp("da3", "Beispiel-Pi-hole", "http://192.0.2.12/admin", "shield-check", "Netzwerk", { host_id: "demo-pi" })), host: "Beispiel-Raspberry-Pi", sort_order: 2 },
];

/** Beispieldaten wie das Backend sie anlegt (core/demo_seed.py): fuenf Server ohne Zugang, Adressen aus 192.0.2.0/24. */
const demoHost = (id: string, display_name: string, address: string, status: string, tags: string[]) => ({
  ...host(id, display_name, address, null, status, { name: id, provider_ext_id: null }),
  tags, last_seen_at: status === "up" ? new Date().toISOString() : null,
});
const DEMO_HOSTS = [
  demoHost("demo-nas", "Beispiel-NAS", "192.0.2.10", "up", ["demo", "speicher"]),
  demoHost("demo-webserver", "Beispiel-Webserver", "192.0.2.11", "up", ["demo", "web"]),
  demoHost("demo-pi", "Beispiel-Raspberry-Pi", "192.0.2.12", "up", ["demo", "zuhause"]),
  demoHost("demo-sicherung", "Beispiel-Sicherungsziel", "192.0.2.13", "down", ["demo", "sicherung"]),
  demoHost("demo-testrechner", "Beispiel-Testrechner", "192.0.2.14", "unknown", ["demo", "test"]),
];
const demoAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
const DEMO_NOTIFICATIONS = [
  { id: "dn1", ts: demoAgo(12), severity: "critical", title: "Beispiel: Sicherung fehlgeschlagen", body: "Die nächtliche Sicherung auf dem Beispiel-Sicherungsziel ist nicht angekommen. So sieht eine dringende Meldung aus.", source_ext_id: null, correlation_id: null, read_at: null, payload: { path: "/hosts/demo-sicherung", demo: true } },
  { id: "dn2", ts: demoAgo(150), severity: "warning", title: "Beispiel: Speicherplatz wird knapp", body: "Auf dem Beispiel-NAS sind nur noch 8 % frei. So sieht eine Warnung aus.", source_ext_id: null, correlation_id: null, read_at: null, payload: { path: "/hosts/demo-nas", demo: true } },
  { id: "dn3", ts: demoAgo(60 * 26), severity: "info", title: "Beispiel: Updates verfügbar", body: "Für den Beispiel-Webserver warten 14 Updates. So sieht ein Hinweis aus.", source_ext_id: null, correlation_id: null, read_at: null, payload: { path: "/hosts/demo-webserver", demo: true } },
  { id: "dn4", ts: demoAgo(60 * 24 * 3), severity: "info", title: "Beispiel: Willkommen bei den Beispieldaten", body: "Alles, was du hier siehst, ist ausgedacht. Über das Band oben löschst du die Beispieldaten mit einem Klick wieder.", source_ext_id: null, correlation_id: null, read_at: demoAgo(60 * 24 * 3), payload: { path: "/", demo: true } },
];
const demoStatus = () => ({
  active: previewState.demo, hosts: previewState.demo ? DEMO_HOSTS.length : 0, notifications: previewState.demo ? DEMO_NOTIFICATIONS.length : 0,
  apps: previewState.demo ? DEMO_APPS.length : 0, layout_replaced: false, hosts_with_access: [] as string[],
});

/** Einrichtungsassistent (`?path=/setup`): die mitgelieferten Module, alle noch ausgeschaltet (Texte aus den Manifesten). */
const SETUP_MODULES = [
  {"id": "backups", "name": "Backups", "description": "Zeigt alle Proxmox-Backups auf einen Blick, warnt bei fehlgeschlagenen oder fehlenden Sicherungen und bei knappem Speicher. Backup-Jobs lassen sich anlegen, ändern und neu starten.", "icon": "database-backup", "category": "servers", "sort_order": 50},
  {"id": "documents", "name": "Dokumente", "description": "Dokumentenarchiv mit Texterkennung: PDFs und Scans hochladen, automatisch verschlagworten lassen und per Volltextsuche wiederfinden.", "icon": "file-text", "category": "tools", "sort_order": 20},
  {"id": "gameserver", "name": "Gameserver", "description": "Gameserver starten und stoppen und den aktuellen Beitritts-Code anzeigen (z. B. Valheim). Server werden über eine Markierung (Tag) erkannt.", "icon": "gamepad-2", "category": "servers", "sort_order": 60},
  {"id": "hello-world", "name": "Hello World", "description": "Beispiel-Erweiterung für Nodvard Deck, gedacht für Entwickler. Zeigt, wie eigene Seiten, Kacheln und Aufgaben eingebunden werden – im Alltag nicht nötig.", "icon": "sparkles", "category": "example", "sort_order": 10},
  {"id": "inventory", "name": "Inventar", "description": "Inventar für Geräte und Gegenstände: Standorte, Kategorien, Kaufpreis, Garantie mit Ablaufwarnung, Fotos und CSV-Export.", "icon": "package", "category": "tools", "sort_order": 30},
  {"id": "network", "name": "Netzwerk", "description": "Pi-hole und Nginx Proxy Manager auf einen Blick: Anfragen und Blockierung, Proxy-Hosts und ablaufende Zertifikate. Blockierung pausieren und Proxy-Hosts ein- oder ausschalten – über die Freigabe.", "icon": "activity", "category": "servers", "sort_order": 70},
  {"id": "nextcloud", "name": "Nextcloud", "description": "Bindet deine Nextcloud in den Dateimanager von Nodvard Deck ein: Dateien ansehen, hoch- und herunterladen und zwischen Servern kopieren.", "icon": "cloud", "category": "connections", "sort_order": 20},
  {"id": "nexus-soc", "name": "Nodvard Shield", "description": "Virenschutz für alle Server: ClamAV-Scans mit Zeitplan, Echtzeit-Wächter, Quarantäne, Lynis-Härtungsaudits – dazu die KI-Container-Wache (Nodvard KI), die abgestürzte Docker-Container erkennt und Lösungen vorschlägt.", "icon": "shield-alert", "category": "security", "sort_order": 10},
  {"id": "ntfy", "name": "ntfy-Benachrichtigungen", "description": "Schickt Meldungen von Nodvard Deck als Push-Nachricht aufs Handy über ntfy (ntfy.sh oder eigener Server).", "icon": "bell", "category": "connections", "sort_order": 10},
  {"id": "proxmox", "name": "Proxmox VE", "description": "Liest deine Proxmox-Server mit allen VMs und Containern in Nodvard Deck ein: Auslastung, Start/Stopp/Neustart, Snapshots, Konsole und Speicher-Übersicht.", "icon": "server", "category": "servers", "sort_order": 40},
  {"id": "scripts", "name": "Skripte", "description": "Eigene Skripte zentral verwalten, versionieren und auf einem, mehreren oder allen Servern ausführen – sofort oder nach Zeitplan.", "icon": "terminal-square", "category": "tools", "sort_order": 10},
  {"id": "service-matrix", "name": "Service-Matrix", "description": "Findet Docker-Container auf markierten Servern und zeigt sie als Kacheln mit Status. Container lassen sich starten, stoppen und neu starten. Auf Wunsch zeigt sie, für welche Container es neuere Images gibt – und spielt sie bei Compose-Diensten auf Knopfdruck ein.", "icon": "layout-grid", "category": "servers", "sort_order": 30},
  {"id": "system", "name": "System", "description": "Zustand eines Linux-Servers auf einer Seite: Auslastung live und im Verlauf, Temperaturen, Speicherplatz, Dienste (mit Neustart über die Freigabe), Updates und Prozesse.", "icon": "cpu", "category": "servers", "sort_order": 10},
  {"id": "terminal", "name": "Terminal", "description": "Web-Terminal und Dateizugriff per SSH direkt im Browser, mit mehreren Sitzungen gleichzeitig.", "icon": "terminal", "category": "servers", "sort_order": 20},
];
/** Module, die nach dem Einschalten noch Angaben brauchen (wie im echten Backend: Pflichtfelder fehlen). */
const SETUP_NEEDS_SETUP = new Set(["backups", "network", "nextcloud", "nexus-soc", "ntfy", "proxmox", "service-matrix"]);
const setupState = { enabled: new Set<string>(), totp: false, timezone: "UTC" };

/** Antworten des Assistenten, der nach dem Konto Module, Zeitzone und Zwei-Faktor anfasst. */
function setupRoutes(p: string, method: string, body?: unknown): { handled: boolean; value?: unknown } {
  if (p === "/me") {
    return { handled: true, value: { id: "u1", username: "admin", display_name: "Admin", email: null, is_owner: true, locale: "de", permissions: ["*"], totp_enabled: setupState.totp, timezone: setupState.timezone } };
  }
  if (p === "/settings/system.timezone" && method === "PUT") {
    setupState.timezone = (body as { value: string }).value;
    return { handled: true, value: { key: p.slice("/settings/".length), value: setupState.timezone } };
  }
  if (p === "/extensions" && method === "GET") {
    return {
      handled: true,
      value: SETUP_MODULES.map((m) => {
        const enabled = setupState.enabled.has(m.id);
        return {
          ...m, version: "0.1.0", api_version: "0.1.0", source: "bundled", granted_permissions: [], last_error: null, has_settings: true,
          state: enabled ? "enabled" : "disabled", needs_setup: enabled && SETUP_NEEDS_SETUP.has(m.id),
        };
      }),
    };
  }
  const toggle = p.match(/^\/extensions\/([^/]+)\/(enable|disable)$/);
  if (toggle && method === "POST") {
    if (toggle[2] === "enable") setupState.enabled.add(toggle[1]);
    else setupState.enabled.delete(toggle[1]);
    return { handled: true, value: { id: toggle[1], state: toggle[2] === "enable" ? "enabled" : "disabled" } };
  }
  if (p === "/me/totp/setup" && method === "POST") {
    return {
      handled: true,
      value: { secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Nodvard%20Deck:admin?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Nodvard%20Deck" },
    };
  }
  if (p === "/me/totp/confirm" && method === "POST") {
    setupState.totp = true;
    return { handled: true, value: { recovery_codes: ["K7MQ-X2VD1", "H9PA-4RTWE", "3NBC-ZQ8LF", "P6YU-JD5GM", "W2XS-9KAHT", "E8VR-C3NQB", "M5ZL-7TYDU", "A4GF-R6JPX", "Q9HK-2WBNS", "T3CE-8UVMY"] } };
  }
  return { handled: false };
}

const PI_FINGERPRINT = "SHA256:q3Zc1oB0m7uVxk9sYtEw2nPjR4dLhGfA8yKcVbNzXe0";
const OLD_FINGERPRINT = "SHA256:Zk4n8WcP1xR7aYdT5uLoH2vJmQe9sBgF3iNbXtCyU6A";

/** Frische Installation: nichts da, was das Cockpit zeigen koennte. */
const EMPTY_OVERVIEW = {
  services: [], services_running: 0, backups: null, pending_actions: 0, unread_notifications: 0, attention: [], errors: [],
  generated_at: Date.now() / 1000,
};

/** Push-Erweiterung: eigene Zeile, sie hat in der Vorschau keine eigene Seite im Menue. */
const NTFY = { id: "ntfy", name: "ntfy-Benachrichtigungen", description: "Push-Nachrichten aufs Handy", icon: "bell", has_settings: true };

const REQUIREMENTS = [
  { ext_id: "nexus-soc", id: "root", label: "Root-Rechte (Nodvard Shield)", check_command: null, ok_text: "", fail_hint: "", unix_group: null, needs_root: true, root_reason: "Updates einspielen, Quarantäne, Härtungs-Audit, Fail2ban", order: 50 },
  { ext_id: "system", id: "root", label: "Root-Rechte (System)", check_command: null, ok_text: "", fail_hint: "", unix_group: null, needs_root: true, root_reason: "Dienste neu starten", order: 60 },
  { ext_id: "service-matrix", id: "docker-group", label: "Docker ohne sudo (Service-Matrix)", check_command: "docker ps -q", ok_text: "Docker ist erreichbar.", fail_hint: "Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}", unix_group: "docker", needs_root: false, root_reason: null, order: 100 },
];

function hostRequirements(id: string) {
  const h = HOSTS.find((x) => x.id === id);
  if (!h) return undefined;
  if (h.os_family !== "linux") return [];
  return REQUIREMENTS.filter((r) => r.id !== "docker-group" || (h.tags as string[]).includes("docker"));
}

const KNOWN_KEYS: Record<string, object[]> = {
  "h-pi": [{ key_type: "ssh-ed25519", fingerprint: PI_FINGERPRINT, first_seen_at: "2026-09-12T09:30:00Z", accepted_by_user_id: "u1", accepted_by_label: "admin" }],
  "g-docker": [{ key_type: "ssh-ed25519", fingerprint: "SHA256:b8Tn2kRz5QvLw0xYhGd7EaPjU1mCcFoS6iNtVeXqA3M", first_seen_at: "2026-09-10T18:02:00Z", accepted_by_user_id: null, accepted_by_label: null }],
  "n-pve2": [{ key_type: "ssh-ed25519", fingerprint: "SHA256:Hj7sWe3Lr9ZxKp1NbTqY5cDfVuAo2mGiR8yXtCvB0nE", first_seen_at: "2026-09-10T18:05:00Z", accepted_by_user_id: null, accepted_by_label: null }],
};

function setupFor(hostId: string, sudo: boolean, groups: string[]) {
  const h = HOSTS.find((x) => x.id === hostId);
  const user = h?.credential?.username ?? "lattice";
  const pub = `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPr3vK9xQzWnYc0mH5bLJ2dTuE8sAfGo7iRkXe1NpVq4 lattice@${h?.name ?? "server"}`;
  const lines = [
    "set -e",
    `U=${user}`,
    `K="${pub}"`,
    'id "$U" >/dev/null 2>&1 || { useradd -m -s /bin/bash -c "Nodvard Deck" "$U"; usermod -p "*" "$U"; echo "Benutzer $U angelegt."; }',
    'H=$(getent passwd "$U" | cut -d: -f6); G=$(id -gn "$U"); install -d -m 700 -o "$U" -g "$G" "$H/.ssh"; touch "$H/.ssh/authorized_keys"',
    'grep -qF "$K" "$H/.ssh/authorized_keys" || echo "restrict,pty $K" >> "$H/.ssh/authorized_keys"',
    ...groups.map((g) => `if getent group ${g} >/dev/null; then usermod -aG ${g} "$U"; fi`),
    ...(sudo ? ['echo "$U ALL=(root) NOPASSWD: ALL" > /etc/sudoers.d/lattice-$U'] : []),
    'echo "Fertig. Jetzt im Dashboard „Verbindung prüfen“ drücken."',
  ];
  const notes: string[] = [];
  if (sudo || groups.length) notes.push("Wer in der Gruppe docker ist oder sudo ohne Passwort darf, kann auf dem Server praktisch alles.");
  if (user === "root") notes.push("Bei einem Proxmox-Cluster gilt der Schlüssel für alle Knoten.");
  return {
    username: user, public_key: pub, fingerprint: "SHA256:Tt8sJ2wLr0ZxKp1NbQqY5cDfVuAo3mGiR9yXeCvB6nE",
    one_liner: `S=; [ "$(id -u)" = 0 ] || S=sudo; $S sh -c '${lines.join("; ")}'`,
    script: lines.join("\n"), notes, groups, sudo,
  };
}

function checkResult(hostId: string) {
  const h = HOSTS.find((x) => x.id === hostId);
  const user = h?.credential?.username ?? "lattice";
  const reachable = { id: "reachable", label: "Server erreichbar", status: "ok", detail: "SSH-Dienst antwortet (SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3).", hint: "" };
  const base = { checked_at: new Date().toISOString(), os: null, credential_id: h?.credential?.id ?? null };
  if (previewState.scenario === "new") {
    return {
      ...base, ok: false,
      host_key: { status: "new", key_type: "ssh-ed25519", fingerprint: PI_FINGERPRINT, expected: null },
      items: [
        reachable,
        { id: "host_key", label: "Server-Schlüssel", status: "confirm", detail: `Nodvard Deck kennt diesen Server noch nicht. Fingerabdruck: ${PI_FINGERPRINT}`, hint: "Zum Vergleichen auf dem Server: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub" },
      ],
    };
  }
  if (previewState.scenario === "changed") {
    return {
      ...base, ok: false,
      host_key: { status: "changed", key_type: "ssh-ed25519", fingerprint: PI_FINGERPRINT, expected: OLD_FINGERPRINT },
      items: [
        reachable,
        { id: "host_key", label: "Server-Schlüssel", status: "fail", detail: `Achtung: Der Server meldet einen anderen Schlüssel als bisher. Bisher ${OLD_FINGERPRINT}, jetzt ${PI_FINGERPRINT} (ssh-ed25519).`, hint: "Das passiert nach einer Neuinstallation – oder wenn sich jemand dazwischenschaltet. Nur wenn du den Server neu aufgesetzt hast: gemerkten Schlüssel vergessen, erneut prüfen und den neuen Fingerabdruck bestätigen." },
      ],
    };
  }
  if (previewState.scenario === "error") {
    return {
      ...base, ok: false, host_key: null,
      items: [{ id: "reachable", label: "Server erreichbar", status: "fail", detail: "Der Server lehnt Verbindungen auf Port 22 ab.", hint: "Läuft dort SSH? Debian/Raspberry Pi OS: „sudo apt install openssh-server“ und „sudo systemctl enable --now ssh“ (Raspberry Pi OS: auch über raspi-config)." }],
    };
  }
  return {
    ...base, ok: true, os: { pretty_name: "Debian GNU/Linux 12 (bookworm)", arch: "aarch64", model: "Raspberry Pi 4 Model B Rev 1.4" },
    host_key: { status: "known", key_type: "ssh-ed25519", fingerprint: PI_FINGERPRINT, expected: PI_FINGERPRINT },
    items: [
      reachable,
      { id: "host_key", label: "Server-Schlüssel", status: "ok", detail: "Bekannt (ssh-ed25519).", hint: "" },
      { id: "login", label: `Anmeldung als ${user}`, status: "ok", detail: "Mit Schlüssel angemeldet.", hint: "" },
      { id: "root", label: "Root-Rechte (für Updates einspielen, Quarantäne, Dienste neu starten)", status: "warn", detail: "sudo verlangt ein Passwort.", hint: "Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ erneut ausführen." },
      { id: "req:service-matrix:docker-group", label: "Docker ohne sudo (Service-Matrix)", status: "ok", detail: "Docker ist erreichbar.", hint: "" },
      { id: "os", label: "Betriebssystem", status: "ok", detail: "Debian GNU/Linux 12 (bookworm) · aarch64 · Raspberry Pi 4 Model B Rev 1.4", hint: "" },
    ],
  };
}

const GROUPS = [
  { id: "grp-docker", name: "docker-server", description: "" },
  { id: "grp-proxmox", name: "proxmox-knoten", description: "" },
];
const GROUP_MEMBERS: Record<string, string[]> = { "grp-docker": ["g-docker", "h-pi"], "grp-proxmox": ["n-pve1", "n-pve2"] };

function serverAccessRoutes(p: string, method: string, body: unknown): { handled: boolean; value?: unknown } {
  if (p === "/host-groups" && method === "GET") return { handled: true, value: previewState.noHosts ? [] : GROUPS };
  if (p === "/hosts" && method === "GET" && previewState.noHosts) return { handled: true, value: [] };
  if (p === "/host-groups" && method === "POST") return { handled: true, value: { id: "grp-new", name: (body as { name?: string })?.name ?? "neu", description: "" } };
  if (/^\/host-groups\/[^/]+/.test(p) && method !== "GET") return { handled: true, value: method === "PATCH" ? { ...GROUPS[0], ...(body as object) } : null };
  const byGroup = p.match(/^\/hosts\?group=([^&]+)$/);
  if (byGroup) return { handled: true, value: HOSTS.filter((h) => (GROUP_MEMBERS[decodeURIComponent(byGroup[1])] ?? []).includes(h.id)) };
  if (p === "/hosts" && method === "POST") {
    const b = body as { name?: string; display_name?: string; address?: string };
    return { handled: true, value: { ...host("h-new", b.display_name || b.name || "neu", b.address ?? "", null, "unknown", { name: b.name, provider_ext_id: null }) } };
  }
  const sub = p.match(/^\/hosts\/([^/?]+)\/(credentials|known-hosts|requirements|check)(?:\/([^/?]+)(?:\/(setup|make-default))?)?(?:\?(.*))?$/);
  if (!sub) return { handled: false };
  const [, id, what, , action, query] = sub;
  const h = HOSTS.find((x) => x.id === id);
  if (!h) return { handled: true, value: undefined };
  if (what === "requirements") return { handled: true, value: hostRequirements(id) };
  if (what === "known-hosts") return { handled: true, value: method === "POST" ? { ...(body as object), first_seen_at: new Date().toISOString(), accepted_by_user_id: "u1", accepted_by_label: "admin" } : KNOWN_KEYS[id] ?? [] };
  if (what === "check") return { handled: true, value: checkResult(id) };
  if (what === "credentials" && action === "setup") {
    const params = new URLSearchParams(query ?? "");
    return { handled: true, value: setupFor(id, params.get("sudo") === "true", params.getAll("groups")) };
  }
  if (what === "credentials" && action === "make-default") return { handled: true, value: { ...h.credential, host_id: id, is_default: true, created_at: "2026-09-30T10:00:00Z", notice: null } };
  if (what === "credentials" && method === "POST" && p.endsWith("/generate-key")) {
    const b = body as { username?: string; port?: number };
    const cred = { id: "c-new", host_id: id, kind: "ssh_key", username: b?.username ?? "lattice", port: b?.port ?? 22, is_default: !h.credential, created_at: new Date().toISOString() };
    return { handled: true, value: { credential: cred, public_key: setupFor(id, false, []).public_key, fingerprint: setupFor(id, false, []).fingerprint } };
  }
  if (what === "credentials" && method === "POST") return { handled: true, value: { ...(body as object), id: "c-new", host_id: id, created_at: new Date().toISOString() } };
  if (what === "credentials" && method === "DELETE") return { handled: true, value: null };
  if (what === "credentials") return { handled: true, value: h.credential ? [{ ...h.credential, host_id: id, is_default: true, created_at: "2026-09-12T09:30:00Z" }] : [] };
  return { handled: false };
}

/** Einstellungen -> System -> Sicherung (`?scenario=new`: noch kein Sicherungspasswort). */
const BACKUP_KEY = { key_id: "3f9a1c0be47d2a61", created_at: "2026-09-12T18:20:00Z" };
const BACKUP_ITEMS = [
  { name: "nodvard-deck-sicherung-20261001-023000.ndbak", size: 48_300_000, created_at: "2026-10-01T00:30:00Z", app_version: "0.5.0", key_id: BACKUP_KEY.key_id, mode: "schluessel", status: "ok", checked_at: "2026-10-01T07:12:00Z", check_ok: true, key_current: true },
  { name: "nodvard-deck-sicherung-20260930-023000.ndbak", size: 47_900_000, created_at: "2026-09-30T00:30:00Z", app_version: "0.5.0", key_id: BACKUP_KEY.key_id, mode: "schluessel", status: "ok", key_current: true },
  { name: "nodvard-deck-sicherung-20260929-023000.ndbak", size: 47_100_000, created_at: "2026-09-29T00:30:00Z", app_version: "0.4.2", key_id: "77aa01cd9e3b4f20", mode: "schluessel", status: "ungeprueft", key_current: false },
];

function backupOverview() {
  const fresh = previewState.scenario === "new";
  return {
    config: { enabled: !fresh, schedule: "30 2 * * *", keep: 7, dir: "/app/data/backups", include_runs: false },
    key: fresh ? null : BACKUP_KEY,
    target: {
      dir: "/app/data/backups", default_dir: "/app/data/backups", external_root: "/backups", external_available: false,
      same_storage_as_data: true, free_bytes: 21_400_000_000, error: null,
    },
    backups: fresh ? [] : BACKUP_ITEMS,
    last_run: fresh ? null : { at: "2026-10-01T00:30:41Z", ok: true, trigger: "schedule", name: BACKUP_ITEMS[0].name, size: BACKUP_ITEMS[0].size, error: null },
    running: null,
    sqlite: true,
    limits: { keep_min: 1, keep_max: 60, password_min_length: 12 },
  };
}

function backupRoutes(p: string, method: string): { handled: boolean; value?: unknown } {
  if (!p.startsWith("/system/")) return { handled: false };
  if (p === "/system/info") return { handled: true, value: { version: "0.5.0", build: null, image: null, timezone: "Europe/Berlin", data_dir: "/app/data", data_free_bytes: 21_400_000_000, database: "sqlite", updater_available: false, pre_update_copies: [{ name: "20260930T220000Z_0.4.0_0.5.0.db", created_at: "2026-09-30T22:00:00Z", from_version: "0.4.0", to_version: "0.5.0", size: 8_400_000 }] } };
  if (p === "/system/updates" || p === "/system/updates/check") {
    return {
      handled: true,
      value: {
        current: "0.5.0", latest: "0.6.0", latest_digest: null, available: true, channel: "stable", enabled: true,
        checked_at: "2026-10-01T04:41:00Z", attempted_at: "2026-10-01T04:41:00Z", source: method === "POST" ? "live" : "cache", error: null,
        official_image: true, image: "ghcr.io/nodvard/deck", official_image_name: "ghcr.io/nodvard/deck", helper: false,
        release_notes_url: "https://github.com/nodvard/deck/blob/v0.6.0/CHANGELOG.md",
      },
    };
  }
  if (p === "/system/backups") return { handled: true, value: backupOverview() };
  if (p === "/system/backups/config") return { handled: true, value: backupOverview() };
  if (p === "/system/backups/key") return { handled: true, value: { key_id: "5be3e6c41d0a9f87", recipient: "age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290gq", recovery_key: "AGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSDV5NZE" } };
  if (p === "/system/backups/run") return { handled: true, value: { started: true } };
  const ticket = { ticket: "vorschau", status: "ready", filename: BACKUP_ITEMS[0].name, size: BACKUP_ITEMS[0].size, error: null, expires_in: 300, url: "#vorschau-download" };
  if (p === "/system/backups/download" || p.endsWith("/ticket") || p.endsWith("/status")) return { handled: true, value: ticket };
  if (p.endsWith("/verify")) return { handled: true, value: { ok: true, detail: "Die Datei ist unverändert." } };
  if (method === "DELETE") return { handled: true, value: null };
  return { handled: false };
}

/** Wiederherstellen: Zwischenstand, Vormerkung und Ergebnis; derselbe Ablauf unter /system (Owner) und /auth/bootstrap (Assistent). */
export const RESTORE_ID = "c0ffee00c0ffee00c0ffee00c0ffee00";
const RESTORE_SUMMARY = {
  created_at: "2026-09-30T00:30:41Z", app_version: "0.5.0", instance_id: "9d41c7a2e0b84f6aa1d3b5c8e7f20416", mode: "passwort", owner_name: "admin",
  users: 3, hosts: 11,
  extensions: ["proxmox", "backups", "system", "terminal", "service-matrix", "nexus-soc", "ntfy"].map((id) => ({ id, version: "0.5.0" })),
  includes: { runs: false, branding: true, jwt_secret: true },
  warnings: [
    "Die Sicherung stammt aus Version 0.4.2, installiert ist 0.5.0. Beim Start werden die Daten auf den neuen Stand gebracht.",
    "Die Sicherung stammt von einer anderen Installation als dieser. Nur einspielen, wenn die Sicherung selbst erstellt wurde.",
  ],
};
const restoreStaged = (state: "uploaded" | "ready") => ({
  id: RESTORE_ID, state, size: 48_300_000, header: { mode: "passwort", created_at: "2026-09-30T00:30:41Z", app_version: "0.4.2" },
  summary: state === "ready" ? RESTORE_SUMMARY : null, expires_in: 3300,
});
const restorePending = { id: RESTORE_ID, source: "owner", scheduled_at: "2026-10-01T09:00:00Z", expires_in: 2700, sign_out_all: true, backup: { owner_name: "admin", app_version: "0.4.2" } };

function restoreRoutes(p: string, method: string): { handled: boolean; value?: unknown } {
  const owner = p.startsWith("/system/restore");
  const wizard = p.startsWith("/auth/bootstrap/restore") || p === "/auth/bootstrap/restart";
  if (!owner && !wizard && p !== "/system/restart") return { handled: false };
  if (p === "/system/restore/status") {
    const r = previewState.restore;
    return { handled: true, value: {
      pending: r === "pending" ? restorePending : null,
      staged: r === "uploaded" ? restoreStaged("uploaded") : r === "ready" ? restoreStaged("ready") : null,
      result: r === "result" || r === "replaced"
        ? { ok: r === "replaced", at: "2026-10-01T03:12:09Z", message: r === "result" ? "Die Sicherung ließ sich nicht auf den Stand dieser Version bringen (OperationalError). Alles ist wie vorher." : "Die Sicherung wurde eingespielt.", source: "owner", actor: "admin", replaced: null, rolled_back: r === "result" ? true : null }
        : null,
      replaced: r === "replaced" ? { name: "replaced-20261001T031209", size: 52_400_000, at: "20261001T031209" } : null,
      limits: { max_upload_bytes: 4 * 1024 ** 3, max_unpacked_bytes: 16 * 1024 ** 3, expires_in: 3600 },
      busy: false,
    } };
  }
  if (p.endsWith("/inspect")) return { handled: true, value: restoreStaged("ready") };
  if (p.endsWith("/schedule")) return { handled: true, value: restorePending };
  if (p.endsWith("/restart")) return { handled: true, value: { restarting: true, exit_code: 75 } };
  if (method === "DELETE") return { handled: true, value: {} };
  return { handled: true, value: {} };
}

export function respond(path: string, method: string, body?: unknown): unknown {
  const p = path.replace(/^\/api\/v1/, "");
  const restore = restoreRoutes(p, method);
  if (restore.handled) return restore.value;
  if (p === "/demo" && method === "GET") return demoStatus();
  if (p === "/demo/seed" && method === "POST") {
    previewState.demo = true;
    return { ...demoStatus(), created: true };
  }
  if (p === "/demo" && method === "DELETE") {
    previewState.demo = false;
    return { removed_hosts: DEMO_HOSTS.length, kept_hosts: 0, removed_notifications: DEMO_NOTIFICATIONS.length, layout_restored: false, removed_apps: DEMO_APPS.length, kept_apps: 0 };
  }
  if (previewState.demo) {
    // Beispieldaten: Server, Meldungen und Uebersicht aus dem Seed; der Rest bleibt wie sonst.
    if (p === "/hosts" && method === "GET") return DEMO_HOSTS;
    const demoHostRoute = p.match(/^\/hosts\/(demo-[^/?]+)$/);
    if (demoHostRoute) return DEMO_HOSTS.find((h) => h.id === demoHostRoute[1]);
    if (p === "/overview") {
      const attention = DEMO_NOTIFICATIONS.filter((n) => !n.read_at && n.severity !== "info").map(({ id, ts, severity, title, source_ext_id }) => ({ id, ts, severity, title, source_ext_id }));
      return { ...EMPTY_OVERVIEW, unread_notifications: 3, attention, apps: [...DEMO_APPS, ...customApps().map(appTile)] };
    }
    if (p === "/notifications/unread-count") return { unread: 3 };
    if (p.startsWith("/notifications?")) return DEMO_NOTIFICATIONS;
    if (p === "/host-groups") return [];
  }
  if (previewState.setup) {
    const setup = setupRoutes(p, method, body);
    if (setup.handled) return setup.value;
  }
  const access = serverAccessRoutes(p, method, body);
  if (access.handled) return access.value;
  const backup = backupRoutes(p, method);
  if (backup.handled) return backup.value;
  if (p === "/app/changelog") return CHANGELOG;
  if (previewState.ohneProxmox && /^\/ext\/(backups|proxmox)\/(inventory|unprotected)$/.test(p)) return { guests: [], errors: [] };
  if (previewState.ohneProxmox && /^\/ext\/(backups|proxmox)\/(connections|jobs|nodes)$/.test(p)) return [];
  if (p === "/ext/gameserver/servers") return previewState.noHosts ? [] : [GAMESERVER];
  if (previewState.noHosts && p.startsWith("/hosts?tag=")) return [];
  if (previewState.noHosts && p === "/ext/proxmox/connections") return [];
  if (previewState.noHosts && p.startsWith("/ext/service-matrix/widgets/matrix")) return { data: [], meta: {} };
  if (p.startsWith("/ext/gameserver/servers/")) return GAMESERVER;
  if (p === "/ext/gameserver/profiles") {
    return [{ id: "valheim-windows", label: "Valheim (Windows-Dienst)", fields: [
      { key: "service_name", label: "Dienstname", help: "Leer = automatisch erkennen.", default: "" },
      { key: "process_name", label: "Prozessname", help: "Für CPU/RAM-Anzeige.", default: "valheim_server" },
      { key: "log_path", label: "Log-Datei", help: "Leer = aus der NSSM-Konfiguration.", default: "" },
    ] }];
  }
  // `?path=/setup`: frische Installation (Einrichtungsassistent), sonst gibt es schon ein Konto.
  if (p === "/auth/bootstrap") return { needed: new URLSearchParams(window.location.search).get("path") === "/setup" && method === "GET" };
  if (p === "/auth/login" && method === "POST") return {
    access_token: "preview", expires_in: 900,
    user: { id: "u1", username: "admin", display_name: "Admin", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  };
  // Die Shell erneuert das Token vorab (lib/tokenRefresh.ts) -- sonst waere die Vorschau
  // nach 13 min abgemeldet. Format wie api/v1/auth.py::_tokens_response (Web: ohne refresh_token).
  if (p === "/auth/refresh" && method === "POST") return {
    access_token: "preview", expires_in: 900,
    user: { id: "u1", username: "admin", display_name: "Admin", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  };
  if (p === "/ext/inventory/categories") return [{ id: "c1", name: "Netzwerk" }, { id: "c2", name: "Werkzeug" }, { id: "c3", name: "Server" }];
  if (p === "/ext/inventory/locations") return [{ id: "l1", name: "Serverschrank", parent_id: null }, { id: "l2", name: "Keller", parent_id: null }];
  if (p.startsWith("/ext/inventory/items")) return [
    { id: "i1", name: "TP-Link TL-SG108 Switch", description: "8-Port Gigabit", category_id: "c1", location_id: "l1", quantity: 2, purchase_date: "2024-03-02", purchase_price_cents: 2499, warranty_until: "2026-11-20", notes: null, images: [] },
    { id: "i2", name: "Raspberry Pi 4 (4 GB)", description: null, category_id: "c3", location_id: "l1", quantity: 1, purchase_date: "2023-06-11", purchase_price_cents: 6490, warranty_until: "2025-06-11", notes: "Raspberry Pi", images: [] },
    { id: "i3", name: "Bosch Akkuschrauber", description: null, category_id: "c2", location_id: "l2", quantity: 1, purchase_date: "2025-01-05", purchase_price_cents: 8999, warranty_until: "2028-01-05", notes: null, images: [] },
  ];
  if (p === "/ext/documents/tags") return [{ id: "t1", name: "Rechnungen", match_keyword: "rechnung" }, { id: "t2", name: "Verträge", match_keyword: "vertrag" }];
  if (p.startsWith("/ext/documents/documents")) return [
    { id: "d1", original_filename: "stromrechnung-2026-08.pdf", content_type: "application/pdf", size_bytes: 40050, page_count: 2, ocr_text: "Stadtwerke …", ocr_status: "done", ocr_error: null, tags: [{ id: "t1", name: "Rechnungen", match_keyword: "rechnung" }] },
    { id: "d2", original_filename: "mietvertrag.pdf", content_type: "application/pdf", size_bytes: 1250000, page_count: 12, ocr_text: "Mietvertrag …", ocr_status: "done", ocr_error: null, tags: [{ id: "t2", name: "Verträge", match_keyword: "vertrag" }] },
    { id: "d3", original_filename: "garantie-scan.jpg", content_type: "image/jpeg", size_bytes: 820000, page_count: null, ocr_text: null, ocr_status: "error", ocr_error: "Kein Text erkannt", tags: [] },
  ];
  if (p === "/ext/scripts/scripts") return [
    { id: "lynis-audit", name: "Lynis-Sicherheitsaudit", description: "", content: "#!/bin/sh\nlynis audit system --quick --quiet\ngrep -E 'warning\\[\\]=|suggestion\\[\\]=' /var/log/lynis-report.dat\n", params_schema: {}, target: { kind: "all" }, schedule: "0 1 * * *", enabled: true, job_id: "script-lynis" },
    { id: "apt-updates", name: "Sicherheitsupdates", description: "", content: "#!/bin/sh\napt-get update && apt-get -y upgrade\n", params_schema: {}, target: { kind: "host", host_id: "h-pi" }, schedule: "0 3 * * 0", enabled: false, job_id: "script-apt" },
  ];
  if (p === "/ext/scripts/scripts/lynis-audit/runs") return [
    { action_id: "a1", status: "failed", host_id: "h-pve1", host_name: "Proxmox-Knoten pve1", proposed_by: "extension/scripts", created_at: "2026-09-25T23:00:00Z", finished_at: null, exit_code: null, output: "", error: "Zeitüberschreitung – der Befehl hat zu lange gebraucht.", duration_ms: null },
    { action_id: "a2", status: "succeeded", host_id: "h-pve2", host_name: "Proxmox-Knoten pve2", proposed_by: "extension/scripts", created_at: "2026-09-25T23:00:00Z", finished_at: null, exit_code: 0, output: "warning[]=SSH-7408\nhardening_index=68", error: "", duration_ms: 31900 },
  ];
  if (p.startsWith("/ext/scripts/scripts/lynis-audit/history")) return [{ sha: "a1b2c3d4e5", message: "scripts: Lynis-Sicherheitsaudit als Erststand angelegt", commit_time: 1758700000 }];
  if (p === "/jobs") return [];
  if (previewState.ohneProxmox && p.startsWith("/ext/nexus-soc/defender/") && method === "GET") {
    const base = respondWithoutScenario(p, method, body);
    const mapped = base && typeof base === "object" ? ohneProxmoxSoc(p, base as Record<string, unknown>) : base;
    if (mapped !== undefined) return mapped;
  }
  if (p === "/ext/nexus-soc/defender/updates") {
    const t = Date.now() / 1000;
    const st = (count: number, sec: number, extra: object = {}) => ({
      manager: "apt", count, security_count: sec, reboot_required: false, reboot_reasons: [], kernel: "6.8.12-4-pve", latest_kernel: null,
      uptime_s: 1_900_000, unattended: false, refresh_error: null, error: null, checked_at: t - 900,
      packages: [
        ...Array.from({ length: sec }, (_, i) => ({ name: ["openssl", "libssl3", "openssh-server", "libc6"][i % 4], new_version: "3.0.14-1~deb12u2", current_version: "3.0.13-1~deb12u1", repo: "stable-security", security: true })),
        ...Array.from({ length: count - sec }, (_, i) => ({ name: ["base-files", "tzdata", "curl", "pve-manager", "proxmox-kernel-6.8"][i % 5], new_version: "12.4+deb12u7", current_version: "12.4+deb12u6", repo: "stable", security: false })),
      ],
      ...extra,
    });
    const run = { id: "r1", host_id: "h-docker", host_name: "docker", mode: "security", mode_label: "Sicherheitsupdates", trigger: "schedule", status: "ok", upgraded: 4, summary: "4 Paket(e) aktualisiert", output_tail: "Reading package lists...\n4 upgraded, 0 newly installed, 0 to remove and 2 not upgraded.", started_at: t - 80000, finished_at: t - 79800 };
    return {
      hosts: [
        { host_id: "h-pve2", host_name: "Proxmox-Knoten pve2", host_status: "up", checking: false, busy: null, last_run: null,
          status: st(5, 2, { reboot_required: true, reboot_reasons: ["neuer Kernel 6.8.12-10-pve (läuft: 6.8.12-4-pve)"], latest_kernel: "6.8.12-10-pve" }) },
        { host_id: "h-pi", host_name: "Raspberry Pi", host_status: "up", checking: false, busy: null, last_run: null, status: st(3, 0, { kernel: "6.6.51+rpt-rpi-2712" }) },
        { host_id: "h-docker", host_name: "docker", host_status: "up", checking: false, busy: null, last_run: run, status: st(0, 0, { unattended: true, kernel: "6.1.0-25-amd64" }) },
        { host_id: "h-pve1", host_name: "Proxmox-Knoten pve1", host_status: "down", checking: false, busy: null, last_run: null,
          status: { ...st(0, 0), manager: null, error: "Zeitüberschreitung – der Vorgang hat zu lange gebraucht." } },
      ],
      summary: { hosts: 4, checked: 3, up_to_date: 1, packages: 8, security: 2, reboot: 1, errors: 1 },
      runs: [run, { ...run, id: "r2", host_id: "h-pi", host_name: "Raspberry Pi", mode: "all", mode_label: "Alle Updates", trigger: "manual", status: "error", upgraded: 0, summary: "E: dpkg was interrupted, you must manually run 'dpkg --configure -a'", started_at: t - 200000 }],
      config: { check_enabled: true, check_cron: "0 6 * * *", auto_enabled: true, auto_mode: "security", auto_cron: "30 3 * * *", auto_reboot: false, auto_tag: null },
    };
  }
  if (p === "/ext/nexus-soc/defender/guard") {
    const t = Date.now() / 1000;
    const port = (port: number, proto: string, process: string, pub = true, isNew = false) => ({ key: `${proto}/${port}/${process}`, proto, address: pub ? "0.0.0.0" : "127.0.0.1", port, process, public: pub, new: isNew });
    return {
      hosts: [
        { host_id: "h-pve2", host_name: "Proxmox-Knoten pve2", host_status: "up", checking: false, open_events: 2,
          view: { is_root: true, ssh_log_found: true, failed_24h: 312, attacker_count: 4, banned_count: 3, fail2ban: "running", jails: { sshd: { banned: ["198.51.100.12", "203.0.113.40", "203.0.113.7"] } },
            attackers: [
              { ip: "203.0.113.7", count: 188, users: ["root"], last_ts: t - 120, banned: true },
              { ip: "198.51.100.12", count: 71, users: ["admin", "ubuntu", "test", "oracle"], last_ts: t - 3000, banned: true },
              { ip: "198.51.100.9", count: 38, users: ["root", "pi"], last_ts: t - 7000, banned: false },
              { ip: "203.0.113.40", count: 15, users: ["git"], last_ts: t - 9000, banned: true },
            ],
            logins: [{ user: "root", ip: "192.168.2.77", method: "publickey", ts: t - 1800 }, { user: "root", ip: "192.168.2.43", method: "publickey", ts: t - 7200 }],
            ports: [port(22, "tcp", "sshd"), port(111, "tcp", "rpcbind"), port(3128, "tcp", "spiceproxy"), port(8006, "tcp", "pveproxy"), port(25, "tcp", "master", false), port(85, "tcp", "pvedaemon", false), port(4444, "tcp", "nc", true, true)],
            files_watched: 26, files_pending: ["/etc/passwd"], checked_at: t - 300 } },
        { host_id: "h-pi", host_name: "Raspberry Pi", host_status: "up", checking: false, open_events: 0,
          view: { is_root: true, ssh_log_found: true, failed_24h: 0, attacker_count: 0, banned_count: 0, fail2ban: "none", jails: {}, attackers: [],
            logins: [{ user: "admin", ip: "192.168.2.77", method: "password", ts: t - 600 }],
            ports: [port(22, "tcp", "sshd"), port(80, "tcp", "docker-proxy"), port(53, "udp", "pihole-FTL")], files_watched: 19, files_pending: [], checked_at: t - 300 } },
      ],
      summary: { failed_24h: 312, attackers: 4, banned: 3, open_events: 2, fail2ban_running: 1, hosts: 2, new_ports: 1, files_pending: 1 },
      config: { enabled: true, interval_min: 15, threshold: 20, file_watch: true },
    };
  }
  if (p.startsWith("/ext/nexus-soc/defender/events")) {
    const t = Date.now() / 1000;
    return [
      { id: "ev2", host_id: "h-pve2", host_name: "Proxmox-Knoten pve2", kind: "file_changed", kind_label: "Datei geändert", severity: "warning", title: "Wichtige Datei geändert: /etc/passwd", acknowledged: false, created_at: t - 400,
        detail: { changes: [{ path: "/etc/passwd", change: "changed", severity: "warning", note: "Inhalt geändert" }] } },
      { id: "ev1", host_id: "h-pve2", host_name: "Proxmox-Knoten pve2", kind: "new_port", kind_label: "Neuer offener Port", severity: "warning", title: "Neuer offener Port: 4444/tcp (nc)", acknowledged: false, created_at: t - 900,
        detail: { ports: [{ key: "tcp/4444/nc", proto: "tcp", address: "0.0.0.0", port: 4444, process: "nc", public: true, new: true }] } },
    ];
  }
  if (p.startsWith("/ext/nexus-soc/defender/overview")) return {
    hosts: [
      { host_id: "h-pi", host_name: "Raspberry Pi", host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.0.7", signature_version: "27410", signature_date: "Thu Sep 25 07:12 2026", freshclam_active: true, lynis_installed: true, quarantine_files: 1,
        last_scan: { id: "s1", host_id: "h-pi", host_name: "Raspberry Pi", kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "schedule", status: "clean", files_scanned: 14210, infected: 0, error: null, output_tail: null, started_at: Date.now() / 1000 - 5400, finished_at: null },
        last_audit: { status: "ok", hardening_index: 71, warnings: 1, created_at: Date.now() / 1000 - 80000, error: null }, scanning: false, auditing: false },
      { host_id: "h-pve2", host_name: "Proxmox-Knoten pve2", host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.0.7", signature_version: "27410", signature_date: "Thu Sep 25 06:40 2026", freshclam_active: false, lynis_installed: true,
        last_scan: { id: "s2", host_id: "h-pve2", host_name: "pve2", kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "schedule", status: "infected", files_scanned: 50311, infected: 1, error: null, output_tail: null, started_at: Date.now() / 1000 - 5000, finished_at: null },
        last_audit: { status: "ok", hardening_index: 62, warnings: 3, created_at: Date.now() / 1000 - 80000, error: null }, scanning: true, auditing: false },
      { host_id: "h-docker", host_name: "docker", host_status: "up", reachable: true, clamav_installed: false, lynis_installed: false, last_scan: null, last_audit: null, scanning: false, auditing: false },
      { host_id: "h-pve1", host_name: "Proxmox-Knoten pve1", host_status: "down", reachable: false, error: "Zeitüberschreitung", clamav_installed: false, lynis_installed: false, last_scan: null, last_audit: null, scanning: false, auditing: false },
    ],
    summary: { hosts: 4, protected: 2, open_threats: 1, quarantined: 2, neutralized_total: 5, findings_30d: 3, avg_hardening: 67, score: 58 },
    config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
  };
  if (p === "/host-groups") return [];
  if (p === "/me") return { id: "u1", username: "admin", display_name: "Admin", email: "admin@example.org", is_owner: true, locale: "de", permissions: ["*"], totp_enabled: false, timezone: "Europe/Berlin" };
  if (p === "/roles") return [
    { id: "r1", name: "admin", description: "", is_builtin: true },
    { id: "r2", name: "operator", description: "", is_builtin: true },
    { id: "r3", name: "viewer", description: "", is_builtin: true },
  ];
  if (p === "/users") return [
    { id: "u1", username: "admin", display_name: "Admin", email: "admin@example.org", is_active: true, is_owner: true, roles: [] },
    { id: "u2", username: "max", display_name: "Max Mustermann", email: null, is_active: true, is_owner: false, roles: [{ id: "r2", name: "operator", description: "", is_builtin: true }] },
    { id: "u3", username: "praktikant", display_name: "", email: null, is_active: false, is_owner: false, roles: [{ id: "r3", name: "viewer", description: "", is_builtin: true }] },
  ];
  if (p === "/settings") return [
    { key: "autonomy.mode", value: "propose" }, { key: "autonomy.max_risk", value: "low" },
    { key: "security.deny_patterns", value: ["docker\\s+volume\\s+rm"] },
    { key: "maintenance.windows", value: [{ cron: "0 3 * * 0", duration_minutes: 90, host_ids: "all" }] },
    { key: "system.timezone", value: "Europe/Berlin" }, { key: "audit.retention_days", value: 90 }, { key: "jobs.run_retention_days", value: 30 },
    { key: "hosts.reachability.enabled", value: true }, { key: "hosts.reachability.interval_minutes", value: 2 },
  ];
  if (p.startsWith("/settings/") && method === "PUT") return { key: p.slice("/settings/".length), value: null };
  if (p === "/extensions/proxmox/settings") return {
    schema: PROXMOX_SCHEMA,
    values: { connections: [{ name: "pve2", base_url: "https://192.168.2.11:8006", token_id: "nodvard@pve!dashboard", tls_insecure_skip_verify: true }] },
    secrets: [{ label: "proxmox-token:pve2", title: "API-Token-Geheimnis", description: "Der geheime Wert, den Proxmox beim Anlegen des Tokens einmalig anzeigt.", item: "pve2", is_set: true }],
  };
  if (p === "/extensions/nexus-soc/settings") return {
    schema: NEXUS_SCHEMA, values: { ollama_url: "http://192.168.2.43:11434" },
    secrets: [{ label: "nexus-soc-ollama-key", title: "Nodvard KI: API-Schlüssel des Servers (optional)", description: null, item: null, is_set: false }],
  };
  if (p.startsWith("/extensions/") && p.split("/").length === 3) {
    const id = p.split("/")[2];
    const pg = PAGES.find((x) => x.ext_id === id);
    return { id, name: pg?.title ?? "Proxmox VE", description: "Liest deine Proxmox-Server mit allen VMs und Containern ein.", icon: pg?.icon ?? "server", state: "enabled", version: "0.1.0" };
  }
  if (p === "/extensions" && previewState.ohneProxmox) return ohneProxmoxExtensions();
  if (p === "/extensions") return [
    ...PAGES.map((pg) => ({
      id: pg.ext_id, version: "1.0.0", api_version: "0.1.0", source: "bundled", has_settings: ["proxmox", "backups", "nexus-soc", "gameserver", "service-matrix"].includes(pg.ext_id),
      state: previewState.noHosts ? "disabled" : pg.ext_id === "nexus-soc" ? "disabled" : pg.ext_id === "gameserver" ? "error" : "enabled",
      name: pg.title, description: `Modul ${pg.title}`, icon: pg.icon, granted_permissions: [],
      last_error: !previewState.noHosts && pg.ext_id === "gameserver" ? "on_start() fehlgeschlagen: Verbindung zu game-win abgelehnt" : null,
      needs_setup: previewState.start && pg.ext_id === "proxmox",
      setup_reasons: previewState.start && pg.ext_id === "proxmox" ? ["Zugangsdaten fehlen: API-Token-Geheimnis (pve2)."] : [],
    })),
    {
      ...NTFY, version: "1.0.0", api_version: "0.1.0", source: "bundled", granted_permissions: [], last_error: null,
      state: previewState.noHosts ? "disabled" : "enabled", needs_setup: previewState.start,
      setup_reasons: previewState.start ? ["„ntfy-Server“ ist noch nicht ausgefüllt."] : [],
    },
  ];
  if (p === "/me/preferences") {
    if (method === "PATCH") previewState.firstStepsDismissed = (body as { first_steps_dismissed?: boolean }).first_steps_dismissed ?? previewState.firstStepsDismissed;
    return { first_steps_dismissed: previewState.firstStepsDismissed };
  }
  if (p.startsWith("/audit")) return [
    { id: "a1", ts: "2026-09-25T20:15:00Z", actor_type: "user", actor_id: "u1", action: "login.succeeded", target_type: null, target_id: null, outcome: "success", reason: null, detail: {}, correlation_id: null, ip: "192.168.2.77", user_agent: "Chrome" },
    { id: "a2", ts: "2026-09-25T19:02:00Z", actor_type: "user", actor_id: "u1", action: "action.approved", target_type: "action", target_id: "act-42", outcome: "success", reason: null, detail: { command: "systemctl restart nginx" }, correlation_id: null, ip: null, user_agent: null },
    { id: "a3", ts: "2026-09-25T18:40:00Z", actor_type: "system", actor_id: "gate", action: "exec.denied", target_type: "host", target_id: "h-pi", outcome: "denied", reason: "rm -rf /", detail: {}, correlation_id: null, ip: null, user_agent: null },
    { id: "a4", ts: "2026-09-25T17:11:00Z", actor_type: "user", actor_id: "max", action: "login.failed", target_type: null, target_id: null, outcome: "failure", reason: "Passwort falsch", detail: {}, correlation_id: null, ip: "192.168.2.31", user_agent: null },
  ];
  if (p === "/branding") {
    return {
      product_name: "Nodvard Deck", short_name: "Nodvard Deck", logo_url: null, favicon_url: null,
      login_subtitle: null, support_url: null,
      colors: { accent: "#e11d48", accent_strong: "#7c3aed", background: "#0b0f17", surface: "#121826", text: "#e5e7eb" },
    };
  }
  if (p === "/pages") return previewState.noHosts ? [] : PAGES;
  if (p === "/capabilities") return { api_version: "0.1.0", view_types: [], extensions: [], feature_flags: {} };
  if (p === "/widgets") return previewState.noHosts ? [] : WIDGETS;
  if (p === "/hosts" && previewState.start) return [{ ...HOSTS.find((h) => h.id === "h-pi")!, credential: null, status: "unknown" }];
  if (p === "/hosts") return HOSTS;
  const hostSub = p.match(/^\/hosts\/([^/?]+)(?:\/(tools|actions))?$/);
  if (hostSub && hostSub[2] === "tools") return hostTools(hostSub[1]);
  if (hostSub && hostSub[2] === "actions") return hostActions(hostSub[1]);
  if (hostSub) return HOSTS.find((h) => h.id === hostSub[1]);
  if (/^\/ext\/system\/hosts\/[^/]+\/live$/.test(p)) return previewLive();
  const history = p.match(/^\/hosts\/([^/]+)\/metrics\/history\?range=(\w+)$/);
  // Ohne das Modul „System“ gibt es keinen Messwert-Anbieter: das Backend antwortet mit 404.
  if (history) return previewState.ohneProxmoxMin ? undefined : metricsHistory(history[1], history[2]);
  const metrics = p.match(/^\/hosts\/([^/]+)\/metrics$/);
  if (metrics) return previewState.ohneProxmoxMin ? undefined : METRICS[metrics[1]];
  if (p === "/hosts/metrics/latest") return { hosts: previewState.noHosts || previewState.start ? {} : LATEST, stale_after_s: 120 };
  const apps = appRoutes(p, method, body);
  if (apps.handled) return apps.value;
  if (p === "/overview") return withApps(previewState.noHosts ? EMPTY_OVERVIEW : OVERVIEW, customApps().map(appTile));
  // Echtes Backend-Format: nur die IDs (api/v1/terminal.py list_terminal_hosts -> list[str]).
  if (p === "/terminal/hosts") return previewState.start || previewState.noHosts ? [] : HOSTS.filter((h) => h.kind !== "hypervisor").map((h) => h.id);
  if (p === "/console/hosts") return previewState.start || previewState.noHosts ? [] : HOSTS.filter((h) => h.kind === "vm" || h.kind === "lxc").map((h) => h.id);
  if (p === "/actions" || p.startsWith("/actions?")) {
    const status = new URLSearchParams(p.split("?")[1] ?? "").get("status_");
    return status ? ACTIONS.filter((a) => a.status === status) : ACTIONS;
  }
  if (p === "/files/sources") return previewState.noHosts ? [] : FILE_SOURCES;
  if (/^\/files\/[^/]+\/list\?/.test(p)) return { items: FILE_ENTRIES, next_cursor: null };
  if (p === "/notifications/unread-count") return { unread: 3 };
  if (p.startsWith("/notifications?")) return NOTIFICATIONS;
  if (p.startsWith("/notifications")) return [];
  if (p === "/dashboard/layouts") return [{ id: "l1", name: "Standard", is_default: true, items: previewState.noHosts ? [] : layoutItems, created_at: "", updated_at: "" }];
  if (p === "/dashboard/layouts/l1" && method === "PUT") return { id: "l1", name: "Standard", is_default: true, items: layoutItems, created_at: "", updated_at: "" };
  if (p in WIDGET_DATA) return { data: WIDGET_DATA[p], meta: {} };
  layoutItems = layoutItems.slice();
  return undefined;
}

/** Meldungen fuer die Glocken-Schnellansicht (relativ zu "jetzt", damit "vor 5 min" stimmt). */
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
const NOTIFICATIONS = [
  { id: "n1", ts: minutesAgo(5), severity: "critical", title: "Speicher fast voll: local-lvm auf pve2 bei 95 %", body: "", source_ext_id: "proxmox", correlation_id: null, read_at: null, payload: { path: "/hosts/pve2" } },
  { id: "n2", ts: minutesAgo(32), severity: "warning", title: "Einbruchschutz: 2 neue Sicherheitsereignisse, unter anderem ein ungewöhnlicher SSH-Login von einer unbekannten Adresse", body: "", source_ext_id: "nexus-soc", correlation_id: null, read_at: null, payload: { path: "/ext/nexus-soc/soc?tab=guard" } },
  { id: "n3", ts: minutesAgo(95), severity: "info", title: "", body: "Backup „docker“ erfolgreich abgeschlossen\n2,4 GB in 6 min", source_ext_id: "backups", correlation_id: null, read_at: null, payload: {} },
  { id: "n4", ts: minutesAgo(60 * 7), severity: "info", title: "Morgen-Briefing: alles im grünen Bereich", body: "", source_ext_id: "nexus-soc", correlation_id: null, read_at: minutesAgo(60 * 6), payload: { path: "/ext/nexus-soc/soc" } },
  { id: "n5", ts: minutesAgo(60 * 30), severity: "warning", title: "Updates verfügbar: 12 Pakete auf gameserver", body: "", source_ext_id: "nexus-soc", correlation_id: null, read_at: minutesAgo(60 * 29), payload: {} },
];

const PROXMOX_SCHEMA = {"type": "object", "properties": {"connections": {"type": "array", "title": "Proxmox-Server", "description": "Ein Eintrag je Proxmox-Server oder -Cluster. Knoten, VMs und Container werden automatisch eingelesen.", "x-item-title": "Server", "items": {"type": "object", "properties": {"name": {"type": "string", "title": "Kurzname", "description": "Eindeutig und ohne Leerzeichen, z. B. „pve2“ oder „pve1“. Nach dem ersten Einlesen nicht mehr ändern."}, "base_url": {"type": "string", "title": "Adresse", "description": "z. B. https://192.168.2.11:8006"}, "token_id": {"type": "string", "title": "API-Token-ID", "description": "Format benutzer@realm!tokenname, z. B. nodvard@pve!dashboard. Erstellen unter Rechenzentrum → Berechtigungen → API-Token."}, "tls_insecure_skip_verify": {"type": "boolean", "default": false, "title": "Selbstsigniertes Zertifikat erlauben", "description": "Nur im eigenen Netz aktivieren. Nötig für das Standardzertifikat von Proxmox."}}, "required": ["name", "base_url", "token_id"]}}}, "required": ["connections"], "x-secrets": [{"label": "proxmox-token:{name}", "per_item": "connections", "title": "API-Token-Geheimnis", "description": "Der geheime Wert, den Proxmox beim Anlegen des Tokens einmalig anzeigt."}]};
const NEXUS_SCHEMA = {"type": "object", "properties": {"ollama_url": {"type": "string", "title": "Nodvard KI: Server (Ollama)", "description": "Adresse deines Ollama-Servers für Nodvard KI, z. B. http://192.168.2.43:11434"}, "ollama_model": {"type": "string", "default": "qwen2.5:7b", "title": "Nodvard KI: Modell", "description": "Name des Modells auf dem Ollama-Server."}, "ollama_failover_url": {"type": "string", "title": "Nodvard KI: Ersatz-Server (optional)", "description": "Wird nur gefragt, wenn der erste Server nicht antwortet."}, "ollama_failover_model": {"type": "string", "default": "qwen2.5:0.5b", "title": "Nodvard KI: Ersatz-Modell", "description": "Meist ein kleineres, schnelleres Modell."}, "docker_host_tag": {"type": "string", "default": "docker", "title": "Überwachte Server", "description": "Server mit dieser Markierung (Tag) werden auf abgestürzte Container überwacht."}, "incident_batch_delay_s": {"type": "number", "default": 300, "title": "Sammelzeit (Sekunden)", "description": "So lange werden Vorfälle gesammelt, bevor die KI eine Einschätzung abgibt.", "x-advanced": true}, "host_target_cooldown_s": {"type": "number", "default": 1800, "title": "Ruhezeit je Container (Sekunden)", "description": "Mindestabstand, bevor derselbe Container erneut als Vorfall gemeldet wird.", "x-advanced": true}, "suppressed_hosts": {"type": "array", "items": {"type": "string"}, "title": "Ignorierte Server", "description": "Vorfälle dieser Server werden nie gemeldet, z. B. absichtlich pausierte Systeme.", "x-advanced": true}, "forbidden_host_keywords": {"type": "array", "items": {"type": "string"}, "default": ["pve", "proxmox", "host", "server", "node", "router", "gateway", "nas"], "title": "Schutzwörter", "description": "Schlägt die KI einen Container vor, dessen Name eines dieser Wörter enthält, wird der Vorschlag verworfen (Schutz davor, einen ganzen Server statt eines Containers neu zu starten). Die Vorgabe ist nur ein allgemeiner Startwert; ergänze hier die Namen deiner eigenen Server.", "x-advanced": true}}, "required": ["ollama_url"], "x-secrets": [{"label": "nexus-soc-ollama-key", "title": "Nodvard KI: API-Schlüssel des Servers (optional)", "description": "Nur nötig, wenn dein Ollama-Server hinter einer Anmeldung liegt."}]};

// ---------------------------------------------------------------------------
// Szenario „ohne Proxmox“ (`?scenario=ohne-proxmox`, `…-min`): eine Installation ganz ohne Proxmox.
// Vier von Hand angelegte Server mit SSH-Zugang (Raspberry Pi, Debian-VM, NAS, Spiele-PC), die
// Module Proxmox und Backups sind aus. Die Server haben nie „Verbindung prüfen“ erlebt, ausser
// dem Pi -- so sieht es aus, solange nichts den Zustand von Hand angelegter Server nachführt.
// Bei `-min` sind auch System, Service-Matrix, Nodvard Shield & Co. aus (nur Terminal/Dateien laufen).
// ---------------------------------------------------------------------------

const KEY_PW: PreviewCredential = { kind: "ssh_password", username: "admin", port: 22 };

function respondWithoutScenario(p: string, method: string, body?: unknown): unknown {
  const saved = previewState.ohneProxmox;
  previewState.ohneProxmox = false;
  try {
    return respond(`/api/v1${p}`, method, body);
  } finally {
    previewState.ohneProxmox = saved;
  }
}

const NP_ID: Record<string, { id: string; name: string }> = {
  "h-pi": { id: "m-pi", name: "Raspberry Pi" },
  "h-docker": { id: "m-deb", name: "Debian-VM" },
};

/** Nodvard-Shield-Antworten auf die Server dieses Szenarios umbiegen (Pi und Debian-VM). */
function ohneProxmoxSoc(p: string, base: Record<string, unknown>): unknown {
  if (Array.isArray(base)) {
    return base
      .filter((e) => NP_ID[(e as { host_id: string }).host_id])
      .map((e) => ({ ...(e as object), host_id: NP_ID[(e as { host_id: string }).host_id].id, host_name: NP_ID[(e as { host_id: string }).host_id].name }));
  }
  const hosts = (base.hosts as { host_id: string; host_name: string; host_status: string }[] | undefined) ?? [];
  const kept = hosts
    .filter((h) => NP_ID[h.host_id])
    .map((h) => ({ ...h, host_id: NP_ID[h.host_id].id, host_name: NP_ID[h.host_id].name, host_status: h.host_id === "h-pi" ? "up" : "unknown" }));
  const summary = { ...(base.summary as Record<string, number>), hosts: kept.length };
  if (p.includes("/defender/updates")) {
    Object.assign(summary, { checked: kept.length, up_to_date: 1, packages: 3, security: 0, reboot: 0, errors: 0 });
  }
  if (p.includes("/defender/guard")) {
    Object.assign(summary, { failed_24h: 0, attackers: 0, banned: 0, open_events: 0, fail2ban_running: 0, new_ports: 0, files_pending: 0 });
  }
  if (p.includes("/defender/overview")) {
    Object.assign(summary, { protected: 1, open_threats: 0, quarantined: 0, score: 71 });
  }
  return { ...base, hosts: kept, summary, runs: [], ...(p.includes("/defender/updates") ? { runs: [] } : {}) };
}

function ohneProxmoxExtensions() {
  const row = (id: string, name: string, icon: string, state: string, hasSettings = false) => ({
    id, version: "1.0.0", api_version: "0.1.0", source: "bundled", has_settings: hasSettings, state, name,
    description: `Modul ${name}`, icon, granted_permissions: [], last_error: null, needs_setup: false, setup_reasons: [],
  });
  const on = previewState.ohneProxmoxMin ? "disabled" : "enabled";
  return [
    row("terminal", "Terminal", "terminal", "enabled"),
    row("system", "System", "cpu", on),
    row("service-matrix", "Service-Matrix", "layout-grid", on, true),
    row("proxmox", "Proxmox VE", "server", "disabled", true),
    row("backups", "Backups", "database-backup", "disabled", true),
    row("gameserver", "Gameserver", "gamepad-2", "disabled", true),
    row("nexus-soc", "Nodvard Shield", "shield-alert", on, true),
    row("scripts", "Skripte", "terminal-square", on),
    { ...NTFY, version: "1.0.0", api_version: "0.1.0", source: "bundled", granted_permissions: [], last_error: null, state: "disabled", needs_setup: false, setup_reasons: [] },
  ];
}

/** Stellt die Testdaten auf das Szenario „ohne Proxmox“ um. Einmal vor dem ersten Rendern aufrufen. */
export function applyOhneProxmox(min: boolean, many = false): void {
  previewState.ohneProxmox = true;
  previewState.ohneProxmoxMin = min;
  const mk = (id: string, name: string, address: string, status: string, extra: { os?: string; cred: PreviewCredential; tags?: string[] }) => ({
    ...host(id, name, address, null, status, { name: name.toLowerCase().replace(/[^a-z0-9]+/g, "-"), provider_ext_id: null, os_family: extra.os ?? "linux", credential: extra.cred }),
    tags: (extra.tags ?? []) as never[], last_seen_at: (status === "up" ? new Date(Date.now() - 3 * 86_400_000).toISOString() : null) as never,
  });
  HOSTS.splice(0, HOSTS.length,
    mk("m-pi", "Raspberry Pi", "192.168.2.31", "up", { cred: KEY_LATTICE, tags: ["docker"] }),
    mk("m-deb", "Debian-VM", "192.168.2.40", "unknown", { cred: KEY_LATTICE, tags: ["docker"] }),
    mk("m-nas", "NAS", "192.168.2.20", "unknown", { cred: KEY_PW }),
    mk("m-win", "Spiele-PC", "192.168.2.50", "unknown", { os: "windows", cred: KEY_PW, tags: ["gameserver"] }),
  );
  for (const key of Object.keys(HOST_ID_BY_NAME)) delete HOST_ID_BY_NAME[key];
  for (const h of HOSTS) HOST_ID_BY_NAME[h.display_name] = h.id;
  for (const key of Object.keys(GROUP_MEMBERS)) delete GROUP_MEMBERS[key];

  const on = !min;
  PAGES.splice(0, PAGES.length,
    ...(on ? [
      page("system", "system", "/system", "System", "cpu", "Infrastruktur", 10),
      page("service-matrix", "matrix", "/matrix", "Service-Matrix", "layout-grid", "Infrastruktur", 30),
      page("nexus-soc", "soc", "/soc", "Nodvard Shield", "shield-alert", "Sicherheit", 20),
      page("scripts", "scripts", "/scripts", "Skripte", "terminal-square", "Automatisierung", 20),
    ] : []),
  );
  const svcNp = (hostName: string, name: string, image: string, port: number | null, state = "running") => ({
    id: `${hostName}:${name}`, name, host: hostName, host_id: HOST_ID_BY_NAME[hostName], state, tone: state === "running" ? "good" : "danger",
    url: port ? `http://192.168.2.${hostName === "Raspberry Pi" ? 31 : 40}:${port}` : null, image,
  });
  const services = on ? [
    svcNp("Raspberry Pi", "pihole", "pihole/pihole", 8088),
    svcNp("Raspberry Pi", "deploy-nodvard-deck-1", "nodvard-deck:latest", 8080),
    svcNp("Debian-VM", "nextcloud-app", "nextcloud:29", 8081),
    svcNp("Debian-VM", "nextcloud-db", "mariadb:11", null),
    svcNp("Debian-VM", "uptime-kuma", "louislam/uptime-kuma", 3001),
  ] : [];
  Object.assign(OVERVIEW, {
    services, services_running: services.length, backups: null, pending_actions: 0, unread_notifications: on ? 1 : 0, attention: [], errors: [],
  });
  WIDGETS.splice(0, WIDGETS.length, ...(on ? [
    w("system", "health", "Server-Zustand", "cpu", "widgets/health", {
      kind: "list", empty_text: "Keine Linux-Server mit SSH-Zugang", max_items: null,
      item: listItem("{{ name }}", "{{ summary }}", { text: "{{ badge }}", tone: "{{ tone }}" }),
    }),
    w("service-matrix", "matrix", "Service-Matrix", "layout-grid", "widgets/matrix", {
      kind: "status_grid", tile_title: "{{ name }}", tile_subtitle: "{{ host }}", tile_tone: "{{ tone }}", tile_link: null,
    }),
    w("nexus-soc", "incidents", "Vorfälle", "shield-alert", "widgets/incidents", {
      kind: "list", empty_text: "Keine offenen Vorfälle", max_items: null,
      item: listItem("{{ title }}", "{{ host }}", { text: "{{ status_label }}", tone: "{{ tone }}" }),
    }),
  ] : []));
  layoutItems = WIDGETS.map((widget, i) => ({ widget_id: widget.id, ext_id: widget.ext_id, x: (i * 2) % 12, y: 0, w: 2, h: 2, config: {} }));
  for (const key of Object.keys(WIDGET_DATA)) delete WIDGET_DATA[key];
  Object.assign(WIDGET_DATA, {
    "/ext/system/widgets/health": [
      { name: "Raspberry Pi", summary: "Debian 13 · CPU 6 % · RAM 44 % · Platte 30 %", badge: "gut", tone: "good" },
      { name: "Debian-VM", summary: "Debian 12 · CPU 2 % · RAM 31 % · Platte 52 %", badge: "gut", tone: "good" },
      { name: "NAS", summary: "Debian 12 · CPU 1 % · RAM 18 % · Platte 78 %", badge: "gut", tone: "good" },
    ],
    "/ext/service-matrix/widgets/matrix": services.map((sv) => ({ name: sv.name, host: sv.host, tone: sv.tone })),
    "/ext/nexus-soc/widgets/incidents": [],
  });
  for (const key of Object.keys(LATEST)) delete LATEST[key];
  if (on) {
    Object.assign(LATEST, {
      "m-pi": latestOf(6.4, 1.7, 3.7, 22.6, 58.0, 70454),
      "m-deb": latestOf(2.1, 1.2, 3.9, 21.3, 40.0, 1_240_000),
      "m-nas": latestOf(1.2, 0.7, 3.9, 1560, 2000, 5_400_000),
    });
  }
  Object.assign(METRICS, {
    "m-pi": { values: { cpu_percent: 6.4, mem_used_bytes: 1.7 * GB, mem_total_bytes: 3.7 * GB, uptime_s: 70454 }, sampled_at: "" },
    "m-deb": { values: { cpu_percent: 2.1, mem_used_bytes: 1.2 * GB, mem_total_bytes: 3.9 * GB, uptime_s: 1_240_000 }, sampled_at: "" },
    "m-nas": { values: { cpu_percent: 1.2, mem_used_bytes: 0.7 * GB, mem_total_bytes: 3.9 * GB, uptime_s: 5_400_000 }, sampled_at: "" },
  });
  if (many && on) addManyServers();
  FILE_SOURCES.splice(0, FILE_SOURCES.length, ...HOSTS.map((h) => ({ source_id: `ssh-sftp:${h.id}`, label: `SSH (SFTP): ${h.display_name}`, icon: "server", caps: FILE_CAPS })));
}

/** `?scenario=ohne-proxmox-viele`: 30 Server, damit man sieht, ob das Cockpit-Raster übersichtlich bleibt. Meist aktuell,
 * ein paar mit veraltetem Wert, zwei nicht erreichbar, einige Windows-/Geräte-Zeilen ohne Messwerte. */
function addManyServers(): void {
  const names = ["nas-keller", "web", "db", "mail", "git", "monitoring", "backup", "dns", "vpn", "media", "wiki", "cloud", "build", "test", "proxy", "iot",
    "pi-flur", "pi-garage", "pi-werkstatt", "ci-runner", "registry", "chat", "druck", "home-assistant", "kamera", "router-lab", "zimmer-pc", "vm-alt"];
  names.forEach((name, i) => {
    const id = `x-${String(i + 1).padStart(2, "0")}`;
    const windows = i % 9 === 8;
    const offline = i === 5 || i === 17;
    HOSTS.push({
      ...host(id, name, `192.168.2.${100 + i}`, null, offline ? "down" : "up", {
        name, provider_ext_id: null, os_family: windows ? "windows" : "linux", credential: KEY_LATTICE,
      }),
      tags: [] as never[], last_seen_at: new Date(Date.now() - 60_000).toISOString() as never,
    });
    HOST_ID_BY_NAME[name] = id;
    if (windows) return; // Windows: keine Messung, bleibt eine Zeile
    const stale = i % 7 === 3;
    LATEST[id] = latestOf(
      (i * 13) % 97, 0.5 + ((i * 7) % 30) / 10, 4, 10 + ((i * 37) % 80), 100, 86_400 * (1 + i),
      stale ? 240 + i * 10 : offline ? 900 : 5 + (i % 20),
    );
  });
}
