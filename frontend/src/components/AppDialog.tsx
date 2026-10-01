/**
 * „+ App hinzufuegen“ / „App bearbeiten“: Formular im Dialog (Name, Adresse, Symbol, Farbe, Gruppe, Server,
 * Tab-Verhalten). Prueft beim Speichern mit denselben Regeln wie das Backend (lib/apps.ts) und zeigt Fehler am
 * Feld; was der Server trotzdem ablehnt (422), landet ebenfalls am passenden Feld.
 *
 * Symbol: nur aus der festen Auswahl (lib/appIcons.ts) oder ein einzelnes Emoji -- keine Bild-Adressen.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ApiError } from "../lib/api";
import { APP_ICON_LABELS, APP_ICON_NAMES, appIconComponent, isAppIconName } from "../lib/appIcons";
import {
  type AppFormErrors,
  type AppFormField,
  type AppFormValues,
  APP_COLORS,
  EMPTY_FORM,
  formFromApp,
  GROUP_MAX,
  NAME_MAX,
  normalizeUrl,
  useAppActions,
  validateAppForm,
} from "../lib/apps";
import type { AppTileOut, HostOut } from "../lib/overview";
import { FormField } from "../routes/settings/hosts/FormField";
import { Button, inputClass } from "../routes/settings/ui";
import { AppAvatar } from "./AppTile";

const FIELDS: AppFormField[] = ["name", "url", "icon", "color", "group", "host_id"];

/** 422 vom Server -> Meldungen an den Feldern (`loc` = ["body", "url"]); `null`, wenn es nichts Passendes gibt. */
function serverErrors(err: unknown): AppFormErrors | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const detail = (err.detail as { detail?: unknown } | undefined)?.detail;
  if (!Array.isArray(detail)) return null;
  const errors: AppFormErrors = {};
  for (const item of detail) {
    const { loc, msg } = (item ?? {}) as { loc?: unknown[]; msg?: unknown };
    const field = Array.isArray(loc) ? loc[loc.length - 1] : undefined;
    if (typeof msg === "string" && FIELDS.includes(field as AppFormField)) errors[field as AppFormField] = msg;
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

function errorMessage(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "Das hat nicht geklappt. Bitte versuch es noch einmal.";
}

export function AppDialog({
  open, onOpenChange, app, groups, hosts, onCloseAutoFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Beim Schliessen: wohin der Fokus zurueckgeht (der Dialog hat keinen eigenen Ausloeser, Radix wuesste es sonst nicht). */
  onCloseAutoFocus?: (event: Event) => void;
  /** Gesetzt = bearbeiten, sonst neu anlegen. */
  app?: AppTileOut | null;
  /** Gruppen, die es schon gibt (Vorschlaege). */
  groups: string[];
  hosts: HostOut[];
}) {
  const actions = useAppActions();
  const [values, setValues] = useState<AppFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<AppFormErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  // Beim Oeffnen frisch starten: leer (neu) oder mit den Werten der App (bearbeiten).
  useEffect(() => {
    if (!open) return;
    setValues(app ? formFromApp(app) : EMPTY_FORM);
    setErrors({});
    setFailure(null);
    setBusy(false);
  }, [open, app]);

  function set<K extends keyof AppFormValues>(key: K, value: AppFormValues[K]) {
    setValues((v) => ({ ...v, [key]: value }));
    if (key in errors) setErrors((e) => ({ ...e, [key as AppFormField]: undefined }));
    setFailure(null);
  }

  /** Springt zum ersten Feld mit Fehler -- auf dem Handy liegt es sonst womoeglich ausserhalb des sichtbaren Bereichs. */
  function focusFirstError() {
    setTimeout(() => form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found = validateAppForm(values);
    if (Object.keys(found).length > 0) {
      setErrors(found);
      focusFirstError();
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      if (app) await actions.update(app.id, values);
      else await actions.create(values);
      onOpenChange(false);
    } catch (err) {
      const fieldErrors = serverErrors(err);
      if (fieldErrors) {
        setErrors(fieldErrors);
        focusFirstError();
      } else setFailure(errorMessage(err));
      setBusy(false);
    }
  }

  const iconIsEmoji = values.icon !== "" && !isAppIconName(values.icon);
  const preview = { name: values.name.trim() || "App", icon: values.icon || null, color: values.color || null };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <Dialog.Content
          onCloseAutoFocus={onCloseAutoFocus}
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-white/10 bg-[var(--color-surface)] shadow-xl"
        >
          <form ref={form} onSubmit={(e) => void submit(e)} noValidate className="flex min-h-0 flex-1 flex-col" data-testid="app-form">
            <div className="min-h-0 flex-1 overflow-y-auto p-4" data-testid="app-form-body">
              <Dialog.Title className="text-lg font-semibold">{app ? "App bearbeiten" : "App hinzufügen"}</Dialog.Title>
              <Dialog.Description className="mb-4 mt-1 text-sm text-white/60">
                Eine Kachel mit einem Link im Cockpit, zum Beispiel für deinen Router, die NAS-Oberfläche oder Pi-hole.
                Nodvard Deck ruft die Adresse nicht selbst auf – sie ist nur ein Link für dich und alle anderen Benutzer.
              </Dialog.Description>

              <div className="space-y-4">
                <FormField label="Name" error={errors.name}>
                  {(p) => (
                    <input
                      {...p}
                      value={values.name}
                      onChange={(e) => set("name", e.target.value)}
                      maxLength={NAME_MAX * 2}
                      autoFocus
                      placeholder="z. B. Router"
                      autoComplete="off"
                      className={inputClass}
                    />
                  )}
                </FormField>

                <FormField
                  label="Adresse"
                  error={errors.url}
                  hint="Mit http:// oder https://, ohne Benutzername und Passwort. Fehlt der Anfang, ergänzen wir http://."
                >
                  {(p) => (
                    <input
                      {...p}
                      type="text"
                      inputMode="url"
                      value={values.url}
                      onChange={(e) => set("url", e.target.value)}
                      onBlur={() => {
                        const next = normalizeUrl(values.url);
                        if (values.url.trim() && next !== values.url) set("url", next);
                      }}
                      placeholder="http://192.168.2.1"
                      autoComplete="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      className={inputClass}
                    />
                  )}
                </FormField>

                <fieldset className="text-sm">
                  <legend className="mb-1.5 flex w-full items-center gap-2 text-white/70">
                    Symbol
                    <span className="ml-auto flex items-center gap-2 text-xs text-white/40">
                      Vorschau <AppAvatar app={preview} />
                    </span>
                  </legend>
                  <div className="grid grid-cols-8 gap-1 sm:grid-cols-10" role="group" aria-label="Symbol wählen">
                    <button
                      type="button"
                      aria-pressed={values.icon === ""}
                      title="Kein Symbol (Anfangsbuchstaben)"
                      aria-label="Kein Symbol"
                      onClick={() => set("icon", "")}
                      className={`grid h-9 place-items-center rounded-lg border text-xs font-semibold ${values.icon === "" ? "border-[var(--color-accent)] bg-white/[0.12] text-white" : "border-white/10 bg-white/[0.04] text-white/50 hover:bg-white/10"}`}
                    >
                      Aa
                    </button>
                    {APP_ICON_NAMES.map((name) => {
                      const Component = appIconComponent(name)!;
                      const selected = values.icon === name;
                      return (
                        <button
                          key={name}
                          type="button"
                          aria-pressed={selected}
                          aria-label={APP_ICON_LABELS[name]}
                          title={APP_ICON_LABELS[name]}
                          onClick={() => set("icon", name)}
                          className={`grid h-9 place-items-center rounded-lg border ${selected ? "border-[var(--color-accent)] bg-white/[0.12] text-white" : "border-white/10 bg-white/[0.04] text-white/60 hover:bg-white/10 hover:text-white"}`}
                        >
                          <Component size={16} strokeWidth={1.8} aria-hidden />
                        </button>
                      );
                    })}
                  </div>
                  <FormField label="… oder ein Emoji" error={errors.icon} className="mt-3">
                    {(p) => (
                      <input
                        {...p}
                        value={iconIsEmoji ? values.icon : ""}
                        onChange={(e) => set("icon", e.target.value.trim())}
                        placeholder="z. B. 🏠"
                        maxLength={24}
                        autoComplete="off"
                        className={`${inputClass} sm:w-40`}
                      />
                    )}
                  </FormField>
                </fieldset>

                <fieldset className="text-sm">
                  <legend className="mb-1.5 text-white/70">Farbe</legend>
                  <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Farbe wählen">
                    <button
                      type="button"
                      aria-pressed={values.color === ""}
                      onClick={() => set("color", "")}
                      className={`rounded-full border px-3 py-1 text-xs ${values.color === "" ? "border-[var(--color-accent)] bg-white/[0.12] text-white" : "border-white/10 bg-white/[0.04] text-white/60 hover:bg-white/10"}`}
                    >
                      Automatisch
                    </button>
                    {APP_COLORS.map((c) => (
                      <button
                        key={c.value}
                        type="button"
                        aria-pressed={values.color === c.value}
                        aria-label={c.label}
                        title={c.label}
                        onClick={() => set("color", c.value)}
                        style={{ background: c.value }}
                        className={`h-7 w-7 rounded-full border-2 ${values.color === c.value ? "border-white" : "border-transparent hover:border-white/50"}`}
                      />
                    ))}
                  </div>
                  {errors.color && <p role="alert" className="mt-1 text-xs text-red-300">{errors.color}</p>}
                </fieldset>

                <FormField label="Gruppe (optional)" error={errors.group} hint="Zum Sortieren und Filtern, zum Beispiel „Netzwerk“. Leer lassen, wenn du keine brauchst.">
                  {(p) => (
                    <>
                      <input
                        {...p}
                        list="app-groups"
                        value={values.group}
                        onChange={(e) => set("group", e.target.value)}
                        maxLength={GROUP_MAX * 2}
                        placeholder="z. B. Netzwerk"
                        autoComplete="off"
                        className={inputClass}
                      />
                      <datalist id="app-groups">
                        {groups.map((g) => <option key={g} value={g} />)}
                      </datalist>
                    </>
                  )}
                </FormField>

                {hosts.length > 0 && (
                  <FormField label="Gehört zu Server (optional)" error={errors.host_id} hint="Nur zur Anzeige auf der Kachel.">
                    {(p) => (
                      <select {...p} value={values.host_id} onChange={(e) => set("host_id", e.target.value)} className={inputClass}>
                        <option value="">Kein Server</option>
                        {hosts.map((h) => <option key={h.id} value={h.id}>{h.display_name || h.name}</option>)}
                      </select>
                    )}
                  </FormField>
                )}

                <label className="flex items-center gap-2 text-sm text-white/80">
                  <input
                    type="checkbox"
                    checked={values.open_in_new_tab}
                    onChange={(e) => set("open_in_new_tab", e.target.checked)}
                  />
                  In einem neuen Tab öffnen
                </label>
              </div>
            </div>

            {/* Fester Fussbereich: eine Meldung ohne Feldbezug (Obergrenze, App weg, Server nicht erreichbar) steht
                hier, wo man sie sieht -- im scrollenden Teil laege sie auf dem Handy unter dem Bildschirmrand. */}
            <div className="flex-none border-t border-white/[0.08] px-4 py-3" data-testid="app-form-footer">
              {failure && (
                <p role="alert" className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                  {failure}
                </p>
              )}
              <div className="flex justify-end gap-2">
                <Button disabled={busy} onClick={() => onOpenChange(false)}>Abbrechen</Button>
                <Button type="submit" variant="primary" busy={busy}>{app ? "Speichern" : "Hinzufügen"}</Button>
              </div>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
