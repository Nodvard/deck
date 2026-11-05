import { findDeck } from "../../../_shared/frontend/src/deck";

export function productText(text: string): string {
  if (!text || findDeck()?.getProductName?.() !== "Nodvard Dock") return text;
  return text.replace(/Nodvard Deck/g, "Nodvard Dock").replace(/\bDeck\b/g, "Dock");
}
