import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { mobileLayout } from "../test/mobileLayout";
import { FolderPickerDialog } from "./FolderPickerDialog";

afterEach(() => vi.unstubAllGlobals());

it("390 px: Quellen und Zielordner stehen untereinander", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ items: [{ name: "Dokumente", path: "/Dokumente", is_dir: true }] })));
  render(<FolderPickerDialog open onOpenChange={vi.fn()} fileName="Rechnung.pdf" initialSourceId="dateien" allowMove onConfirm={vi.fn()}
    sources={[{ source_id: "dateien", label: "Meine Dateien", caps: { write: true, mkdir: true } }]} />);
  await screen.findByRole("button", { name: /Dokumente/ });
  const source = screen.getByRole("list", { name: "Zielquelle" });
  expect(source.parentElement).toHaveClass("flex-col", "sm:flex-row");
  expect(source).not.toHaveClass("w-44");
  expect(source).toHaveClass("sm:w-44");
  await mobileLayout(screen.getByRole("dialog"));
}, 30000);
