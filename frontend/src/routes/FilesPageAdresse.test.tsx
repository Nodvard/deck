/**
 * Dateimanager: Adresse (`?source=`, `?host=`, `?path=`) und „Wichtige Orte“.
 * Die Seite liest die Adresse beim Oeffnen (und bei einer neuen Adresse von aussen), schreibt sie beim Klicken mit
 * (`replace`) und zeigt zu einer Server-Quelle die Orte aus `GET /files/places`.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { FilesPage } from "./FilesPage";

vi.mock("../lib/ws", () => ({ useWsSubscription: () => undefined }));

const CAPS = { write: true, rename: true, remove: true, mkdir: true, search: false, range_read: true };
const SOURCES = [
  { source_id: "ssh-sftp:pve2", label: "SSH (SFTP): pve2", icon: "server", caps: CAPS, host_id: "h-pve2" },
  { source_id: "ssh-sftp:pi", label: "SSH (SFTP): bastel-pi", icon: "server", caps: CAPS, host_id: "h-pi" },
  { source_id: "ssh-sftp:pi-zweit", label: "SSH (SFTP): bastel-pi (zweiter Zugang)", icon: "server", caps: CAPS, host_id: "h-pi" },
  { source_id: "nextcloud", label: "Nextcloud", icon: "cloud", caps: CAPS },
];
const PLACES = [
  { label: "Docker: shop", path: "/srv/shop", icon: "boxes", hint: "Ordner des Compose-Projekts „shop“" },
  { label: "Protokolle", path: "/var/log", icon: "file-text", hint: null },
];
const FOLDER = [{ name: "app", path: "/srv/shop/app", is_dir: true, size: null, modified_at: null, mime: null }];

let sources: unknown[];
let listCalls: { source: string; path: string }[];
let placesCalls: string[];
let placesAnswer: () => Response;

function mockFetch() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/v1/files/sources") return new Response(JSON.stringify(sources), { status: 200 });
    if (url.pathname === "/api/v1/hosts") return new Response("[]", { status: 200 });
    if (url.pathname === "/api/v1/files/places") {
      placesCalls.push(url.searchParams.get("host_id") ?? "");
      return placesAnswer();
    }
    const list = url.pathname.match(/^\/api\/v1\/files\/([^/]+)\/list$/);
    if (list) {
      const path = url.searchParams.get("path") ?? "/";
      listCalls.push({ source: decodeURIComponent(list[1]), path });
      return new Response(JSON.stringify({ items: path === "/srv/shop" ? FOLDER : [] }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${url.pathname}`);
  }));
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
  sources = SOURCES;
  listCalls = [];
  placesCalls = [];
  placesAnswer = () => new Response(JSON.stringify(PLACES), { status: 200 });
  mockFetch();
});

/** Zeigt die Adresse, damit die Tests sehen, was die Seite mitschreibt, und bietet einen Weg zurueck. */
function Probe() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <p data-testid="adresse">{pathname}{search}</p>
      <button type="button" onClick={() => navigate(-1)}>Zurück</button>
      <Link to="/files?host=h-pve2&path=/etc">Link zu pve2</Link>
    </>
  );
}

function renderAt(entry: string, history: string[] = []) {
  return render(
    <MemoryRouter initialEntries={[...history, entry]} initialIndex={history.length}>
      <Probe />
      <FilesPage />
    </MemoryRouter>,
  );
}

const address = () => {
  const text = screen.getByTestId("adresse").textContent ?? "";
  const [pathname, query = ""] = text.split("?");
  return { pathname, params: Object.fromEntries(new URLSearchParams(query)) };
};
const lastList = () => listCalls[listCalls.length - 1];

describe("FilesPage: Adresse lesen", () => {
  it("?host= öffnet die Quelle dieses Servers", async () => {
    renderAt("/files?host=h-pve2");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/" }));
    expect(listCalls.every((c) => c.source === "ssh-sftp:pve2")).toBe(true);
    expect(screen.queryByTestId("files-link-notice")).toBeNull();
  });

  it("?host= mit mehreren Quellen: die erste", async () => {
    renderAt("/files?host=h-pi");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pi", path: "/" }));
    expect(listCalls.some((c) => c.source === "ssh-sftp:pi-zweit")).toBe(false);
  });

  it("?host= mit ?path= öffnet diesen Ordner", async () => {
    renderAt("/files?host=h-pve2&path=/srv/shop");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/srv/shop" }));
    expect(await screen.findByRole("button", { name: /app/ })).toBeInTheDocument();
  });

  it("?source= und ?path= öffnen genau diesen Ordner dieser Quelle", async () => {
    renderAt("/files?source=ssh-sftp%3Api-zweit&path=%2Fvar%2Flog");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pi-zweit", path: "/var/log" }));
  });

  it("?source= gewinnt gegen ?host=", async () => {
    renderAt("/files?source=nextcloud&host=h-pve2");
    await waitFor(() => expect(lastList()).toEqual({ source: "nextcloud", path: "/" }));
    expect(listCalls.some((c) => c.source === "ssh-sftp:pve2")).toBe(false);
  });

  it("unbekannte Quelle: normaler Start mit Hinweis, der Pfad aus der Adresse gilt dann nicht", async () => {
    renderAt("/files?source=gibt-es-nicht&path=/srv/shop");
    const notice = await screen.findByTestId("files-link-notice");
    expect(notice.textContent).toContain("„gibt-es-nicht“ gibt es nicht");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/" }));
    expect(listCalls.some((c) => c.path === "/srv/shop")).toBe(false);
  });

  it("Server ohne Quelle: ein freundlicher Satz statt einer leeren Seite, es geht mit der ersten Quelle weiter", async () => {
    renderAt("/files?host=h-ohne");
    const notice = await screen.findByTestId("files-link-notice");
    expect(notice.textContent).toContain("Für diesen Server gibt es keine Dateiquelle");
    expect(notice.textContent).toContain("SSH-Zugang");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/" }));
  });

  it("Server ohne Quelle und gar keine Quellen: Hinweis und der Leerzustand", async () => {
    sources = [];
    renderAt("/files?host=h-ohne");
    expect((await screen.findByTestId("files-link-notice")).textContent).toContain("Für diesen Server gibt es keine Dateiquelle");
    expect(screen.getByTestId("files-no-sources")).toBeInTheDocument();
    expect(listCalls).toEqual([]);
  });

  it.each(["etc/passwd", "~/daten", "", "https://böse.example/"])("ungültiger Pfad %j: Hauptordner mit Hinweis", async (path) => {
    renderAt(`/files?source=nextcloud&path=${encodeURIComponent(path)}`);
    expect((await screen.findByTestId("files-link-notice")).textContent).toContain("ist ungültig");
    await waitFor(() => expect(lastList()).toEqual({ source: "nextcloud", path: "/" }));
    expect(listCalls.every((c) => c.path === "/")).toBe(true);
  });

  it("ohne Angaben: erste Quelle, kein Hinweis", async () => {
    renderAt("/files");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/" }));
    expect(screen.queryByTestId("files-link-notice")).toBeNull();
  });

  it("nur ?path= ohne Quelle: erste Quelle in diesem Ordner", async () => {
    renderAt("/files?path=/srv/shop");
    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/srv/shop" }));
  });

  it("eine neue Adresse von aussen (Link, während die Seite offen ist) wechselt Quelle und Ordner", async () => {
    renderAt("/files?source=nextcloud");
    await waitFor(() => expect(lastList()).toEqual({ source: "nextcloud", path: "/" }));

    fireEvent.click(screen.getByRole("link", { name: "Link zu pve2" }));

    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/etc" }));
  });
});

describe("FilesPage: Adresse mitschreiben", () => {
  it("schreibt Quelle und Ordner in die Adresse, auch für den Start ohne Angaben und aus ?host=", async () => {
    renderAt("/files?host=h-pve2&path=/srv/shop");
    await waitFor(() => expect(address()).toEqual({ pathname: "/files", params: { source: "ssh-sftp:pve2", path: "/srv/shop" } }));
  });

  it("Klick auf einen Ordner, einen Pfadteil und eine Quelle führt die Adresse mit", async () => {
    renderAt("/files?source=ssh-sftp%3Apve2&path=%2Fsrv%2Fshop");
    fireEvent.click(await screen.findByRole("button", { name: /app/ }));
    await waitFor(() => expect(address().params).toEqual({ source: "ssh-sftp:pve2", path: "/srv/shop/app" }));

    fireEvent.click(screen.getByRole("button", { name: "shop" }));
    await waitFor(() => expect(address().params.path).toBe("/srv/shop"));

    fireEvent.click(screen.getByRole("button", { name: "Nextcloud" }));
    await waitFor(() => expect(address().params).toEqual({ source: "nextcloud", path: "/" }));
  });

  it("jeder Klick ersetzt den Verlaufseintrag: ein Schritt zurück verlässt die Seite", async () => {
    renderAt("/files?host=h-pve2&path=/srv/shop", ["/hosts/h-pve2"]);
    fireEvent.click(await screen.findByRole("button", { name: /app/ }));
    await waitFor(() => expect(address().params.path).toBe("/srv/shop/app"));
    fireEvent.click(screen.getByRole("button", { name: "Nextcloud" }));
    await waitFor(() => expect(address().params.source).toBe("nextcloud"));

    fireEvent.click(screen.getByRole("button", { name: "Zurück" }));

    await waitFor(() => expect(address().pathname).toBe("/hosts/h-pve2"));
  });

  it("die eigene Adresse löst kein erneutes Laden aus", async () => {
    renderAt("/files?source=ssh-sftp%3Apve2&path=%2Fsrv%2Fshop");
    await screen.findByRole("button", { name: /app/ });
    const loads = listCalls.length;
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(listCalls.length).toBe(loads);
  });
});

describe("FilesPage: Wichtige Orte", () => {
  it("zeigt zu einer Server-Quelle die Orte des Servers, ein Klick springt dorthin und führt die Adresse mit", async () => {
    renderAt("/files?host=h-pve2");
    const block = await screen.findByTestId("files-places");
    expect(within(block).getByRole("heading", { name: "Wichtige Orte" })).toBeInTheDocument();
    expect(within(block).getAllByRole("button").map((b) => b.textContent)).toEqual(["Docker: shop", "Protokolle"]);
    expect(placesCalls).toEqual(["h-pve2"]);

    fireEvent.click(within(block).getByRole("button", { name: "Protokolle" }));

    await waitFor(() => expect(lastList()).toEqual({ source: "ssh-sftp:pve2", path: "/var/log" }));
    expect(address().params).toEqual({ source: "ssh-sftp:pve2", path: "/var/log" });
    expect(within(block).getByRole("button", { name: "Protokolle" }).className).toContain("font-medium");
    expect(within(block).getByRole("button", { name: "Docker: shop" })).toHaveAttribute("title", "Ordner des Compose-Projekts „shop“ (/srv/shop)");
    expect(within(block).getByRole("button", { name: "Protokolle" })).toHaveAttribute("title", "/var/log");
  });

  it("keine Orte: kein Block", async () => {
    placesAnswer = () => new Response("[]", { status: 200 });
    renderAt("/files?host=h-pve2");
    await waitFor(() => expect(placesCalls).toEqual(["h-pve2"]));
    await waitFor(() => expect(lastList()).toBeDefined());
    expect(screen.queryByTestId("files-places")).toBeNull();
  });

  it("ein Fehler beim Laden der Orte lässt nur den Block weg", async () => {
    placesAnswer = () => new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 });
    renderAt("/files?host=h-pve2");
    await waitFor(() => expect(placesCalls).toEqual(["h-pve2"]));
    expect(screen.queryByTestId("files-places")).toBeNull();
    expect(screen.queryByText(/kaputt/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Nextcloud" }));
    await waitFor(() => expect(lastList()).toEqual({ source: "nextcloud", path: "/" }));
  });

  it("eine Quelle ohne Server (Nextcloud) fragt keine Orte ab und zeigt keinen Block", async () => {
    renderAt("/files?source=nextcloud");
    await waitFor(() => expect(lastList()).toEqual({ source: "nextcloud", path: "/" }));
    expect(placesCalls).toEqual([]);
    expect(screen.queryByTestId("files-places")).toBeNull();
  });

  it("beim Wechsel der Quelle gehören die Orte zum jeweiligen Server und werden je Server nur einmal geholt", async () => {
    placesAnswer = () => new Response(JSON.stringify(PLACES), { status: 200 });
    renderAt("/files?host=h-pve2");
    await screen.findByTestId("files-places");

    fireEvent.click(screen.getByRole("button", { name: "Nextcloud" }));
    await waitFor(() => expect(screen.queryByTestId("files-places")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: /SSH \(SFTP\): pve2/ }));
    expect(await screen.findByTestId("files-places")).toBeInTheDocument();
    expect(placesCalls).toEqual(["h-pve2"]);
  });
});
