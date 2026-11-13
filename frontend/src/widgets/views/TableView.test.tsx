import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { cardTable } from "../../test/mobileLayout";
import { TableView } from "./TableView";

it("390 px: Widget-Spalten werden zu beschrifteten Karten", async () => {
  useAuthStore.setState({ user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] } });
  render(<TableView extId="test" onAction={vi.fn()} limit={1} data={[{ name: "Dateipfad/".repeat(30), value: "123 GB" }, { name: "ausgeblendet" }]} view={{
    kind: "table", empty_text: "Leer", columns: [
      { field: "name", label: "Name", template: null, align: "left", width: 400 },
      { field: "value", label: "Belegung", template: null, align: "right", width: null },
    ],
    row_actions: [{ id: "start", label: "Starten", endpoint: "/start", method: "POST", body: null, confirm: false, confirm_text: null, style: "primary", permissions: [] }],
  }} />);
  expect(screen.queryByText("ausgeblendet")).toBeNull();
  expect(screen.getByRole("button", { name: "Starten" }).closest("td")!.firstElementChild).toHaveClass("flex-wrap");
  await cardTable(screen.getByRole("table"), ["Name", "Belegung"]);
}, 30000);
