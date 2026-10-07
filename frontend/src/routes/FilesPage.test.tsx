import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../components/GlobalDialogs";
import { useAuthStore } from "../state/auth";
import { useDialogsStore } from "../state/dialogs";
import { FilesPage } from "./FilesPage";

// Transfer-Fortschritt kommt im Betrieb ueber den WS-Hub (`runs.<id>`) -- hier ein
// Stellvertreter, ueber den ein Test das "succeeded"/"failed" des Laufs selbst schickt.
const wsHandlers = new Map<string, (payload: unknown) => void>();
vi.mock("../lib/ws", () => ({
  useWsSubscription: (channel: string | null | undefined, handler: (payload: unknown) => void) => {
    if (channel) wsHandlers.set(channel, handler);
  },
}));

/**
 * Echtes Drag&Drop im Browser laesst sich in jsdom nicht 1:1 simulieren (kein
 * natives `DataTransfer`) -- diese Tests bilden trotzdem das ECHTE Nutzerverhalten
 * ab: ein `dataTransfer`-Objekt, das ueber `dragStart` gesetzt und in `drop`
 * ausgelesen wird, genau wie der Browser es waehrend einer echten Ziehgeste tut,
 * nur ohne den Browser selbst. Der eigentliche visuelle/geraeteseitige Beweis lief
 * zusaetzlich live im echten Browser.
 */
function makeDataTransfer() {
  const store = new Map<string, string>();
  const types: string[] = [];
  return {
    effectAllowed: "none",
    dropEffect: "none",
    get types() {
      return types;
    },
    setData(type: string, value: string) {
      store.set(type, value);
      if (!types.includes(type)) types.push(type);
    },
    getData(type: string) {
      return store.get(type) ?? "";
    },
  };
}

const SOURCES = [
  { source_id: "ssh-sftp:pve2", label: "SSH (SFTP): pve2", icon: "server", caps: { write: true, rename: true, remove: true, mkdir: true, search: false, range_read: true } },
  { source_id: "nextcloud", label: "Nextcloud", icon: "cloud", caps: { write: true, rename: true, remove: true, mkdir: true, search: true, range_read: true } },
];

const POWER_ROOT = [
  { name: "notes.txt", path: "/notes.txt", is_dir: false, size: 42, modified_at: null, mime: "text/plain" },
  { name: "docs", path: "/docs", is_dir: true, size: null, modified_at: null, mime: null },
];

// Ordnerinhalt der pve2-Quelle -- Tests koennen ihn ersetzen (Links etc.).
let powerRoot: unknown[] = POWER_ROOT;

let mkdirCalls: unknown[];
let renameCalls: unknown[];
let removeCalls: unknown[];

function mockFetch(onTransfer?: (body: unknown) => void) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.endsWith("/api/v1/files/sources") && method === "GET") {
      return new Response(JSON.stringify(SOURCES), { status: 200 });
    }
    if (url.includes("/api/v1/files/ssh-sftp%3Apve2/list") && method === "GET") {
      return new Response(JSON.stringify({ items: powerRoot }), { status: 200 });
    }
    if (url.includes("/api/v1/files/nextcloud/list") && method === "GET") {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    if (url.endsWith("/api/v1/files/transfer") && method === "POST") {
      const body = JSON.parse(init!.body as string);
      onTransfer?.(body);
      return new Response(JSON.stringify({ run_id: "run-1" }), { status: 200 });
    }
    if (url.endsWith("/api/v1/files/ssh-sftp%3Apve2/mkdir") && method === "POST") {
      mkdirCalls.push(JSON.parse(init!.body as string));
      return new Response(JSON.stringify({}), { status: 200 });
    }
    if (url.endsWith("/api/v1/files/ssh-sftp%3Apve2/rename") && method === "POST") {
      renameCalls.push(JSON.parse(init!.body as string));
      return new Response(JSON.stringify({}), { status: 200 });
    }
    if (url.endsWith("/api/v1/files/ssh-sftp%3Apve2/remove") && method === "POST") {
      removeCalls.push(JSON.parse(init!.body as string));
      return new Response(JSON.stringify({}), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
  useDialogsStore.setState({ confirmPending: null, promptPending: null });
  mkdirCalls = [];
  renameCalls = [];
  removeCalls = [];
  powerRoot = POWER_ROOT;
  wsHandlers.clear();
});

describe("FilesPage Drag&Drop", () => {
  it("zieht eine Datei auf eine andere Quelle: Zielordner-Dialog oeffnet mit dieser Quelle, Bestaetigen kopiert", async () => {
    let transferBody: unknown = null;
    vi.stubGlobal("fetch", mockFetch((body) => (transferBody = body)));
    render(<FilesPage />);

    const fileRow = (await screen.findByText("⠿ notes.txt")).closest("tr")!;
    const nextcloudEntry = await screen.findByRole("button", { name: "Nextcloud" });

    const dataTransfer = makeDataTransfer();
    fireEvent.dragStart(fileRow, { dataTransfer });
    fireEvent.drop(nextcloudEntry, { dataTransfer });

    // Ordner-Auswahl: nicht mehr blind in die Wurzel -- erst waehlen.
    const dialog = await screen.findByRole("dialog");
    expect(transferBody).toBeNull();
    const sourceList = within(dialog).getByRole("list", { name: "Zielquelle" });
    expect(within(sourceList).getByRole("button", { name: "Nextcloud" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher kopieren" }));

    await waitFor(() => expect(transferBody).not.toBeNull());
    expect(transferBody).toEqual({
      from: { source: "ssh-sftp:pve2", path: "/notes.txt" },
      to: { source: "nextcloud", path: "/notes.txt" },
    });
  });

  it("zieht eine Datei in einen Ordner derselben Quelle", async () => {
    let transferBody: unknown = null;
    vi.stubGlobal("fetch", mockFetch((body) => (transferBody = body)));
    render(<FilesPage />);

    const fileRow = (await screen.findByText("⠿ notes.txt")).closest("tr")!;
    const folderRow = (await screen.findByText("📁 docs")).closest("tr")!;

    const dataTransfer = makeDataTransfer();
    fireEvent.dragStart(fileRow, { dataTransfer });
    fireEvent.drop(folderRow, { dataTransfer });

    await waitFor(() => expect(transferBody).not.toBeNull());
    expect(transferBody).toEqual({
      from: { source: "ssh-sftp:pve2", path: "/notes.txt" },
      to: { source: "ssh-sftp:pve2", path: "/docs/notes.txt" },
    });
  });

  it("Ordner sind nicht ziehbar (draggable=false), nur Dateien", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const folderRow = (await screen.findByText("📁 docs")).closest("tr")!;
    const fileRow = (await screen.findByText("⠿ notes.txt")).closest("tr")!;

    expect(folderRow).toHaveAttribute("draggable", "false");
    expect(fileRow).toHaveAttribute("draggable", "true");
  });

  it("ueberspringt den Transfer, wenn Ziel und Quelle identisch sind, statt sinnlos zu kopieren", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<FilesPage />);

    const fileRow = (await screen.findByText("⠿ notes.txt")).closest("tr")!;
    // "SSH (SFTP): pve2" erscheint zweimal (Seitenleiste UND Breadcrumb, wenn diese
    // Quelle aktiv ist) -- die Seitenleiste ist im DOM immer die erste.
    const [powerEntry] = await screen.findAllByRole("button", { name: "SSH (SFTP): pve2" });

    const dataTransfer = makeDataTransfer();
    fireEvent.dragStart(fileRow, { dataTransfer });
    fireEvent.drop(powerEntry, { dataTransfer });
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher kopieren" }));

    expect(await screen.findByText(/Ziel ist identisch mit der Quelle/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/files/transfer"), expect.anything());
  });
});

/**
 * Umbenennen/Löschen/Neuer-Ordner gingen bisher ueber `window.prompt()`/
 * `confirm()` (siehe FilesPage.tsx-Modul-Docstring) -- jetzt echte Radix-Dialoge
 * (state/dialogs.ts). `GlobalDialogs` wird hier bewusst MIT gerendert, wie in der
 * echten App via AppShell.tsx -- FilesPage zeichnet selbst kein Dialog-UI mehr.
 */
describe("FilesPage Handy-Layout", () => {
  it("Quellen stehen auf schmalen Bildschirmen über der Liste, die Tabelle scrollt in sich", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    await screen.findByText("⠿ notes.txt");
    const sidebar = screen.getByRole("heading", { name: "Dateien" }).parentElement!;
    expect(sidebar).toHaveClass("w-full", "lg:w-48");
    expect(sidebar).not.toHaveClass("w-48");
    expect(sidebar.parentElement).toHaveClass("flex-col", "lg:flex-row");
    expect(screen.getByRole("table").parentElement).toHaveClass("overflow-x-auto");
  });
});

describe("FilesPage Dialoge (Umbenennen/Löschen/Neuer-Ordner)", () => {
  it("bricht das Löschen ab, wenn der Bestaetigungsdialog abgebrochen wird", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(
      <>
        <FilesPage />
        <GlobalDialogs />
      </>,
    );

    const deleteButtons = await screen.findAllByRole("button", { name: "Löschen" });
    fireEvent.click(deleteButtons[0]); // notes.txt ist die erste Zeile

    await screen.findByText("'notes.txt' wirklich löschen?");
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));

    await waitFor(() => expect(screen.queryByText("'notes.txt' wirklich löschen?")).not.toBeInTheDocument());
    expect(removeCalls).toHaveLength(0);
  });

  it("loescht, sobald im echten Dialog bestaetigt wird", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(
      <>
        <FilesPage />
        <GlobalDialogs />
      </>,
    );

    const deleteButtons = await screen.findAllByRole("button", { name: "Löschen" });
    fireEvent.click(deleteButtons[0]);

    await screen.findByText("'notes.txt' wirklich löschen?");
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Löschen" }));

    await waitFor(() => expect(removeCalls).toEqual([{ path: "/notes.txt", recursive: false }]));
  });

  it("legt einen Ordner an, mit dem im echten Prompt-Dialog eingegebenen Namen", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(
      <>
        <FilesPage />
        <GlobalDialogs />
      </>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "+ Ordner" }));
    const dialog = within(await screen.findByRole("dialog"));
    await dialog.findByText("Name des neuen Ordners:");
    fireEvent.change(dialog.getByRole("textbox"), { target: { value: "neuer-ordner" } });
    fireEvent.click(dialog.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(mkdirCalls).toEqual([{ path: "/neuer-ordner" }]));
  });

  it("bricht Umbenennen ab, wenn der Prompt-Dialog leer bestaetigt wird", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(
      <>
        <FilesPage />
        <GlobalDialogs />
      </>,
    );

    const renameButtons = await screen.findAllByRole("button", { name: "Umbenennen" });
    fireEvent.click(renameButtons[0]);

    const dialog = within(await screen.findByRole("dialog"));
    const input = dialog.getByRole("textbox");
    expect(input).toHaveValue("notes.txt"); // Startwert ist der aktuelle Name
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(dialog.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(renameCalls).toHaveLength(0);
  });
});

/**
 * Upload laeuft ueber XMLHttpRequest (nicht fetch(), siehe FilesPage.tsx-Docstring
 * bei uploadWithProgress -- fetch() kann Request-Upload-Fortschritt nicht melden).
 * jsdoms XMLHttpRequest fuehrt keine echten Netzwerk-Requests aus -- hier durch eine
 * minimale Fake-Implementierung ersetzt, die `open`/`send` aufzeichnet und
 * Fortschritts-/Abschluss-Events manuell ausloest, wie ein echter Browser es waehrend
 * eines echten Uploads tut.
 */
describe("FilesPage Upload-Fortschritt", () => {
  it("zeigt den Fortschrittsbalken waehrend des Uploads und blendet ihn danach aus", async () => {
    vi.stubGlobal("fetch", mockFetch());

    let capturedXhr: FakeXhr | null = null;
    class FakeXhr {
      upload = { onprogress: null as ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      status = 200;
      openedUrl = "";
      open(_method: string, url: string) {
        this.openedUrl = url;
      }
      setRequestHeader() {
        /* no-op im Fake */
      }
      send() {
        capturedXhr = this;
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr as unknown as typeof XMLHttpRequest);

    render(<FilesPage />);
    const file = new File(["hallo welt"], "notiz.txt", { type: "text/plain" });
    await screen.findByRole("button", { name: "Hochladen" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(capturedXhr).not.toBeNull());
    expect(capturedXhr!.openedUrl).toContain("/upload?path=");

    act(() => capturedXhr!.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 }));
    await screen.findByText(/notiz\.txt.*wird hochgeladen … 50%/);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");

    act(() => capturedXhr!.upload.onprogress?.({ lengthComputable: true, loaded: 10, total: 10 }));
    await screen.findByText(/wird hochgeladen … 100%/);

    act(() => capturedXhr!.onload?.());
    await waitFor(() => expect(screen.queryByRole("progressbar")).not.toBeInTheDocument());
    await screen.findByText("'notiz.txt' hochgeladen.");
  });
});

describe("FilesPage Zielordner-Dialog", () => {
  async function openPickerFor(name: string) {
    const row = (await screen.findByText(`⠿ ${name}`)).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Kopieren nach …" }));
    return screen.findByRole("dialog");
  }

  it("Kopieren nach …: Quelle waehlen, in einen Unterordner navigieren, dorthin kopieren", async () => {
    let transferBody: unknown = null;
    vi.stubGlobal("fetch", mockFetch((body) => (transferBody = body)));
    render(<FilesPage />);

    const dialog = await openPickerFor("notes.txt");
    fireEvent.click(await within(dialog).findByRole("button", { name: "📁 docs" }));
    await waitFor(() => expect(within(dialog).getByText(/Ziel: SSH \(SFTP\): pve2 · \/docs\/notes\.txt/)).toBeInTheDocument());
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher kopieren" }));

    await waitFor(() => expect(transferBody).toEqual({
      from: { source: "ssh-sftp:pve2", path: "/notes.txt" },
      to: { source: "ssh-sftp:pve2", path: "/docs/notes.txt" },
    }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Verschieben loescht das Original erst NACH erfolgreichem Transfer", async () => {
    let transferBody: unknown = null;
    vi.stubGlobal("fetch", mockFetch((body) => (transferBody = body)));
    render(<FilesPage />);

    const dialog = await openPickerFor("notes.txt");
    fireEvent.click(within(dialog).getByRole("button", { name: "Nextcloud" }));
    fireEvent.click(within(dialog).getByLabelText("Verschieben (Original danach löschen)"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher verschieben" }));

    await waitFor(() => expect(transferBody).toEqual({
      from: { source: "ssh-sftp:pve2", path: "/notes.txt" },
      to: { source: "nextcloud", path: "/notes.txt" },
    }));
    expect(removeCalls).toEqual([]);

    await waitFor(() => expect(wsHandlers.has("runs.run-1")).toBe(true));
    act(() => wsHandlers.get("runs.run-1")!({ status: "succeeded", bytes: 42 }));
    await waitFor(() => expect(removeCalls).toEqual([{ path: "/notes.txt", recursive: false }]));
    expect(await screen.findByText(/'notes.txt' verschoben/)).toBeInTheDocument();
  });

  it("Verschieben loescht das Original nicht, wenn weniger Bytes ankamen als die Quelle hat", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const dialog = await openPickerFor("notes.txt");
    fireEvent.click(within(dialog).getByRole("button", { name: "Nextcloud" }));
    fireEvent.click(within(dialog).getByLabelText("Verschieben (Original danach löschen)"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher verschieben" }));

    await waitFor(() => expect(wsHandlers.has("runs.run-1")).toBe(true));
    act(() => wsHandlers.get("runs.run-1")!({ status: "succeeded", bytes: 0 }));
    expect(await screen.findByText(/das Original bleibt/)).toBeInTheDocument();
    expect(removeCalls).toEqual([]);
  });

  it("Verschieben loescht das Original nicht, wenn die Groesse der Quelle unbekannt war", async () => {
    powerRoot = [{ name: "notes.txt", path: "/notes.txt", is_dir: false, size: null, modified_at: null, mime: null }];
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const dialog = await openPickerFor("notes.txt");
    fireEvent.click(within(dialog).getByRole("button", { name: "Nextcloud" }));
    fireEvent.click(within(dialog).getByLabelText("Verschieben (Original danach löschen)"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher verschieben" }));

    await waitFor(() => expect(wsHandlers.has("runs.run-1")).toBe(true));
    act(() => wsHandlers.get("runs.run-1")!({ status: "succeeded", bytes: 42 }));
    expect(await screen.findByText(/Größe der Quelle war nicht bekannt/)).toBeInTheDocument();
    expect(removeCalls).toEqual([]);
  });

  it("Verschieben ohne Erfolg laesst das Original stehen", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const dialog = await openPickerFor("notes.txt");
    fireEvent.click(within(dialog).getByRole("button", { name: "Nextcloud" }));
    fireEvent.click(within(dialog).getByLabelText("Verschieben (Original danach löschen)"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Hierher verschieben" }));

    await waitFor(() => expect(wsHandlers.has("runs.run-1")).toBe(true));
    act(() => wsHandlers.get("runs.run-1")!({ status: "failed", error: "Kein Platz" }));
    expect(await screen.findByText(/Transfer fehlgeschlagen: Kein Platz/)).toBeInTheDocument();
    expect(removeCalls).toEqual([]);
  });

  it("legt im Dialog einen neuen Zielordner an und wechselt hinein", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const dialog = await openPickerFor("notes.txt");
    fireEvent.click(within(dialog).getByRole("button", { name: "+ Neuer Ordner hier" }));
    fireEvent.change(within(dialog).getByLabelText("Name des neuen Ordners"), { target: { value: "Archiv" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Anlegen" }));

    await waitFor(() => expect(mkdirCalls).toEqual([{ path: "/Archiv" }]));
    await waitFor(() => expect(within(dialog).getByText(/· \/Archiv\/notes\.txt/)).toBeInTheDocument());
  });
});

describe("FilesPage Links", () => {
  beforeEach(() => {
    powerRoot = [
      ...POWER_ROOT,
      { name: "current", path: "/current", is_dir: true, size: null, modified_at: null, mime: null, metadata: { symlink: true } },
      { name: "latest.log", path: "/latest.log", is_dir: false, size: 10, modified_at: null, mime: null, metadata: { symlink: true } },
      { name: "kaputt", path: "/kaputt", is_dir: false, size: null, modified_at: null, mime: null, metadata: { symlink: true, broken: true } },
    ];
  });

  it("zeigt bei Links ein Link-Symbol, bei normalen Einträgen keines", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const folderLinkRow = (await screen.findByText("📁 current")).closest("tr")!;
    const fileLinkRow = screen.getByText("⠿ latest.log").closest("tr")!;
    const plainRow = screen.getByText("⠿ notes.txt").closest("tr")!;

    expect(within(folderLinkRow).getByRole("img", { name: "Link" })).toBeInTheDocument();
    expect(within(fileLinkRow).getByRole("img", { name: "Link" })).toBeInTheDocument();
    expect(within(plainRow).queryByRole("img", { name: "Link" })).toBeNull();
    // Ein Link auf einen Ordner bleibt ein oeffnbarer Ordner.
    expect(within(folderLinkRow).getByRole("button", { name: "📁 current" })).toBeInTheDocument();
    // Ein funktionierender Link auf eine Datei ist wie eine Datei ladbar und ziehbar.
    expect(within(fileLinkRow).getByRole("button", { name: "Laden" })).toBeInTheDocument();
    expect(fileLinkRow).toHaveAttribute("draggable", "true");
    expect(within(fileLinkRow).queryByText("Link ins Leere")).toBeNull();
  });

  it("markiert einen Link ins Leere und bietet dafür weder Laden noch Kopieren noch Ziehen an", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<FilesPage />);

    const brokenRow = (await screen.findByText("Link ins Leere")).closest("tr")!;

    expect(within(brokenRow).getByText("kaputt")).toBeInTheDocument();
    expect(within(brokenRow).getByRole("img", { name: "Link" })).toBeInTheDocument();
    expect(within(brokenRow).queryByRole("button", { name: "Laden" })).toBeNull();
    expect(within(brokenRow).queryByRole("button", { name: "Kopieren nach …" })).toBeNull();
    expect(brokenRow).toHaveAttribute("draggable", "false");
    // Wegräumen (Umbenennen/Löschen) bleibt möglich.
    expect(within(brokenRow).getByRole("button", { name: "Umbenennen" })).toBeInTheDocument();
    expect(within(brokenRow).getByRole("button", { name: "Löschen" })).toBeInTheDocument();
  });

  it("löscht einen Link auf einen Ordner nur als Link (nicht rekursiv), einen echten Ordner rekursiv", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(
      <>
        <FilesPage />
        <GlobalDialogs />
      </>,
    );

    async function loescheZeile(name: string) {
      const row = (await screen.findByText(name)).closest("tr")!;
      fireEvent.click(within(row).getByRole("button", { name: "Löschen" }));
      const dialog = within(await screen.findByRole("dialog"));
      fireEvent.click(dialog.getByRole("button", { name: "Löschen" }));
    }

    await loescheZeile("📁 current"); // Link auf einen Ordner
    await waitFor(() => expect(removeCalls).toEqual([{ path: "/current", recursive: false }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await loescheZeile("📁 docs"); // echter Ordner
    await waitFor(() => expect(removeCalls).toHaveLength(2));
    expect(removeCalls[1]).toEqual({ path: "/docs", recursive: true });
  });

  it("benennt einen Link ins Leere um und lädt danach die Liste neu", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <>
        <FilesPage />
        <GlobalDialogs />
      </>,
    );

    const brokenRow = (await screen.findByText("Link ins Leere")).closest("tr")!;
    const listCallsBefore = fetchMock.mock.calls.filter(([u]) => String(u).includes("/list")).length;
    fireEvent.click(within(brokenRow).getByRole("button", { name: "Umbenennen" }));

    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.change(dialog.getByRole("textbox"), { target: { value: "neuer-name" } });
    fireEvent.click(dialog.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(renameCalls).toEqual([{ src: "/kaputt", dst: "/neuer-name" }]));
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/list")).length).toBeGreaterThan(listCallsBefore),
    );
  });

  it("startet beim Ziehen eines Links ins Leere keinen Transfer", async () => {
    let transferBody: unknown = null;
    vi.stubGlobal("fetch", mockFetch((body) => (transferBody = body)));
    render(<FilesPage />);

    const brokenRow = (await screen.findByText("Link ins Leere")).closest("tr")!;
    const folderRow = screen.getByText("📁 docs").closest("tr")!;

    const dataTransfer = makeDataTransfer();
    fireEvent.dragStart(brokenRow, { dataTransfer });
    fireEvent.drop(folderRow, { dataTransfer });

    expect(dataTransfer.types).toEqual([]);
    expect(transferBody).toBeNull();
  });
});

describe("FilesPage ohne Quellen", () => {
  function renderEmpty() {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/v1/files/sources")) return new Response("[]", { status: 200 });
      throw new Error(`Unerwarteter Fetch: ${String(input)}`);
    }));
    render(<MemoryRouter><FilesPage /></MemoryRouter>);
  }
  const user = (permissions: string[]) =>
    useAuthStore.setState({ user: { id: "u1", username: "a", display_name: null, email: null, is_owner: false, locale: "de", permissions } });

  it("sagt, was zu tun ist, mit Knöpfen zu Modulen und Servern (je nach Recht)", async () => {
    user(["extensions.manage", "hosts.write"]);
    renderEmpty();
    const empty = await screen.findByTestId("files-no-sources");
    expect(empty.textContent).toContain("Noch keine Dateiquellen");
    expect(within(empty).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
    expect(within(empty).getByRole("link", { name: "Server & Zugänge" })).toHaveAttribute("href", "/settings/hosts");
    expect(document.body.textContent).not.toContain("FileSource");
  });

  it("ohne Rechte nur Text", async () => {
    user(["files.read"]);
    renderEmpty();
    const empty = await screen.findByTestId("files-no-sources");
    expect(empty.textContent).toContain("Administrator");
    expect(within(empty).queryByRole("link")).toBeNull();
  });
});

/**
 * Abgelaufene Anmeldung (Tab lange im Hintergrund, Rechner im Ruhezustand): Laden und
 * Hochladen erneuern wie `api.get` einmal still und versuchen es noch einmal -- statt mit
 * "Nicht authentifiziert" zu scheitern. Das Token "tok" ist hier abgelaufen, erst "tok-neu" gilt.
 */
describe("FilesPage bei abgelaufener Anmeldung", () => {
  const UNAVAILABLE = "Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.";
  const USER = { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] };
  const SOURCE = "ssh-sftp%3Apve2";

  let refreshCalls: number;
  let downloadAuth: (string | null)[];
  let clickedDownloads: string[];

  /** Antwortet auf alles, was die Seite beim Start braucht, und auf /auth/refresh nach `refresh`. */
  function stubBackend(opts: { refresh: () => Response; download?: (auth: string | null) => Response }) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const auth = new Headers(init?.headers).get("Authorization");
      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        return opts.refresh();
      }
      if (url.endsWith("/api/v1/files/sources")) return new Response(JSON.stringify(SOURCES), { status: 200 });
      if (url.includes(`/api/v1/files/${SOURCE}/list`)) return new Response(JSON.stringify({ items: POWER_ROOT }), { status: 200 });
      if (url.includes(`/api/v1/files/${SOURCE}/download`)) {
        downloadAuth.push(auth);
        if (opts.download) return opts.download(auth);
        return auth === "Bearer tok-neu"
          ? new Response("inhalt", { status: 200 })
          : new Response(JSON.stringify({ detail: "Nicht authentifiziert." }), { status: 401 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
    }));
  }

  const refreshOk = () => new Response(JSON.stringify({ access_token: "tok-neu", user: USER }), { status: 200 });

  beforeEach(() => {
    refreshCalls = 0;
    downloadAuth = [];
    clickedDownloads = [];
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
    // jsdom kennt keine Blob-Adressen.
    const urlStatics = URL as unknown as Record<string, unknown>;
    urlStatics.createObjectURL = () => "blob:test";
    urlStatics.revokeObjectURL = () => {};
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clickedDownloads.push(this.download);
    });
  });

  afterEach(() => {
    const urlStatics = URL as unknown as Record<string, unknown>;
    delete urlStatics.createObjectURL;
    delete urlStatics.revokeObjectURL;
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function clickDownload() {
    const row = (await screen.findByText("⠿ notes.txt")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Laden" }));
  }

  describe("Laden", () => {
    it("erste Antwort 401: erneuert einmal und lädt mit dem neuen Token", async () => {
      stubBackend({ refresh: refreshOk });
      render(<FilesPage />);

      await clickDownload();

      await waitFor(() => expect(clickedDownloads).toEqual(["notes.txt"]));
      expect(refreshCalls).toBe(1);
      expect(downloadAuth).toEqual(["Bearer tok", "Bearer tok-neu"]);
      expect(screen.queryByText(/Fehler/)).toBeNull();
    });

    it("Erneuerung abgelehnt: verständliche Meldung statt HTTP 401, kein dritter Versuch", async () => {
      stubBackend({ refresh: () => new Response(JSON.stringify({ detail: "Abgelaufen" }), { status: 401 }) });
      render(<FilesPage />);

      await clickDownload();

      expect(await screen.findByText("Fehler: Nicht authentifiziert.")).toBeInTheDocument();
      expect(refreshCalls).toBe(1);
      expect(downloadAuth).toEqual(["Bearer tok"]);
      expect(clickedDownloads).toEqual([]);
    });

    it("Server beim Erneuern nicht erreichbar: klare Meldung, kein dritter Versuch, weiter angemeldet", async () => {
      stubBackend({ refresh: () => new Response("Bad Gateway", { status: 502 }) });
      render(<FilesPage />);

      await clickDownload();

      expect(await screen.findByText(`Fehler: ${UNAVAILABLE}`)).toBeInTheDocument();
      expect(downloadAuth).toEqual(["Bearer tok"]);
      expect(useAuthStore.getState().status).toBe("authenticated");
    });

    it("anderer Fehler: Text des Servers statt HTTP <Status>, ohne Erneuerung", async () => {
      stubBackend({ refresh: refreshOk, download: () => new Response(JSON.stringify({ detail: "Die Quelle antwortet nicht." }), { status: 502 }) });
      render(<FilesPage />);

      await clickDownload();

      expect(await screen.findByText("Fehler: Die Quelle antwortet nicht.")).toBeInTheDocument();
      expect(refreshCalls).toBe(0);
      expect(downloadAuth).toEqual(["Bearer tok"]);
    });
  });

  describe("Hochladen", () => {
    class FakeXhr {
      static instances: FakeXhr[] = [];
      upload = { onprogress: null as ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      status = 0;
      responseText = "";
      headers: Record<string, string> = {};
      open() {
        /* no-op im Fake */
      }
      setRequestHeader(name: string, value: string) {
        this.headers[name] = value;
      }
      send() {
        FakeXhr.instances.push(this);
      }
      /** Der Server antwortet. */
      respond(status: number, body: unknown = "") {
        this.status = status;
        this.responseText = typeof body === "string" ? body : JSON.stringify(body);
        act(() => this.onload?.());
      }
      progress(loaded: number, total: number) {
        act(() => this.upload.onprogress?.({ lengthComputable: true, loaded, total }));
      }
    }

    async function startUpload() {
      render(<FilesPage />);
      await screen.findByRole("button", { name: "Hochladen" });
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      fireEvent.change(input, { target: { files: [new File(["hallo welt"], "notiz.txt", { type: "text/plain" })] } });
      await waitFor(() => expect(FakeXhr.instances).toHaveLength(1));
    }

    const expired = { detail: "Nicht authentifiziert." };

    beforeEach(() => {
      FakeXhr.instances = [];
      vi.stubGlobal("XMLHttpRequest", FakeXhr as unknown as typeof XMLHttpRequest);
    });

    it("erste Antwort 401: erneuert einmal, wiederholt mit dem neuen Token, Fortschritt beginnt neu", async () => {
      stubBackend({ refresh: refreshOk });
      await startUpload();
      const [first] = FakeXhr.instances;
      expect(first.headers.Authorization).toBe("Bearer tok");
      first.progress(5, 10);
      await screen.findByText(/wird hochgeladen … 50%/);

      first.respond(401, expired);

      await waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
      const second = FakeXhr.instances[1];
      expect(second.headers.Authorization).toBe("Bearer tok-neu");
      expect(refreshCalls).toBe(1);
      await screen.findByText(/wird hochgeladen … 0%/);
      second.progress(10, 10);
      await screen.findByText(/wird hochgeladen … 100%/);
      second.respond(200);

      await screen.findByText("'notiz.txt' hochgeladen.");
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });

    it("Erneuerung abgelehnt: verständliche Meldung statt HTTP 401, kein zweiter Versuch", async () => {
      stubBackend({ refresh: () => new Response(JSON.stringify({ detail: "Abgelaufen" }), { status: 401 }) });
      await startUpload();

      FakeXhr.instances[0].respond(401, expired);

      expect(await screen.findByText("Fehler: Nicht authentifiziert.")).toBeInTheDocument();
      expect(FakeXhr.instances).toHaveLength(1);
      expect(refreshCalls).toBe(1);
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });

    it("Server beim Erneuern nicht erreichbar: klare Meldung, kein zweiter Versuch", async () => {
      stubBackend({ refresh: () => new Response("Bad Gateway", { status: 502 }) });
      await startUpload();

      FakeXhr.instances[0].respond(401, expired);

      expect(await screen.findByText(`Fehler: ${UNAVAILABLE}`)).toBeInTheDocument();
      expect(FakeXhr.instances).toHaveLength(1);
    });

    it("auch der zweite Versuch bekommt 401: Meldung, kein dritter Versuch", async () => {
      stubBackend({ refresh: refreshOk });
      await startUpload();

      FakeXhr.instances[0].respond(401, expired);
      await waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
      FakeXhr.instances[1].respond(401, expired);

      expect(await screen.findByText("Fehler: Nicht authentifiziert.")).toBeInTheDocument();
      expect(FakeXhr.instances).toHaveLength(2);
      expect(refreshCalls).toBe(1);
    });

    it("anderer Fehler: Text des Servers, weder Erneuerung noch zweiter Versuch", async () => {
      stubBackend({ refresh: refreshOk });
      await startUpload();

      FakeXhr.instances[0].respond(413, { detail: "Die Datei ist zu groß." });

      expect(await screen.findByText("Fehler: Die Datei ist zu groß.")).toBeInTheDocument();
      expect(FakeXhr.instances).toHaveLength(1);
      expect(refreshCalls).toBe(0);
    });
  });
});
