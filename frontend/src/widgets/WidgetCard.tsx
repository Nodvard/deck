import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, GripVertical } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Icon } from "../components/Icon";
import { usePages } from "../lib/catalog";

import { useWsSubscription } from "../lib/ws";
import { MIN_ROWS, MORE_LINE_PX, capRows, fitCount, rowsToPx, stageFor } from "./fit";
import { widgetDataQuery } from "./query";
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
/** Zahl und Tacho stehen mittig in der Karte; alles andere beginnt oben. */
const CENTERED_KINDS = new Set(["stat", "gauge"]);

/** Gemessene Stufe für einen Stand (Daten und Breite); `limit` kürzt Listen, die nicht in die größte Stufe passen. */
interface Fit {
  key: string;
  rows: number;
  limit: number | null;
}

/**
 * Eine Karte pro Widget: lädt `data_endpoint`, bleibt per `refresh` aktuell und zeigt eine unerwartete Form als Fehler
 * der Erweiterung. Die Höhe misst sie selbst (Stufen in `fit.ts`) und meldet sie über `onRows`; was nicht passt, endet
 * mit „+ x weitere“.
 */
export function WidgetCard({
  widget, onRows, draggable = false, fixedRows = null, edit,
}: {
  widget: WidgetOut;
  onRows?: (rows: number) => void;
  /** Ein Griff im Kopf zum Verschieben im Raster. */
  draggable?: boolean;
  /** Gewählte Höhe in Rasterzeilen; sie ist dann die einzige Stufe. */
  fixedRows?: number | null;
  /** Steuerung im Bearbeiten-Modus: Der Kopf führt nicht weg, der Inhalt nimmt weder Klicks noch Tastatur an. */
  edit?: ReactNode;
}) {
  const queryClient = useQueryClient();
  const query = widgetDataQuery(widget);
  const { queryKey } = query;

  const { data: response, error, isLoading, dataUpdatedAt, errorUpdatedAt } = useQuery(query);

  useWsSubscription(widget.refresh.ws_channel, () => {
    void queryClient.invalidateQueries({ queryKey });
  });

  function refetch() {
    void queryClient.invalidateQueries({ queryKey });
  }

  // Der Kopf führt zur Seite derselben Extension (die mit der kleinsten nav_order), generisch über den Seitenkatalog,
  // der Kern kennt keine Extension namentlich.
  const { data: pages } = usePages();
  const page = (pages ?? [])
    .filter((p) => p.ext_id === widget.ext_id && p.show_in_nav !== false)
    .sort((a, b) => a.nav_order - b.nav_order)[0];
  // Im Bearbeiten-Modus führt nichts von der Seite weg.
  const pageHref = page && !edit ? `/ext/${page.ext_id}${page.path}` : null;

  const cardRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [fit, setFit] = useState<Fit | null>(null);
  const minRows = MIN_ROWS[widget.view.kind] ?? 1;
  const maxRows = capRows(minRows, fixedRows);
  const editing = edit !== undefined;
  // Neue Daten, eine andere Breite oder Höhe, die Steuerung: Der alte Zuschnitt gilt nicht mehr, gemessen wird wieder
  // der ganze Inhalt.
  const fitKey = `${dataUpdatedAt}:${errorUpdatedAt}:${width}:${maxRows}:${editing}`;
  const limit = fit?.key === fitKey ? fit.limit : null;
  const settled = Boolean(response || error);
  const fitRef = useRef(fit);
  fitRef.current = fit;

  const measure = useCallback(() => {
    // Schon gekürzt: nicht nachmessen, die gekürzte Liste ist kürzer und die Karte wechselte sonst zwischen gekürzt
    // und ungekürzt. Neue Daten oder eine andere Breite ändern `fitKey`, dann zählt der ganze Inhalt wieder.
    if (fitRef.current?.key === fitKey && fitRef.current.limit !== null) return;
    const card = cardRef.current;
    const body = bodyRef.current;
    const content = contentRef.current;
    // Ohne Layout (verborgen, jsdom) ist alles 0: dann nicht messen, die Startseite nimmt ihre Vorgabe.
    if (!settled || !card || !body || !content || card.offsetHeight === 0) return;
    const chrome = card.offsetHeight - body.clientHeight;
    const needed = chrome + content.offsetHeight;
    let next: Fit;
    const stage = stageFor(needed, minRows, fixedRows);
    if (stage !== null) {
      next = { key: fitKey, rows: stage, limit: null };
    } else {
      // Zu lang für die größte Stufe: so viele ganze Zeilen, wie über „+ x weitere“ Platz haben. Ohne Zeilen (Text,
      // Diagramm) bleibt die Karte in dieser Stufe und scrollt.
      const rowEls = [...content.querySelectorAll<HTMLElement>("[data-fit-row]")];
      const top = content.getBoundingClientRect().top;
      const padBottom = Number.parseFloat(getComputedStyle(content).paddingBottom) || 0;
      const available = rowsToPx(maxRows) - chrome - padBottom - MORE_LINE_PX;
      const rowLimit = rowEls.length ? fitCount(rowEls.map((el) => el.getBoundingClientRect().bottom - top), available) : null;
      next = { key: fitKey, rows: maxRows, limit: rowLimit };
    }
    setFit((prev) => (prev && prev.key === next.key && prev.rows === next.rows && prev.limit === next.limit ? prev : next));
  }, [minRows, maxRows, fixedRows, settled, fitKey]);

  // Vor dem Zeichnen messen, damit die ungekürzte Liste nie zu sehen ist.
  useLayoutEffect(() => {
    measure();
  });

  // Breite der Karte beobachten; Inhalt, der später wächst (Bilder, Schrift), löst ebenfalls ein Nachmessen aus.
  const measureRef = useRef(measure);
  measureRef.current = measure;
  useEffect(() => {
    const card = cardRef.current;
    const content = contentRef.current;
    if (!card || !content || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.target === card) setWidth(Math.round(entry.contentRect.width));
      }
      measureRef.current();
    });
    observer.observe(card);
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  // `inert` statt nur `pointer-events-none`: Sonst löste Tab und Enter einen Knopf im Inhalt doch aus.
  useEffect(() => {
    bodyRef.current?.toggleAttribute("inert", editing);
  }, [editing]);

  const rows = fit?.rows ?? null;
  const onRowsRef = useRef(onRows);
  onRowsRef.current = onRows;
  useLayoutEffect(() => {
    if (rows !== null) onRowsRef.current?.(rows);
  }, [rows]);

  const total = response && Array.isArray(response.data) ? response.data.length : 0;
  const maxItems = widget.view.kind === "list" ? widget.view.max_items : null;
  const hiddenCount = limit !== null ? Math.min(total, maxItems ?? total) - limit : 0;

  const titleRow = (
    <>
      <span className="grid h-6 w-6 flex-none place-items-center rounded-md bg-white/[0.06] text-white/55">
        <Icon name={widget.icon} size={13} />
      </span>
      <h3 className="min-w-0 flex-1 break-words text-[13px] font-semibold" title={widget.title}>{widget.title}</h3>
    </>
  );

  return (
    <div ref={cardRef} className="panel flex h-full flex-col overflow-hidden" data-testid={`widget-${widget.ext_id}-${widget.id}`}>
      <div className="flex items-center gap-1 border-b border-white/[0.06] px-4 py-2.5">
        {pageHref ? (
          <Link
            to={pageHref}
            className="group/head flex min-w-0 flex-1 items-center gap-2 rounded-md hover:text-white"
            aria-label={`${widget.title} öffnen`}
          >
            {titleRow}
            <ArrowUpRight
              size={13}
              className="flex-none text-white/45 opacity-0 transition-opacity group-hover/head:opacity-100 group-focus-visible/head:opacity-100"
              aria-hidden="true"
            />
          </Link>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2">{titleRow}</div>
        )}
        {draggable && (
          <span
            className="widget-drag-handle grid h-6 w-5 flex-none cursor-grab place-items-center rounded text-white/60 hover:bg-white/5 hover:text-white active:cursor-grabbing"
            title="Zum Verschieben ziehen"
            aria-hidden
          >
            <GripVertical size={14} />
          </span>
        )}
      </div>
      {edit}
      <div ref={bodyRef} className={`widget-body flex min-h-0 flex-1 flex-col overflow-y-auto ${editing ? "pointer-events-none" : ""}`}>
        {/* `my-auto` statt `justify-center`: Die Höhe des Inhalts bleibt messbar, und was nicht passt, beginnt oben. */}
        <div ref={contentRef} className={`px-4 py-2.5 ${CENTERED_KINDS.has(widget.view.kind) ? "my-auto" : ""}`}>
          {isLoading && (
            <div className="space-y-2" aria-label="Lade …">
              <div className="h-3 w-3/4 animate-pulse rounded bg-white/10" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-white/10" />
              <div className="h-3 w-2/3 animate-pulse rounded bg-white/10" />
            </div>
          )}
          {error && (
            <p className="text-sm text-red-400">
              Fehler im Modul: {error instanceof Error ? error.message : String(error)}
            </p>
          )}
          {response && renderBody(widget, response)}
          {hiddenCount > 0 &&
            (pageHref ? (
              <Link to={pageHref} className="mt-2 block text-[12px] leading-5 text-white/50 hover:text-white" data-testid="widget-more">
                + {hiddenCount} weitere
              </Link>
            ) : (
              <p className="mt-2 text-[12px] leading-5 text-white/50" data-testid="widget-more">
                + {hiddenCount} weitere
              </p>
            ))}
        </div>
      </div>
    </div>
  );

  function renderBody(spec: WidgetOut, res: WidgetDataResponse) {
    if (typeof res !== "object" || res === null || !("data" in res)) {
      return <p className="text-sm text-red-400">Fehler im Modul: Die Antwort enthält keine Daten.</p>;
    }
    const { data } = res;
    const expectsArray = ARRAY_KINDS.has(spec.view.kind);
    if (expectsArray && !Array.isArray(data)) {
      return (
        <p className="text-sm text-red-400">
          Fehler im Modul: "{spec.view.kind}" erwartet ein Array in "data", bekam {typeof data}.
        </p>
      );
    }
    if (!expectsArray && Array.isArray(data)) {
      return (
        <p className="text-sm text-red-400">
          Fehler im Modul: "{spec.view.kind}" erwartet ein Objekt in "data", bekam ein Array.
        </p>
      );
    }

    switch (spec.view.kind) {
      case "stat":
        return <StatView view={spec.view} data={data} />;
      case "list":
        return <ListView view={spec.view} data={data} extId={spec.ext_id} onAction={refetch} limit={limit} />;
      case "table":
        return <TableView view={spec.view} data={data} extId={spec.ext_id} onAction={refetch} limit={limit} />;
      case "chart":
        return <ChartView view={spec.view} data={data} />;
      case "status_grid":
        return <StatusGridView view={spec.view} data={data} limit={limit} />;
      case "gauge":
        return <GaugeView view={spec.view} data={data} />;
      case "markdown":
        return <MarkdownView view={spec.view} data={data} />;
      case "actions":
        return <ActionsView view={spec.view} data={data} extId={spec.ext_id} onAction={refetch} />;
      case "log":
        return <LogView view={spec.view} data={data} />;
      default:
        return <p className="text-sm text-red-400">Fehler im Modul: unbekannte Anzeigeart.</p>;
    }
  }
}
