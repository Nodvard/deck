/**
 * Darstellung: das Theme gilt je Konto; Name, Logo, Anmeldeseite und Akzentfarbe gelten für alle und brauchen
 * `branding.write`. Branding-Änderungen sind sofort als Vorschau zu sehen; ohne Speichern gilt beim Verlassen wieder das alte.
 */
import { Check, ImageUp, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, apiFetch } from "../../lib/api";
import { DEFAULT_ACCENT, SHORT_NAME_HINT, applyBranding, localImageUrl, markFaviconChanged, type Branding } from "../../lib/branding";
import { customCssDisabled, enableCustomCss, loadCustomCss } from "../../lib/customCss";
import { THEMES, applyTheme, currentTheme, type ThemeId } from "../../lib/theme";
import { useAuthStore } from "../../state/auth";
import { useBrandingStore } from "../../state/branding";
import { Button, Card, Field, NoticeLine, PageHeader, errorText, inputClass, type Notice } from "./ui";

const LOGO_CONTENT_TYPES = ["image/png", "image/jpeg", "image/svg+xml", "image/webp"];
const FAVICON_CONTENT_TYPES = ["image/png", "image/x-icon", "image/vnd.microsoft.icon", "image/svg+xml"];
const FOREIGN_URL_SHOWN = 80;

/** Hinweis zu einer alten fremden Bild-Adresse; sie steht (gekürzt) mit dabei, damit man das Bild dort holen kann. */
function ForeignImageHint({ what, url, className }: { what: string; url: string; className: string }): JSX.Element {
  const shown = url.length > FOREIGN_URL_SHOWN ? `${url.slice(0, FOREIGN_URL_SHOWN)}…` : url;
  return (
    <p role="status" className={`text-amber-200 ${className}`}>
      {what} liegt auf einer fremden Adresse (<span className="break-all">{shown}</span>) und wird nicht mehr geladen,
      weil Deck Bilder nur noch vom eigenen Server lädt. Bitte als Datei hochladen.
    </p>
  );
}

/** Manche Systeme geben für .ico keinen Typ an; der Server entscheidet über den Content-Type. */
function faviconType(file: File): string {
  return file.type || (file.name.toLowerCase().endsWith(".ico") ? "image/x-icon" : "");
}

/** Akzent-Vorlagen; die erste nimmt die Farbe des gewählten Themes. */
export const PRESETS: { name: string; accent: string; accent_strong: string }[] = [
  { name: "Vom Theme", accent: DEFAULT_ACCENT, accent_strong: "#0369a1" },
  { name: "Rubin", accent: "#e11d48", accent_strong: "#7c3aed" },
  { name: "Smaragd", accent: "#10b981", accent_strong: "#0d9488" },
  { name: "Bernstein", accent: "#f59e0b", accent_strong: "#ea580c" },
  { name: "Violett", accent: "#8b5cf6", accent_strong: "#db2777" },
  { name: "Graphit", accent: "#94a3b8", accent_strong: "#475569" },
];

const HEX = /^#[0-9a-fA-F]{6}$/;

export function AppearanceSettings(): JSX.Element {
  const canBrand = useAuthStore((s) => s.hasPermission("branding.write"));
  return (
    <div>
      <PageHeader title="Darstellung" description="Wie Nodvard Deck aussieht: dein Theme und, für Admins, das Branding für alle." />
      <ThemeCard />
      {canBrand && <BrandingSettings />}
      {canBrand && <CustomCssCard />}
    </div>
  );
}

function ThemeCard(): JSX.Element {
  const [selected, setSelected] = useState<ThemeId>(currentTheme());
  const [notice, setNotice] = useState<Notice>(null);

  async function choose(theme: ThemeId) {
    if (theme === selected) return;
    applyTheme(theme);
    setSelected(theme);
    setNotice(null);
    try {
      await api.patch("/me/preferences", { theme });
    } catch (err) {
      setNotice({ kind: "error", text: `Nicht gespeichert, das Theme gilt nur in diesem Browser: ${errorText(err)}` });
    }
  }

  return (
    <Card title="Theme" description="Gilt für dein Konto auf jedem Gerät.">
      <div role="radiogroup" aria-label="Theme" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {THEMES.map((theme) => {
          const active = theme.id === selected;
          const [background, panel, accent] = theme.swatch;
          return (
            <button
              key={theme.id}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => void choose(theme.id)}
              className={`rounded-lg border p-2 text-left transition ${active ? "border-[var(--color-accent)]" : "border-white/10 hover:border-white/25"}`}
            >
              <span aria-hidden className="flex h-16 gap-1.5 overflow-hidden rounded-md border border-white/10 p-1.5" style={{ background }}>
                <span className="w-1/4 rounded-sm" style={{ background: panel }} />
                <span className="flex flex-1 flex-col gap-1.5">
                  <span className="h-2 w-1/2 rounded-sm" style={{ background: accent }} />
                  <span className="flex-1 rounded-sm" style={{ background: panel }} />
                </span>
              </span>
              <span className="mt-2 flex items-center gap-1.5 text-sm font-medium">
                {theme.label}
                {active && <Check size={14} className="text-[var(--color-accent)]" />}
              </span>
              <span className="mt-0.5 block text-xs text-white/50">{theme.hint}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-3"><NoticeLine notice={notice} /></div>
    </Card>
  );
}

function BrandingSettings(): JSX.Element {
  const saved = useBrandingStore((s) => s.branding);
  const loaded = useBrandingStore((s) => s.loaded);
  const setAndApply = useBrandingStore((s) => s.setAndApply);
  const [draft, setDraft] = useState<Branding | null>(saved);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [logoVersion, setLogoVersion] = useState(0);
  const [faviconVersion, setFaviconVersion] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const faviconInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (saved && !draft) setDraft(saved);
  }, [saved, draft]);

  useEffect(() => {
    if (draft && HEX.test(draft.colors.accent)) applyBranding(draft);
  }, [draft]);
  useEffect(() => () => {
    const current = useBrandingStore.getState().branding;
    if (current) applyBranding(current);
  }, []);

  if (loaded && !saved) {
    return <NoticeLine notice={{ kind: "error", text: "Das Branding konnte nicht geladen werden. Bitte Seite neu laden." }} />;
  }
  if (!draft || !saved) return <p className="text-sm text-white/50">Lade Branding …</p>;

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const set = <K extends keyof Branding>(key: K, value: Branding[K]) => setDraft({ ...draft, [key]: value });
  const setAccent = (accent: string, accent_strong = draft.colors.accent_strong) =>
    setDraft({ ...draft, colors: { ...draft.colors, accent, accent_strong } });

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
        logo_url: next.logo_url?.trim() || null,
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

  async function uploadFavicon(file: File) {
    const type = faviconType(file);
    if (!FAVICON_CONTENT_TYPES.includes(type)) {
      setNotice({ kind: "error", text: `Nicht unterstützter Bildtyp: ${type || "(unbekannt)"}. Erlaubt: PNG, ICO, SVG.` });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const updated = await apiFetch<Branding>("/branding/favicon", {
        method: "POST",
        body: file,
        headers: { "Content-Type": type },
      });
      const now = Date.now();
      markFaviconChanged(now);
      setAndApply(updated);
      setDraft({ ...draft!, favicon_url: updated.favicon_url });
      setFaviconVersion(now);
      setNotice({ kind: "ok", text: "Favicon hochgeladen." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const initial = (draft.short_name || draft.product_name || "?").slice(0, 1).toUpperCase();
  const logoUrl = localImageUrl(draft.logo_url);
  const faviconUrl = localImageUrl(draft.favicon_url);
  const withVersion = (url: string, version: number) =>
    version && !url.startsWith("data:") && !url.includes("?") ? `${url}?v=${version}` : url;

  return (
    <section aria-label="Branding" className="mt-8">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">Branding</h3>
          <p className="mt-1 text-sm text-white/55">Name, Logo und Akzentfarbe für alle Nutzer. Änderungen siehst du sofort als Vorschau.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" disabled={!dirty || busy} onClick={() => { setDraft(saved); setNotice(null); }}>
            <RotateCcw size={14} /> Verwerfen
          </Button>
          <Button variant="primary" busy={busy} disabled={!dirty} onClick={() => void save()}>Speichern</Button>
        </div>
      </div>
      <NoticeLine notice={notice} />

      <Card title="Marke" description="Erscheint in der Seitenleiste, im Browser-Tab und auf der Anmeldeseite.">
        <div className="flex flex-col gap-5 sm:flex-row">
          <div className="flex flex-col items-center gap-2">
            <div className="grid h-20 w-20 place-items-center overflow-hidden rounded-2xl border border-white/10 bg-black/20">
              {logoUrl ? (
                <img src={withVersion(logoUrl, logoVersion)} alt="Aktuelles Logo" className="max-h-full max-w-full object-contain" />
              ) : (
                <span className="accent-gradient grid h-full w-full place-items-center text-2xl font-bold text-on-accent" title="kein Logo">
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
            {draft.logo_url && !logoUrl && (
              <ForeignImageHint what="Das Logo" url={draft.logo_url} className="max-w-[12rem] text-center text-[11px]" />
            )}
          </div>
          <div className="grid flex-1 gap-4 sm:grid-cols-2">
            <Field label="Produktname" hint="Voller Name, z. B. „Muster GmbH Serververwaltung“">
              <input value={draft.product_name} maxLength={128} onChange={(e) => set("product_name", e.target.value)} className={inputClass} />
            </Field>
            <Field label="Untertitel" hint={`${SHORT_NAME_HINT} Der erste Buchstabe steht im Symbol, wenn du kein Logo hochlädst.`}>
              <input value={draft.short_name} maxLength={64} onChange={(e) => set("short_name", e.target.value)} className={inputClass} />
            </Field>
            <div className="text-sm sm:col-span-2">
              <span className="mb-1.5 block text-white/70">Favicon</span>
              <div className="flex flex-wrap items-center gap-2">
                <span className="grid h-9 w-9 place-items-center overflow-hidden rounded-lg border border-white/10 bg-black/20">
                  {faviconUrl ? (
                    <img src={withVersion(faviconUrl, faviconVersion)} alt="Aktuelles Favicon" className="h-5 w-5 object-contain" />
                  ) : (
                    <span className="text-xs text-white/40" title="kein eigenes Favicon">–<span className="sr-only">kein eigenes Favicon</span></span>
                  )}
                </span>
                <input
                  ref={faviconInputRef}
                  type="file"
                  aria-label="Favicon-Datei"
                  accept={[".ico", ...FAVICON_CONTENT_TYPES].join(",")}
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void uploadFavicon(file);
                    e.target.value = "";
                  }}
                />
                <Button disabled={busy} onClick={() => faviconInputRef.current?.click()}><ImageUp size={14} /> Favicon hochladen</Button>
                {draft.favicon_url && (
                  <Button variant="ghost" disabled={busy} onClick={() => void save({ ...draft, favicon_url: null })}><Trash2 size={14} /> Entfernen</Button>
                )}
              </div>
              <span className="mt-1 block text-xs text-white/40">Optional, Symbol im Browser-Tab: PNG, ICO oder SVG, max. 512 KB</span>
              {draft.favicon_url && !faviconUrl && (
                <ForeignImageHint what="Das Favicon" url={draft.favicon_url} className="mt-1 text-xs" />
              )}
            </div>
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

      <Card title="Akzentfarbe" description="Knöpfe, aktive Menüpunkte und Hervorhebungen, in allen Themes. „Vom Theme“ nimmt die Farbe des gewählten Themes.">
        <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {PRESETS.map((preset) => {
            const active = preset.accent === draft.colors.accent.toLowerCase();
            return (
              <button
                key={preset.name}
                type="button"
                onClick={() => setAccent(preset.accent, preset.accent_strong)}
                className={`flex items-center gap-2 rounded-lg border p-2 text-left text-xs transition ${active ? "border-[var(--color-accent)] bg-white/[0.06]" : "border-white/10 hover:border-white/25"}`}
              >
                <span
                  aria-hidden
                  className="h-5 w-5 flex-none rounded-md border border-white/10"
                  style={{ background: preset.accent === DEFAULT_ACCENT ? "var(--color-accent)" : preset.accent }}
                />
                {preset.name}
              </button>
            );
          })}
        </div>
        <Field label="Akzent" hint="Eigene Farbe als Hex-Wert, z. B. #e11d48." className="max-w-xs">
          <span className="flex gap-2">
            <input
              type="color"
              aria-label="Akzent wählen"
              value={HEX.test(draft.colors.accent) ? draft.colors.accent : "#000000"}
              onChange={(e) => setAccent(e.target.value)}
              className="h-9 w-12 flex-none cursor-pointer rounded-lg border border-white/10 bg-transparent p-1"
            />
            <input
              aria-label="Akzent (Hex)"
              value={draft.colors.accent}
              maxLength={7}
              onChange={(e) => setAccent(e.target.value.trim())}
              className={`${inputClass} font-mono ${HEX.test(draft.colors.accent) ? "" : "border-red-500/60"}`}
            />
          </span>
        </Field>
      </Card>
    </section>
  );
}

/** Eigenes CSS für alle; lädt erst beim Öffnen, damit die Seite ohne diesen Abruf auskommt. */
function CustomCssCard(): JSX.Element {
  const [saved, setSaved] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  // Der Notausgang `?ohne-css=1` gilt für den ganzen Tab; die Karte sagt es, sonst wundert man sich über die Wirkung.
  const [safeMode, setSafeMode] = useState(() => customCssDisabled());

  async function open() {
    setBusy(true);
    setNotice(null);
    try {
      const { css } = await api.get<{ css: string }>("/branding/custom-css");
      setSaved(css);
      setDraft(css);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const { css } = await api.put<{ css: string }>("/branding/custom-css", { css: draft });
      setSaved(css);
      loadCustomCss(Date.now());
      setNotice({ kind: "ok", text: "Gespeichert, das eigene CSS gilt jetzt für alle." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const editing = saved !== null;
  return (
    <Card
      title="Eigenes CSS"
      description="Für alle Nutzer, nach dem Theme geladen. Am einfachsten überschreibst du Variablen der Themes, z. B. :root { --radius: 0px; } für eckige Panels."
      footer={
        editing ? (
          <>
            <Button variant="ghost" disabled={busy || draft === saved} onClick={() => { setDraft(saved); setNotice(null); }}>
              <RotateCcw size={14} /> Verwerfen
            </Button>
            <Button variant="primary" busy={busy} disabled={draft === saved} onClick={() => void save()}>Speichern</Button>
          </>
        ) : (
          <Button busy={busy} onClick={() => void open()}>Bearbeiten</Button>
        )
      }
    >
      {safeMode && (
        <div role="status" className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          <span>In diesem Tab ist das eigene CSS abgeschaltet (Notausgang „?ohne-css=1“). Für alle anderen gilt es weiter.</span>
          <Button variant="ghost" onClick={() => { enableCustomCss(); setSafeMode(false); }}>Wieder einschalten</Button>
        </div>
      )}
      {editing && (
        <textarea
          aria-label="Eigenes CSS"
          value={draft}
          spellCheck={false}
          rows={12}
          maxLength={65536}
          onChange={(e) => setDraft(e.target.value)}
          className={`${inputClass} font-mono text-xs`}
        />
      )}
      <p className="mt-2 text-xs text-white/50">
        Erlaubt sind normale Regeln, eingebettete Bilder (data:) und Pfade auf diesem Server, höchstens 64 KB. @import und fremde
        Adressen lehnt der Server ab, ebenso Leer- und Steuerzeichen in einer Adresse (in data: als %20 schreiben). Ist die
        Oberfläche kaputt gestylt, öffne die Seite mit ?ohne-css=1 am Ende der Adresse: Dann bleibt das eigene CSS in diesem Tab aus.
      </p>
      <div className="mt-2"><NoticeLine notice={notice} /></div>
    </Card>
  );
}
