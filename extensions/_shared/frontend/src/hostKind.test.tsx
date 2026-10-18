import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { HostPicker, statusTone, tileFromHost } from "./HostPicker";
import { groupHosts, guestSummary, hostCategory, isSwitchedOff, kindLabel } from "./hostKind";

afterEach(cleanup);

describe("hostKind", () => {
  it("ordnet nach Art: Knoten und von Hand angelegte Server sind physisch, ältere Server ohne category über kind", () => {
    expect(hostCategory({ category: "lxc" })).toBe("lxc");
    expect(hostCategory({ kind: "vm" })).toBe("vm");
    expect(hostCategory({ kind: "hypervisor", category: "server" })).toBe("server");
    expect(hostCategory({ kind: null })).toBe("server");
    expect(kindLabel({ kind: "lxc", category: "lxc", parent_label: "pve1" })).toBe("LXC auf pve1");
    expect(kindLabel({ kind: "vm", category: "vm" })).toBe("VM");
    expect(kindLabel({ kind: "hypervisor", category: "server" })).toBe("Knoten");
    expect(kindLabel({ kind: null })).toBe("Server");
  });

  it("Gruppen in fester Reihenfolge, leere fallen weg; Zusammenfassung der Gäste", () => {
    const hosts = [{ id: "c", category: "lxc" }, { id: "p", category: "server" }, { id: "c2", category: "lxc" }];
    expect(groupHosts(hosts).map((g) => [g.title, g.hosts.map((h) => h.id)])).toEqual([
      ["Physische Server", ["p"]],
      ["LXC", ["c", "c2"]],
    ]);
    expect(guestSummary([...hosts, { category: "vm" }])).toBe("1 VM · 2 LXC");
    expect(guestSummary([{ category: "server" }])).toBe("");
  });

  it("gestoppt oder pausiert ist aus, nicht gestört", () => {
    expect(isSwitchedOff({ power_state: "stopped" })).toBe(true);
    expect(isSwitchedOff({ power_state: "paused" })).toBe(true);
    expect(isSwitchedOff({ power_state: "running" })).toBe(false);
    expect(statusTone("down", "stopped")).toEqual({ tone: "off", label: "aus" });
    expect(statusTone("down").tone).toBe("bad");
    // Beim Anbieter nicht mehr vorhanden: grau, auch wenn der letzte Stand „up“ war.
    expect(statusTone("up", null, "2026-10-16T08:00:00Z")).toEqual({ tone: "off", label: "nicht mehr vorhanden" });
  });

  it("die Server-Auswahl gruppiert ab zwei Arten und zeigt Plakette und „aus“", () => {
    const hosts = [
      { id: "p", name: "pve1", display_name: "pve1", address: "10.0.0.2", kind: "hypervisor", category: "server", status: "up" },
      { id: "d", name: "dns", display_name: "DNS", address: "10.0.0.5", kind: "lxc", category: "lxc", parent_label: "pve1", power_state: "stopped", status: "down" },
    ].map(tileFromHost);
    render(<HostPicker hosts={hosts} value={null} onChange={() => {}} storageKey="kind" />);
    expect(screen.getByTestId("group-server").textContent).toContain("Physische Server");
    const lxc = screen.getByRole("button", { name: /DNS/ });
    expect(lxc.textContent).toContain("LXC auf pve1");
    expect(lxc.textContent).toContain("aus");
    expect(within(lxc).getByRole("img").getAttribute("aria-label")).toBe("aus");
    cleanup();

    // Nur eine Art: keine Überschrift, wie bisher.
    render(<HostPicker hosts={[hosts[0]]} value={null} onChange={() => {}} storageKey="kind" />);
    expect(screen.queryByTestId("group-server")).toBeNull();
  });
});
