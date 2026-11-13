import { describe, expect, it } from "vitest";

import { previewState, respond } from "./fixtures";

type Row = Record<string, unknown>;

it("Vorschau zeigt Update-Arten und Freigabe zusammen", () => {
  const helper = previewState.helper;
  previewState.helper = "bereit";
  try {
    expect(respond("/api/v1/system/updates", "GET")).toMatchObject({
      available: true,
      kinds: ["behoben"],
      kinds_complete: true,
      approved: true,
      approved_key: "21fe31dfa154a261",
    });
  } finally {
    previewState.helper = helper;
  }
});

function rows(path: string): Row[] {
  const value = respond(path, "GET") as Row[] | { data: Row[] };
  return Array.isArray(value) ? value : value.data;
}

const overview = () => respond("/api/v1/overview", "GET") as Row & { backups: { total: number; ok: number }; services_running: number; attention: Row[] };

describe("Vorschau: Übersicht und Modulseiten erzählen dieselben Daten", () => {
  it("Gäste im Proxmox-Widget wie in der Serverliste", () => {
    const hosts = new Map(rows("/api/v1/hosts?tag=proxmox").map((h) => [h.display_name, h.status]));
    for (const g of rows("/api/v1/ext/proxmox/widgets/overview")) {
      expect(hosts.get(g.name), String(g.name)).toBe(g.status === "stopped" ? "down" : "up");
    }
  });

  it("laufende Container wie auf der Docker-Seite", () => {
    const running = rows("/api/v1/ext/docker/widgets/matrix").filter((c) => c.state === "running").length;
    expect(overview().services_running).toBe(running);
  });

  it("Proxmox-Updates im Widget wie auf der Proxmox-Seite", () => {
    const nodes = (respond("/api/v1/ext/proxmox/updates", "GET") as { nodes: Row[] }).nodes;
    for (const w of rows("/api/v1/ext/proxmox/widgets/updates")) {
      const node = nodes.find((n) => n.node === w.node)!;
      expect(w.badge, String(w.node)).toBe(node.badge);
    }
  });

  it("Backups in der Übersicht wie im Backup-Center", () => {
    const jobs = rows("/api/v1/ext/backups/widgets/summary").filter((j) => j.job_ref);
    expect(overview().backups.total).toBe(jobs.length);
    expect(overview().backups.ok).toBe(jobs.filter((j) => j.tone === "good").length);
  });

  it("was Aufmerksamkeit braucht, steht auch unter Meldungen", () => {
    const titles = rows("/api/v1/notifications?limit=50").map((n) => n.title);
    for (const a of overview().attention) expect(titles).toContain(a.title);
  });
});

describe("Vorschau: Module", () => {
  it("Backup-Widget zeigt den Leertext der Anwendung", () => {
    const widget = rows("/api/v1/widgets").find((w) => w.ext_id === "backups" && w.id === "summary");
    expect(widget?.view).toMatchObject({ kind: "list", empty_text: "Keine Sicherungsjobs gefunden" });
  });

  it("jedes Modul zeigt seine Beschreibung aus der extension.toml, keinen Platzhalter", () => {
    const modules = rows("/api/v1/extensions");
    expect(modules.length).toBeGreaterThan(5);
    for (const m of modules) {
      expect(m.description, String(m.id)).toEqual(expect.stringMatching(/\S/));
      expect(m.description, String(m.id)).not.toMatch(/^Modul /);
    }
    expect(modules.find((m) => m.id === "docker")?.description).toMatch(/^Findet Docker-Container/);
  });
});
