/**
 * Aussehen: Produktname, Logo, Anmeldeseite und alle fuenf Farben. Aenderungen an
 * den Farben werden sofort als Vorschau angewendet; ohne Speichern wird beim
 * Verlassen der Seite wieder das gespeicherte Branding gesetzt.
 */
import { ImageUp, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, apiFetch } from "../../lib/api";
import { applyBranding, type Branding, type BrandingColors } from "../../lib/branding";
import { useBrandingStore } from "../../state/branding";
import { Button, Card, Field, NoticeLine, PageHeader, errorText, inputClass, type Notice } from "./ui";

const LOGO_CONTENT_TYPES = ["image/png", "image/jpeg", "image/svg+xml", "image/webp"];

const COLOR_FIELDS: { key: keyof BrandingColors; label: string; hint: string }[] = [
  { key: "accent", label: "Akzent", hint: "Buttons, aktive Menüpunkte, Hervorhebungen" },
  { key: "accent_strong", label: "Akzent (Verlauf)", hint: "Zweite Farbe im Farbverlauf" },
  { key: "background", label: "Hintergrund", hint: "Fläche hinter allen Inhalten" },
  { key: "surface", label: "Karten", hint: "Kacheln, Panels, Menü" },
  { key: "text", label: "Text", hint: "Standard-Schriftfarbe" },
];

export const PRESETS: { name: string; colors: BrandingColors }[] = [
  { name: "Rubin", colors: { accent: "#e11d48", accent_strong: "#7c3aed", background: "#0b0f17", surface: "#121826", text: "#e5e7eb" } },
  { name: "Ozean", colors: { accent: "#0ea5e9", accent_strong: "#0369a1", background: "#0b1220", surface: "#111a2b", text: "#e6ebf5" } },
  { name: "Smaragd", colors: { accent: "#10b981", accent_strong: "#0d9488", background: "#0a1210", surface: "#111c19", text: "#e3ede9" } },
  { name: "Bernstein", colors: { accent: "#f59e0b", accent_strong: "#ea580c", background: "#110e0a", surface: "#1a1611", text: "#f1ece4" } },
  { name: "Violett", colors: { accent: "#8b5cf6", accent_strong: "#db2777", background: "#0e0b17", surface: "#171326", text: "#e9e5f5" } },
  { name: "Graphit", colors: { accent: "#94a3b8", accent_strong: "#475569", background: "#0a0a0b", surface: "#141416", text: "#e4e4e7" } },
];

const HEX = /^#[0-9a-fA-F]{6}$/;

export function AppearanceSettings(): JSX.Element {
  const saved = useBrandingStore((s) => s.branding);
  const loaded = useBrandingStore((s) => s.loaded);
  const setAndApply = useBrandingStore((s) => s.setAndApply);
  const [draft, setDraft] = useState<Branding | null>(saved);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [logoVersion, setLogoVersion] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (saved && !draft) setDraft(saved);
  }, [saved, draft]);

  // Vorschau: Farben sofort anwenden, beim Verlassen das gespeicherte Branding zurueck.
  useEffect(() => {
    if (draft && Object.values(draft.colors).every((c) => HEX.test(c))) applyBranding(draft);
  }, [draft]);
  useEffect(() => () => {
    const current = useBrandingStore.getState().branding;
    if (current) applyBranding(current);
  }, []);

  if (loaded && !saved) {
    return <NoticeLine notice={{ kind: "error", text: "Das Branding konnte nicht geladen werden. Bitte Seite neu laden." }} />;
  }
  if (!draft || !saved) return <p className="text-sm text-white/50">Lade …</p>;

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const set = <K extends keyof Branding>(key: K, value: Branding[K]) => setDraft({ ...draft, [key]: value });
  const setColor = (key: keyof BrandingColors, value: string) => setDraft({ ...draft, colors: { ...draft.colors, [key]: value } });

  async function save(next: Branding = draft!) {
    const bad = Object.entries(next.colors).find(([, v]) => !HEX.test(v));
    if (bad) return setNotice({ kind: "error", text: `Ungültige Farbe: ${bad[1]} (erwartet z. B. #e11d48).` });
    if (!next.product_name.trim()) return setNotice({ kind: "error", text: "Der Produktname darf nicht leer sein." });
    setBusy(true);
    setNotice(null);
    try {
      const updated = await api.put<Branding>("/branding", {
        ...next,
        short_name: next.short_name.trim() || next.product_name.trim(),
        login_subtitle: next.login_subtitle?.trim() || null,
        support_url: next.support_url?.trim() || null,
        favicon_url: next.favicon_url?.trim() || null,
      });
      setAndApply(updated);
      setDraft(updated);
      setNotice({ kind: "ok", text: "Gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function uploadLogo(file: File) {
    if (!LOGO_CONTENT_TYPES.includes(file.type)) {
      setNotice({ kind: "error", text: `Nicht unterstützter Bildtyp: ${file.type || "(unbekannt)"}. Erlaubt: PNG, JPEG, SVG, WebP.` });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const updated = await apiFetch<Branding>("/branding/logo", {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type },
      });
      setAndApply(updated);
      setDraft({ ...draft!, logo_url: updated.logo_url });
      setLogoVersion(Date.now());
      setNotice({ kind: "ok", text: "Logo hochgeladen." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const initial = (draft.short_name || draft.product_name || "?").slice(0, 1).toUpperCase();

  return (
    <div>
      <PageHeader
        title="Aussehen"
        description="Passe Name, Logo und Farben an dein Unternehmen an. Farbänderungen siehst du sofort als Vorschau."
        actions={
          <>
            <Button variant="ghost" disabled={!dirty || busy} onClick={() => { setDraft(saved); setNotice(null); }}>
              <RotateCcw size={14} /> Verwerfen
            </Button>
            <Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Speichern</Button>
          </>
        }
      />
      <NoticeLine notice={notice} />

      <Card title="Marke" description="Erscheint in der Seitenleiste, im Browser-Tab und auf der Anmeldeseite.">
        <div className="flex flex-col gap-5 sm:flex-row">
          <div className="flex flex-col items-center gap-2">
            <div className="grid h-20 w-20 place-items-center overflow-hidden rounded-2xl border border-white/10 bg-black/20">
              {draft.logo_url ? (
                <img src={logoVersion ? `${draft.logo_url}?v=${logoVersion}` : draft.logo_url} alt="Aktuelles Logo" className="max-h-full max-w-full object-contain" />
              ) : (
                <span className="accent-gradient grid h-full w-full place-items-center text-2xl font-bold text-white" title="kein Logo">
                  {initial}
                  <span className="sr-only">kein Logo</span>
                </span>
              )}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept={LOGO_CONTENT_TYPES.join(",")}
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void uploadLogo(file);
                e.target.value = "";
              }}
            />
            <Button disabled={busy} onClick={() => fileInputRef.current?.click()}><ImageUp size={14} /> Logo hochladen</Button>
            {draft.logo_url && (
              <Button variant="ghost" disabled={busy} onClick={() => void save({ ...draft, logo_url: null })}><Trash2 size={14} /> Entfernen</Button>
            )}
            <p className="text-center text-[11px] text-white/40">PNG, JPEG, SVG oder WebP,<br />max. 2 MB, am besten quadratisch</p>
          </div>
          <div className="grid flex-1 gap-4 sm:grid-cols-2">
            <Field label="Produktname" hint="Voller Name, z. B. „Muster GmbH Serververwaltung“">
              <input value={draft.product_name} maxLength={128} onChange={(e) => set("product_name", e.target.value)} className={inputClass} />
            </Field>
            <Field label="Kurzname / Zusatz" hint="Kleine Zeile unter dem Namen in der Seitenleiste">
              <input value={draft.short_name} maxLength={64} onChange={(e) => set("short_name", e.target.value)} className={inputClass} />
            </Field>
            <Field label="Favicon-Adresse" hint="Optional, Bild-URL für das Browser-Tab-Symbol" className="sm:col-span-2">
              <input value={draft.favicon_url ?? ""} placeholder="https://…/favicon.png" onChange={(e) => set("favicon_url", e.target.value)} className={inputClass} />
            </Field>
          </div>
        </div>
      </Card>

      <Card title="Anmeldeseite" description="Texte, die deine Nutzer vor der Anmeldung sehen.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Begrüßungstext" hint="Großer Text links auf der Anmeldeseite; leer = Standardtext">
            <textarea
              rows={3}
              maxLength={200}
              value={draft.login_subtitle ?? ""}
              placeholder="Die Verwaltungsoberfläche für deine Server, Dienste und Backups."
              onChange={(e) => set("login_subtitle", e.target.value)}
              className={`${inputClass} resize-none`}
            />
          </Field>
          <Field label="Support-Adresse" hint="Link „Support kontaktieren“ unter dem Anmeldeformular, z. B. https://… oder mailto:…">
            <input value={draft.support_url ?? ""} placeholder="mailto:support@firma.de" onChange={(e) => set("support_url", e.target.value)} className={inputClass} />
          </Field>
        </div>
      </Card>

      <Card title="Farben" description="Wähle eine Vorlage oder stelle jede Farbe einzeln ein.">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-white/40">Vorlagen</p>
        <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {PRESETS.map((preset) => {
            const active = JSON.stringify(preset.colors) === JSON.stringify(draft.colors);
            return (
              <button
                key={preset.name}
                type="button"
                onClick={() => setDraft({ ...draft, colors: { ...preset.colors } })}
                className={`rounded-lg border p-2 text-left transition ${active ? "border-[var(--color-accent)] bg-white/[0.06]" : "border-white/10 hover:border-white/25"}`}
              >
                <span className="flex h-8 overflow-hidden rounded-md" style={{ background: preset.colors.background }}>
                  <span className="m-1.5 w-1/3 rounded" style={{ background: preset.colors.surface }} />
                  <span className="my-1.5 mr-1.5 flex-1 rounded" style={{ background: `linear-gradient(135deg, ${preset.colors.accent}, ${preset.colors.accent_strong})` }} />
                </span>
                <span className="mt-1.5 block text-xs">{preset.name}</span>
              </button>
            );
          })}
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {COLOR_FIELDS.map(({ key, label, hint }) => (
            <Field key={key} label={label} hint={hint}>
              <span className="flex gap-2">
                <input
                  type="color"
                  aria-label={`${label} wählen`}
                  value={HEX.test(draft.colors[key]) ? draft.colors[key] : "#000000"}
                  onChange={(e) => setColor(key, e.target.value)}
                  className="h-9 w-12 flex-none cursor-pointer rounded-lg border border-white/10 bg-transparent p-1"
                />
                <input
                  aria-label={`${label} (Hex)`}
                  value={draft.colors[key]}
                  maxLength={7}
                  onChange={(e) => setColor(key, e.target.value.trim())}
                  className={`${inputClass} font-mono ${HEX.test(draft.colors[key]) ? "" : "border-red-500/60"}`}
                />
              </span>
            </Field>
          ))}
        </div>
      </Card>
    </div>
  );
}
