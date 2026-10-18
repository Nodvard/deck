import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StackForm } from "./StackForm";

const FILE = `services:
  web:
    image: nginx:1.27 # fest
    ports:
      - "8080:80"
    environment:
      TZ: Europe/Berlin
`;

let current = "";

function Harness({ start }: { start: string }): JSX.Element {
  const [text, setText] = useState(start);
  current = text;
  return <StackForm text={text} onChange={(next) => { current = next; setText(next); }} />;
}

function type(label: string, value: string) {
  const input = screen.getByRole("textbox", { name: label });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

describe("StackForm", () => {
  it("ändert das Image an seiner Stelle, der Kommentar bleibt", () => {
    render(<Harness start={FILE} />);
    type("Image von web", "nginx:1.28");
    expect(current).toBe(FILE.replace("nginx:1.27", "nginx:1.28"));
  });

  it("Schreibweise verschiebt sich beim Tippen nicht", () => {
    render(<Harness start={FILE} />);
    const input = screen.getByRole("textbox", { name: "Image von web" });
    fireEvent.focus(input);
    for (const value of ["nginx:1.27 ", "nginx:1.27 x", "nginx:1.28"]) fireEvent.change(input, { target: { value } });
    fireEvent.blur(input);
    expect(current).toBe(FILE.replace("nginx:1.27", "nginx:1.28"));
  });

  it("Ports hinzufügen und entfernen", () => {
    render(<Harness start={FILE} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Ports für web hinzufügen" }), { target: { value: "443:443" } });
    fireEvent.click(within(screen.getByRole("region", { name: "Dienst web" })).getAllByRole("button", { name: "Hinzufügen" })[0]);
    expect(current).toBe(FILE.replace(`- "8080:80"\n`, `- "8080:80"\n      - "443:443"\n`));
    fireEvent.click(screen.getByRole("button", { name: "Ports 1 von web entfernen" }));
    expect(current).toBe(FILE.replace(`- "8080:80"\n`, `- "443:443"\n`));
  });

  it("Neustart-Regel und Umgebung", () => {
    render(<Harness start={FILE} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Neustart von web" }), { target: { value: "unless-stopped" } });
    expect(current).toBe(`${FILE}    restart: unless-stopped\n`);
    fireEvent.change(screen.getByRole("textbox", { name: "Name der neuen Variable für web" }), { target: { value: "PUID" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Wert der neuen Variable für web" }), { target: { value: "1000" } });
    fireEvent.click(within(screen.getByRole("region", { name: "Dienst web" })).getAllByRole("button", { name: "Hinzufügen" })[2]);
    expect(current).toContain('      TZ: Europe/Berlin\n      PUID: "1000"\n    restart: unless-stopped\n');
    fireEvent.click(screen.getByRole("button", { name: "TZ aus web entfernen" }));
    expect(current).not.toContain("TZ");
  });

  it("neuer Dienst", () => {
    render(<Harness start={FILE} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Name des neuen Dienstes" }), { target: { value: "web" } });
    expect(screen.getByText("Den Namen gibt es schon.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Name des neuen Dienstes" }), { target: { value: "cache" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Image des neuen Dienstes" }), { target: { value: "redis:7" } });
    fireEvent.click(screen.getByRole("button", { name: "Dienst hinzufügen" }));
    expect(current).toBe(`${FILE}  cache:\n    image: redis:7\n`);
    expect(screen.getByRole("region", { name: "Dienst cache" })).toBeInTheDocument();
  });

  it("geteilte Stellen nur im Text", () => {
    const text = "x: &i nginx\nservices:\n  a:\n    image: *i\n  b:\n    <<: {image: y}\n";
    render(<Harness start={text} />);
    expect(screen.getByText(/Der Wert kommt von einer anderen Stelle der Datei \(\*i\)/)).toBeInTheDocument();
    expect(screen.getByText(/übernimmt Einstellungen von einer anderen Stelle/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Image von a" })).toBeNull();
  });

  it("kaputte Datei: Grund statt Formular", () => {
    render(<Harness start={"services: [a\n"} />);
    expect(screen.getByText(/kein gültiges YAML/)).toBeInTheDocument();
    expect(screen.queryByTestId("stack-form")).toBeNull();
  });

  it("Image mit Anker nur im Text, der Rest geht", () => {
    const text = "services:\n  a:\n    image: &img nginx\n  b:\n    image: x\n";
    render(<Harness start={text} />);
    expect(screen.getByText(/Besonders geschrieben/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Ports für a hinzufügen" }), { target: { value: "1:1" } });
    fireEvent.click(within(screen.getByRole("region", { name: "Dienst a" })).getAllByRole("button", { name: "Hinzufügen" })[0]);
    expect(current).toBe(`services:\n  a:\n    image: &img nginx\n    ports:\n      - "1:1"\n  b:\n    image: x\n`);
  });

  it("abgelehnte Änderung: Grund, Datei bleibt", () => {
    const text = "services:\n  a:\n    image: x\n    environment: {A: \"1\", B: \"2\"}\n";
    render(<Harness start={text} />);
    fireEvent.click(screen.getByRole("button", { name: "A aus a entfernen" }));
    expect(screen.getByRole("alert").textContent).toMatch(/nicht sicher ändern/);
    expect(current).toBe(text);
  });
});
