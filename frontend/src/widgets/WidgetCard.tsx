import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import { Icon } from "../components/Icon";
import { usePages } from "../lib/catalog";

import { api } from "../lib/api";
import { useWsSubscription } from "../lib/ws";
import type { WidgetDataResponse, WidgetOut } from "./types";
import {
  ActionsView,
  ChartView,
  GaugeView,
  ListView,
  LogView,
  MarkdownView,
  StatView,
  StatusGridView,
  TableView,
} from "./views";

const ARRAY_KINDS = new Set(["list", "table", "chart", "status_grid"]);

function queryKeyFor(widget: WidgetOut): [string, string, string] {
  return ["widget-data", widget.ext_id, widget.data_endpoint];
}

/**
 * Eine Karte pro Widget: holt `data_endpoint` (docs/02-EXTENSION-API.md §4:
 * `{data, meta}`), haelt sich per `refresh.interval_s` und/oder `refresh.ws_channel`
 * aktuell, und validiert die Form GEGEN DEN VIEW-TYP defensiv -- eine Formabweichung
 * wird als Extension-Fehler angezeigt statt als kaputtes/leeres UI (docs/02 §4).
 */
export function WidgetCard({ widget }: { widget: WidgetOut }) {
  const queryClient = useQueryClient();
  const queryKey = queryKeyFor(widget);

  const { data: response, error, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<WidgetDataResponse>(`/ext/${widget.ext_id}/${widget.data_endpoint}`),
    refetchInterval: widget.refresh.interval_s ? widget.refresh.interval_s * 1000 : false,
  });

  useWsSubscription(widget.refresh.ws_channel, () => {
    void queryClient.invalidateQueries({ queryKey });
  });

  function refetch() {
    void queryClient.invalidateQueries({ queryKey });
  }

  // "Oeffnen" fuehrt zur Seite derselben Extension (die mit der kleinsten nav_order) --
  // generisch ueber den Seitenkatalog, der Kern kennt keine Extension namentlich.
  const { data: pages } = usePages();
  const page = (pages ?? [])
    .filter((p) => p.ext_id === widget.ext_id && p.show_in_nav !== false)
    .sort((a, b) => a.nav_order - b.nav_order)[0];

  return (
    <div className="panel flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-2.5">
        <span className="accent-soft grid h-6 w-6 flex-none place-items-center rounded-md">
          <Icon name={widget.icon} size={13} />
        </span>
        <h3 className="min-w-0 flex-1 break-words text-[13px] font-semibold" title={widget.title}>{widget.title}</h3>
        {page && (
          <Link
            to={`/ext/${page.ext_id}${page.path}`}
            className="flex flex-none items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[11px] text-white/45 hover:bg-white/5 hover:text-white"
            aria-label={`${widget.title} öffnen`}
          >
            Öffnen <ArrowUpRight size={12} />
          </Link>
        )}
      </div>
      <div className="widget-body min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {isLoading && (
          <div className="space-y-2" aria-label="Lade …">
            <div className="h-3 w-3/4 animate-pulse rounded bg-white/10" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-white/10" />
            <div className="h-3 w-2/3 animate-pulse rounded bg-white/10" />
          </div>
        )}
        {error && (
          <p className="text-sm text-red-400">
            Extension-Fehler: {error instanceof Error ? error.message : String(error)}
          </p>
        )}
        {response && renderBody(widget, response)}
      </div>
    </div>
  );

  function renderBody(spec: WidgetOut, res: WidgetDataResponse) {
    if (typeof res !== "object" || res === null || !("data" in res)) {
      return <p className="text-sm text-red-400">Extension-Fehler: Antwort hat keine "data"-Eigenschaft.</p>;
    }
    const { data } = res;
    const expectsArray = ARRAY_KINDS.has(spec.view.kind);
    if (expectsArray && !Array.isArray(data)) {
      return (
        <p className="text-sm text-red-400">
          Extension-Fehler: "{spec.view.kind}" erwartet ein Array in "data", bekam {typeof data}.
        </p>
      );
    }
    if (!expectsArray && Array.isArray(data)) {
      return (
        <p className="text-sm text-red-400">
          Extension-Fehler: "{spec.view.kind}" erwartet ein Objekt in "data", bekam ein Array.
        </p>
      );
    }

    switch (spec.view.kind) {
      case "stat":
        return <StatView view={spec.view} data={data} />;
      case "list":
        return <ListView view={spec.view} data={data} extId={spec.ext_id} onAction={refetch} />;
      case "table":
        return <TableView view={spec.view} data={data} extId={spec.ext_id} onAction={refetch} />;
      case "chart":
        return <ChartView view={spec.view} data={data} />;
      case "status_grid":
        return <StatusGridView view={spec.view} data={data} />;
      case "gauge":
        return <GaugeView view={spec.view} data={data} />;
      case "markdown":
        return <MarkdownView view={spec.view} data={data} />;
      case "actions":
        return <ActionsView view={spec.view} data={data} extId={spec.ext_id} onAction={refetch} />;
      case "log":
        return <LogView view={spec.view} data={data} />;
      default:
        return <p className="text-sm text-red-400">Extension-Fehler: unbekannter View-Typ.</p>;
    }
  }
}
