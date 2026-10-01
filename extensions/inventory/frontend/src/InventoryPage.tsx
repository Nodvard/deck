/**
 * Inventar-Seite (Inventar-Modul im Stil von Homebox) -- PageSpec
 * `component="InventoryPage"` (siehe nodvard_deck_ext_inventory/__init__.py). Ruft
 * ausschliesslich die eigenen `/ext/inventory/...`-Endpunkte auf.
 *
 * Authentifizierung wie jede andere Extension-Seite: `window.__nodvardDeck.
 * getAccessToken()` (kein Zugriff auf state/auth.ts ueber den Import-Map-Shim).
 */
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { AuthImage } from "../../../_shared/frontend/src/AuthImage";
import { Badge, Button, Card, EmptyState, Field, Icon, Loading, Notice, Page, SearchInput, Stat, inputClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

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
}

type WarrantyFilter = "" | "active" | "soon" | "expired" | "none";

const DAY_MS = 86_400_000;

/** Garantie-Zustand relativ zu `today`: laeuft, laeuft in <= 90 Tagen ab, abgelaufen, keine.
 * Die 90 Tage gelten auch fuer die Dashboard-Kachel (`_WARRANTY_SOON_DAYS` im Backend). */
export function warrantyState(until: string | null, today = new Date()): Exclude<WarrantyFilter, ""> {
  if (!until) return "none";
  const days = (new Date(`${until}T00:00:00`).getTime() - new Date(today.toDateString()).getTime()) / DAY_MS;
  if (days < 0) return "expired";
  return days <= 90 ? "soon" : "active";
}

function csvCell(value: string | number | null | undefined): string {
  const text = value == null ? "" : String(value);
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** CSV fuer Excel/LibreOffice (Semikolon, UTF-8 mit BOM, Komma als Dezimaltrenner). */
export function inventoryCsv(items: Item[], categoryName: (id: string | null) => string, locationName: (id: string | null) => string): string {
  const header = ["Name", "Kategorie", "Standort", "Menge", "Kaufdatum", "Preis (EUR)", "Garantie bis", "Beschreibung", "Notizen"];
  const rows = items.map((i) => [
    i.name, categoryName(i.category_id), locationName(i.location_id), i.quantity, i.purchase_date,
    i.purchase_price_cents == null ? "" : (i.purchase_price_cents / 100).toFixed(2).replace(".", ","),
    i.warranty_until, i.description, i.notes,
  ].map(csvCell).join(";"));
  return "\ufeff" + [header.join(";"), ...rows].join("\r\n");
}

const EMPTY_FORM = {
  name: "", description: "", category_id: "", location_id: "", quantity: "1",
  purchase_date: "", purchase_price: "", warranty_until: "", notes: "",
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
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-white/45"><Icon name={icon} size={13} /> {title}</p>
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

export function InventoryPage(): JSX.Element {
  const [items, setItems] = useState<Item[] | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
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
  const [expanded, setExpanded] = useState<string | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    const params = search ? `?q=${encodeURIComponent(search)}` : "";
    Promise.all([
      authedFetch(`/ext/inventory/items${params}`).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<Item[]>; }),
      authedFetch("/ext/inventory/categories").then((r) => r.json() as Promise<Category[]>),
      authedFetch("/ext/inventory/locations").then((r) => r.json() as Promise<Location[]>),
    ])
      .then(([itemsData, categoriesData, locationsData]) => {
        setItems(itemsData);
        setCategories(categoriesData);
        setLocations(locationsData);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [search]);

  useEffect(() => { load(); }, [load]);

  function startCreate() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setShowForm(true);
  }

  function startEdit(item: Item) {
    setForm({
      name: item.name, description: item.description ?? "", category_id: item.category_id ?? "",
      location_id: item.location_id ?? "", quantity: String(item.quantity),
      purchase_date: item.purchase_date ?? "", purchase_price: item.purchase_price_cents != null ? (item.purchase_price_cents / 100).toFixed(2) : "",
      warranty_until: item.warranty_until ?? "", notes: item.notes ?? "",
    });
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
    await authedFetch(`/ext/inventory/items/${item.id}`, { method: "DELETE" });
    load();
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
    await authedFetch(`/ext/inventory/items/${item.id}/images/${image.id}`, { method: "DELETE" });
    load();
  }

  const categoryName = (id: string | null) => categories.find((c) => c.id === id)?.name ?? "–";
  const locationName = (id: string | null) => locations.find((l) => l.id === id)?.name ?? "–";

  const visible = (items ?? []).filter((i) =>
    (!categoryFilter || i.category_id === categoryFilter)
    && (!locationFilter || i.location_id === locationFilter)
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
  if (error) return <Page title="Inventar"><Notice text={`Fehler: ${error}`} /></Page>;

  const input = (key: keyof typeof EMPTY_FORM, props: Record<string, unknown> = {}) => (
    <input value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} className={inputClass} {...props} />
  );

  return (
    <Page
      title="Inventar"
      description="Geräte und Gegenstände mit Standort, Kaufpreis und Garantie."
      actions={
        <>
          <Button onClick={() => setShowTaxonomy((v) => !v)} pressed={showTaxonomy}><Icon name="tag" size={14} /> Kategorien &amp; Standorte</Button>
          <Button onClick={exportCsv} disabled={visible.length === 0}><Icon name="download" size={14} /> Als CSV exportieren</Button>
          <Button variant="primary" onClick={startCreate}><Icon name="plus" size={14} /> Neuer Gegenstand</Button>
        </>
      }
    >
      {showTaxonomy && <TaxonomyPanel categories={categories} locations={locations} onChanged={load} />}
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
              <Field label="Menge">{input("quantity", { type: "number", min: "0" })}</Field>
              <Field label="Preis (€)">{input("purchase_price", { type: "number", step: "0.01", min: "0" })}</Field>
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
          <div className="mb-3 flex flex-wrap gap-2">
            <SearchInput value={search} onChange={setSearch} placeholder="Suchen …" label="Suchen" />
            <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} aria-label="Nach Kategorie filtern" className={`${inputClass} w-auto`}>
              <option value="">Alle Kategorien</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <select value={locationFilter} onChange={(e) => setLocationFilter(e.target.value)} aria-label="Nach Standort filtern" className={`${inputClass} w-auto`}>
              <option value="">Alle Standorte</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
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
                        {categoryName(item.category_id)} · {locationName(item.location_id)} · Menge {item.quantity}
                      </p>
                    </div>
                    <div className="hidden flex-none text-right sm:block">
                      <p className="text-sm tabular-nums">{formatPrice(item.purchase_price_cents)}</p>
                      {item.warranty_until && <Badge tone={WARRANTY_BADGE[ws].tone}>{WARRANTY_BADGE[ws].label(item.warranty_until)}</Badge>}
                    </div>
                    <div className="flex flex-none items-center gap-1">
                      <Button small onClick={() => startEdit(item)}><Icon name="edit" size={13} /> Bearbeiten</Button>
                      <Button small variant="danger" onClick={() => void removeItem(item)}><Icon name="trash" size={13} /> Löschen</Button>
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
                      </dl>
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
                            type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                            onChange={(e) => { const file = e.target.files?.[0]; if (file) void uploadImage(item, file); e.target.value = ""; }}
                          />
                        </label>
                      </div>
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
