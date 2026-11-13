// @vitest-environment node
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

it.each([
  ["FolderPickerDialog.tsx", "42rem", 1], ["CommandPalette.tsx", "36rem", 1],
  ["AppDialog.tsx", "32rem", 1], ["../routes/ConsolePage.tsx", "32rem", 1],
  ["../routes/WidgetPickerDialog.tsx", "28rem", 1], ["GlobalDialogs.tsx", "24rem", 3],
])("390 px: %s begrenzt die Dialogbreite", (file, width, count) => {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  expect(source.split(`max-w-[min(100vw-2rem,${width})]`)).toHaveLength(count + 1);
  expect(source).not.toContain("w-[calc(100%-2rem)]");
});
