import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchemaFields, generateText, validateValues, type JsonSchema } from "./SchemaForm";

const TOPIC: JsonSchema = {
  type: "string", title: "Thema", pattern: "^[A-Za-z0-9_-]{1,64}$", "x-pattern-message": "Nur Buchstaben, Zahlen, - und _.",
  "x-generate": { label: "Zufällig wählen", prefix: "nodvard-", length: 20 },
};

function Harness({ schema, initial = {}, onValues }: { schema: JsonSchema; initial?: Record<string, unknown>; onValues?: (v: Record<string, unknown>) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  return <SchemaFields schema={schema} values={values} onChange={(v) => { setValues(v); onValues?.(v); }} />;
}

const form = (field: JsonSchema): JsonSchema => ({ type: "object", properties: { topic: field } });

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("x-generate: Knopf „Zufällig wählen“", () => {
  it("füllt das Feld mit Prefix und zufälligen Zeichen aus a-z und 0-9, die das Muster erfüllen", () => {
    const seen: Record<string, unknown>[] = [];
    render(<Harness schema={form(TOPIC)} onValues={(v) => seen.push(v)} />);
    fireEvent.click(screen.getByRole("button", { name: /Zufällig wählen/ }));
    const value = seen.at(-1)!.topic as string;
    expect(value).toMatch(/^nodvard-[a-z0-9]{20}$/);
    expect(screen.getByLabelText("Thema")).toHaveValue(value);
    expect(validateValues(form(TOPIC), { topic: value })).toEqual([]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("jeder Klick gibt einen neuen Wert und ersetzt den alten", () => {
    const seen: Record<string, unknown>[] = [];
    render(<Harness schema={form(TOPIC)} initial={{ topic: "mein-thema" }} onValues={(v) => seen.push(v)} />);
    const button = screen.getByRole("button", { name: /Zufällig wählen/ });
    fireEvent.click(button);
    fireEvent.click(button);
    const [first, second] = seen.map((v) => v.topic as string);
    expect(first).toMatch(/^nodvard-/);
    expect(second).toMatch(/^nodvard-/);
    expect(first).not.toBe(second);
  });

  it("nimmt den Zufall des Browsers (crypto.getRandomValues), nie Math.random", () => {
    const random = vi.spyOn(Math, "random").mockImplementation(() => { throw new Error("Math.random darf nicht benutzt werden"); });
    const crypto = vi.spyOn(globalThis.crypto, "getRandomValues");
    render(<Harness schema={form(TOPIC)} />);
    fireEvent.click(screen.getByRole("button", { name: /Zufällig wählen/ }));
    expect(crypto).toHaveBeenCalled();
    expect(random).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Thema") as HTMLInputElement).value).toMatch(/^nodvard-[a-z0-9]{20}$/);
  });

  it("verwirft Bytes, die ungleich verteilen würden (ab 252), und fragt erneut", () => {
    const crypto = vi.spyOn(globalThis.crypto, "getRandomValues");
    crypto.mockImplementationOnce(((array: Uint8Array) => { array.fill(255); return array; }) as typeof globalThis.crypto.getRandomValues);
    crypto.mockImplementationOnce(((array: Uint8Array) => { array.fill(35); return array; }) as typeof globalThis.crypto.getRandomValues);
    expect(generateText({ ...TOPIC, "x-generate": { prefix: "x-", length: 4 } })).toBe("x-9999");
    expect(crypto).toHaveBeenCalledTimes(2);
  });

  it("setzt keinen Wert ein, der nicht zum Muster passt, und sagt das", () => {
    const seen: Record<string, unknown>[] = [];
    const schema = form({ ...TOPIC, pattern: "^[A-Z]+$" });
    render(<Harness schema={schema} onValues={(v) => seen.push(v)} />);
    fireEvent.click(screen.getByRole("button", { name: /Zufällig wählen/ }));
    expect(seen).toEqual([]);
    expect(screen.getByLabelText("Thema")).toHaveValue("");
    expect(screen.getByRole("alert")).toHaveTextContent("Der Zufallswert ließ sich nicht erzeugen.");
  });

  it("ohne sicheren Zufall des Browsers kein Wert und keine Ersatzlösung mit Math.random", () => {
    vi.stubGlobal("crypto", undefined);
    const random = vi.spyOn(Math, "random");
    expect(generateText(TOPIC)).toBeNull();
    render(<Harness schema={form(TOPIC)} />);
    fireEvent.click(screen.getByRole("button", { name: /Zufällig wählen/ }));
    expect(random).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("ließ sich nicht erzeugen");
  });

  it("die Fehlermeldung verschwindet nach einem gelungenen Klick", () => {
    render(<Harness schema={form(TOPIC)} />);
    const button = screen.getByRole("button", { name: /Zufällig wählen/ });
    vi.stubGlobal("crypto", undefined);
    fireEvent.click(button);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    vi.unstubAllGlobals();
    fireEvent.click(button);
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByLabelText("Thema") as HTMLInputElement).value).toMatch(/^nodvard-[a-z0-9]{20}$/);
  });

  it("Standardtext des Knopfs, Länge begrenzt, Prefix darf fehlen", () => {
    const bare: JsonSchema = { type: "string", "x-generate": {} };
    render(<Harness schema={form(bare)} />);
    expect(screen.getByRole("button", { name: /Zufällig wählen/ })).toBeInTheDocument();
    expect(generateText(bare)).toMatch(/^[a-z0-9]{16}$/);
    expect(generateText({ type: "string", "x-generate": { length: 100000 } })).toHaveLength(128);
    expect(generateText({ type: "string", "x-generate": { length: -3, prefix: "p-" } })).toMatch(/^p-[a-z0-9]{16}$/);
    expect(generateText({ type: "string" })).toBeNull();
  });

  it("eigener Text am Knopf; ohne x-generate gibt es keinen", () => {
    const { unmount } = render(<Harness schema={form({ ...TOPIC, "x-generate": { label: "Neuer Name", length: 10 } })} />);
    expect(screen.getByRole("button", { name: /Neuer Name/ })).toBeInTheDocument();
    unmount();
    render(<Harness schema={form({ type: "string", title: "Thema" })} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
