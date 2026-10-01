import * as actions from "./actions";
import * as chart from "./chart";
import * as gauge from "./gauge";
import * as list from "./list";
import * as log from "./log";
import * as markdown from "./markdown";
import * as statFixture from "./stat";
import * as statusGrid from "./status_grid";
import * as table from "./table";

/**
 * Ein Eintrag pro View-Kind (docs/02-EXTENSION-API.md §4 nennt genau diese neun) --
 * WidgetRenderers.test.tsx iteriert diese Liste, damit ein zehntes Widget-Kind ohne
 * Fixture beim Test sofort auffaellt (Laenge der Liste wird mitgeprueft).
 */
export const fixtures = [
  { name: "stat", ...statFixture },
  { name: "list", ...list },
  { name: "table", ...table },
  { name: "chart", ...chart },
  { name: "status_grid", ...statusGrid },
  { name: "gauge", ...gauge },
  { name: "markdown", ...markdown, expectNotContains: markdown.expectNotContains },
  { name: "actions", ...actions },
  { name: "log", ...log },
] as const;

export const VIEW_KIND_COUNT = 9;
