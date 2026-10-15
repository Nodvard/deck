/**
 * Inventar-Seite (Inventar-Modul im Stil von Homebox) -- PageSpec
 * `component="InventoryPage"` (siehe nodvard_deck_ext_inventory/__init__.py). Ruft die eigenen
 * `/ext/inventory/...`-Endpunkte auf, dazu `GET /hosts` (Auswahl „Gehört zu Server“).
 *
 * Authentifizierung wie jede andere Extension-Seite: `window.__nodvardDeck.
 * getAccessToken()` (kein Zugriff auf state/auth.ts ueber den Import-Map-Shim).
 */
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { AuthImage } from "../../../_shared/frontend/src/AuthImage";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Badge, Button, Card, EmptyState, Field, Icon, Loading, Notice, Page, SearchInput, Stat, buttonClass, inputClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";
import { MoreMenu } from "../../../_shared/frontend/src/menu";

import { type ImportResult, type ImportRow, MAX_IMPORT_BYTES, decodeCsv, rowsFromCsv } from "./importCsv";

interface Category {
  id: string;
  name: string;
}

interface Location {
  id: string;
  name: string;
  parent_id: string | null;
}

interface ItemImage {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  url: string;
}

interface Item {
  id: string;
  name: string;
  description: string | null;
  category_id: string | null;
  location_id: string | null;
  quantity: number;
  purchase_date: string | null;
  purchase_price_cents: number | null;
  warranty_until: string | null;
  notes: string | null;
  images: ItemImage[];
  manufacturer?: string | null;
  model?: string | null;
  serial_number?: string | null;
  product_url?: string | null;
  host_id?: string | null;
  project_id?: string | null;
  rack_id?: string | null;
  rack_position?: number | null;
  rack_units?: number | null;
}

interface Rack {
  id: string;
  name: string;
  height_units: number;
  location_id: string | null;
  notes: string | null;
}

/**
 * Vollständiger Datensatz für `PUT /items/{id}`: die ursprünglichen Felder ersetzt der Server immer (fehlt eins, ist
 * es danach leer), darum gehen sie alle mit. Von den neuen Feldern nur die geänderten -- die übrigen lässt der
 * Server dann, wie sie sind.
 */
export function itemPayload(item: Item, changes: Partial<Item> = {}): Record<string, unknown> {
  const i = { ...item, ...changes };
  return {
    name: i.name, description: i.description, category_id: i.category_id, location_id: i.location_id, quantity: i.quantity,
    purchase_date: i.purchase_date, purchase_price_cents: i.purchase_price_cents, warranty_until: i.warranty_until, notes: i.notes,
    ...Object.fromEntries(Object.keys(changes).map((key) => [key, (i as unknown as Record<string, unknown>)[key] ?? null])),
  };
}

/** Belegung eines Racks von oben nach unten: je Zeile ein Gerät (über seine Höhe) oder eine freie Höheneinheit. */
export function rackRows(height: number, items: Item[]): ({ kind: "device"; top: number; bottom: number; item: Item } | { kind: "free"; unit: number })[] {
  const rows: ({ kind: "device"; top: number; bottom: number; item: Item } | { kind: "free"; unit: number })[] = [];
  let unit = height;
  while (unit >= 1) {
    const item = items.find((i) => i.rack_position != null && i.rack_position <= unit && unit <= i.rack_position + (i.rack_units ?? 1) - 1);
    if (item) {
      const bottom = item.rack_position as number;
      rows.push({ kind: "device", top: unit, bottom, item });
      unit = bottom - 1;
    } else {
      rows.push({ kind: "free", unit });
      unit -= 1;
    }
  }
  return rows;
}

type ProjectStatus = "planned" | "building" | "done";

interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  description: string | null;
  budget_cents: number | null;
  item_count: number;
  spent_cents: number;
  /** Teile ohne Kaufpreis (fehlt bei älteren Servern). */
  unpriced_count?: number;
}

interface HostRef {
  id: string;
  name?: string;
  display_name: string;
  os_family?: string;
  /** Zugang des Servers (Kern: `credential`), `null` = kein SSH-Zugang hinterlegt. */
  credential?: unknown;
}

interface ScanSuggestion {
  key: string;
  kind: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  quantity: number;
  /** Kommt als Beschreibung ins Inventar ("8 Kerne", "2 Anschlüsse"). */
  detail: string | null;
  /** Nur für die Vorschau ("/dev/sda", „kann auch auf dem Mainboard sitzen“). */
  hint?: string | null;
  /** Vorab ankreuzen? Nein bei Karten (oft auf dem Mainboard) und auf VMs/Containern. */
  preselect: boolean;
}

interface ScanResult {
  host_id: string;
  host_name: string;
  virtual: boolean;
  /** Programme, die auf dem Server fehlen (lspci, lsblk, lscpu): deren Teile fehlen in der Liste. */
  missing?: string[];
  suggestions: ScanSuggestion[];
}

const PROJECT_STATUS: Record<ProjectStatus, { label: string; tone: Tone }> = {
  planned: { label: "geplant", tone: "neutral" },
  building: { label: "im Bau", tone: "info" },
  done: { label: "fertig", tone: "good" },
};

/** Suchbegriff für den Preisvergleich: Hersteller + Modell, sonst der Name. */
export function priceQuery(item: Pick<Item, "name" | "manufacturer" | "model">): string {
  const exact = [item.manufacturer, item.model].map((v) => v?.trim()).filter(Boolean).join(" ");
  return item.model?.trim() ? exact : item.name.trim();
}

/** Such-Links zu Preisvergleichen. Nodvard Deck liest diese Seiten nicht aus, es öffnet nur die Suche. */
export function priceLinks(item: Pick<Item, "name" | "manufacturer" | "model">): { label: string; href: string }[] {
  const q = encodeURIComponent(priceQuery(item));
  return [
    { label: "Geizhals", href: `https://geizhals.de/?fs=${q}` },
    { label: "idealo", href: `https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=${q}` },
  ];
}

type WarrantyFilter = "" | "active" | "soon" | "expired" | "none";

const DAY_MS = 86_400_000;

/** Wie `inputClass`, aber ohne volle Breite: für Felder in einer Zeile (sonst gewinnt `w-full` gegen `w-auto`). */
const inlineInput = inputClass.replace("w-full ", "");

/** Garantie-Zustand relativ zu `today`: laeuft, laeuft in <= 90 Tagen ab, abgelaufen, keine.
 * Die 90 Tage gelten auch fuer die Dashboard-Kachel (`_WARRANTY_SOON_DAYS` im Backend). */
export function warrantyState(until: string | null, today = new Date()): Exclude<WarrantyFilter, ""> {
  if (!until) return "none";
  const days = (new Date(`${until}T00:00:00`).getTime() - new Date(today.toDateString()).getTime()) / DAY_MS;
  if (days < 0) return "expired";
  return days <= 90 ? "soon" : "active";
}

function csvCell(value: string | number | null | undefined): string {
  let text = value == null ? "" : String(value);
  // Text, der mit = + - @ beginnt, hielte Excel für eine Formel: ein Apostroph davor macht ihn zu Text. Zahlen
  // (Menge, Preis) bleiben, wie sie sind; beim Einlesen fällt der Apostroph wieder weg.
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** CSV fuer Excel/LibreOffice (Semikolon, UTF-8 mit BOM, Komma als Dezimaltrenner). */
export function inventoryCsv(items: Item[], categoryName: (id: string | null) => string, locationName: (id: string | null) => string): string {
  const header = [
    "Name", "Kategorie", "Standort", "Menge", "Kaufdatum", "Preis (EUR)", "Garantie bis", "Beschreibung", "Notizen",
    "Hersteller", "Modell", "Seriennummer", "Produktlink",
  ];
  const rows = items.map((i) => [
    i.name, categoryName(i.category_id), locationName(i.location_id), i.quantity, i.purchase_date,
    i.purchase_price_cents == null ? "" : (i.purchase_price_cents / 100).toFixed(2).replace(".", ","),
    i.warranty_until, i.description, i.notes, i.manufacturer, i.model, i.serial_number, i.product_url,
  ].map(csvCell).join(";"));
  return "\ufeff" + [header.join(";"), ...rows].join("\r\n");
}

const EMPTY_FORM = {
  name: "", description: "", category_id: "", location_id: "", quantity: "1",
  purchase_date: "", purchase_price: "", warranty_until: "", notes: "",
  manufacturer: "", model: "", serial_number: "", product_url: "", host_id: "", project_id: "",
};

function formatPrice(cents: number | null): string {
  if (cents === null) return "–";
  return (cents / 100).toLocaleString("de-DE", { style: "currency", currency: "EUR" });
}

const WARRANTY_BADGE: Record<Exclude<WarrantyFilter, "">, { tone: Tone; label: (d: string) => string }> = {
  active: { tone: "good", label: (d) => `Garantie bis ${formatDate(d)}` },
  soon: { tone: "warn", label: (d) => `Garantie endet ${formatDate(d)}` },
  expired: { tone: "bad", label: (d) => `Garantie abgelaufen ${formatDate(d)}` },
  none: { tone: "neutral", label: () => "" },
};

function formatDate(iso: string | null): string {
  if (!iso) return "–";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function TaxonomyPanel({
  categories, locations, onChanged,
}: {
  categories: Category[]; locations: Location[]; onChanged: () => void;
}): JSX.Element {
  const [newCategory, setNewCategory] = useState("");
  const [newLocation, setNewLocation] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function add(kind: "categories" | "locations", name: string, reset: () => void) {
    if (!name.trim()) return;
    const res = await authedFetch(`/ext/inventory/${kind}`, { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { setMessage(`Fehler: ${errorFromBody(body, res.status)}`); return; }
    reset();
    onChanged();
  }

  async function remove(kind: "categories" | "locations", id: string, what: string) {
    const ok = await deck().confirmDialog(`${what} wirklich entfernen?`, { danger: true });
    if (!ok) return;
    await authedFetch(`/ext/inventory/${kind}/${id}`, { method: "DELETE" });
    onChanged();
  }

  const column = (
    title: string, icon: "tag" | "map-pin", entries: { id: string; name: string }[], kind: "categories" | "locations",
    value: string, setValue: (v: string) => void, placeholder: string, what: string,
  ) => (
    <div>
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-white/45"><Icon name={icon} size={13} /> {title}</p>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {entries.map((e) => (
          <span key={e.id} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] py-1 pl-2.5 pr-1.5 text-sm">
            {e.name}
            <button type="button" aria-label={`${e.name} entfernen`} onClick={() => void remove(kind, e.id, what)} className="rounded p-0.5 text-white/40 hover:bg-white/10 hover:text-white">
              <Icon name="x" size={12} />
            </button>
          </span>
        ))}
        {entries.length === 0 && <span className="text-sm text-white/40">Noch keine.</span>}
      </div>
      <form onSubmit={(e: FormEvent) => { e.preventDefault(); void add(kind, value, () => setValue("")); }} className="flex gap-2">
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={placeholder} className={inputClass} />
        <Button type="submit" ariaLabel={`${what} hinzufügen`}><Icon name="plus" size={14} /></Button>
      </form>
    </div>
  );

  return (
    <Card title="Kategorien & Standorte" description="Zum Ordnen und Filtern. Entfernen lässt die Gegenstände selbst unverändert." className="mb-5">
      {message && <Notice text={message} onClose={() => setMessage(null)} />}
      <div className="grid gap-6 sm:grid-cols-2">
        {column("Kategorien", "tag", categories, "categories", newCategory, setNewCategory, "Neue Kategorie", "Kategorie")}
        {column("Standorte", "map-pin", locations, "locations", newLocation, setNewLocation, "Neuer Standort", "Standort")}
      </div>
    </Card>
  );
}

/**
 * Bauprojekte („NAS bauen“, „Rack aufräumen“): welche Teile dazugehören, was schon ausgegeben ist und ob das
 * Budget reicht. Ein Klick auf „Teile zeigen“ filtert die Liste unten.
 */
function ProjectsPanel({
  projects, onChanged, onShowItems, activeId,
}: {
  projects: Project[]; onChanged: () => void; onShowItems: (id: string) => void; activeId: string;
}): JSX.Element {
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function send(path: string, method: string, body?: unknown): Promise<boolean> {
    const res = await authedFetch(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!res.ok && res.status !== 204) {
      const data = await res.json().catch(() => ({}));
      setMessage(`Fehler: ${errorFromBody(data, res.status)}`);
      return false;
    }
    setMessage(null);
    onChanged();
    return true;
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const cents = budget ? Math.round(parseFloat(budget) * 100) : null;
    if (await send("/ext/inventory/projects", "POST", { name: name.trim(), budget_cents: cents })) {
      setName("");
      setBudget("");
    }
  }

  async function remove(project: Project) {
    const ok = await deck().confirmDialog(
      `Bauprojekt „${project.name}“ entfernen? Die Teile bleiben im Inventar, sie gehören dann nur zu keinem Projekt mehr.`,
      { danger: true, confirmLabel: "Entfernen" },
    );
    if (ok) await send(`/ext/inventory/projects/${project.id}`, "DELETE");
  }

  return (
    <Card title="Bauprojekte" description="Plane, was du bauen willst, und sieh, welche Teile dafür da sind und was es kostet." className="mb-5">
      {message && <Notice text={message} onClose={() => setMessage(null)} />}
      {projects.length === 0 && <p className="mb-3 text-sm text-white/45">Noch kein Bauprojekt.</p>}
      <ul className="mb-4 divide-y divide-white/[0.06]" data-testid="projects">
        {projects.map((p) => {
          const over = p.budget_cents != null && p.spent_cents > p.budget_cents;
          return (
            <li key={p.id} className="flex flex-wrap items-center gap-2 py-2 text-sm" data-testid={`project-${p.id}`}>
              <span className="font-medium">{p.name}</span>
              <Badge tone={PROJECT_STATUS[p.status]?.tone ?? "neutral"}>{PROJECT_STATUS[p.status]?.label ?? p.status}</Badge>
              <span className="text-white/55">
                {p.item_count === 1 ? "1 Teil" : `${p.item_count} Teile`} · {formatPrice(p.spent_cents)} ausgegeben
                {p.budget_cents != null && <> von {formatPrice(p.budget_cents)}</>}
                {!!p.unpriced_count && <> · {p.unpriced_count === 1 ? "1 Teil ohne Preis" : `${p.unpriced_count} Teile ohne Preis`}</>}
              </span>
              {over && <Badge tone="bad">über Budget</Badge>}
              <span className="flex-1" />
              <select
                value={p.status} aria-label={`Status von ${p.name}`} className={`${inlineInput} w-auto py-1 text-xs`}
                onChange={(e) => void send(`/ext/inventory/projects/${p.id}`, "PUT", {
                  name: p.name, status: e.target.value, description: p.description, budget_cents: p.budget_cents,
                })}
              >
                {(Object.keys(PROJECT_STATUS) as ProjectStatus[]).map((st) => <option key={st} value={st}>{PROJECT_STATUS[st].label}</option>)}
              </select>
              <Button small pressed={activeId === p.id} onClick={() => onShowItems(p.id)}>Teile zeigen</Button>
              <Button small variant="danger" ariaLabel={`${p.name} entfernen`} onClick={() => void remove(p)}><Icon name="trash" size={13} /></Button>
            </li>
          );
        })}
      </ul>
      <form onSubmit={(e) => void create(e)} className="flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Neues Bauprojekt, z. B. NAS bauen" aria-label="Name des Bauprojekts" className={`${inlineInput} min-w-[12rem] flex-1`} />
        <input value={budget} onChange={(e) => setBudget(e.target.value)} type="number" min="0" max="10000000" step="0.01" placeholder="Budget (€, optional)" aria-label="Budget in Euro" className={`${inlineInput} w-44`} />
        <Button type="submit" variant="primary" disabled={!name.trim()}><Icon name="plus" size={14} /> Anlegen</Button>
      </form>
    </Card>
  );
}

/**
 * Racks: Höheneinheiten (HE) von oben nach unten, Geräte über ihre Höhe. Ein Gerät setzt man in eine freie HE ein
 * (Platz und Höhe prüft der Server), „Herausnehmen“ lässt es im Inventar.
 */
function RacksPanel({ items, onChanged }: { items: Item[]; onChanged: () => void }): JSX.Element {
  const [racks, setRacks] = useState<Rack[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [height, setHeight] = useState("42");
  const [placing, setPlacing] = useState<number | null>(null);
  const [placeItem, setPlaceItem] = useState("");
  const [placeUnits, setPlaceUnits] = useState("1");
  // Belegung eigens laden: die Liste der Seite kann durch die Suche gefiltert sein, das Rack zeigt immer alles.
  const [rackItems, setRackItems] = useState<Item[] | null>(null);

  const loadRackItems = useCallback((rackId: string) => {
    if (!rackId) { setRackItems([]); return; }
    fetchJson<Item[]>(`/ext/inventory/items?rack_id=${encodeURIComponent(rackId)}`)
      .then(setRackItems)
      .catch((err: unknown) => setMessage(`Fehler: ${errorMessage(err)}`));
  }, []);
  // Auch nach jeder Änderung an der Liste der Seite (Gerät gelöscht oder umbenannt): sonst zeigte das Rack Altes.
  useEffect(() => { loadRackItems(selected); }, [selected, loadRackItems, items]);

  const loadRacks = useCallback(() => {
    setLoadError(null);
    fetchJson<Rack[]>("/ext/inventory/racks")
      .then((data) => {
        setRacks(data);
        setSelected((current) => (data.some((r) => r.id === current) ? current : data[0]?.id ?? ""));
      })
      .catch((err: unknown) => setLoadError(errorMessage(err)));
  }, []);
  useEffect(() => { loadRacks(); }, [loadRacks]);

  async function send(path: string, method: string, body?: unknown): Promise<boolean> {
    const res = await authedFetch(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!res.ok && res.status !== 204) {
      const data = await res.json().catch(() => ({}));
      setMessage(`Fehler: ${errorFromBody(data, res.status)}`);
      return false;
    }
    setMessage(null);
    return true;
  }

  async function createRack(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const res = await authedFetch("/ext/inventory/racks", {
      method: "POST", body: JSON.stringify({ name: name.trim(), height_units: parseInt(height, 10) || 42 }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setMessage(`Fehler: ${errorFromBody(data, res.status)}`); return; }
    setMessage(null);
    setName("");
    setSelected((data as Rack).id);
    loadRacks();
  }

  async function removeRack(rack: Rack) {
    const ok = await deck().confirmDialog(
      `Rack „${rack.name}“ entfernen? Die Geräte bleiben im Inventar, sie stecken dann nur in keinem Rack mehr.`,
      { danger: true, confirmLabel: "Entfernen" },
    );
    if (!ok) return;
    if (await send(`/ext/inventory/racks/${rack.id}`, "DELETE")) {
      loadRacks();
      onChanged();
    }
  }

  async function place(rack: Rack, unit: number) {
    const item = items.find((i) => i.id === placeItem);
    if (!item) return;
    const units = Math.max(1, parseInt(placeUnits, 10) || 1);
    // Die eingesetzte HE ist die oberste des Geräts: es reicht von dort nach unten.
    if (unit - units + 1 < 1) {
      setMessage(`Unter HE ${unit} ist nicht genug Platz für ${units} HE. Wähle eine kleinere Höhe oder eine höhere HE.`);
      return;
    }
    const changes = { rack_id: rack.id, rack_position: unit - units + 1, rack_units: units };
    if (await send(`/ext/inventory/items/${item.id}`, "PUT", itemPayload(item, changes))) {
      setPlacing(null);
      setPlaceItem("");
      setPlaceUnits("1");
      loadRackItems(rack.id);
      onChanged();
    }
  }

  async function takeOut(item: Item) {
    if (await send(`/ext/inventory/items/${item.id}`, "PUT", itemPayload(item, { rack_id: null, rack_position: null, rack_units: null }))) {
      loadRackItems(selected);
      onChanged();
    }
  }

  if (loadError) return <LoadError what="Die Liste der Racks" error={loadError} onRetry={loadRacks} />;
  if (!racks) return <Card title="Racks" className="mb-5"><Loading /></Card>;
  const rack = racks.find((r) => r.id === selected) ?? null;
  const inRack = rack ? (rackItems ?? []).filter((i) => i.rack_id === rack.id) : [];
  const unplaced = inRack.filter((i) => i.rack_position == null);
  const candidates = items.filter((i) => i.rack_id !== rack?.id);

  return (
    <Card title="Racks" description="Wo welches Gerät im Serverschrank steckt. Höheneinheiten (HE) von oben nach unten." className="mb-5">
      {message && <Notice text={message} onClose={() => setMessage(null)} />}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {racks.map((r) => (
          <Button key={r.id} small pressed={r.id === selected} onClick={() => setSelected(r.id)}>{r.name} · {r.height_units} HE</Button>
        ))}
        {racks.length === 0 && <span className="text-sm text-white/45">Noch kein Rack.</span>}
      </div>

      {rack && (
        <div className="mb-4">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-medium">{rack.name}</span>
            <span className="flex-1" />
            <Button small variant="danger" ariaLabel={`Rack ${rack.name} entfernen`} onClick={() => void removeRack(rack)}><Icon name="trash" size={13} /></Button>
          </div>
          <ol className="max-w-xl overflow-hidden rounded-lg border border-white/15 bg-black/25" data-testid={`rack-${rack.id}`}>
            {rackRows(rack.height_units, inRack).map((row) =>
              row.kind === "device" ? (
                <li
                  key={`d-${row.item.id}`}
                  className="flex items-center gap-2 border-b border-white/[0.06] bg-[color-mix(in_srgb,var(--color-accent)_14%,transparent)] px-2 text-xs"
                  style={{ minHeight: `${(row.top - row.bottom + 1) * 1.75}rem` }}
                  data-testid={`rack-device-${row.item.id}`}
                >
                  <span className="w-14 flex-none tabular-nums text-white/45">{row.top === row.bottom ? `HE ${row.top}` : `HE ${row.bottom}–${row.top}`}</span>
                  <span className="min-w-0 flex-1 truncate font-medium">{row.item.name}</span>
                  <Button small variant="ghost" onClick={() => void takeOut(row.item)}>Herausnehmen</Button>
                </li>
              ) : (
                <li key={`f-${row.unit}`} className="flex min-h-[1.75rem] flex-wrap items-center gap-2 border-b border-white/[0.04] px-2 text-xs text-white/35">
                  <span className="w-14 flex-none tabular-nums">HE {row.unit}</span>
                  {placing === row.unit ? (
                    <form onSubmit={(e) => { e.preventDefault(); void place(rack, row.unit); }} className="flex flex-1 flex-wrap items-center gap-1.5 py-1 text-white">
                      <select value={placeItem} onChange={(e) => setPlaceItem(e.target.value)} aria-label="Gerät" className={`${inlineInput} w-auto flex-1 py-1 text-xs`}>
                        <option value="">Gerät wählen …</option>
                        {candidates.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                      </select>
                      <label className="flex items-center gap-1 text-white/60">
                        Höhe
                        <input value={placeUnits} onChange={(e) => setPlaceUnits(e.target.value)} type="number" min="1" max={rack.height_units} aria-label="Höhe in HE" className={`${inlineInput} w-16 py-1 text-xs`} />
                        HE
                      </label>
                      <Button small type="submit" variant="primary" disabled={!placeItem}>Einsetzen</Button>
                      <Button small variant="ghost" onClick={() => setPlacing(null)}>Abbrechen</Button>
                    </form>
                  ) : (
                    <>
                      <span className="flex-1">frei</span>
                      <button type="button" onClick={() => { setPlacing(row.unit); setPlaceItem(""); setPlaceUnits("1"); }} className="rounded px-1.5 py-0.5 text-white/50 hover:bg-white/[0.06] hover:text-white" aria-label={`Gerät in HE ${row.unit} einsetzen`} title="Gerät hier einsetzen">
                        <Icon name="plus" size={12} />
                      </button>
                    </>
                  )}
                </li>
              ),
            )}
          </ol>
          {unplaced.length > 0 && (
            <p className="mt-2 text-xs text-white/55">Im Rack, aber ohne festen Platz: {unplaced.map((i) => i.name).join(", ")}</p>
          )}
        </div>
      )}

      <form onSubmit={(e) => void createRack(e)} className="flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Neues Rack, z. B. Keller" aria-label="Name des Racks" className={`${inlineInput} min-w-[12rem] flex-1`} />
        <select value={height} onChange={(e) => setHeight(e.target.value)} aria-label="Höhe des Racks" className={`${inlineInput} w-auto`}>
          {["6", "9", "12", "15", "18", "22", "27", "32", "42", "47"].map((h) => <option key={h} value={h}>{h} HE</option>)}
        </select>
        <Button type="submit" variant="primary" disabled={!name.trim()}><Icon name="plus" size={14} /> Anlegen</Button>
      </form>
    </Card>
  );
}

/** Ergebnis von `createItems`: was angelegt ist (Schlüssel) und was nicht (mit Grund). */
interface CreateOutcome<K> {
  created: K[];
  failed: { label: string; reason: string }[];
  /** Nach mehreren Fehlern in Folge aufgehört (Server weg, Anmeldung abgelaufen): so viele blieben unversucht. */
  stopped: boolean;
  untried: number;
}

/**
 * Legt Gegenstände nacheinander an. Scheitert einer, geht es mit dem nächsten weiter; nach fünf Fehlern in Folge
 * ist Schluss (dann klemmt etwas Grundsätzliches, der Rest würde genauso scheitern).
 */
async function createItems<K>(
  entries: { key: K; label: string; body: Record<string, unknown> }[], onProgress: (done: number) => void,
): Promise<CreateOutcome<K>> {
  const outcome: CreateOutcome<K> = { created: [], failed: [], stopped: false, untried: 0 };
  let inRow = 0;
  for (const [i, entry] of entries.entries()) {
    onProgress(i);
    try {
      const res = await authedFetch("/ext/inventory/items", { method: "POST", body: JSON.stringify(entry.body) });
      if (res.ok) {
        outcome.created.push(entry.key);
        inRow = 0;
        continue;
      }
      const body = await res.json().catch(() => ({}));
      outcome.failed.push({ label: entry.label, reason: errorFromBody(body, res.status) });
    } catch (err) {
      outcome.failed.push({ label: entry.label, reason: err instanceof Error ? err.message : String(err) });
    }
    inRow += 1;
    if (inRow >= 5 && i < entries.length - 1) {
      outcome.stopped = true;
      outcome.untried = entries.length - 1 - i;
      break;
    }
  }
  onProgress(entries.length);
  return outcome;
}

/** Fortschritt für Lesegeräte in Zehnerschritten: sonst käme bei 500 Einträgen 500-mal eine Ansage. */
function progressText(progress: [number, number] | null): string {
  if (!progress || progress[1] === 0) return "";
  return `Übernehme … ${Math.floor((progress[0] / progress[1]) * 10) * 10} %`;
}

/** Was beim Übernehmen nicht geklappt hat: je Eintrag der Grund. Was geklappt hat, ist abgehakt und bleibt es. */
function FailureList({ outcome, what, onClose }: { outcome: CreateOutcome<unknown>; what: [string, string]; onClose: () => void }): JSX.Element {
  const n = outcome.failed.length;
  return (
    <div role="alert" className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
      <div className="flex items-start justify-between gap-3">
        <p>
          {outcome.created.length > 0 && `${outcome.created.length} übernommen. `}
          {n === 1 ? `1 ${what[0]} ging nicht` : `${n} ${what[1]} gingen nicht`}
          {outcome.stopped ? ` – nach mehreren Fehlern in Folge abgebrochen, ${outcome.untried} nicht versucht` : ""}. Diese bleiben angekreuzt, du kannst es noch einmal versuchen.
        </p>
        <button type="button" aria-label="Hinweis schließen" onClick={onClose} className="opacity-60 hover:opacity-100"><Icon name="x" size={14} /></button>
      </div>
      <ul className="mt-1 list-disc pl-5">
        {outcome.failed.slice(0, 8).map((f, i) => <li key={i}>„{f.label}“: {f.reason}</li>)}
        {n > 8 && <li>… und {n - 8} weitere</li>}
      </ul>
    </div>
  );
}

const MISSING_TOOLS: Record<string, string> = {
  lspci: "lspci (Paket „pciutils“) fehlt: Grafik- und Netzwerkkarten stehen deshalb nicht in der Liste.",
  lsblk: "lsblk (Paket „util-linux“) fehlt: Datenträger stehen deshalb nicht in der Liste.",
  lscpu: "lscpu (Paket „util-linux“) fehlt: der Prozessor steht deshalb nicht in der Liste.",
};

/**
 * Teile eines Servers auslesen: Nodvard Deck fragt per SSH (nur lesend) Prozessor, Arbeitsspeicher, Datenträger,
 * Mainboard und Karten ab. Übernommen wird nur, was angekreuzt ist. Vorab angekreuzt ist, was sicher ein eigenes
 * Teil ist und noch nicht im Inventar steht (gleiche Seriennummer).
 */
function ScanPanel({
  hosts, categories, locations, projects, onDone,
}: {
  hosts: HostRef[]; categories: Category[]; locations: Location[]; projects: Project[]; onDone: (message: string) => void;
}): JSX.Element {
  const usable = (h: HostRef) => (h.os_family ?? "linux") === "linux" && h.credential !== null;
  const [hostId, setHostId] = useState(() => hosts.find(usable)?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  // Was schon im Inventar steht (aus der ganzen Liste, nicht nur der gerade gefilterten): Seriennummern, und für
  // Teile ohne Seriennummer (Prozessor, Arbeitsspeicher …) „gleicher Server + gleicher Name“. `null`: Liste nicht ladbar.
  const [known, setKnown] = useState<{ serials: ReadonlySet<string>; hostNames: ReadonlySet<string> } | null>(null);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set());
  const [taken, setTaken] = useState<ReadonlySet<string>>(() => new Set());
  const [categoryId, setCategoryId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [progress, setProgress] = useState<[number, number] | null>(null);
  const [outcome, setOutcome] = useState<CreateOutcome<string> | null>(null);

  const norm = (value: string | null | undefined) => (value ?? "").trim().toLowerCase();
  const isKnownIn = (k: typeof known, hostId: string, sg: ScanSuggestion) =>
    !!k && (sg.serial_number ? k.serials.has(norm(sg.serial_number)) : k.hostNames.has(`${hostId}\n${norm(sg.name)}`));
  const isKnown = (sg: ScanSuggestion) => !!result && isKnownIn(known, result.host_id, sg);

  async function scan() {
    setBusy(true);
    setError(null);
    setResult(null);
    setOutcome(null);
    setTaken(new Set());
    try {
      const [res, items] = await Promise.all([
        authedFetch(`/ext/inventory/hosts/${encodeURIComponent(hostId)}/scan`, { method: "POST" }),
        fetchJson<Item[]>("/ext/inventory/items").catch(() => null),
      ]);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const data = body as ScanResult;
      const found = items && {
        serials: new Set(items.map((i) => norm(i.serial_number)).filter(Boolean)),
        hostNames: new Set(items.filter((i) => i.host_id).map((i) => `${i.host_id}\n${norm(i.name)}`)),
      };
      setKnown(found);
      setResult(data);
      // Ohne Liste lässt sich nicht prüfen, was schon da ist: dann nichts vorab ankreuzen (sonst doppelt).
      setChosen(new Set(found ? data.suggestions.filter((sg) => sg.preselect && !isKnownIn(found, data.host_id, sg)).map((sg) => sg.key) : []));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function take() {
    if (!result) return;
    const picked = result.suggestions.filter((x) => chosen.has(x.key) && !taken.has(x.key));
    setOutcome(null);
    setProgress([0, picked.length]);
    const done = await createItems(
      picked.map((sg) => ({
        key: sg.key, label: sg.name,
        body: {
          name: sg.name, quantity: sg.quantity, description: sg.detail, manufacturer: sg.manufacturer, model: sg.model,
          serial_number: sg.serial_number, host_id: result.host_id, category_id: categoryId || null,
          location_id: locationId || null, project_id: projectId || null,
        },
      })),
      (n) => setProgress([n, picked.length]),
    );
    setProgress(null);
    // Angelegtes ist abgehakt und lässt sich nicht noch einmal übernehmen.
    setTaken((prev) => new Set([...prev, ...done.created]));
    setChosen((prev) => new Set([...prev].filter((key) => !done.created.includes(key))));
    const count = done.created.length;
    if (count) onDone(count === 1 ? "1 Teil übernommen." : `${count} Teile übernommen.`);
    if (done.failed.length) setOutcome(done);
    else setResult(null);
  }

  const toggle = (key: string) => setChosen((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const saving = progress !== null;
  const open = result ? result.suggestions.filter((sg) => chosen.has(sg.key) && !taken.has(sg.key)).length : 0;

  return (
    <Card
      title="Teile eines Servers auslesen"
      description="Nodvard Deck fragt den Server per SSH, was drinsteckt: Prozessor, Arbeitsspeicher, Datenträger, Mainboard, Netzwerk- und Grafikkarten. Es liest nur, es ändert nichts."
      className="mb-5"
    >
      {hosts.length === 0 ? (
        <p className="text-sm text-white/55">Noch kein Server in Nodvard Deck. Füge zuerst unter Einstellungen → Server &amp; Zugänge einen hinzu.</p>
      ) : (
        <div className="mb-3 flex flex-wrap gap-2">
          <select
            value={hostId} disabled={busy || saving} onChange={(e) => { setHostId(e.target.value); setResult(null); setOutcome(null); }}
            aria-label="Server zum Auslesen" className={`${inlineInput} min-w-[12rem]`}
          >
            <option value="">Server wählen …</option>
            {hosts.map((h) => (
              <option key={h.id} value={h.id} disabled={!usable(h)}>
                {h.display_name}{(h.os_family ?? "linux") !== "linux" ? " (nur Linux)" : h.credential === null ? " (ohne SSH-Zugang)" : ""}
              </option>
            ))}
          </select>
          <Button variant="primary" onClick={() => void scan()} disabled={!hostId || busy || saving}>
            {busy ? "Lese aus … (bis zu 30 Sekunden)" : "Auslesen"}
          </Button>
        </div>
      )}
      <p className="sr-only" role="status">{busy ? "Lese den Server aus …" : progressText(progress)}</p>
      {error && <Notice text={`Fehler: ${error}`} onClose={() => setError(null)} />}
      {result && (
        <div data-testid="scan-result">
          {result.virtual && (
            <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              „{result.host_name}“ ist eine virtuelle Maschine oder ein Container: Die Teile gehören dem Wirt (etwa dem
              Proxmox-Knoten) oder sind nur nachgebildet. Darum ist nichts vorab angekreuzt. Lies am besten den Wirt aus.
            </p>
          )}
          {!known && (
            <p className="mb-3 text-sm text-amber-200">
              Konnte nicht prüfen, was schon im Inventar steht. Darum ist nichts vorab angekreuzt – sieh die Liste durch.
            </p>
          )}
          {(result.missing ?? []).filter((tool) => MISSING_TOOLS[tool]).map((tool) => (
            <p key={tool} className="mb-3 text-sm text-white/55">{MISSING_TOOLS[tool]}</p>
          ))}
          {outcome && <FailureList outcome={outcome} what={["Teil", "Teile"]} onClose={() => setOutcome(null)} />}
          {result.suggestions.length === 0 ? (
            <p className="text-sm text-white/55">Nichts gefunden, was sich als Teil anbietet.</p>
          ) : (
            <>
              <ul className="mb-3 divide-y divide-white/[0.06]">
                {result.suggestions.map((sg) => {
                  const known = isKnown(sg);
                  const done = taken.has(sg.key);
                  return (
                    <li key={sg.key}>
                      <label className="flex cursor-pointer flex-wrap items-start gap-2 py-2 text-sm">
                        <input
                          type="checkbox" checked={done || chosen.has(sg.key)} disabled={done || saving} onChange={() => toggle(sg.key)}
                          aria-label={`${sg.name} übernehmen${known ? " (schon im Inventar)" : ""}${done ? " (übernommen)" : ""}`} className="mt-1"
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block">{sg.name}{sg.quantity > 1 ? ` × ${sg.quantity}` : ""}</span>
                          <span className="block text-xs text-white/45">
                            {[sg.kind, sg.manufacturer, sg.serial_number ? `Seriennummer ${sg.serial_number}` : null, sg.detail, sg.hint].filter(Boolean).join(" · ")}
                          </span>
                        </span>
                        {done ? <Badge tone="good">übernommen</Badge> : known && <Badge tone="info">schon im Inventar</Badge>}
                      </label>
                    </li>
                  );
                })}
              </ul>
              <div className="flex flex-wrap items-center gap-2">
                {categories.length > 0 && (
                  <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} aria-label="Kategorie für die neuen Teile" className={`${inlineInput} w-auto`}>
                    <option value="">Kategorie: –</option>
                    {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                )}
                <select value={locationId} onChange={(e) => setLocationId(e.target.value)} aria-label="Standort für die neuen Teile" className={`${inlineInput} w-auto`}>
                  <option value="">Standort: –</option>
                  {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
                {projects.length > 0 && (
                  <select value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Bauprojekt für die neuen Teile" className={`${inlineInput} w-auto`}>
                    <option value="">Bauprojekt: –</option>
                    {projects.map((pr) => <option key={pr.id} value={pr.id}>{pr.name}</option>)}
                  </select>
                )}
                <Button variant="primary" onClick={() => void take()} disabled={open === 0 || saving}>
                  {progress ? `Übernehme … ${progress[0]} von ${progress[1]}` : open === 1 ? "1 Teil übernehmen" : `${open} Teile übernehmen`}
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

/**
 * Bestellliste einlesen (z. B. den Amazon-Bestellverlauf oder eine früher exportierte Inventar-CSV): Vorschau mit
 * Häkchen, übernommen wird nur das Angekreuzte. Die Datei bleibt im Browser.
 */
function ImportPanel({
  categories, locations, onDone,
}: {
  categories: Category[]; locations: Location[]; onDone: (message: string) => void;
}): JSX.Element {
  const [result, setResult] = useState<(ImportResult & { file: string }) | null>(null);
  // Ganze Gegenstandsliste für „schon im Inventar?“; `null`, wenn sie nicht zu laden war.
  const [existing, setExisting] = useState<Item[] | null>([]);
  const [chosen, setChosen] = useState<ReadonlySet<number>>(() => new Set());
  const [taken, setTaken] = useState<ReadonlySet<number>>(() => new Set());
  const [filter, setFilter] = useState("");
  const [locationId, setLocationId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<[number, number] | null>(null);
  const [outcome, setOutcome] = useState<CreateOutcome<number> | null>(null);

  const norm = (value: string | null | undefined) => (value ?? "").trim().toLowerCase();
  const byName = <T extends { id: string; name: string }>(list: T[], name: string | null) =>
    (name ? list.find((x) => norm(x.name) === norm(name))?.id : undefined) ?? null;
  // Schon im Inventar? Gleiche Seriennummer oder gleicher Produktlink sind sicher, gleicher Name ist ein Verdacht.
  const known = {
    names: new Set((existing ?? []).map((i) => norm(i.name))),
    serials: new Set((existing ?? []).map((i) => norm(i.serial_number)).filter(Boolean)),
    urls: new Set((existing ?? []).map((i) => norm(i.product_url)).filter(Boolean)),
  };
  const isKnown = (row: ImportRow) =>
    known.names.has(norm(row.name)) || (!!row.serial_number && known.serials.has(norm(row.serial_number)))
    || (!!row.product_url && known.urls.has(norm(row.product_url)));

  async function readFile(file: File) {
    setError(null);
    setOutcome(null);
    if (file.size > MAX_IMPORT_BYTES) {
      setResult(null);
      setError(`Die Datei ist größer als ${MAX_IMPORT_BYTES / 1024 / 1024} MB. Teile sie auf oder exportiere einen kürzeren Zeitraum.`);
      return;
    }
    try {
      const [parsed, items] = await Promise.all([
        file.arrayBuffer().then((bytes) => rowsFromCsv(decodeCsv(bytes))),
        // Die ganze Liste, nicht nur die gerade gefilterte: sonst wäre „schon im Inventar“ unvollständig.
        fetchJson<Item[]>("/ext/inventory/items").catch(() => null),
      ]);
      if (parsed.rows.length === 0) {
        setResult(null);
        setError("In der Datei steht nichts, was sich übernehmen lässt. Die Datei braucht eine Spalte mit dem Namen (z. B. „Name“ oder „Product Name“).");
        return;
      }
      setExisting(items);
      setResult({ ...parsed, file: file.name });
      setTaken(new Set());
      setFilter("");
      const seen = {
        names: new Set((items ?? []).map((i) => norm(i.name))),
        serials: new Set((items ?? []).map((i) => norm(i.serial_number)).filter(Boolean)),
        urls: new Set((items ?? []).map((i) => norm(i.product_url)).filter(Boolean)),
      };
      const dupe = (row: ImportRow) => seen.names.has(norm(row.name)) || (!!row.serial_number && seen.serials.has(norm(row.serial_number)))
        || (!!row.product_url && seen.urls.has(norm(row.product_url)));
      // Ohne Liste lässt sich nicht prüfen, was schon da ist: dann nichts vorab ankreuzen (sonst doppelt).
      setChosen(new Set(items ? parsed.rows.map((r, i) => (dupe(r) ? -1 : i)).filter((i) => i >= 0) : []));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function take() {
    if (!result) return;
    const picked = [...result.rows.entries()].filter(([i]) => chosen.has(i) && !taken.has(i));
    setOutcome(null);
    setProgress([0, picked.length]);
    const done = await createItems(
      picked.map(([i, row]) => {
        const { category, location, ...fields } = row;
        return {
          key: i, label: row.name,
          body: { ...fields, category_id: byName(categories, category), location_id: byName(locations, location) ?? (locationId || null) },
        };
      }),
      (n) => setProgress([n, picked.length]),
    );
    setProgress(null);
    setTaken((prev) => new Set([...prev, ...done.created]));
    setChosen((prev) => new Set([...prev].filter((i) => !done.created.includes(i))));
    const count = done.created.length;
    if (count) onDone(count === 1 ? "1 Gegenstand übernommen." : `${count} Gegenstände übernommen.`);
    if (done.failed.length) setOutcome(done);
    else setResult(null);
  }

  const toggle = (i: number) => setChosen((prev) => {
    const next = new Set(prev);
    if (next.has(i)) next.delete(i); else next.add(i);
    return next;
  });
  const sourceText = { amazon: "Amazon-Bestellverlauf", inventar: "Inventar-Export", andere: "Liste" };
  const saving = progress !== null;
  const shown = result
    ? [...result.rows.entries()].filter(([, row]) => !filter.trim() || norm(row.name).includes(norm(filter)))
    : [];
  const open = result ? [...chosen].filter((i) => !taken.has(i)).length : 0;
  // Kategorien und Standorte aus der Datei, die es hier nicht gibt: die bleiben leer (bzw. Standort aus der Auswahl).
  const unknownNames = (list: { id: string; name: string }[], key: "category" | "location") =>
    result ? [...new Set(result.rows.map((r) => r[key]).filter((n): n is string => !!n && !byName(list, n)))] : [];
  const unknownCategories = unknownNames(categories, "category");
  const unknownLocations = unknownNames(locations, "location");
  const setAll = (on: boolean) => setChosen((prev) => {
    const next = new Set(prev);
    for (const [i] of shown) {
      if (taken.has(i)) continue;
      if (on) next.add(i); else next.delete(i);
    }
    return next;
  });

  return (
    <Card
      title="Bestellliste einlesen"
      description="Eine CSV-Datei mit Käufen ins Inventar holen, z. B. den Amazon-Bestellverlauf (bei Amazon unter „Meine Daten anfordern“, Datei „Retail.OrderHistory.1.csv“) oder einen früheren Export dieses Inventars. Die Datei bleibt in deinem Browser."
      className="mb-5"
    >
      <label className={`${buttonClass("secondary")} cursor-pointer focus-within:ring-2 focus-within:ring-[var(--color-accent)]`}>
        <Icon name="upload" size={14} /> CSV-Datei wählen
        <input
          type="file" accept=".csv,.tsv,.txt,text/csv" className="sr-only" aria-label="CSV-Datei wählen" disabled={saving}
          onChange={(e) => { const file = e.target.files?.[0]; if (file) void readFile(file); e.target.value = ""; }}
        />
      </label>
      <p className="sr-only" role="status">{progressText(progress)}</p>
      {error && <div className="mt-3"><Notice text={`Fehler: ${error}`} onClose={() => setError(null)} /></div>}
      {result && (
        <div className="mt-4" data-testid="import-result">
          <p className="mb-2 text-sm text-white/60">
            Erkannt: {sourceText[result.source]} „{result.file}“, {result.rows.length === 1 ? "1 Eintrag" : `${result.rows.length} Einträge`}
            {result.skipped > 0 && ` (${result.skipped} ohne Namen oder storniert übersprungen)`}.
          </p>
          {[
            ...result.notes,
            ...(unknownCategories.length ? [`Diese Kategorien gibt es hier nicht, die Gegenstände bleiben ohne Kategorie: ${unknownCategories.slice(0, 5).join(", ")}${unknownCategories.length > 5 ? " …" : ""}.`] : []),
            ...(unknownLocations.length ? [`Diese Standorte gibt es hier nicht, es gilt der Standort unten: ${unknownLocations.slice(0, 5).join(", ")}${unknownLocations.length > 5 ? " …" : ""}.`] : []),
          ].map((note) => <p key={note} className="mb-2 text-sm text-amber-200">{note}</p>)}
          {!existing && (
            <p className="mb-2 text-sm text-amber-200">
              Konnte nicht prüfen, was schon im Inventar steht. Darum ist nichts vorab angekreuzt – sieh die Liste durch.
            </p>
          )}
          {outcome && <FailureList outcome={outcome} what={["Gegenstand", "Gegenstände"]} onClose={() => setOutcome(null)} />}
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {result.rows.length > 8 && (
              <input
                type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="In der Liste suchen …"
                aria-label="In der eingelesenen Liste suchen" className={`${inlineInput} min-w-[12rem] flex-1`}
              />
            )}
            <Button small onClick={() => setAll(true)} disabled={saving}>{filter.trim() ? "Alle gefundenen ankreuzen" : "Alle ankreuzen"}</Button>
            <Button small onClick={() => setAll(false)} disabled={saving}>{filter.trim() ? "Gefundene abwählen" : "Keine"}</Button>
          </div>
          <ul className="mb-3 max-h-96 divide-y divide-white/[0.06] overflow-y-auto">
            {shown.slice(0, 500).map(([i, row]) => {
              const dupe = isKnown(row);
              const done = taken.has(i);
              return (
                <li key={i}>
                  <label className="flex cursor-pointer flex-wrap items-start gap-2 py-2 text-sm">
                    <input
                      type="checkbox" checked={done || chosen.has(i)} disabled={done || saving} onChange={() => toggle(i)}
                      aria-label={`${row.name} übernehmen${dupe ? " (schon im Inventar?)" : ""}${done ? " (übernommen)" : ""}`} className="mt-1"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block">{row.name}{row.quantity > 1 ? ` × ${row.quantity}` : ""}</span>
                      <span className="block text-xs text-white/45">
                        {[formatDate(row.purchase_date), row.purchase_price_cents != null ? formatPrice(row.purchase_price_cents) : null].filter((v) => v && v !== "–").join(" · ")}
                      </span>
                    </span>
                    {done ? <Badge tone="good">übernommen</Badge> : dupe && <Badge tone="info">schon im Inventar?</Badge>}
                  </label>
                </li>
              );
            })}
            {shown.length > 500 && (
              <li className="py-2 text-xs text-white/45">
                Angezeigt werden die ersten 500 von {shown.length}. Die Knöpfe oben gelten für alle {shown.length}; mit der Suche findest du einzelne.
              </li>
            )}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <select value={locationId} onChange={(e) => setLocationId(e.target.value)} aria-label="Standort für die neuen Gegenstände" className={`${inlineInput} w-auto`}>
              <option value="">Standort: –</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <Button variant="primary" onClick={() => void take()} disabled={open === 0 || saving}>
              {progress ? `Übernehme … ${progress[0]} von ${progress[1]}` : open === 1 ? "1 Gegenstand übernehmen" : `${open} Gegenstände übernehmen`}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

interface LinkedDocument {
  id: string;
  original_filename: string;
  content_type: string;
}

/**
 * Dokumente zum Gegenstand (Rechnung, Handbuch, Garantieschein) aus dem Modul Dokumente: ansehen, gleich hier
 * hochladen, Zuordnung lösen. Ist das Modul aus oder fehlt das Recht, erscheint der Bereich gar nicht.
 */
function ItemDocuments({ item }: { item: Item }): JSX.Element | null {
  const canRead = deck().hasPermission("documents.read");
  const canWrite = deck().hasPermission("documents.write");
  const [docs, setDocs] = useState<LinkedDocument[] | null>(null);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Fehler beim Hochladen, Herunterladen oder Lösen: eigener Hinweis, kein „Liste ließ sich nicht laden“.
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await authedFetch(`/ext/documents/documents?item_id=${encodeURIComponent(item.id)}`);
      // Modul Dokumente aus (oder kein Zugriff): Bereich weglassen statt Fehler zeigen.
      if (res.status === 404 || res.status === 403) { setHidden(true); return; }
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorFromBody(body ?? {}, res.status));
      setDocs(body as LinkedDocument[]);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [item.id]);

  useEffect(() => { if (canRead) void load(); }, [canRead, load]);

  async function upload(file: File) {
    setBusy(true);
    setActionError(null);
    try {
      const res = await authedFetch(
        `/ext/documents/documents?filename=${encodeURIComponent(file.name)}&item_id=${encodeURIComponent(item.id)}`,
        { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } },
      );
      if (!res.ok) throw new Error(errorFromBody(await res.json().catch(() => ({})), res.status));
      await load();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function open(doc: LinkedDocument) {
    setActionError(null);
    try {
      const res = await authedFetch(`/ext/documents/documents/${doc.id}/download`);
      if (!res.ok) throw new Error(errorFromBody(await res.json().catch(() => ({})), res.status));
      const url = URL.createObjectURL(await res.blob());
      const a = window.document.createElement("a");
      a.href = url;
      a.download = doc.original_filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  async function unlink(doc: LinkedDocument) {
    setActionError(null);
    try {
      const res = await authedFetch(`/ext/documents/documents/${doc.id}/items/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(errorFromBody(await res.json().catch(() => ({})), res.status));
      await load();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }

  if (!canRead || hidden) return null;
  return (
    <div className="mb-4" data-testid={`documents-${item.id}`}>
      <p className="mb-2 text-xs text-white/45">Dokumente (Rechnung, Handbuch …)</p>
      {error && <LoadError compact what="Die Liste der Dokumente" error={error} onRetry={() => void load()} />}
      {actionError && <Notice text={`Fehler: ${actionError}`} onClose={() => setActionError(null)} />}
      {docs && docs.length === 0 && <p className="mb-2 text-xs text-white/45">Noch keine. Lade die Rechnung oder das Handbuch hier hoch.</p>}
      {docs && docs.length > 0 && (
        <ul className="mb-2 space-y-1">
          {docs.map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-center gap-2">
              {/* Auf dem Handy der Name in einer eigenen Zeile, die Knöpfe darunter. */}
              <span className="flex min-w-0 basis-full items-center gap-2 sm:flex-1 sm:basis-0">
                <Icon name="file" size={13} className="flex-none text-[var(--color-accent)]" />
                <span className="truncate">{doc.original_filename}</span>
              </span>
              <Button small onClick={() => void open(doc)} ariaLabel={`${doc.original_filename} herunterladen`}>
                <Icon name="download" size={12} /> Herunterladen
              </Button>
              {canWrite && (
                <Button
                  small variant="ghost" onClick={() => void unlink(doc)} title="Das Dokument selbst bleibt erhalten."
                  ariaLabel={`Zuordnung von ${doc.original_filename} zu diesem Gegenstand lösen`}
                >
                  Zuordnung lösen
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canWrite && (
        <label className={`${buttonClass("secondary", true)} cursor-pointer focus-within:ring-2 focus-within:ring-[var(--color-accent)]`}>
          <Icon name="upload" size={12} /> {busy ? "Lädt hoch …" : "Dokument hochladen"}
          <input
            type="file" accept="application/pdf,image/png,image/jpeg,image/webp" className="sr-only" disabled={busy}
            aria-label={`Dokument zu ${item.name} hochladen`}
            onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }}
          />
        </label>
      )}
    </div>
  );
}

export function InventoryPage(): JSX.Element {
  const [items, setItems] = useState<Item[] | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  // Server aus Nodvard Deck für „Gehört zu Server“; ohne Leserecht bleibt die Auswahl einfach weg.
  const [hosts, setHosts] = useState<HostRef[] | null>(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [projectFilter, setProjectFilter] = useState("");
  const [showProjects, setShowProjects] = useState(false);
  const [showRacks, setShowRacks] = useState(false);
  const [showScan, setShowScan] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [locationFilter, setLocationFilter] = useState("");
  const [warrantyFilter, setWarrantyFilter] = useState<WarrantyFilter>("");
  const [showForm, setShowForm] = useState(false);
  const [showTaxonomy, setShowTaxonomy] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [techOpen, setTechOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    const params = search ? `?q=${encodeURIComponent(search)}` : "";
    Promise.all([
      // Alle drei mit Statusprüfung: sonst landete bei einem Fehler die Fehlerantwort als „Liste“ in der Seite.
      fetchJson<Item[]>(`/ext/inventory/items${params}`),
      fetchJson<Category[]>("/ext/inventory/categories"),
      fetchJson<Location[]>("/ext/inventory/locations"),
    ])
      .then(([itemsData, categoriesData, locationsData]) => {
        setItems(itemsData);
        setCategories(categoriesData);
        setLocations(locationsData);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
    // Bauprojekte getrennt: kommen sie nicht an, bleibt das Inventar benutzbar, der Grund steht bei den Projekten.
    fetchJson<Project[]>("/ext/inventory/projects")
      .then((data) => { setProjects(data); setProjectsError(null); })
      .catch((err: unknown) => setProjectsError(errorMessage(err)));
  }, [search]);

  useEffect(() => { load(); }, [load]);
  // Ein entferntes Bauprojekt filtert nicht weiter (die Auswahl dafür gäbe es nicht mehr, die Liste bliebe leer).
  useEffect(() => {
    if (projectFilter && !projects.some((p) => p.id === projectFilter)) setProjectFilter("");
  }, [projects, projectFilter]);
  useEffect(() => {
    fetchJson<HostRef[]>("/hosts").then(setHosts).catch(() => setHosts(null));
  }, []);

  function startCreate() {
    setForm(EMPTY_FORM);
    setTechOpen(false);
    setEditingId(null);
    setShowForm(true);
  }

  function startEdit(item: Item) {
    setForm({
      name: item.name, description: item.description ?? "", category_id: item.category_id ?? "",
      location_id: item.location_id ?? "", quantity: String(item.quantity),
      purchase_date: item.purchase_date ?? "", purchase_price: item.purchase_price_cents != null ? (item.purchase_price_cents / 100).toFixed(2) : "",
      warranty_until: item.warranty_until ?? "", notes: item.notes ?? "",
      manufacturer: item.manufacturer ?? "", model: item.model ?? "", serial_number: item.serial_number ?? "",
      product_url: item.product_url ?? "", host_id: item.host_id ?? "", project_id: item.project_id ?? "",
    });
    setTechOpen(Boolean(item.manufacturer || item.model || item.serial_number || item.product_url || item.host_id || item.project_id));
    setEditingId(item.id);
    setShowForm(true);
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  }

  async function submitForm(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    const payload = {
      name: form.name,
      description: form.description || null,
      category_id: form.category_id || null,
      location_id: form.location_id || null,
      quantity: parseInt(form.quantity, 10) || 1,
      purchase_date: form.purchase_date || null,
      purchase_price_cents: form.purchase_price ? Math.round(parseFloat(form.purchase_price) * 100) : null,
      warranty_until: form.warranty_until || null,
      notes: form.notes || null,
      manufacturer: form.manufacturer.trim() || null,
      model: form.model.trim() || null,
      serial_number: form.serial_number.trim() || null,
      product_url: form.product_url.trim() || null,
      host_id: form.host_id || null,
      project_id: form.project_id || null,
    };
    const path = editingId ? `/ext/inventory/items/${editingId}` : "/ext/inventory/items";
    const method = editingId ? "PUT" : "POST";
    const res = await authedFetch(path, { method, body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { setMessage(`Fehler: ${errorFromBody(body, res.status)}`); return; }
    setShowForm(false);
    load();
  }

  async function removeItem(item: Item) {
    const ok = await deck().confirmDialog(`"${item.name}" wirklich löschen?`, { danger: true, confirmLabel: "Löschen" });
    if (!ok) return;
    const res = await authedFetch(`/ext/inventory/items/${item.id}`, { method: "DELETE" });
    if (!res.ok) await showError(res);
    load();
  }

  /** „Fehler: <Grund vom Server>“, statt einen Fehler still zu übergehen. */
  async function showError(res: Response) {
    const body = await res.json().catch(() => ({}));
    setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
  }

  async function uploadImage(item: Item, file: File) {
    setUploading(item.id);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/inventory/items/${item.id}/images`, {
        method: "POST", body: file, headers: { "Content-Type": file.type },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(null);
    }
  }

  async function removeImage(item: Item, image: ItemImage) {
    const res = await authedFetch(`/ext/inventory/items/${item.id}/images/${image.id}`, { method: "DELETE" });
    if (!res.ok) await showError(res);
    load();
  }

  const categoryName = (id: string | null) => categories.find((c) => c.id === id)?.name ?? "–";
  const locationName = (id: string | null) => locations.find((l) => l.id === id)?.name ?? "–";
  const projectName = (id: string | null | undefined) => projects.find((p) => p.id === id)?.name ?? null;
  // Unbekannter Server (gelöscht oder ohne Leserecht): die Kennung statt nichts.
  const hostName = (id: string | null | undefined) => {
    if (!id) return null;
    const host = hosts?.find((h) => h.id === id);
    return host ? host.display_name || host.name || id : "unbekannter Server";
  };

  const visible = (items ?? []).filter((i) =>
    (!categoryFilter || i.category_id === categoryFilter)
    && (!locationFilter || i.location_id === locationFilter)
    && (!projectFilter || i.project_id === projectFilter)
    && (!warrantyFilter || warrantyState(i.warranty_until) === warrantyFilter));
  const totalCents = visible.reduce((sum, i) => sum + (i.purchase_price_cents ?? 0) * i.quantity, 0);
  const soonCount = visible.filter((i) => warrantyState(i.warranty_until) === "soon").length;
  const expiredCount = visible.filter((i) => warrantyState(i.warranty_until) === "expired").length;

  function exportCsv() {
    const blob = new Blob([inventoryCsv(visible, categoryName, locationName)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = window.document.createElement("a");
    a.href = url;
    a.download = `inventar-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!items && !error) return <Loading />;
  // Ohne Liste der Grund statt der Seite; scheitert erst ein späteres Laden (Suche), bleibt die Seite stehen.
  if (error && !items) return <Page title="Inventar"><LoadError what="Das Inventar" error={error} onRetry={load} /></Page>;

  const input = (key: keyof typeof EMPTY_FORM, props: Record<string, unknown> = {}) => (
    <input value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} className={inputClass} {...props} />
  );

  return (
    <Page
      title="Inventar"
      description="Geräte und Gegenstände mit Standort, Kaufpreis und Garantie."
      actions={
        <>
          {/* Was man oft braucht, steht da; Einrichten und Ein-/Ausgabe liegen unter „Mehr“. */}
          <Button onClick={() => setShowProjects((v) => !v)} pressed={showProjects}><Icon name="package" size={14} /> Bauprojekte</Button>
          <Button onClick={() => setShowRacks((v) => !v)} pressed={showRacks}><Icon name="folder" size={14} /> Racks</Button>
          <MoreMenu
            testId="inventory-more"
            title="Weitere Funktionen des Inventars"
            align="right"
            items={[
              // Auslesen führt einen Befehl auf dem Server aus: nur mit dem Recht dazu (sonst lehnt der Server ab).
              ...(hosts && deck().hasPermission("hosts.execute")
                ? [{ id: "scan", label: showScan ? "Teile auslesen ausblenden" : "Teile eines Servers auslesen", run: () => setShowScan((v) => !v) }]
                : []),
              { id: "import", label: showImport ? "Bestellliste ausblenden" : "Bestellliste einlesen (CSV)", run: () => setShowImport((v) => !v) },
              { id: "taxonomy", label: showTaxonomy ? "Kategorien & Standorte ausblenden" : "Kategorien & Standorte", run: () => setShowTaxonomy((v) => !v) },
              { id: "export", label: "Als CSV exportieren", run: exportCsv, disabled: visible.length === 0 },
            ]}
          />
          <Button variant="primary" onClick={startCreate}><Icon name="plus" size={14} /> Neuer Gegenstand</Button>
        </>
      }
    >
      {showProjects && projectsError && <LoadError what="Die Liste der Bauprojekte" error={projectsError} onRetry={load} />}
      {showProjects && !projectsError && (
        <ProjectsPanel
          projects={projects} onChanged={load} activeId={projectFilter}
          onShowItems={(id) => setProjectFilter((current) => (current === id ? "" : id))}
        />
      )}
      {showScan && hosts && deck().hasPermission("hosts.execute") && (
        <ScanPanel
          hosts={hosts} categories={categories} locations={locations} projects={projects}
          onDone={(text) => { setInfo(text); load(); }}
        />
      )}
      {showImport && (
        <ImportPanel categories={categories} locations={locations} onDone={(text) => { setInfo(text); load(); }} />
      )}
      {showRacks && <RacksPanel items={items ?? []} onChanged={load} />}
      {showTaxonomy && <TaxonomyPanel categories={categories} locations={locations} onChanged={load} />}
      {error && <LoadError what="Das Inventar" error={error} onRetry={load} hint="Angezeigt wird der letzte geladene Stand." />}
      {info && <Notice kind="ok" text={info} onClose={() => setInfo(null)} />}
      {message && <Notice text={message} onClose={() => setMessage(null)} />}

      {items && items.length > 0 && (
        <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="inventory-summary">
          <Stat label="Gegenstände" value={visible.length} hint={`${visible.length} Gegenstand/Gegenstände angezeigt`} />
          <Stat label="Gesamtwert" value={(totalCents / 100).toLocaleString("de-DE", { style: "currency", currency: "EUR" })} />
          <Stat label="Garantie endet bald" value={soonCount} hint="in den nächsten 90 Tagen" tone={soonCount ? "warn" : undefined} />
          <Stat label="Garantie abgelaufen" value={expiredCount} hint={`${expiredCount} abgelaufen`} tone={expiredCount ? "bad" : undefined} />
        </div>
      )}

      {showForm && (
        <form onSubmit={(e) => void submitForm(e)} className="mb-5">
          <Card
            title={editingId ? "Gegenstand bearbeiten" : "Neuer Gegenstand"}
            actions={<Button variant="ghost" ariaLabel="Formular schließen" onClick={() => setShowForm(false)}><Icon name="x" size={14} /></Button>}
          >
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Name" className="sm:col-span-2">{input("name", { required: true })}</Field>
              <Field label="Menge">{input("quantity", { type: "number", min: "0", max: "100000" })}</Field>
              <Field label="Preis (€)">{input("purchase_price", { type: "number", step: "0.01", min: "0", max: "10000000" })}</Field>
              <Field label="Kategorie">
                <select value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} className={inputClass}>
                  <option value="">–</option>
                  {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </Field>
              <Field label="Standort">
                <select value={form.location_id} onChange={(e) => setForm({ ...form, location_id: e.target.value })} className={inputClass}>
                  <option value="">–</option>
                  {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </Field>
              <Field label="Kaufdatum">{input("purchase_date", { type: "date" })}</Field>
              <Field label="Garantie bis">{input("warranty_until", { type: "date" })}</Field>
              <Field label="Beschreibung" className="sm:col-span-2">
                <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className={inputClass} rows={3} />
              </Field>
              <Field label="Notizen" className="sm:col-span-2">
                <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className={inputClass} rows={3} />
              </Field>
            </div>
            {/* Für Technik: woran man das Teil erkennt, wo man es nachkauft, wozu es gehört. Alles freiwillig. */}
            <details
              className="mt-4 rounded-lg border border-white/[0.06] p-3"
              open={techOpen}
              onToggle={(e) => setTechOpen((e.currentTarget as HTMLDetailsElement).open)}
              data-testid="tech-fields"
            >
              <summary className="cursor-pointer text-sm text-white/70">Technik: Hersteller, Modell, Server, Bauprojekt</summary>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Hersteller">{input("manufacturer", { maxLength: 200 })}</Field>
                <Field label="Modell">{input("model", { maxLength: 200 })}</Field>
                <Field label="Seriennummer">{input("serial_number", { maxLength: 200 })}</Field>
                <Field label="Produktlink">{input("product_url", { type: "url", placeholder: "https://…", maxLength: 2000 })}</Field>
                {hosts && (
                  <Field label="Gehört zu Server">
                    <select value={form.host_id} onChange={(e) => setForm({ ...form, host_id: e.target.value })} className={inputClass}>
                      <option value="">–</option>
                      {form.host_id && !hosts.some((h) => h.id === form.host_id) && <option value={form.host_id}>{form.host_id}</option>}
                      {hosts.map((h) => <option key={h.id} value={h.id}>{h.display_name || h.name}</option>)}
                    </select>
                  </Field>
                )}
                <Field label="Bauprojekt">
                  <select value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })} className={inputClass}>
                    <option value="">–</option>
                    {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </Field>
              </div>
            </details>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setShowForm(false)}>Abbrechen</Button>
              <Button type="submit" variant="primary">{editingId ? "Speichern" : "Anlegen"}</Button>
            </div>
          </Card>
        </form>
      )}

      {items && items.length === 0 ? (
        <EmptyState
          icon="package"
          title="Noch keine Gegenstände erfasst."
          text="Erfasse Geräte, Werkzeug oder Ersatzteile mit Standort, Kaufpreis und Garantie – dann warnt das Dashboard rechtzeitig, bevor eine Garantie abläuft."
          action={!showForm && <Button variant="primary" onClick={startCreate}><Icon name="plus" size={14} /> Ersten Gegenstand anlegen</Button>}
        />
      ) : (
        <>
          {/* Suche und Filter als Raster: nebeneinander statt jede Auswahl in einer eigenen vollen Zeile. */}
          <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            <SearchInput value={search} onChange={setSearch} placeholder="Suchen …" label="Suchen" />
            <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} aria-label="Nach Kategorie filtern" className={`${inputClass} w-auto`}>
              <option value="">Alle Kategorien</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <select value={locationFilter} onChange={(e) => setLocationFilter(e.target.value)} aria-label="Nach Standort filtern" className={`${inputClass} w-auto`}>
              <option value="">Alle Standorte</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            {projects.length > 0 && (
              <select value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)} aria-label="Nach Bauprojekt filtern" className={`${inputClass} w-auto`}>
                <option value="">Alle Bauprojekte</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            )}
            <select value={warrantyFilter} onChange={(e) => setWarrantyFilter(e.target.value as WarrantyFilter)} aria-label="Nach Garantie filtern" className={`${inputClass} w-auto`}>
              <option value="">Garantie: alle</option>
              <option value="active">läuft noch</option>
              <option value="soon">läuft in 90 Tagen ab</option>
              <option value="expired">abgelaufen</option>
              <option value="none">keine Angabe</option>
            </select>
          </div>

          <ul className="panel divide-y divide-white/[0.06]">
            {visible.length === 0 && <li className="px-5 py-8 text-center text-sm text-white/45">Nichts gefunden – Filter anpassen.</li>}
            {visible.map((item) => {
              const isExpanded = expanded === item.id;
              const ws = warrantyState(item.warranty_until);
              return (
                <li key={item.id} className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <button
                      type="button" onClick={() => setExpanded(isExpanded ? null : item.id)}
                      aria-label={isExpanded ? "Details ausblenden" : "Details anzeigen"} aria-expanded={isExpanded}
                      className="rounded p-1 text-white/40 hover:bg-white/[0.06] hover:text-white"
                    >
                      <Icon name={isExpanded ? "chevron-down" : "chevron-right"} size={15} />
                    </button>
                    {item.images[0] ? (
                      <AuthImage src={item.images[0].url} alt="" className="h-11 w-11 flex-none rounded-lg object-cover" />
                    ) : (
                      <span className="grid h-11 w-11 flex-none place-items-center rounded-lg bg-white/[0.05] text-white/35"><Icon name="package" size={18} /></span>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{item.name}</p>
                      <p className="truncate text-xs text-white/50">
                        {[item.manufacturer, item.model].filter(Boolean).join(" ") || categoryName(item.category_id)} · {locationName(item.location_id)} · Menge {item.quantity}
                        {hostName(item.host_id) && <> · {hostName(item.host_id)}</>}
                      </p>
                    </div>
                    <div className="hidden flex-none text-right sm:block">
                      <p className="text-sm tabular-nums">{formatPrice(item.purchase_price_cents)}</p>
                      {item.warranty_until && <Badge tone={WARRANTY_BADGE[ws].tone}>{WARRANTY_BADGE[ws].label(item.warranty_until)}</Badge>}
                    </div>
                    {/* Auf dem Handy nur die Symbole, sonst bliebe für den Namen kein Platz (der Text bleibt für Lesegeräte). */}
                    <div className="flex flex-none items-center gap-1">
                      <Button small onClick={() => startEdit(item)} title="Bearbeiten"><Icon name="edit" size={13} /><span className="sr-only sm:not-sr-only"> Bearbeiten</span></Button>
                      <Button small variant="danger" onClick={() => void removeItem(item)} title="Löschen"><Icon name="trash" size={13} /><span className="sr-only sm:not-sr-only"> Löschen</span></Button>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="ml-10 mt-3 rounded-lg border border-white/[0.06] bg-black/15 p-4 text-sm">
                      {item.description && <p className="mb-3 text-white/80">{item.description}</p>}
                      <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
                        <div><dt className="text-xs text-white/45">Kaufdatum</dt><dd>{formatDate(item.purchase_date)}</dd></div>
                        <div><dt className="text-xs text-white/45">Preis</dt><dd>{formatPrice(item.purchase_price_cents)}</dd></div>
                        <div><dt className="text-xs text-white/45">Garantie bis</dt><dd>{formatDate(item.warranty_until)}</dd></div>
                        <div><dt className="text-xs text-white/45">Notizen</dt><dd>{item.notes ?? "–"}</dd></div>
                        {item.manufacturer && <div><dt className="text-xs text-white/45">Hersteller</dt><dd>{item.manufacturer}</dd></div>}
                        {item.model && <div><dt className="text-xs text-white/45">Modell</dt><dd>{item.model}</dd></div>}
                        {item.serial_number && <div><dt className="text-xs text-white/45">Seriennummer</dt><dd className="font-mono text-xs">{item.serial_number}</dd></div>}
                        {hostName(item.host_id) && <div><dt className="text-xs text-white/45">Server</dt><dd>{hostName(item.host_id)}</dd></div>}
                        {projectName(item.project_id) && <div><dt className="text-xs text-white/45">Bauprojekt</dt><dd>{projectName(item.project_id)}</dd></div>}
                        {item.rack_id && (
                          <div>
                            <dt className="text-xs text-white/45">Im Rack</dt>
                            <dd>
                              {item.rack_position == null
                                ? "ohne festen Platz"
                                : (item.rack_units ?? 1) > 1
                                  ? `HE ${item.rack_position}–${item.rack_position + (item.rack_units ?? 1) - 1}`
                                  : `HE ${item.rack_position}`}
                            </dd>
                          </div>
                        )}
                      </dl>
                      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs" data-testid={`links-${item.id}`}>
                        {item.product_url && (
                          <a href={item.product_url} target="_blank" rel="noopener noreferrer" className="accent-soft rounded-lg px-2.5 py-1 hover:brightness-125">
                            Zum Produkt
                          </a>
                        )}
                        <span className="text-white/45">Preise vergleichen:</span>
                        {priceLinks(item).map((link) => (
                          <a key={link.label} href={link.href} target="_blank" rel="noopener noreferrer" className="rounded-lg border border-white/10 bg-white/[0.06] px-2.5 py-1 hover:bg-white/[0.12]">
                            {link.label}
                          </a>
                        ))}
                      </div>
                      <p className="mb-2 text-xs text-white/45">Fotos</p>
                      <div className="flex flex-wrap items-center gap-2">
                        {item.images.map((img) => (
                          <div key={img.id} className="group relative">
                            <AuthImage src={img.url} alt="" className="h-20 w-20 rounded-lg object-cover" />
                            <button
                              type="button" aria-label="Foto entfernen" onClick={() => void removeImage(item, img)}
                              className="absolute right-1 top-1 rounded-full bg-black/70 p-1 text-white opacity-0 transition group-hover:opacity-100"
                            >
                              <Icon name="x" size={11} />
                            </button>
                          </div>
                        ))}
                        <label className="flex h-20 w-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-white/20 text-[11px] text-white/50 hover:border-white/40 hover:text-white">
                          {uploading === item.id ? "Lädt …" : <><Icon name="upload" size={16} /> Foto</>}
                          <input
                            type="file" accept="image/png,image/jpeg,image/webp" className="hidden" aria-label={`Foto zu ${item.name} hochladen`}
                            onChange={(e) => { const file = e.target.files?.[0]; if (file) void uploadImage(item, file); e.target.value = ""; }}
                          />
                        </label>
                      </div>
                      <div className="mt-4"><ItemDocuments item={item} /></div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Page>
  );
}
