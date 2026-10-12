import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { FilePreview, looksLikeText, previewKind, readHead, TEXT_LIMIT } from "./FilePreview";

const entry = (name: string, size: number | null = 100) => ({ name, path: `/etc/${name}`, size });

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
  const urlStatics = URL as unknown as Record<string, unknown>;
  urlStatics.createObjectURL = vi.fn(() => "blob:vorschau");
  urlStatics.revokeObjectURL = vi.fn();
});

afterEach(() => {
  const urlStatics = URL as unknown as Record<string, unknown>;
  delete urlStatics.createObjectURL;
  delete urlStatics.revokeObjectURL;
  vi.unstubAllGlobals();
});

describe("previewKind", () => {
  it("Text und Bilder ja, HTML nur als Text, SVG/PDF/Programme nie", () => {
    expect(previewKind(entry("nginx.conf"))).toBe("text");
    expect(previewKind(entry("docker-compose.yml"))).toBe("text");
    expect(previewKind(entry("Dockerfile"))).toBe("text");
    expect(previewKind(entry(".env"))).toBe("text");
    expect(previewKind(entry("index.html"))).toBe("text");
    expect(previewKind(entry("foto.JPG"))).toBe("image");
    expect(previewKind(entry("riesig.png", 50 * 1024 * 1024))).toBeNull();
    for (const name of ["logo.svg", "handbuch.pdf", "backup.tar.gz", "programm", "lattice.db"]) {
      expect(previewKind(entry(name))).toBeNull();
    }
  });

  it("erkennt Binärdaten am Nullbyte", () => {
    expect(looksLikeText("server {\n  listen 80;\n}")).toBe(true);
    expect(looksLikeText("ELF\u0000\u0001")).toBe(false);
  });
});

describe("readHead", () => {
  it("liest nur bis zur Grenze und bricht den Rest ab", async () => {
    let cancelled = false;
    const chunks = [new Uint8Array(6).fill(65), new Uint8Array(6).fill(66), new Uint8Array(6).fill(67)];
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        const next = chunks.shift();
        if (next) controller.enqueue(next);
        else controller.close();
      },
      cancel() {
        cancelled = true;
      },
    });
    const { bytes, more } = await readHead(new Response(body), 8);
    expect(new TextDecoder().decode(bytes)).toBe("AAAAAABB");
    expect(more).toBe(true);
    expect(cancelled).toBe(true);
    expect(chunks.length).toBeGreaterThan(0);
  });

  it("eine kleine Datei kommt ganz", async () => {
    const { bytes, more } = await readHead(new Response("hallo"), 8);
    expect(new TextDecoder().decode(bytes)).toBe("hallo");
    expect(more).toBe(false);
  });
});

describe("FilePreview", () => {
  function stubDownload(body: BodyInit, status = 200, seen: RequestInit[] = []) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/files/ssh-sftp%3Apve2/download?path=%2Fetc%2Fnginx.conf".replace("nginx.conf", seenName));
      seen.push(init ?? {});
      return new Response(body, { status });
    }));
    return seen;
  }
  let seenName = "nginx.conf";

  it("zeigt Text als reinen Text und fragt nur den Anfang an", async () => {
    seenName = "nginx.conf";
    const seen = stubDownload("server { listen 80; }\n<script>alert(1)</script>");
    render(<FilePreview sourceId="ssh-sftp:pve2" entry={entry("nginx.conf")} kind="text" onClose={() => {}} onDownload={() => {}} />);
    const pre = await screen.findByTestId("file-preview-text");
    expect(pre.textContent).toContain("<script>alert(1)</script>");
    expect(document.querySelector("script")).toBeNull();
    expect(new Headers(seen[0].headers).get("Range")).toBe(`bytes=0-${TEXT_LIMIT - 1}`);
    expect(screen.queryByText(/Nur der Anfang/)).toBeNull();
  });

  it("sagt, wenn nur der Anfang zu sehen ist", async () => {
    seenName = "syslog.log";
    stubDownload("x".repeat(TEXT_LIMIT + 10));
    render(<FilePreview sourceId="ssh-sftp:pve2" entry={entry("syslog.log", TEXT_LIMIT + 10)} kind="text" onClose={() => {}} onDownload={() => {}} />);
    expect(await screen.findByText(/Nur der Anfang/)).toBeInTheDocument();
    expect((await screen.findByTestId("file-preview-text")).textContent).toHaveLength(TEXT_LIMIT);
  });

  it("Binärdaten werden nicht als Text gezeigt", async () => {
    seenName = "seltsam.conf";
    stubDownload("ELF\u0000\u0001\u0002");
    render(<FilePreview sourceId="ssh-sftp:pve2" entry={entry("seltsam.conf")} kind="text" onClose={() => {}} onDownload={() => {}} />);
    expect(await screen.findByText(/sieht nicht nach Text aus/)).toBeInTheDocument();
  });

  it("zeigt Bilder über eine Blob-Adresse mit festem Bild-Typ", async () => {
    seenName = "foto.png";
    stubDownload(new Uint8Array([137, 80, 78, 71]));
    render(<FilePreview sourceId="ssh-sftp:pve2" entry={entry("foto.png")} kind="image" onClose={() => {}} onDownload={() => {}} />);
    const img = await screen.findByRole("img", { name: "foto.png" });
    expect(img).toHaveAttribute("src", "blob:vorschau");
    const blob = (URL.createObjectURL as ReturnType<typeof vi.fn>).mock.calls[0][0] as Blob;
    expect(blob.type).toBe("image/png");
  });

  it("Fehler vom Server, Schließen mit Esc und Laden", async () => {
    seenName = "weg.txt";
    stubDownload(JSON.stringify({ detail: "'/etc/weg.txt' nicht gefunden." }), 404);
    const onClose = vi.fn();
    const onDownload = vi.fn();
    render(<FilePreview sourceId="ssh-sftp:pve2" entry={entry("weg.txt")} kind="text" onClose={onClose} onDownload={onDownload} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nicht gefunden");
    fireEvent.click(screen.getByRole("button", { name: "Laden" }));
    expect(onDownload).toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("Esc in einem Eingabefeld oder bei einem anderen offenen Dialog schließt die Vorschau nicht", async () => {
    seenName = "nginx.conf";
    stubDownload("server {}");
    const onClose = vi.fn();
    render(
      <>
        <input aria-label="Neuer Name" />
        <FilePreview sourceId="ssh-sftp:pve2" entry={entry("nginx.conf")} kind="text" onClose={onClose} onDownload={() => {}} />
      </>,
    );
    await screen.findByTestId("file-preview-text");
    fireEvent.keyDown(screen.getByLabelText("Neuer Name"), { key: "Escape" });
    const other = document.createElement("div");
    other.setAttribute("role", "alertdialog");
    document.body.appendChild(other);
    fireEvent.keyDown(window, { key: "Escape" });
    other.remove();
    const prevented = new KeyboardEvent("keydown", { key: "Escape", cancelable: true });
    prevented.preventDefault();
    window.dispatchEvent(prevented);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("bricht den Abruf beim Schließen ab und gibt den Fokus zurück", async () => {
    seenName = "foto.png";
    const seen = stubDownload(new Uint8Array([137, 80, 78, 71]));
    const opener = document.createElement("button");
    opener.textContent = "Ansehen";
    document.body.appendChild(opener);
    opener.focus();
    const view = render(<FilePreview sourceId="ssh-sftp:pve2" entry={entry("foto.png")} kind="image" onClose={() => {}} onDownload={() => {}} />);
    expect(screen.getByRole("button", { name: "Schließen" })).toHaveFocus();
    await waitFor(() => expect(seen).toHaveLength(1));
    view.unmount();
    expect(seen[0].signal?.aborted).toBe(true);
    expect(opener).toHaveFocus();
    opener.remove();
  });
});
