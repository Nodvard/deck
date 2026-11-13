import type { View } from "../types";

import * as actions from "./actions";
import * as chart from "./chart";
import * as gauge from "./gauge";
import * as list from "./list";
import * as log from "./log";
import * as markdown from "./markdown";
import * as statFixture from "./stat";
import * as statusGrid from "./status_grid";
import * as table from "./table";

type FixturesByKind = {
  [Kind in View["kind"]]: {
    name: Kind;
    view: Extract<View, { kind: Kind }>;
    data: unknown;
    expectContains: readonly string[];
    expectNotContains?: readonly string[];
  };
};

const fixturesByKind = {
  stat: { name: "stat", ...statFixture },
  list: { name: "list", ...list },
  table: { name: "table", ...table },
  chart: { name: "chart", ...chart },
  status_grid: { name: "status_grid", ...statusGrid },
  gauge: { name: "gauge", ...gauge },
  markdown: { name: "markdown", ...markdown, expectNotContains: markdown.expectNotContains },
  actions: { name: "actions", ...actions },
  log: { name: "log", ...log },
} satisfies FixturesByKind;

export const fixtures = Object.values(fixturesByKind);
