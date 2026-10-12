import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HostPicker, osLabel, rememberedHost, statusTone } from "./HostPicker";

const HOSTS = [
  { id: "a", name: "Raspberry Pi", address: "192.168.1.72", os: "linux", status: "up", extra: "CPU 12 %" },
  { id: "b", name: "NAS", address: "192.168.1.20", os: "linux", status: "unknown", hint: "ohne SSH-Zugang" },
  { id: "c", name: "Spiele-PC", address: "192.168.1.50", os: "windows", status: "down" },
];

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

describe("HostPicker", () => {
  it("zeigt Kacheln mit Name, Adresse, System, Status und Auslastung; ein Klick wählt und merkt sich den Server", () => {
    const onChange = vi.fn();
    render(<HostPicker hosts={HOSTS} value={null} onChange={onChange} storageKey="test" />);
    const pi = screen.getByRole("button", { name: /Raspberry Pi/ });
    expect(pi.textContent).toContain("192.168.1.72");
    expect(pi.textContent).toContain("Linux");
    expect(pi.textContent).toContain("CPU 12 %");
    expect(pi.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe("erreichbar");
    expect(screen.getByRole("button", { name: /Spiele-PC/ }).querySelector('[role="img"]')?.getAttribute("aria-label")).toBe("nicht erreichbar");
    expect(screen.getByRole("button", { name: /NAS/ }).textContent).toContain("ohne SSH-Zugang");
    expect(screen.queryByLabelText("Server suchen")).toBeNull(); // drei Server: keine Suche noetig

    fireEvent.click(pi);
    expect(onChange).toHaveBeenCalledWith("a");
    expect(rememberedHost("test")).toBe("a");
  });

  it("der zuletzt gewählte Server ist vorgewählt – aber nur, wenn es ihn noch gibt und die Seite keinen hat", () => {
    window.localStorage.setItem("nodvard.host-picker.test", "b");
    const onChange = vi.fn();
    render(<HostPicker hosts={HOSTS} value={null} onChange={onChange} storageKey="test" />);
    expect(onChange).toHaveBeenCalledWith("b");
    cleanup();

    const withValue = vi.fn();
    render(<HostPicker hosts={HOSTS} value="a" onChange={withValue} storageKey="test" />);
    expect(withValue).not.toHaveBeenCalled(); // ?host= in der Adresszeile gewinnt
    cleanup();

    window.localStorage.setItem("nodvard.host-picker.test", "geloescht");
    const gone = vi.fn();
    render(<HostPicker hosts={HOSTS} value={null} onChange={gone} storageKey="test" />);
    expect(gone).not.toHaveBeenCalled();
  });

  it("mit gewähltem Server nur eine Zeile; „Server wechseln“ öffnet die Kacheln, Abbrechen schließt sie wieder", () => {
    render(<HostPicker hosts={HOSTS} value="a" onChange={vi.fn()} storageKey="test" />);
    expect(screen.getByTestId("host-picker-selected").textContent).toContain("Raspberry Pi");
    expect(screen.queryByRole("button", { name: /NAS/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Server wechseln" }));
    expect(screen.getByRole("button", { name: /NAS/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Raspberry Pi/, pressed: true })).toBeInTheDocument(); // die Kachel, markiert

    fireEvent.click(screen.getByRole("button", { name: /Abbrechen/ }));
    expect(screen.getByTestId("host-picker-selected")).toBeInTheDocument();
  });

  it("mit nur einem Server gibt es nichts zu wechseln", () => {
    render(<HostPicker hosts={[HOSTS[0]]} value="a" onChange={vi.fn()} storageKey="test" />);
    expect(screen.queryByRole("button", { name: "Server wechseln" })).toBeNull();
  });

  it("ab sechs Servern gibt es eine Suche über Name, Adresse und System", () => {
    const many = Array.from({ length: 7 }, (_, i) => ({ id: `h${i}`, name: `Server ${i}`, address: `10.0.0.${i}`, os: i === 3 ? "windows" : "linux" }));
    render(<HostPicker hosts={many} value={null} onChange={vi.fn()} storageKey="test" />);
    const search = screen.getByLabelText("Server suchen");
    fireEvent.change(search, { target: { value: "windows" } });
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Server 3/ })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "10.0.0.5" } });
    expect(screen.getByRole("button", { name: /Server 5/ })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "gibt es nicht" } });
    expect(screen.getByText(/Kein Server passt zu „gibt es nicht“/)).toBeInTheDocument();
  });

  it("Status und Betriebssystem heißen verständlich", () => {
    expect(statusTone("up").label).toBe("erreichbar");
    expect(statusTone("running").tone).toBe("ok");
    expect(statusTone("stopped").label).toBe("nicht erreichbar");
    expect(statusTone(undefined).label).toBe("Status unbekannt");
    expect(osLabel("linux")).toBe("Linux");
    expect(osLabel("proxmox")).toBe("Proxmox");
    expect(osLabel("freebsd")).toBe("Freebsd");
    expect(osLabel(null)).toBe("");
  });

  it("als Starter (actions): Kacheln mit Knöpfen, nichts wird gewählt oder vorgewählt", () => {
    window.localStorage.setItem("nodvard.host-picker.test", "a");
    const onChange = vi.fn();
    const opened: string[] = [];
    render(
      <HostPicker hosts={HOSTS} value={null} onChange={onChange} storageKey="test" gridClass="grid-cols-1" searchFrom={2}
        actions={(h) => <button type="button" onClick={() => opened.push(h.id)}>Terminal öffnen</button>} />,
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "NAS" }).textContent).toContain("ohne SSH-Zugang");
    fireEvent.click(screen.getAllByRole("button", { name: "Terminal öffnen" })[0]);
    expect(opened).toEqual(["a"]);
    fireEvent.change(screen.getByLabelText("Server suchen"), { target: { value: "spiele" } });
    expect(screen.getAllByRole("button", { name: "Terminal öffnen" })).toHaveLength(1);
  });

  it("solange die Server laden, steht das da; ohne Server zeigt die Auswahl nichts", () => {
    const { container, rerender } = render(<HostPicker hosts={null} value={null} onChange={vi.fn()} storageKey="test" />);
    expect(screen.getByText("Lade Server …")).toBeInTheDocument();
    rerender(<HostPicker hosts={[]} value={null} onChange={vi.fn()} storageKey="test" />);
    expect(container.textContent).toBe("");
  });
});
