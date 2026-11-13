/**
 * Server anlegen und bearbeiten (Einstellungen → Server & Zugänge). Die Prüfung der Eingaben macht das Backend, seine
 * deutschen Meldungen erscheinen am Feld. Beim Bearbeiten geht nur das über die Leitung, was sich geändert hat: Eine
 * unveränderte Adresse mitzuschicken leert sonst ohne Grund die offenen SSH-Verbindungen.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";

import { api, ApiError } from "../../../lib/api";
import { CANCELLED, withConfirmation } from "../../../lib/confirmation";
import { fieldErrors, refreshHosts, type HostOut } from "../../../lib/hosts";
import { Badge, Button, NoticeLine, Toggle, inputClass, type Notice } from "../ui";
import { FormField } from "./FormField";
import { useEdition } from "../../../state/edition";

const TAG_OPTIONS = [
  { tag: "docker", label: "Docker verwalten", hint: "Container, Stacks, Images und Updates verwalten." },
  { tag: "docker-bedienen", label: "Docker nur bedienen", hint: "Container ansehen, Logs lesen und Container starten, stoppen oder neu starten." },
  { tag: "gameserver", label: "Gameserver", hint: "Zeigt den Server im Modul Gameserver an." },
  { tag: "dashboard", label: "Nodvard Deck läuft hier", hint: "Sperrt den Dienst-Neustart von Docker und containerd. Nodvard Shield plant automatische Neustarts dieses Servers zuletzt." },
  { tag: "nas", label: "NAS", hint: "Kennzeichnet einen Netzwerkspeicher. Schaltet keine zusätzliche Funktion frei." },
];

const UMLAUTS: Record<string, string> = { ä: "ae", ö: "oe", ü: "ue", ß: "ss" };

/** Kurzname aus dem Anzeigenamen: „Müllers NAS“ wird „muellers-nas“, passend zur Regel des Servers. */
export function shortNameFrom(displayName: string): string {
  return displayName
    .normalize("NFC")
    .toLowerCase()
    .replace(/[äöüß]/g, (c) => UMLAUTS[c])
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9_]+/g, "-")
    .replace(/^[-_]+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/, "");
}

function parseTags(text: string): string[] {
  return text.split(/[,\s]+/).map((t) => t.trim().toLowerCase()).filter(Boolean);
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && [...a].sort().every((value, i) => value === [...b].sort()[i]);
}

const PASSWORD_HINT = "Ändert sich die Adresse, wird das gespeicherte Passwort gelöscht. Du gibst es danach neu ein.";

function addressHint(host: HostOut | undefined): string | undefined {
  const passwordHint = host?.credential?.kind === "ssh_password" ? PASSWORD_HINT : undefined;
  let providerHint: string | undefined;
  if (host?.provider_ext_id) {
    providerHint = host.kind === "vm" || host.kind === "lxc"
      ? "Eine von Hand eingetragene Adresse bleibt stehen, solange der Gast keine eigene meldet."
      : "Wird beim nächsten Abgleich von der Erweiterung überschrieben.";
  }
  const parts = [providerHint, passwordHint].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : undefined;
}

export function HostForm({
  host, onSaved, onCancel,
}: {
  /** Ohne `host`: neuen Server anlegen. */
  host?: HostOut;
  onSaved: (host: HostOut) => void;
  onCancel?: () => void;
}) {
  const queryClient = useQueryClient();
  const tagsId = useId();
  const edition = useEdition();
  const tagOptions = TAG_OPTIONS.filter(({ tag }) => edition.choose_modules || (tag !== "gameserver" && tag !== "dashboard"));
  const isPresetTag = (tag: string) => tagOptions.some((option) => option.tag === tag);
  const editing = host !== undefined;
  const managedTags = host?.managed_tags ?? [];
  const manualTags = (host?.tags ?? []).filter((t) => !managedTags.includes(t));

  const [name, setName] = useState("");
  // Bis jemand den Kurznamen selbst ändert, folgt er dem Anzeigenamen.
  const [nameTouched, setNameTouched] = useState(false);
  const [displayName, setDisplayName] = useState(host?.display_name ?? "");
  const [address, setAddress] = useState(host?.address ?? "");
  const [osFamily, setOsFamily] = useState(host?.os_family ?? "linux");
  const [selectedTags, setSelectedTags] = useState(manualTags.filter(isPresetTag));
  const [tagsText, setTagsText] = useState(manualTags.filter((tag) => !isPresetTag(tag)).join(", "));
  const [enabled, setEnabled] = useState(host?.enabled ?? true);
  const [isManaged, setIsManaged] = useState(host?.is_managed ?? true);
  const [fullSudoWanted, setFullSudoWanted] = useState(host?.sudo_full_wanted ?? false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const tags = [...new Set([...selectedTags, ...parseTags(tagsText)])].filter((tag) => !managedTags.includes(tag));

  function toggleTag(tag: string, checked: boolean) {
    setSelectedTags(checked ? [...selectedTags, tag] : selectedTags.filter((t) => t !== tag));
    if (!checked && parseTags(tagsText).includes(tag)) setTagsText(parseTags(tagsText).filter((t) => t !== tag).join(", "));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setNotice(null);
    setBusy(true);
    try {
      let saved: HostOut;
      if (host === undefined) {
        const shortName = name.trim().toLowerCase();
        saved = await api.post<HostOut>("/hosts", {
          name: shortName,
          display_name: displayName.trim() || shortName,
          address: address.trim(),
          os_family: osFamily,
          tags,
        });
      } else {
        const changes: Record<string, unknown> = {};
        const shownName = displayName.trim() || host.name;
        if (shownName !== host.display_name) changes.display_name = shownName;
        // Die Adresse nur, wenn sie sich geändert hat.
        if (address.trim() !== host.address) changes.address = address.trim();
        if (osFamily !== host.os_family) changes.os_family = osFamily;
        if (enabled !== host.enabled) changes.enabled = enabled;
        if (isManaged !== host.is_managed) changes.is_managed = isManaged;
        if (fullSudoWanted !== (host.sudo_full_wanted ?? false)) changes.sudo_full_wanted = fullSudoWanted;
        if (!sameList(tags, manualTags)) changes.tags = tags;
        if (Object.keys(changes).length === 0) {
          setNotice({ kind: "ok", text: "Nichts geändert." });
          return;
        }
        // Eine neue Adresse löscht gespeicherte SSH-Passwörter: dann fragt der Server nach Passwort und Code.
        const patched = await withConfirmation((confirmation) => api.patch<HostOut>(`/hosts/${host.id}`, { ...changes, ...confirmation }));
        if (patched === CANCELLED) return;
        saved = patched;
        setNotice({ kind: "ok", text: "Gespeichert." });
      }
      await refreshHosts(queryClient);
      onSaved(saved);
    } catch (err) {
      const perField = fieldErrors(err);
      setErrors(perField);
      // Ohne Feld-Zuordnung (409: Name schon vergeben, Netzfehler) steht der Text oben.
      if (Object.keys(perField).length === 0) setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="space-y-4">
      <NoticeLine notice={notice} />
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField
          label="Anzeigename"
          hint={host?.provider_ext_id ? "Wird beim nächsten Abgleich von der Erweiterung überschrieben." : "So heißt der Server in den Listen, z. B. „Raspberry Pi“."}
          error={errors.display_name}
        >
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={displayName}
              onChange={(e) => {
                setDisplayName(e.target.value);
                if (!editing && !nameTouched) setName(shortNameFrom(e.target.value));
              }}
              placeholder="Raspberry Pi"
            />
          )}
        </FormField>
        {editing ? (
          <div className="text-sm">
            <p className="mb-1.5 text-white/70">Kurzname</p>
            <p className="rounded-lg border border-white/10 bg-black/15 px-3 py-2 font-mono text-sm text-white/80">{host.name}</p>
            <p className="mt-1 text-xs text-white/40">Der Kurzname lässt sich nicht ändern.</p>
          </div>
        ) : (
          <FormField label="Kurzname" hint="Ergibt sich aus dem Anzeigenamen. Klein, ohne Leerzeichen, lässt sich später nicht ändern." error={errors.name}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                value={name}
                onChange={(e) => { setNameTouched(e.target.value !== ""); setName(e.target.value); }}
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="bastel-pi"
              />
            )}
          </FormField>
        )}
        <FormField label="Adresse (IP oder Name)" hint={addressHint(host) ?? "Nur die Adresse, ohne http:// und ohne Port, z. B. 192.168.2.40."} error={errors.address}>
          {(p) => <input {...p} className={inputClass} value={address} onChange={(e) => setAddress(e.target.value)} inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="192.168.2.40" />}
        </FormField>
        <FormField
          label="Betriebssystem"
          hint={osFamily === "windows" ? "Für Windows-Server gibt es keinen Einrichtungsbefehl und keine Rechte-Prüfung." : undefined}
          error={errors.os_family}
        >
          {(p) => (
            <select {...p} className={inputClass} value={osFamily} onChange={(e) => setOsFamily(e.target.value)}>
              <option value="linux">Linux</option>
              <option value="windows">Windows</option>
            </select>
          )}
        </FormField>
        <fieldset className="space-y-3 sm:col-span-2" aria-describedby={`${tagsId}-hint`}>
          <legend className="text-sm text-white/70">Markierungen</legend>
          <p id={`${tagsId}-hint`} className="text-xs text-white/40">
            Die Haken setzen die üblichen Markierungen. Bei geänderten Modul-Filtern trägst du die passende Markierung unten ein.
            Sind beide Docker-Haken gesetzt, gilt „Docker verwalten“.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {tagOptions.map(({ tag, label, hint }) => (
              <div key={tag} className="flex items-start gap-2 text-sm">
                <input
                  id={`${tagsId}-${tag}`} type="checkbox" className="mt-1 accent-sky-400"
                  checked={managedTags.includes(tag) || tags.includes(tag)} disabled={managedTags.includes(tag)}
                  onChange={(e) => toggleTag(tag, e.target.checked)} aria-describedby={`${tagsId}-${tag}-hint`}
                />
                <div>
                  <label htmlFor={`${tagsId}-${tag}`} className="text-white/80">{label}</label>
                  <p id={`${tagsId}-${tag}-hint`} className="text-xs text-white/40">
                    {hint}{tag === "docker" && edition.choose_modules && " Nodvard Shield überwacht diese Server mit der üblichen Einstellung."}
                    {managedTags.includes(tag) && " Von einer Erweiterung verwaltet, nicht änderbar."}
                  </p>
                </div>
              </div>
            ))}
          </div>
          <FormField label="Eigene Markierungen" hint="Mit Komma oder Leerzeichen trennen." error={errors.tags}>
            {(p) => <input {...p} className={inputClass} value={tagsText} onChange={(e) => setTagsText(e.target.value)} autoCapitalize="none" placeholder="keller, web" />}
          </FormField>
        </fieldset>
      </div>
      {managedTags.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-white/45">
          Von Erweiterungen verwaltet (nicht änderbar):
          {managedTags.map((t) => <Badge key={t}>{t}</Badge>)}
        </p>
      )}

      {editing && edition.choose_modules && (
        <details className="rounded-lg border border-white/[0.08] px-3 py-2">
          <summary className="cursor-pointer text-sm text-white/70">Erweitert</summary>
          <div className="mt-3 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div className="text-sm">
                <p>Messwerte sammeln</p>
                <p className="text-xs text-white/40">Belegung von Prozessor und Speicher für die Verlaufs-Diagramme aufzeichnen.</p>
              </div>
              <Toggle checked={enabled} onChange={setEnabled} label="Messwerte sammeln" />
            </div>
            <div className="flex items-start justify-between gap-3">
              <div className="text-sm">
                <p>Für Skripte und Nodvard Shield verwenden</p>
                <p className="text-xs text-white/40">Der Server erscheint dort in der Serverauswahl.</p>
              </div>
              <Toggle checked={isManaged} onChange={setIsManaged} label="Für Skripte und Nodvard Shield verwenden" />
            </div>
            {osFamily === "linux" && (
              <div className="flex items-start justify-between gap-3">
                <div className="text-sm">
                  <p>Volle sudo-Regel gewollt</p>
                  <p className="text-xs text-white/40">
                    Nur, wenn du über Nodvard Deck eigene Befehle mit sudo startest, etwa in Skripten. Dann rät „Verbindung prüfen“
                    nicht mehr, die volle Regel zu entfernen. Sie entspricht root.
                  </p>
                </div>
                <Toggle checked={fullSudoWanted} onChange={setFullSudoWanted} label="Volle sudo-Regel gewollt" />
              </div>
            )}
          </div>
        </details>
      )}

      <div className="flex flex-wrap justify-end gap-2">
        {onCancel && <Button variant="ghost" onClick={onCancel}>Abbrechen</Button>}
        <Button type="submit" variant="primary" busy={busy}>{editing ? "Speichern" : "Anlegen"}</Button>
      </div>
    </form>
  );
}
