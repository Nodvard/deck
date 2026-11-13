import { expect } from "vitest";

export async function mobileLayout(element: Element, textSelector?: string) {
  const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env;
  if (!env?.DECK_MOBILE_BROWSER) return;
  const name = "playwright";
  const { chromium } = await import(/* @vite-ignore */ name);
  const { default: postcss } = await import("postcss");
  const { default: tailwindcss } = await import("tailwindcss");
  const html = element.outerHTML;
  const { css } = await postcss([tailwindcss({ content: [{ raw: html }], theme: {}, plugins: [] })]).process("@tailwind base; @tailwind utilities;", { from: undefined });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await page.setContent(`<style>${css}</style><main style="padding:16px">${html}</main>`);
    const mobile = await page.evaluate(() => {
      const main = document.querySelector("main")!;
      const tables = [...main.querySelectorAll("table")];
      const rows = tables.flatMap(table => [...table.querySelectorAll("tbody > tr")]);
      return {
        overflow: document.documentElement.scrollWidth > 390,
        tables: tables.map(table => getComputedStyle(table).display),
        rows: rows.map(row => getComputedStyle(row).display),
        labels: [...main.querySelectorAll("td[data-label]")].filter(cell => getComputedStyle(cell).display !== "none")
          .map(cell => [cell.getAttribute("data-label"), getComputedStyle(cell, "::before").content.replace(/^"|"$/g, "")]),
        dialogs: [...main.querySelectorAll('[role="dialog"]')].map(dialog => {
          const box = dialog.getBoundingClientRect();
          return box.left >= 15 && box.right <= 375;
        }),
      };
    });
    expect(mobile.overflow).toBe(false);
    expect(mobile.tables.every((display: string) => display === "block")).toBe(true);
    expect(mobile.rows.every((display: string) => display === "flex" || display === "block")).toBe(true);
    expect(mobile.labels.every(([label, content]: string[]) => label === content)).toBe(true);
    expect(mobile.dialogs.every(Boolean)).toBe(true);
    if (textSelector) {
      const textBounds = await page.locator(textSelector).evaluateAll((elements: Element[]) => elements.map(element => {
        const cell = element.closest("td")!;
        const box = cell.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(element);
        return {
          text: element.textContent,
          fits: [...range.getClientRects()].every(rect => rect.left >= box.left - 1 && rect.right <= box.right + 1),
        };
      }));
      expect(textBounds.length).toBeGreaterThan(0);
      expect(textBounds.filter((entry: { fits: boolean }) => !entry.fits)).toEqual([]);
    }
    await page.setViewportSize({ width: 1024, height: 844 });
    expect(await page.locator("table").evaluateAll((tables: Element[]) => tables.every(table => getComputedStyle(table).display === "table"))).toBe(true);
  } finally {
    await browser.close();
  }
}

export async function cardTable(table: Element, labels: string[]) {
  expect(table.classList).toContain("sm:table");
  const rows = [...table.querySelectorAll("tbody > tr")].filter(row => row.querySelector("td"));
  expect(rows.length).toBeGreaterThan(0);
  for (const row of rows) {
    expect(row.classList).toContain("flex-col");
    expect(row.classList).toContain("sm:table-row");
  }
  for (const label of labels) expect(table.querySelector(`td[data-label="${label}"]`)).not.toBeNull();
  await mobileLayout(table);
}
