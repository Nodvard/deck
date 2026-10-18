import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SynologyPage, dsmTime, durationText, percentText, sizeText, toneOf, usageText,
  type BackupTask, type Disk, type Overview, type Pool, type Problem, type Volume,
} from "./SynologyPage";

const GIB = 1024 ** 3;
const TIB = 1024 ** 4;

const volume1: Volume = {
  id: "volume_1", name: "Volume 1", status: "normal", status_label: "Normal", tone: "good", fs_type: "btrfs",
  used: 412 * GIB, total: Math.round(3.6 * TIB), percent: 11, pool_id: "reuse_1",
};
const volume2: Volume = {
  id: "volume_2", name: "Volume 2", status: "normal", status_label: "Normal", tone: "good", fs_type: null,
  used: 950 * GIB, total: 1000 * GIB, percent: 95, pool_id: null,
};
const disk1: Disk = {
  id: "sata1", name: "Laufwerk 1", model: "WDC WD40EFRX-68N32N0", status: "normal", status_label: "Normal", tone: "good",
  kind: "hdd", temp: 36, temp_warn: false, smart_status: "normal", smart_label: "In Ordnung", smart_tone: "good", warning: null,
};
const disk2: Disk = {
  id: "sata2", name: "Laufwerk 2", model: "Seagate ST4000VN006", status: "normal", status_label: "Normal", tone: "warn",
  kind: "hdd", temp: 39, temp_warn: false, smart_status: "normal", smart_label: "In Ordnung", smart_tone: "good", warning: "Zu viele defekte Sektoren",
};

const pool1: Pool = {
  id: "reuse_1", name: "Speicherpool 1", description: "Hauptpool", raid: "SHR", status: "normal", status_label: "Normal", tone: "good",
  disks: 2, volumes: ["Volume 1", "Volume 2"], total: Math.round(3.6 * TIB),
};
const pool2: Pool = {
  id: "reuse_2", name: "Speicherpool 2", description: null, raid: null, status: "degraded", status_label: "Beeinträchtigt", tone: "warn",
  disks: null, volumes: [], total: null,
};

const task1: BackupTask = {
  id: "1", name: "Nachtsicherung", state: "backupable", running: false, last_result: "done", result_label: "Erfolgreich", tone: "good",
  last_run: "2026-10-16T03:00", last_success: "2026-10-16T03:00", next_run: "2026-10-17T03:00", overdue: false, problem: null,
};
const task2: BackupTask = {
  id: "2", name: "Fotos in die Cloud", state: "backupable", running: false, last_result: "failed", result_label: "Fehlgeschlagen", tone: "danger",
  last_run: null, last_success: null, next_run: null, overdue: true, problem: "Der letzte Lauf ist fehlgeschlagen. Prüfe das Ziel.",
};

const critical: Problem = {
  key: "pool:reuse_1", subject: "Speicherpool 1", severity: "critical", title: "Speicherpool beeinträchtigt",
  text: "Ein Laufwerk ist ausgefallen. Tausche es bald aus.",
};
const warning: Problem = {
  key: "backup-failed:2", subject: "Hyper Backup „Fotos in die Cloud“", severity: "warning", title: "Sicherung fehlgeschlagen",
  text: "Der letzte Lauf ist fehlgeschlagen.",
};
const info: Problem = { key: "update", subject: "DSM", severity: "info", title: "Update", text: "DSM 7.2.2-72806 steht bereit." };

const overview = (over: Partial<Overview> = {}): Overview => ({
  state: "ok",
  message: null,
  url: "https://nas.test:5001",
  system: { model: "DS220+", dsm_version: "DSM 7.2.1-69057 Update 5", uptime_seconds: 3 * 86400 + 4 * 3600 + 120, temperature: 42, temperature_warn: false, ram_mb: 2048, clock: "2026-10-17T14:05" },
  load: { cpu_percent: 12, ram_percent: 41 },
  volumes: [volume1, volume2],
  disks: [disk1, disk2],
  pools: null,
  update: { available: true, version: "DSM 7.2.2-72806" },
  backups: null,
  system_note: null,
  load_note: null,
  storage_note: null,
  update_note: null,
  backup_note: null,
  problems: [],
  ...over,
});

/** Antwort eines älteren Backends: ohne die neueren Felder. */
const legacy = (): Record<string, unknown> => {
  const body: Record<string, unknown> = JSON.parse(JSON.stringify(overview()));
  for (const key of ["pools", "backups", "backup_note", "problems"]) delete body[key];
  for (const disk of body.disks as Record<string, unknown>[]) { delete disk.kind; delete disk.temp_warn; }
  for (const volume of body.volumes as Record<string, unknown>[]) delete volume.pool_id;
  delete (body.system as Record<string, unknown>).clock;
  return body;
};

const failed = (over: Partial<Overview>): Overview =>
  overview({ system: null, load: null, volumes: null, disks: null, update: null, ...over });

let permissions: Set<string>;
let urls: string[];
let answer: () => Response | Promise<Response>;

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

beforeEach(() => {
  permissions = new Set(["hosts.read", "extensions.manage"]);
  urls = [];
  answer = () => ok(overview());
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(),
    hasPermission: (p: string) => permissions.has(p),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    urls.push(url);
    if (url.includes("/ext/synology/overview")) return answer();
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("Synology-Seite", () => {
  it("zeigt Modell, DSM-Version, Kacheln, Volumes und Festplatten", async () => {
    render(<SynologyPage />);
    const device = within(await screen.findByTestId("device"));

    expect(screen.getByRole("heading", { name: "DS220+" })).toBeInTheDocument();
    expect(device.getByTestId("facts")).toHaveTextContent("DSM 7.2.1-69057 Update 5 · 2,0 GB Arbeitsspeicher");
    expect(screen.getByRole("link", { name: "DSM öffnen" })).toHaveAttribute("href", "https://nas.test:5001");
    expect(screen.getByText("Hinweis", { selector: "span" })).toBeInTheDocument(); // Festplatte 2 hat eine Warnung

    const tiles = within(screen.getByTestId("tiles"));
    expect(tiles.getByText("CPU").closest("div")).toHaveTextContent("12 %");
    expect(tiles.getByText("Arbeitsspeicher").closest("div")).toHaveTextContent("41 %");
    expect(tiles.getByText("Arbeitsspeicher").closest("div")).toHaveTextContent("von 2,0 GB");
    expect(tiles.getByText("Temperatur").closest("div")).toHaveTextContent("42 °C");
    expect(tiles.getByText("Laufzeit").closest("div")).toHaveTextContent("3 Tage 4 Std.");

    expect(screen.getByTestId("update")).toHaveTextContent("Update verfügbar: DSM 7.2.2-72806");

    const volumes = screen.getAllByTestId("volume");
    expect(volumes).toHaveLength(2);
    expect(within(volumes[0]).getByText("Volume 1")).toBeInTheDocument();
    expect(within(volumes[0]).getByText("btrfs")).toBeInTheDocument();
    expect(within(volumes[0]).getByText("Normal")).toBeInTheDocument();
    expect(within(volumes[0]).getByText("412 GB von 3,6 TB (11 %)")).toBeInTheDocument();
    expect(within(volumes[0]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "11");
    expect(within(volumes[1]).getByText("950 GB von 1000 GB (95 %)")).toHaveClass("text-amber-300");

    const disks = screen.getAllByTestId("disk");
    expect(disks).toHaveLength(2);
    expect(within(disks[0]).getByText("Laufwerk 1")).toBeInTheDocument();
    expect(within(disks[0]).getByText("WDC WD40EFRX-68N32N0")).toBeInTheDocument();
    expect(within(disks[0]).getByText("36 °C")).toBeInTheDocument();
    expect(within(disks[0]).getByText("In Ordnung")).toBeInTheDocument();
    expect(within(disks[1]).getByTestId("disk-warning")).toHaveTextContent("Zu viele defekte Sektoren");
    expect(urls).toEqual(["/api/v1/ext/synology/overview"]);
  });

  it("ohne Update steht „DSM ist aktuell“, ohne Probleme „in Ordnung“", async () => {
    answer = () => ok(overview({ update: { available: false, version: null }, disks: [disk1] }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("up-to-date")).toHaveTextContent("DSM ist aktuell.");
    expect(screen.queryByTestId("update")).not.toBeInTheDocument();
    expect(screen.getByText("in Ordnung", { selector: "span" })).toBeInTheDocument();
  });

  it("ein Update ohne Versionsangabe nennt keine leere Version", async () => {
    answer = () => ok(overview({ update: { available: true, version: null } }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("update")).toHaveTextContent(/^Update verfügbar$/);
  });

  it("ein ausgefallenes Volume macht die Kopfzeile rot, eine zu warme Temperatur auch", async () => {
    const crashed: Volume = { ...volume1, status: "crashed", status_label: "Ausgefallen", tone: "danger" };
    answer = () => ok(overview({ volumes: [crashed] }));
    const { unmount } = render(<SynologyPage />);
    const row = await screen.findByTestId("volume");
    expect(within(row).getByText("Ausgefallen")).toBeInTheDocument();
    expect(screen.getByText("Problem", { selector: "span" })).toBeInTheDocument();
    unmount();

    answer = () => ok(overview({ volumes: [volume1], disks: [disk1], system: { ...overview().system!, temperature_warn: true } }));
    render(<SynologyPage />);
    expect(await screen.findByText("DSM meldet: zu warm")).toBeInTheDocument();
    expect(screen.getByText("Problem", { selector: "span" })).toBeInTheDocument();
  });

  it("eine hohe Auslastung färbt die Kachel, die Prozentzahl wird gerundet", async () => {
    answer = () => ok(overview({ load: { cpu_percent: 95.4, ram_percent: 40.5 } }));
    render(<SynologyPage />);
    const tiles = within(await screen.findByTestId("tiles"));
    expect(tiles.getByText("95 %")).toHaveClass("text-amber-300");
    expect(tiles.getByText("41 %")).not.toHaveClass("text-amber-300");
  });

  it("fehlende Rechte für den Speicher: Hinweis bei den Volumes, die übrigen Teile bleiben", async () => {
    const note = { kind: "no_rights", message: "Diesem Konto fehlen Rechte. Für Speicher braucht das Konto Administratorrechte (Mitglied der Gruppe „administrators“)." };
    answer = () => ok(overview({ volumes: null, disks: null, storage_note: note }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("note-storage")).toHaveTextContent(note.message);
    expect(screen.queryByTestId("volumes")).not.toBeInTheDocument();
    expect(screen.queryByTestId("disks")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "DS220+" })).toBeInTheDocument();
    expect(screen.getByTestId("tiles")).toBeInTheDocument();
    expect(screen.getByTestId("update")).toBeInTheDocument();
    expect(screen.getByText("unvollständig", { selector: "span" })).toBeInTheDocument();
  });

  it("ein Teil, den dieses DSM nicht kennt, ist nur ein Hinweis und macht die Seite nicht unvollständig", async () => {
    answer = () => ok(overview({ update: null, update_note: { kind: "unavailable", message: "Auf diesem DSM nicht verfügbar." }, disks: [disk1] }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("note-update")).toHaveTextContent("Auf diesem DSM nicht verfügbar.");
    expect(screen.queryByText("unvollständig")).not.toBeInTheDocument();
    expect(screen.getByText("in Ordnung", { selector: "span" })).toBeInTheDocument();
  });

  it("scheitert die Geräteinfo oder die Auslastung, steht der Grund unter den Kacheln und die Werte sind „–“", async () => {
    answer = () => ok(overview({
      system: null, load: null,
      system_note: { kind: "error", message: "Die Antwort von Synology DSM auf die Frage nach dem Gerät hat nicht das erwartete Format." },
      load_note: { kind: "no_rights", message: "Diesem Konto fehlen Rechte. Für die Auslastung braucht das Konto Administratorrechte." },
    }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("note-system")).toHaveTextContent("nicht das erwartete Format");
    expect(screen.getByTestId("note-load")).toHaveTextContent("Für die Auslastung");
    expect(within(screen.getByTestId("tiles")).getAllByText("–")).toHaveLength(4);
    expect(screen.getByRole("heading", { name: "Synology" })).toBeInTheDocument();
    expect(screen.getByTestId("volumes")).toBeInTheDocument();
  });

  it("leere Listen sagen es ausdrücklich", async () => {
    answer = () => ok(overview({ volumes: [], disks: [] }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("no-volumes")).toHaveTextContent("DSM meldet keine Volumes.");
    expect(screen.getByTestId("no-disks")).toHaveTextContent("DSM meldet keine Festplatten.");
    expect(screen.queryByTestId("volumes")).not.toBeInTheDocument();
  });

  it("Namen aus DSM erscheinen nur als Text, nie als HTML", async () => {
    const evil = { ...disk1, name: "<img src=x onerror=alert(1)>" };
    answer = () => ok(overview({ disks: [evil] }));
    const { container } = render(<SynologyPage />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("ein Link mit gefährlichem Schema wird nicht angeboten", async () => {
    answer = () => ok(overview({ url: "javascript:alert(1)" }));
    render(<SynologyPage />);
    await screen.findByTestId("device");
    expect(screen.queryByRole("link", { name: "DSM öffnen" })).not.toBeInTheDocument();
  });

  it("nicht eingerichtet: Hinweis mit Link zu den Einstellungen, ohne Recht der Verweis auf einen Administrator", async () => {
    const message = "Synology DSM ist noch nicht eingerichtet. Unter Einstellungen → Erweiterungen → Synology DSM eintragen.";
    answer = () => ok(failed({ state: "not_configured", message, url: null }));
    const { unmount } = render(<SynologyPage />);
    expect(await screen.findByText("Noch nicht eingerichtet")).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Jetzt einrichten|Einstellungen/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/settings/extensions/synology", "/settings/extensions/synology"]);
    expect(screen.queryByTestId("device")).not.toBeInTheDocument();
    unmount();

    permissions.delete("extensions.manage");
    render(<SynologyPage />);
    expect(await screen.findByText("Noch nicht eingerichtet")).toBeInTheDocument();
    expect(screen.getByText(/Ein Administrator kann Synology DSM/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Grund, Erneut versuchen fragt ohne Zwischenspeicher nach", async () => {
    const message = "Synology DSM antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob die Synology läuft.";
    answer = () => ok(failed({ state: "unreachable", message }));
    render(<SynologyPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.queryByTestId("tiles")).not.toBeInTheDocument();

    answer = () => ok(overview());
    fireEvent.click(problem.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("device")).toBeInTheDocument());
    expect(urls).toEqual(["/api/v1/ext/synology/overview", "/api/v1/ext/synology/overview?refresh=true"]);
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
  });

  it("abgelehnte Anmeldung: Meldung und Einstellungen, aber kein Knopf zum erneuten Versuchen", async () => {
    const message = "Benutzername oder Passwort stimmt nicht. Prüfe beides in den Einstellungen.";
    answer = () => ok(failed({ state: "auth_failed", message }));
    render(<SynologyPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(problem.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
    expect(problem.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/synology");
    expect(screen.getByText("Anmeldung fehlgeschlagen", { selector: "span" })).toBeInTheDocument();
  });

  it("ohne extensions.manage gibt es keinen Link zu den Einstellungen", async () => {
    permissions.delete("extensions.manage");
    answer = () => ok(failed({ state: "error", message: "Unter dieser Adresse läuft kein Synology DSM." }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("problem")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Einstellungen" })).not.toBeInTheDocument();
  });

  it("ein Ladefehler ist keine leere Seite: Grund und Erneut versuchen, danach die Daten", async () => {
    answer = () => new Response(JSON.stringify({ detail: "Datenbank gesperrt." }), { status: 500 });
    render(<SynologyPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Zustand der Synology ließ sich gerade nicht laden: Datenbank gesperrt.");
    expect(screen.queryByText("Noch nicht eingerichtet")).not.toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();

    answer = () => ok(overview());
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("device")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });

  it("schlägt Aktualisieren fehl, bleiben die alten Daten stehen und der Fehler steht dabei", async () => {
    render(<SynologyPage />);
    await screen.findByTestId("device");
    answer = () => new Response(JSON.stringify({ detail: "Server überlastet." }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Server überlastet.");
    expect(screen.getByTestId("device")).toBeInTheDocument();
    expect(urls[1]).toBe("/api/v1/ext/synology/overview?refresh=true");
  });

  it("zeigt „Lade …“, solange die Antwort fehlt", async () => {
    let release: (r: Response) => void = () => {};
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    render(<SynologyPage />);
    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeDisabled();
    release(ok(overview()));
    expect(await screen.findByTestId("device")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("Tabellen liegen in einem eigenen Streifen zum Scrollen, die Seite wird nicht breiter", async () => {
    render(<SynologyPage />);
    const table = await screen.findByTestId("disks");
    expect(table.parentElement?.className).toContain("overflow-x-auto");
    expect(screen.getByTestId("volumes").parentElement?.className).toContain("overflow-x-auto");
    expect(within(screen.getAllByTestId("disk")[0]).getByText("WDC WD40EFRX-68N32N0").className).toContain("break-words");
  });
});

describe("Braucht Aufmerksamkeit", () => {
  it("nennt kritische Meldungen und Warnungen, Infos nicht", async () => {
    answer = () => ok(overview({ problems: [critical, warning, info] }));
    render(<SynologyPage />);
    const items = within(await screen.findByTestId("attention")).getAllByTestId("attention-item");

    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("Speicherpool 1")).toHaveClass("font-semibold");
    expect(within(items[0]).getByText("Ein Laufwerk ist ausgefallen. Tausche es bald aus.")).toHaveClass("text-white/60");
    expect(within(items[0]).getByText("kritisch")).toHaveClass("text-red-300");
    expect(within(items[1]).getByText("Hyper Backup „Fotos in die Cloud“")).toBeInTheDocument();
    expect(within(items[1]).getByText("Warnung")).toHaveClass("text-amber-300");
    expect(screen.queryByText("DSM 7.2.2-72806 steht bereit.")).not.toBeInTheDocument();
    expect(screen.getByTestId("update")).toBeInTheDocument();
  });

  it("steht über den Geräteangaben", async () => {
    answer = () => ok(overview({ problems: [warning] }));
    render(<SynologyPage />);
    const card = await screen.findByTestId("attention");
    expect(card.compareDocumentPosition(screen.getByTestId("device")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("ohne Meldungen oder nur mit Hinweisen fehlt die Karte", async () => {
    const { unmount } = render(<SynologyPage />);
    await screen.findByTestId("device");
    expect(screen.queryByTestId("attention")).not.toBeInTheDocument();
    expect(screen.queryByText("Braucht Aufmerksamkeit")).not.toBeInTheDocument();
    unmount();

    answer = () => ok(overview({ problems: [info] }));
    render(<SynologyPage />);
    await screen.findByTestId("device");
    expect(screen.queryByTestId("attention")).not.toBeInTheDocument();
  });

  it("färbt die Kopfzeile: kritisch ist ein Problem, gelb ein Hinweis, Info ändert nichts", async () => {
    const volumes = [volume1];
    const base = { volumes, disks: [disk1] };
    answer = () => ok(overview({ ...base, problems: [critical] }));
    const first = render(<SynologyPage />);
    expect(await screen.findByText("Problem", { selector: "span" })).toBeInTheDocument();
    first.unmount();

    answer = () => ok(overview({ ...base, problems: [warning] }));
    const second = render(<SynologyPage />);
    expect(await screen.findByText("Hinweis", { selector: "span" })).toBeInTheDocument();
    second.unmount();

    answer = () => ok(overview({ ...base, problems: [info] }));
    render(<SynologyPage />);
    expect(await screen.findByText("in Ordnung", { selector: "span" })).toBeInTheDocument();
  });
});

describe("Speicherpools", () => {
  it("zeigt Name, RAID, Zustand, Festplatten, Volumes und Größe", async () => {
    answer = () => ok(overview({ pools: [pool1, pool2] }));
    render(<SynologyPage />);
    const rows = await screen.findAllByTestId("pool");

    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Speicherpool 1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Hauptpool")).toBeInTheDocument();
    expect(within(rows[0]).getByText("SHR")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Normal")).toHaveClass("text-emerald-300");
    expect(within(rows[0]).getByText("2")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Volume 1, Volume 2")).toBeInTheDocument();
    expect(within(rows[0]).getByText("3,6 TB")).toBeInTheDocument();

    expect(within(rows[1]).getByText("Beeinträchtigt")).toHaveClass("text-amber-300");
    expect(within(rows[1]).getAllByText("–")).toHaveLength(4); // RAID, Festplatten, Volumes, Größe
  });

  it("steht vor den Volumes und liegt wie die anderen Tabellen in einem Scrollstreifen", async () => {
    answer = () => ok(overview({ pools: [pool1] }));
    render(<SynologyPage />);
    const pools = await screen.findByTestId("pools");
    expect(pools.parentElement?.className).toContain("overflow-x-auto");
    expect(pools.compareDocumentPosition(screen.getByTestId("volumes")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("ohne Pools fehlt die Karte", async () => {
    answer = () => ok(overview({ pools: [] }));
    const { unmount } = render(<SynologyPage />);
    await screen.findByTestId("volumes");
    expect(screen.queryByTestId("pools")).not.toBeInTheDocument();
    expect(screen.queryByText("Speicherpools")).not.toBeInTheDocument();
    unmount();

    render(<SynologyPage />);
    await screen.findByTestId("volumes");
    expect(screen.queryByTestId("pools")).not.toBeInTheDocument();
  });
});

describe("Festplatten-Temperatur", () => {
  it("über der Grenze steht sie gelb und mit Hinweis, sonst unauffällig", async () => {
    answer = () => ok(overview({ disks: [{ ...disk1, temp: 58, temp_warn: true }, disk2] }));
    render(<SynologyPage />);
    const rows = await screen.findAllByTestId("disk");

    const hot = within(rows[0]).getByText("58 °C");
    expect(hot).toHaveClass("text-amber-300");
    expect(hot).toHaveAttribute("title", "über der Grenze");
    expect(hot).toHaveTextContent("58 °C (über der Grenze)");
    const normal = within(rows[1]).getByText("39 °C");
    expect(normal).not.toHaveClass("text-amber-300");
    expect(normal).not.toHaveAttribute("title");
  });
});

describe("Hyper Backup", () => {
  it("zeigt Ergebnis, Problem und die Zeiten der Synology ohne Umrechnung", async () => {
    answer = () => ok(overview({ backups: [task1, task2] }));
    render(<SynologyPage />);
    const rows = await screen.findAllByTestId("backup");

    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Nachtsicherung")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Erfolgreich")).toHaveClass("text-emerald-300");
    expect(within(rows[0]).queryByTestId("backup-problem")).not.toBeInTheDocument();
    expect(within(rows[0]).getByText("16.10.2026, 03:00")).toBeInTheDocument();
    expect(within(rows[0]).getByText("17.10.2026, 03:00")).toBeInTheDocument();

    expect(within(rows[1]).getByText("Fehlgeschlagen")).toHaveClass("text-red-300");
    expect(within(rows[1]).getByTestId("backup-problem")).toHaveTextContent("Der letzte Lauf ist fehlgeschlagen. Prüfe das Ziel.");
    expect(within(rows[1]).getByTestId("backup-problem")).toHaveClass("text-red-300");
    expect(within(rows[1]).getByText("kein Zeitplan")).toBeInTheDocument();
    expect(within(rows[1]).getByText("–")).toBeInTheDocument(); // noch kein Lauf

    expect(screen.getByText("Zeiten in Ortszeit der Synology.")).toBeInTheDocument();
  });

  it("ein Problem mit gelbem Ton steht gelb, eine laufende Sicherung als „Läuft gerade“", async () => {
    const running: BackupTask = { ...task1, running: true, result_label: "Läuft gerade", tone: "neutral" };
    const late: BackupTask = { ...task1, id: "3", name: "Archiv", tone: "warn", overdue: true, problem: "Seit 9 Tagen keine erfolgreiche Sicherung." };
    answer = () => ok(overview({ backups: [running, late] }));
    render(<SynologyPage />);
    const rows = await screen.findAllByTestId("backup");
    expect(within(rows[0]).getByText("Läuft gerade")).toBeInTheDocument();
    expect(within(rows[1]).getByTestId("backup-problem")).toHaveClass("text-amber-300");
  });

  it("steht nach den Festplatten und liegt in einem Scrollstreifen", async () => {
    answer = () => ok(overview({ backups: [task1] }));
    render(<SynologyPage />);
    const backups = await screen.findByTestId("backups");
    expect(backups.parentElement?.className).toContain("overflow-x-auto");
    expect(screen.getByTestId("disks").compareDocumentPosition(backups) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("ohne Aufgaben steht, dass noch keine Sicherung eingerichtet ist", async () => {
    answer = () => ok(overview({ backups: [] }));
    render(<SynologyPage />);
    expect(await screen.findByTestId("no-backups")).toHaveTextContent("In Hyper Backup ist noch keine Sicherung eingerichtet.");
    expect(screen.queryByTestId("backups")).not.toBeInTheDocument();
  });

  it("fehlt Hyper Backup, steht der Hinweis leise da und die Seite bleibt „in Ordnung“", async () => {
    const message = "Hyper Backup ist auf dieser Synology nicht installiert.";
    answer = () => ok(overview({ disks: [disk1], backups: null, backup_note: { kind: "unavailable", message } }));
    render(<SynologyPage />);
    const note = await screen.findByTestId("backup-note");
    expect(note).toHaveTextContent(message);
    expect(note).toHaveClass("text-white/50");
    expect(note).not.toHaveAttribute("role");
    expect(screen.queryByTestId("backups")).not.toBeInTheDocument();
    expect(screen.getByText("in Ordnung", { selector: "span" })).toBeInTheDocument();
  });

  it("gibt es mehr Aufgaben, als Deck zeigt, steht das gelb über der Liste", async () => {
    const message = "Hyper Backup hat 70 Aufgaben, Nodvard Deck zeigt und prüft davon die ersten 64.";
    answer = () => ok(overview({ disks: [disk1], backups: [task1], backup_note: { kind: "partial", message } }));
    render(<SynologyPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByTestId("backup-note")).toHaveClass("text-amber-300");
    expect(screen.getByTestId("backups")).toBeInTheDocument();
  });

  it("lässt sich Hyper Backup nicht lesen, ist das ein Fehlerhinweis und die Seite „unvollständig“", async () => {
    const message = "Die Antwort von Hyper Backup hat nicht das erwartete Format.";
    answer = () => ok(overview({ disks: [disk1], backups: null, backup_note: { kind: "error", message } }));
    render(<SynologyPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByTestId("backup-note")).toHaveClass("text-red-300");
    expect(screen.getByText("unvollständig", { selector: "span" })).toBeInTheDocument();
  });
});

describe("Älteres Backend", () => {
  it("ohne die neuen Felder bleibt die Seite heil und zeigt die neuen Karten nicht", async () => {
    answer = () => ok(legacy());
    render(<SynologyPage />);
    await screen.findByTestId("device");

    expect(screen.getAllByTestId("volume")).toHaveLength(2);
    const disks = screen.getAllByTestId("disk");
    expect(within(disks[0]).getByText("36 °C")).not.toHaveClass("text-amber-300");
    for (const id of ["attention", "pools", "backups", "no-backups", "backup-note"]) expect(screen.queryByTestId(id)).not.toBeInTheDocument();
    expect(screen.queryByText("Hyper Backup")).not.toBeInTheDocument();
    expect(screen.queryByText("Speicherpools")).not.toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });
});

describe("Hilfen", () => {
  it.each([
    [0, "0 B"], [512, "512 B"], [1536, "1,5 KB"], [412 * GIB, "412 GB"], [Math.round(3.6 * TIB), "3,6 TB"], [5 * TIB, "5,0 TB"],
  ])("sizeText %s -> %s", (count, expected) => {
    expect(sizeText(count)).toBe(expected);
  });

  it.each([
    [0, "< 1 Min."], [60, "1 Min."], [3600, "1 Std."], [4320, "1 Std. 12 Min."], [86400, "1 Tag"], [90000, "1 Tag 1 Std."], [3 * 86400 + 4 * 3600, "3 Tage 4 Std."],
  ])("durationText %s -> %s", (seconds, expected) => {
    expect(durationText(seconds)).toBe(expected);
  });

  it("percentText rundet kaufmännisch", () => {
    expect([percentText(0), percentText(11.4), percentText(11.5), percentText(100)]).toEqual(["0 %", "11 %", "12 %", "100 %"]);
  });

  it("usageText nennt die Belegung oder „unbekannt“", () => {
    expect(usageText(volume1)).toBe("412 GB von 3,6 TB (11 %)");
    expect(usageText({ ...volume1, percent: null })).toBe("412 GB von 3,6 TB");
    expect(usageText({ ...volume1, total: 0 })).toBe("unbekannt");
    expect(usageText({ ...volume1, used: null })).toBe("unbekannt");
  });

  it.each([
    ["2026-10-17T03:00", "17.10.2026, 03:00"],
    ["2026-01-05T00:30", "05.01.2026, 00:30"],
    ["2026-12-31T23:59:42", "31.12.2026, 23:59"],
    ["gestern Nacht", "gestern Nacht"],
  ])("dsmTime %s -> %s", (value, expected) => {
    expect(dsmTime(value)).toBe(expected);
  });

  it("dsmTime ohne Wert ist leer", () => {
    expect([dsmTime(null), dsmTime("")]).toEqual([null, null]);
  });

  it("toneOf macht aus den Tönen des Servers die des UI-Kits", () => {
    expect(["good", "warn", "danger", "neutral", "etwas", null].map(toneOf)).toEqual(["good", "warn", "bad", "neutral", "neutral", "neutral"]);
  });
});
