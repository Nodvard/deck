/**
 * Server anlegen und bearbeiten (Einstellungen -> Server & Zugaenge). Die Pruefung der
 * Eingaben macht das Backend; seine deutschen Meldungen erscheinen direkt am Feld. Beim
 * Bearbeiten geht nur das ueber die Leitung, was sich wirklich geaendert hat -- vor allem die
 * Adresse: eine unveraenderte Adresse mitzuschicken leert sonst ohne Grund die offenen
 * SSH-Verbindungen (Hinweis aus dem Review von PR A).
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, ApiError } from "../../../lib/api";
import { fieldErrors, refreshHosts, type HostOut } from "../../../lib/hosts";
import { Badge, Button, NoticeLine, Toggle, inputClass, type Notice } from "../ui";
import { FormField } from "./FormField";

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
  const editing = host !== undefined;
  const managedTags = host?.managed_tags ?? [];
  const manualTags = (host?.tags ?? []).filter((t) => !managedTags.includes(t));

  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState(host?.display_name ?? "");
  const [address, setAddress] = useState(host?.address ?? "");
  const [osFamily, setOsFamily] = useState(host?.os_family ?? "linux");
  const [tagsText, setTagsText] = useState(manualTags.join(", "));
  const [enabled, setEnabled] = useState(host?.enabled ?? true);
  const [isManaged, setIsManaged] = useState(host?.is_managed ?? true);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);

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
          tags: parseTags(tagsText),
        });
      } else {
        const changes: Record<string, unknown> = {};
        const shownName = displayName.trim() || host.name;
        if (shownName !== host.display_name) changes.display_name = shownName;
        // Die Adresse nur, wenn sie sich geaendert hat.
        if (address.trim() !== host.address) changes.address = address.trim();
        if (osFamily !== host.os_family) changes.os_family = osFamily;
        if (enabled !== host.enabled) changes.enabled = enabled;
        if (isManaged !== host.is_managed) changes.is_managed = isManaged;
        const tags = parseTags(tagsText);
        if (!sameList(tags, manualTags)) changes.tags = tags;
        if (Object.keys(changes).length === 0) {
          setNotice({ kind: "ok", text: "Nichts geändert." });
          return;
        }
        saved = await api.patch<HostOut>(`/hosts/${host.id}`, changes);
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
        {editing ? (
          <div className="text-sm">
            <p className="mb-1.5 text-white/70">Kurzname</p>
            <p className="rounded-lg border border-white/10 bg-black/15 px-3 py-2 font-mono text-sm text-white/80">{host.name}</p>
            <p className="mt-1 text-xs text-white/40">Der Kurzname lässt sich nicht ändern.</p>
          </div>
        ) : (
          <FormField label="Kurzname" hint="klein, ohne Leerzeichen, z. B. bastel-pi" error={errors.name}>
            {(p) => <input {...p} className={inputClass} value={name} onChange={(e) => setName(e.target.value)} autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="bastel-pi" />}
          </FormField>
        )}
        <FormField
          label="Anzeigename"
          hint={host?.provider_ext_id ? "Wird beim nächsten Abgleich von der Erweiterung überschrieben." : "So heißt der Server in den Listen. Leer lassen = Kurzname."}
          error={errors.display_name}
        >
          {(p) => <input {...p} className={inputClass} value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Raspberry Pi" />}
        </FormField>
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
        <FormField
          className="sm:col-span-2"
          label="Markierungen"
          hint="Mit Komma trennen. docker = Service-Matrix und Container-Wache nehmen den Server; gameserver = erscheint bei Gameserver."
          error={errors.tags}
        >
          {(p) => <input {...p} className={inputClass} value={tagsText} onChange={(e) => setTagsText(e.target.value)} autoCapitalize="none" placeholder="docker, gameserver" />}
        </FormField>
      </div>
      {managedTags.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-white/45">
          Von Erweiterungen verwaltet (nicht änderbar):
          {managedTags.map((t) => <Badge key={t}>{t}</Badge>)}
        </p>
      )}

      {editing && (
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
