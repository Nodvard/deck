import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WatchTemplates } from "./WatchTemplates";

const TEMPLATES = {
  suggestions: [
    { path: "/srv/samba/familie", kinds: ["samba", "nfs"], hosts: ["nas", "pi"], covered_by: null },
    { path: "/home/familie", kinds: ["samba"], hosts: ["nas"], covered_by: "/home", excluded_by: null },
    { path: "/srv/medien/upload", kinds: ["nfs"], hosts: ["nas"], covered_by: null, excluded_by: "/srv/medien" },
  ],
  errors: ["aus: nicht erreichbar, dort wurde nicht gesucht."],
  watch_paths: ["/home"],
};

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/ext/shield/defender/watch-templates") && method === "GET") {
      return new Response(JSON.stringify(TEMPLATES), { status: 200 });
    }
    if (url.endsWith("/ext/shield/defender/watch-paths") && method === "POST") {
      return new Response(JSON.stringify({ added: true, covered_by: null, watch_paths: ["/home", "/srv/samba/familie"] }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

beforeEach(() => {
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("Wächter-Vorlagen", () => {
  it("sucht erst auf Klick, zeigt Art, Server und was schon überwacht wird", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<WatchTemplates />);
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Auf den Servern suchen" }));
    expect(await screen.findByText("/srv/samba/familie")).toBeInTheDocument();
    expect(screen.getByText("Samba-Freigabe, NFS-Freigabe · auf nas, pi")).toBeInTheDocument();
    expect(screen.getByText("wird überwacht (über /home)")).toBeInTheDocument();
    expect(screen.getByText("ausgenommen (über /srv/medien) – dort prüft der Wächter nicht")).toBeInTheDocument();
    expect(screen.getByText("aus: nicht erreichbar, dort wurde nicht gesucht.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Zum Wächter hinzufügen" })).toHaveLength(1);
  });

  it("fügt einen Ordner mit einem Klick hinzu", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<WatchTemplates />);
    fireEvent.click(screen.getByRole("button", { name: "Auf den Servern suchen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Zum Wächter hinzufügen" }));

    expect(await screen.findByRole("status")).toHaveTextContent("/srv/samba/familie ist jetzt im Echtzeit-Wächter. Neue und hineinkopierte Dateien");
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ path: "/srv/samba/familie" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Zum Wächter hinzufügen" })).toBeNull());
    expect(screen.getByText("wird überwacht")).toBeInTheDocument();
  });

  it("ohne das Recht, Einstellungen zu ändern, gibt es keinen Knopf zum Hinzufügen", async () => {
    window.__nodvardDeck.hasPermission = vi.fn((perm: string) => perm !== "settings.write");
    vi.stubGlobal("fetch", mockFetch());
    render(<WatchTemplates />);
    fireEvent.click(screen.getByRole("button", { name: "Auf den Servern suchen" }));
    expect(await screen.findByText("Hinzufügen braucht das Recht, Einstellungen zu ändern.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Zum Wächter hinzufügen" })).toBeNull();
  });
});
