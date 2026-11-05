/**
 * Tabellen unter `sm` als gestapelte Karten: dasselbe `<table>`, Beschriftungen per CSS aus `data-label`, ab `sm`
 * unverändert. Zellen ohne Beschriftung (Titel, Knöpfe) nehmen `CARD_CELL_PLAIN`, leere fallen auf dem Handy weg.
 */

export const CARD_TABLE = "block w-full text-sm sm:table";
export const CARD_HEAD = "hidden sm:table-header-group";
export const CARD_BODY = "block sm:table-row-group sm:divide-y sm:divide-white/5";
export const CARD_ROW =
  "relative mb-3 flex flex-col rounded-lg border border-white/10 p-3 sm:static sm:mb-0 sm:table-row sm:rounded-none sm:border-0 sm:p-0";
export const CARD_CELL_PLAIN = "block empty:hidden sm:table-cell sm:empty:table-cell";
export const CARD_CELL = `${CARD_CELL_PLAIN} before:mb-0.5 before:block before:text-xs before:opacity-60 before:content-[attr(data-label)] sm:before:hidden`;
