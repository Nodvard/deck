import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ImageUpdateBadge } from "./ContainersPage";
import { OperateBadge, OperateNote } from "./operate";

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Partial<Window>).__nodvardDeck;
});

it("Dock nennt sich in den Hinweisen zum Modus Nur bedienen", () => {
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", getProductName: () => "Nodvard Dock",
    confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: () => true,
  };
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ values: {} })));
  render(<><OperateNote hostId="h1" name="Pi" /><OperateBadge hostId="h2" /><ImageUpdateBadge operateOnly /></>);
  expect(screen.getByTestId("operate-h1")).toHaveTextContent("Nodvard Dock darf dort Container ansehen");
  expect(screen.getByText("Nur bedienen")).toHaveAttribute("title", expect.stringContaining("Nodvard Dock darf dort Container ansehen"));
  expect(screen.getByTitle(/prüft Nodvard Dock nicht auf neue Images/)).toBeInTheDocument();
});
