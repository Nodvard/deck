import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { SchemaFields, patternError, validateValues, type JsonSchema } from "./SchemaForm";

const HOSTS = [
  { id: "1", name: "bastel-pi", display_name: "Bastel-Pi", tags: ["linux", "auto-update"] },
  { id: "2", name: "docker", display_name: "docker", tags: ["linux", "docker"] },
  { id: "3", name: "mini", display_name: "mini", tags: ["docker"] },
];

type Fetch = (url: string) => Response | Promise<Response>;
const urls: string[] = [];

function mockFetch(handler: Fetch) {
  urls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = (typeof input === "string" ? input : input.toString()).replace(/^\/api\/v1/, "");
    urls.push(url);
    return handler(url);
  }));
}
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: null });
});

/** Steuert das Formular wie die Einstellungsseite: Werte im State, Änderungen sichtbar. */
function Harness({ schema, initial = {}, onValues }: { schema: JsonSchema; initial?: Record<string, unknown>; onValues?: (v: Record<string, unknown>) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  return <SchemaFields schema={schema} values={values} onChange={(v) => { setValues(v); onValues?.(v); }} />;
}

describe("Server- und Markierungs-Auswahl", () => {
  it("host-tag: Markierungen mit Anzahl als Auswahlliste, leerer Eintrag = alle", async () => {
    mockFetch(() => json(HOSTS));
    const seen: Record<string, unknown>[] = [];
    render(
      <Harness
        schema={{ type: "object", properties: { tag: { type: "string", title: "Markierung", "x-widget": "host-tag", "x-empty-label": "alle Server" } } }}
        onValues={(v) => seen.push(v)}
      />,
    );
    await screen.findByRole("option", { name: "alle Server" });
    const select = screen.getByLabelText("Markierung");
    expect(within(select).getByRole("option", { name: "docker (2 Server)" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "linux (2 Server)" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "docker" } });
    expect(seen.at(-1)).toEqual({ tag: "docker" });
    fireEvent.change(select, { target: { value: "" } });
    expect(seen.at(-1)).toEqual({});
  });

  it("host-tag: eine gespeicherte Markierung, die es nicht mehr gibt, bleibt sichtbar", async () => {
    mockFetch(() => json(HOSTS));
    render(<Harness schema={{ type: "object", properties: { tag: { type: "string", title: "Markierung", "x-widget": "host-tag" } } }} initial={{ tag: "alt" }} />);
    await screen.findByRole("option", { name: "alt (nicht in der Liste)" });
    const select = screen.getByLabelText("Markierung");
    expect(select).toHaveValue("alt");
    expect(within(select).getByRole("option", { name: "alt (nicht in der Liste)" })).toBeInTheDocument();
  });

  it("host: Liste von Servern mit Chips, Hinzufügen und Entfernen", async () => {
    mockFetch(() => json(HOSTS));
    const seen: Record<string, unknown>[] = [];
    render(
      <Harness
        schema={{ type: "object", properties: { skip: { type: "array", items: { type: "string" }, title: "Ignorierte Server", "x-widget": "host" } } }}
        initial={{ skip: ["docker"] }}
        onValues={(v) => seen.push(v)}
      />,
    );
    await screen.findByRole("option", { name: "Bastel-Pi (bastel-pi)" });
    const select = screen.getByLabelText("Ignorierte Server");
    expect(screen.getByText("docker")).toBeInTheDocument();
    expect(within(select).queryByRole("option", { name: "docker" })).toBeNull(); // schon gewählt
    fireEvent.change(select, { target: { value: "bastel-pi" } });
    expect(seen.at(-1)).toEqual({ skip: ["docker", "bastel-pi"] });
    expect(screen.getByText("Bastel-Pi (bastel-pi)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "docker entfernen" }));
    expect(seen.at(-1)).toEqual({ skip: ["bastel-pi"] });
  });

  it("fällt auf Textfeld bzw. Wortliste zurück, wenn die Serverliste nicht ladbar ist", async () => {
    mockFetch(() => new Response(JSON.stringify({ detail: "verboten" }), { status: 403 }));
    render(
      <Harness
        schema={{
          type: "object",
          properties: {
            tag: { type: "string", title: "Markierung", "x-widget": "host-tag" },
            skip: { type: "array", items: { type: "string" }, title: "Ignorierte Server", "x-widget": "host" },
          },
        }}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText("Markierung").tagName).toBe("INPUT"));
    const tag = screen.getByLabelText("Markierung");
    fireEvent.change(tag, { target: { value: "frei" } });
    expect(tag).toHaveValue("frei");
    const words = screen.getByLabelText("Ignorierte Server");
    expect(words.tagName).toBe("INPUT");
    fireEvent.change(words, { target: { value: "pihole" } });
    fireEvent.keyDown(words, { key: "Enter" });
    expect(screen.getByText("pihole")).toBeInTheDocument();
  });

  it("mehrere Felder auf einer Seite teilen sich eine Serverabfrage", async () => {
    mockFetch(() => json(HOSTS));
    render(
      <Harness
        schema={{
          type: "object",
          properties: {
            a: { type: "string", title: "A", "x-widget": "host-tag" },
            b: { type: "string", title: "B", "x-widget": "host-tag" },
          },
        }}
      />,
    );
    await screen.findByLabelText("A");
    await waitFor(() => expect((screen.getByLabelText("A") as HTMLSelectElement).disabled).toBe(false));
    expect(urls.filter((u) => u === "/hosts")).toHaveLength(1);
  });
});

describe("Ollama-Modell als Auswahlliste (remote-select)", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      url: { type: "string", title: "KI-Server" },
      model: {
        type: "string", title: "KI-Modell", default: "qwen2.5:7b", "x-widget": "remote-select",
        "x-options-url": "/ext/shield/ai/models?which=primary",
      },
    },
  };

  it("lädt die Modelle vom Server und schickt keine Formularwerte mit", async () => {
    mockFetch(() => json({ options: [{ value: "llama3:8b", label: "llama3:8b (4.7 GB)" }, { value: "qwen2.5:7b", label: "qwen2.5:7b (4.7 GB)" }], error: null }));
    const seen: Record<string, unknown>[] = [];
    render(<Harness schema={schema} initial={{ url: "http://ki:11434" }} onValues={(v) => seen.push(v)} />);
    await screen.findByRole("option", { name: "llama3:8b (4.7 GB)" }, { timeout: 3000 });
    const select = screen.getByLabelText("KI-Modell");
    expect(urls).toEqual(["/ext/shield/ai/models?which=primary"]);
    expect(select).toHaveValue("qwen2.5:7b"); // der Standardwert des Schemas
    fireEvent.change(select, { target: { value: "llama3:8b" } });
    expect(seen.at(-1)).toEqual({ url: "http://ki:11434", model: "llama3:8b" });
  });

  it("fällt auf ein Freitextfeld zurück und nennt den Grund, wenn der Server nicht antwortet", async () => {
    mockFetch(() => json({ options: [], error: "Keine Antwort vom KI-Server – Adresse und Port prüfen." }));
    const seen: Record<string, unknown>[] = [];
    render(<Harness schema={schema} initial={{ url: "http://ki:11434" }} onValues={(v) => seen.push(v)} />);
    await screen.findByText(/Keine Antwort vom KI-Server/, {}, { timeout: 3000 });
    const input = screen.getByLabelText("KI-Modell");
    expect(input.tagName).toBe("INPUT");
    fireEvent.change(input, { target: { value: "mistral:7b" } });
    expect(seen.at(-1)).toEqual({ url: "http://ki:11434", model: "mistral:7b" });
  });

  it("fällt auf ein Freitextfeld zurück, wenn die Abfrage selbst scheitert", async () => {
    mockFetch(() => new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 }));
    render(<Harness schema={schema} />);
    await screen.findByText(/Die Auswahl konnte nicht geladen werden/, {}, { timeout: 3000 });
    expect(screen.getByLabelText("KI-Modell").tagName).toBe("INPUT");
  });
});

describe("pattern", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      name: { type: "string", title: "Kurzname", pattern: "^[a-z0-9-]+$", "x-pattern-message": "Nur Kleinbuchstaben, Zahlen und Bindestrich." },
      url: { type: "string", title: "Adresse", pattern: "^https?://\\S+$" },
      words: { type: "array", title: "Wörter", items: { type: "string", pattern: "^[a-z]+$", "x-pattern-message": "Nur Kleinbuchstaben." } },
      list: {
        type: "array", title: "Server", "x-item-title": "Server",
        items: { type: "object", properties: { name: { type: "string", title: "Name", pattern: "^\\S+$", "x-pattern-message": "Keine Leerzeichen." } } },
      },
    },
  };

  it("patternError: leer ist erlaubt, sonst eigene oder allgemeine Meldung", () => {
    expect(patternError(schema.properties!.name, "")).toBeNull();
    expect(patternError(schema.properties!.name, undefined)).toBeNull();
    expect(patternError(schema.properties!.name, "ok-1")).toBeNull();
    expect(patternError(schema.properties!.name, "Nicht ok")).toBe("Nur Kleinbuchstaben, Zahlen und Bindestrich.");
    expect(patternError(schema.properties!.url, "nas")).toBe("Das Format stimmt nicht.");
    expect(patternError({ type: "string", pattern: "([kaputt" }, "egal")).toBeNull();
    expect(patternError({ type: "string", pattern: "^a+$" }, "a".repeat(2001))).toBe("Zu lang (höchstens 2000 Zeichen).");
    expect(patternError({ type: "string", pattern: "^a+$", maxLength: 5000 }, "a".repeat(4000))).toBeNull();
  });

  it("validateValues: findet Verstöße in Feldern, Wortlisten und Listeneinträgen", () => {
    expect(validateValues(schema, { name: "gut", url: "https://x", words: ["abc"], list: [{ name: "a" }] })).toEqual([]);
    expect(validateValues(schema, { name: "Schlecht", url: "x", words: ["ok", "NO"], list: [{ name: "a b" }] })).toEqual([
      "Kurzname: Nur Kleinbuchstaben, Zahlen und Bindestrich.",
      "Adresse: Das Format stimmt nicht.",
      "Wörter: „NO“ – Nur Kleinbuchstaben.",
      "Server › Server a b › Name: Keine Leerzeichen.",
    ]);
  });

  it("zeigt die Meldung direkt am Feld und nimmt Wörter mit falschem Muster nicht auf", () => {
    const seen: Record<string, unknown>[] = [];
    render(<Harness schema={schema} onValues={(v) => seen.push(v)} />);
    const name = screen.getByLabelText(/^Kurzname/);
    fireEvent.change(name, { target: { value: "Falsch!" } });
    expect(screen.getByText("Nur Kleinbuchstaben, Zahlen und Bindestrich.")).toBeInTheDocument();
    fireEvent.change(name, { target: { value: "richtig" } });
    expect(screen.queryByText("Nur Kleinbuchstaben, Zahlen und Bindestrich.")).toBeNull();

    const words = screen.getByLabelText(/^Wörter/);
    fireEvent.change(words, { target: { value: "GROSS" } });
    fireEvent.keyDown(words, { key: "Enter" });
    expect(screen.getByText("Nur Kleinbuchstaben.")).toBeInTheDocument();
    expect(seen.some((v) => Array.isArray(v.words))).toBe(false);
    fireEvent.change(words, { target: { value: "klein" } });
    fireEvent.keyDown(words, { key: "Enter" });
    expect(seen.at(-1)).toMatchObject({ words: ["klein"] });
  });
});

describe("bestehende Zusätze", () => {
  it("x-advanced klappt ein, x-hidden fehlt, x-enum-labels zeigt lesbare Texte, schedule bleibt ein Wähler", async () => {
    render(
      <Harness
        schema={{
          type: "object",
          properties: {
            sichtbar: { type: "string", title: "Sichtbar" },
            versteckt: { type: "string", title: "Versteckt", "x-hidden": true },
            fein: { type: "string", title: "Feinheit", "x-advanced": true },
            modus: { type: "string", title: "Modus", enum: ["a", "b"], "x-enum-labels": { a: "Schön A" } },
            zeit: { type: "string", title: "Zeitpunkt", "x-widget": "schedule", default: "0 2 * * *" },
          },
        }}
      />,
    );
    expect(screen.getByLabelText("Sichtbar")).toBeInTheDocument();
    expect(screen.queryByLabelText("Versteckt")).toBeNull();
    expect(screen.queryByLabelText("Feinheit")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Erweitert/ }));
    expect(screen.getByLabelText("Feinheit")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Schön A" })).toBeInTheDocument();
    expect(screen.getByText("Zeitpunkt")).toBeInTheDocument();
    expect(screen.queryByLabelText("Zeitpunkt", { selector: "input[type=text]" })).toBeNull();
  });
});
