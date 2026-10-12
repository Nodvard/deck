import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LynisFinding, lynisId, TopIssues, topIssues } from "./LynisFinding";

const HELP = {
  "AUTH-9286": { title: "Passwörter regelmäßig wechseln", what: "Passwörter müssen nie geändert werden.", todo: "Bewusst so lassen." },
};

describe("LynisFinding", () => {
  it("zeigt die deutsche Überschrift, den englischen Text klein darunter und auf Klick beide Sätze", () => {
    render(<ul><LynisFinding entry="AUTH-9286: Configure maximum password age" help={HELP} tone="suggestion" /></ul>);
    const title = screen.getByRole("button", { name: /Passwörter regelmäßig wechseln/ });
    expect(screen.getByText("AUTH-9286: Configure maximum password age")).toBeInTheDocument();
    expect(title).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Was heißt das\?/)).toBeNull();
    fireEvent.click(title);
    expect(title).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Was heißt das\?/).parentElement).toHaveTextContent("Passwörter müssen nie geändert werden.");
    expect(screen.getByText(/Was kannst du tun\?/).parentElement).toHaveTextContent("Bewusst so lassen.");
  });

  it("unbekannte Kennungen und alte Audits ohne Texte bleiben wie bisher", () => {
    const { rerender } = render(<ul><LynisFinding entry="ZZZZ-1234: something new" help={HELP} tone="warning" /></ul>);
    expect(screen.getByText("• ZZZZ-1234: something new")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<ul><LynisFinding entry="AUTH-9286: Configure maximum password age" tone="warning" /></ul>);
    expect(screen.getByText("• AUTH-9286: Configure maximum password age")).toBeInTheDocument();
  });

  it("liest die Kennung", () => {
    expect(lynisId("SSH-7408: Consider hardening SSH configuration (MaxAuthTries (set 6 to 3))")).toBe("SSH-7408");
    expect(lynisId("LYNIS: Version of Lynis is very old")).toBe("LYNIS");
    expect(lynisId("ohne Kennung")).toBeNull();
  });
});

describe("Deine wichtigsten Baustellen", () => {
  const H = {
    "PKGS-7392": { title: "Pakete mit Sicherheitslücken", what: "w", todo: "t", weight: 3 },
    "SSH-7408": { title: "SSH-Einstellung härten", what: "w", todo: "t", weight: 3 },
    "KRNL-5820": { title: "Speicherabbilder abschalten", what: "w", todo: "t", weight: 2 },
    "BANN-7126": { title: "Rechtlicher Hinweis", what: "w", todo: "t", weight: 1 },
    "AUTH-9328": { title: "Strengere Rechte", what: "w", todo: "t" },
  };
  const audit = (host: string, warnings: string[], suggestions: string[], status = "ok") => ({ host_name: host, status, warnings, suggestions, help: H });

  it("zählt je Kennung die Server, lässt Unwichtiges weg und sortiert nach Gewicht, Warnung, Anzahl", () => {
    const issues = topIssues([
      audit("pve1", [], ["SSH-7408: x (MaxAuthTries)", "SSH-7408: x (X11Forwarding)", "KRNL-5820: core", "BANN-7126: banner", "AUTH-9328: umask"]),
      audit("docker", ["PKGS-7392: vulnerable"], ["SSH-7408: x (Port)", "KRNL-5820: core"]),
      audit("kaputt", ["PKGS-7392: vulnerable"], [], "error"),
    ]);
    expect(topIssues([
      { host_id: "h1", host_name: "pi", status: "ok", warnings: [], suggestions: ["SSH-7408: x"], help: H },
      { host_id: "h2", host_name: "pi", status: "ok", warnings: [], suggestions: ["SSH-7408: x"], help: H },
    ])[0].hostIds).toEqual(["h1", "h2"]);
    expect(issues.map((i) => [i.id, i.hosts])).toEqual([
      ["PKGS-7392", ["docker"]],
      ["SSH-7408", ["pve1", "docker"]],
      ["KRNL-5820", ["pve1", "docker"]],
    ]);
    expect(topIssues([audit("a", [], ["SSH-7408: x"])], 0)).toEqual([]);
  });

  it("zeigt die Liste mit Rang, Servern und aufklappbaren Sätzen", () => {
    render(<TopIssues issues={topIssues([audit("pve1", ["PKGS-7392: vulnerable"], ["KRNL-5820: core"])])} />);
    const list = screen.getByTestId("top-issues");
    expect(list).toHaveTextContent("1.Pakete mit Sicherheitslücken");
    expect(list).toHaveTextContent("wichtig · auf pve1");
    expect(list).toHaveTextContent("sinnvoll · auf pve1");
    fireEvent.click(screen.getByRole("button", { name: /Speicherabbilder abschalten/ }));
    expect(screen.getByText(/Was kannst du tun\?/)).toBeInTheDocument();
  });

  it("ohne wichtige Hinweise erscheint nichts", () => {
    const { container } = render(<TopIssues issues={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
