/**
 * Benutzerverwaltung unter Einstellungen -> Benutzer (`/settings/users`): Konten
 * anlegen, bearbeiten (Name, E-Mail, Rollen, Passwort zuruecksetzen), sperren und
 * entfernen. Ruft die `users.py`-API (backend/src/nodvard_deck/api/v1/users.py).
 */
import { Pencil, Plus, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { confirmDialog } from "../state/dialogs";
import { useAuthStore } from "../state/auth";
import { Badge, Button, Card, Field, NoticeLine, PageHeader, inputClass, type Notice } from "./settings/ui";

interface Role {
  id: string;
  name: string;
  description: string;
  is_builtin: boolean;
}

interface UserRow {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  is_active: boolean;
  is_owner: boolean;
  totp_enabled?: boolean;
  roles: Role[];
}

const ROLE_HINTS: Record<string, string> = {
  admin: "Volle Verwaltung inkl. Benutzer und Einstellungen",
  operator: "Server bedienen, Aktionen bis mittleres Risiko freigeben",
  viewer: "Nur ansehen, nichts ändern",
};

const EMPTY_FORM = { username: "", password: "", display_name: "", email: "", role_ids: [] as string[] };

function errorOf(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function RolePicker({ roles, selected, onToggle }: { roles: Role[]; selected: string[]; onToggle: (id: string) => void }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {roles.map((r) => (
        <label
          key={r.id}
          className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm transition ${
            selected.includes(r.id) ? "border-[var(--color-accent)] bg-white/[0.05]" : "border-white/10 hover:border-white/25"
          }`}
        >
          <input type="checkbox" className="mt-0.5" checked={selected.includes(r.id)} onChange={() => onToggle(r.id)} aria-label={r.name} />
          <span>
            <span className="block font-medium">{r.name}</span>
            <span className="block text-xs text-white/45">{ROLE_HINTS[r.name] ?? (r.is_builtin ? "" : r.description)}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

export function UsersPage(): JSX.Element {
  const currentUserId = useAuthStore((s) => s.user?.id);
  const canWrite = useAuthStore((s) => s.hasPermission)("users.write");
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<UserRow | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [resetNotice, setResetNotice] = useState<Notice>(null);
  const [resetBusy, setResetBusy] = useState(false);

  function load() {
    setError(null);
    Promise.all([api.get<UserRow[]>("/users"), api.get<Role[]>("/roles")])
      .then(([usersData, rolesData]) => { setUsers(usersData); setRoles(rolesData); })
      .catch((err: unknown) => setError(errorOf(err)));
  }

  useEffect(() => { load(); }, []);

  async function submitCreate(e: React.FormEvent) {
    e.preventDefault();
    setNotice(null);
    try {
      await api.post("/users", {
        username: form.username, password: form.password,
        display_name: form.display_name, email: form.email || null, role_ids: form.role_ids,
      });
      setNotice({ kind: "ok", text: `Benutzer „${form.username}“ angelegt.` });
      setForm(EMPTY_FORM);
      setShowForm(false);
      load();
    } catch (err) {
      setNotice({ kind: "error", text: errorOf(err) });
    }
  }

  async function toggleActive(user: UserRow) {
    setNotice(null);
    try {
      await api.patch(`/users/${user.id}`, { is_active: !user.is_active });
      load();
    } catch (err) {
      setNotice({ kind: "error", text: errorOf(err) });
    }
  }

  async function resetTwoFactor() {
    if (!resetTarget) return;
    setNotice(null);
    setResetNotice(null);
    setResetBusy(true);
    try {
      await api.post(`/users/${resetTarget.id}/reset-2fa`, { current_password: resetPassword });
      setNotice({ kind: "ok", text: `Zwei-Faktor-Anmeldung von „${resetTarget.username}“ zurückgesetzt, alle Anmeldungen beendet.` });
      setResetTarget(null);
      setResetPassword("");
      load();
    } catch (err) {
      setResetNotice({ kind: "error", text: errorOf(err) });
    } finally {
      setResetBusy(false);
    }
  }

  async function removeUser(user: UserRow) {
    const ok = await confirmDialog(`"${user.username}" wirklich entfernen?`, { danger: true, confirmLabel: "Entfernen" });
    if (!ok) return;
    setNotice(null);
    try {
      await api.delete(`/users/${user.id}`);
      load();
    } catch (err) {
      setNotice({ kind: "error", text: errorOf(err) });
    }
  }

  if (error) return <NoticeLine notice={{ kind: "error", text: `Fehler: ${error}` }} />;
  if (!users) return <p className="text-sm text-white/50">Lade …</p>;

  return (
    <div>
      <PageHeader
        title="Benutzer"
        description="Wer Zugang hat und was er darf. Rechte werden über Rollen vergeben."
        actions={canWrite && (
          <Button variant={showForm ? "secondary" : "primary"} onClick={() => setShowForm((v) => !v)}>
            {showForm ? "Abbrechen" : <><Plus size={14} /> Neuer Benutzer</>}
          </Button>
        )}
      />
      <NoticeLine notice={notice} />

      {showForm && (
        <form onSubmit={(e) => void submitCreate(e)}>
          <Card
            title="Neuen Benutzer anlegen"
            description="Teile dem neuen Benutzer Benutzername und Passwort mit; das Passwort kann er danach unter „Mein Konto“ ändern."
            footer={<Button type="submit" variant="primary"><UserPlus size={14} /> Anlegen</Button>}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Benutzername" hint="Nur Kleinbuchstaben, z. B. „admin“. Großbuchstaben werden automatisch umgewandelt.">
                <input required minLength={3} autoCapitalize="none" spellCheck={false} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} className={inputClass} />
              </Field>
              <Field label="Passwort" hint="Mindestens 8 Zeichen">
                <input required type="password" minLength={8} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} className={inputClass} />
              </Field>
              <Field label="Anzeigename">
                <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} className={inputClass} />
              </Field>
              <Field label="E-Mail (optional)">
                <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={inputClass} />
              </Field>
            </div>
            <p className="mb-2 mt-4 text-sm text-white/70">Rollen</p>
            <RolePicker
              roles={roles}
              selected={form.role_ids}
              onToggle={(id) => setForm((f) => ({ ...f, role_ids: f.role_ids.includes(id) ? f.role_ids.filter((r) => r !== id) : [...f.role_ids, id] }))}
            />
          </Card>
        </form>
      )}

      {resetTarget && (
        <Card
          title={`Zwei-Faktor von „${resetTarget.username}“ zurücksetzen`}
          description="Die Person wird überall abgemeldet und kann sich danach nur mit dem Passwort anmelden, bis sie die Zwei-Faktor-Anmeldung unter „Mein Konto“ neu einrichtet."
          footer={
            <>
              <Button variant="ghost" onClick={() => { setResetTarget(null); setResetPassword(""); setResetNotice(null); }}>Abbrechen</Button>
              <Button variant="danger" busy={resetBusy} disabled={!resetPassword} onClick={() => void resetTwoFactor()}>Zurücksetzen</Button>
            </>
          }
        >
          <NoticeLine notice={resetNotice} />
          <div className="max-w-sm">
            <Field label="Dein eigenes Passwort" hint="Zur Sicherheit wird dein aktuelles Passwort verlangt.">
              <input type="password" autoComplete="current-password" value={resetPassword} onChange={(e) => setResetPassword(e.target.value)} className={inputClass} />
            </Field>
          </div>
        </Card>
      )}

      <ul className="panel divide-y divide-white/[0.06]">
        {users.map((user) => {
          const name = user.display_name || user.username;
          return (
            <li key={user.id} data-testid={`user-${user.id}`} className="px-5 py-4">
              <div className="flex flex-wrap items-center gap-4">
                <span className={`grid h-10 w-10 flex-none place-items-center rounded-full text-sm font-semibold ${user.is_active ? "accent-gradient text-white" : "bg-white/10 text-white/50"}`}>
                  {name.slice(0, 2).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{user.username}</span>
                    {user.display_name && <span className="text-sm text-white/50">{user.display_name}</span>}
                    {user.id === currentUserId && <Badge tone="accent">Du</Badge>}
                    {user.is_owner && <Badge tone="warn">Inhaber</Badge>}
                    {user.totp_enabled && <Badge tone="good">Zwei-Faktor</Badge>}
                    {!user.is_active && <Badge tone="bad">Gesperrt</Badge>}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {user.email && <span className="mr-2 text-xs text-white/45">{user.email}</span>}
                    {user.roles.map((r) => <Badge key={r.id}>{r.name}</Badge>)}
                    {user.is_owner && user.roles.length === 0 && <span className="text-xs text-white/40">alle Rechte</span>}
                    {!user.is_owner && user.roles.length === 0 && <span className="text-xs text-white/40">keine Rollen</span>}
                  </div>
                </div>
                {canWrite && (
                  <div className="flex items-center gap-1.5">
                    <Button onClick={() => setEditingId(editingId === user.id ? null : user.id)}><Pencil size={13} /> Bearbeiten</Button>
                    {user.totp_enabled && !user.is_owner && user.id !== currentUserId && (
                      <Button onClick={() => { setResetTarget(user); setResetPassword(""); setResetNotice(null); }}>Zwei-Faktor zurücksetzen</Button>
                    )}
                    {!user.is_owner && user.id !== currentUserId && (
                      <Button onClick={() => void toggleActive(user)}>{user.is_active ? "Deaktivieren" : "Aktivieren"}</Button>
                    )}
                    {!user.is_owner && user.id !== currentUserId && (
                      <Button variant="danger" onClick={() => void removeUser(user)}>Entfernen</Button>
                    )}
                  </div>
                )}
              </div>
              {editingId === user.id && (
                <EditUser
                  user={user}
                  isSelf={user.id === currentUserId}
                  roles={roles}
                  onCancel={() => setEditingId(null)}
                  onSaved={(text) => { setEditingId(null); setNotice({ kind: "ok", text }); load(); }}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EditUser({ user, isSelf, roles, onCancel, onSaved }: { user: UserRow; isSelf: boolean; roles: Role[]; onCancel: () => void; onSaved: (text: string) => void }) {
  const [displayName, setDisplayName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email ?? "");
  const [roleIds, setRoleIds] = useState(user.roles.map((r) => r.id));
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  // Eigenes Passwort nur unter „Mein Konto“ (mit dem alten Passwort), das des Inhabers nur er selbst.
  const passwordLocked = isSelf || user.is_owner;

  async function save() {
    const patch: Record<string, unknown> = {};
    if (displayName !== user.display_name) patch.display_name = displayName;
    if (email !== (user.email ?? "")) patch.email = email || null;
    const before = user.roles.map((r) => r.id);
    if (roleIds.length !== before.length || roleIds.some((id) => !before.includes(id))) patch.role_ids = roleIds;
    if (password && !passwordLocked) {
      if (password.length < 8) return setNotice({ kind: "error", text: "Das neue Passwort muss mindestens 8 Zeichen haben." });
      patch.password = password;
    }
    if (Object.keys(patch).length === 0) return onCancel();
    setBusy(true);
    setNotice(null);
    try {
      await api.patch(`/users/${user.id}`, patch);
      onSaved(password ? `„${user.username}“ gespeichert, neues Passwort gesetzt.` : `„${user.username}“ gespeichert.`);
    } catch (err) {
      setNotice({ kind: "error", text: errorOf(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-4">
      <NoticeLine notice={notice} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Anzeigename">
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={inputClass} />
        </Field>
        <Field label="E-Mail">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
        </Field>
        {passwordLocked ? (
          <p className="text-xs text-white/50 sm:self-end">
            {isSelf
              ? "Dein eigenes Passwort änderst du unter „Mein Konto“."
              : "Das Passwort des Inhabers kann nur der Inhaber selbst ändern."}
          </p>
        ) : (
          <Field label="Neues Passwort setzen" hint="Leer lassen = unverändert">
            <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} />
          </Field>
        )}
      </div>
      {!user.is_owner && (
        <>
          <p className="mb-2 mt-4 text-sm text-white/70">Rollen</p>
          <RolePicker
            roles={roles}
            selected={roleIds}
            onToggle={(id) => setRoleIds((ids) => (ids.includes(id) ? ids.filter((r) => r !== id) : [...ids, id]))}
          />
        </>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>Abbrechen</Button>
        <Button variant="primary" busy={busy} onClick={() => void save()}>Speichern</Button>
      </div>
    </div>
  );
}
