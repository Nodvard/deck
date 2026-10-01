/**
 * Bausteine fuer alle Einstellungs-Bereiche, damit jede Unterseite gleich aussieht:
 * Kopfzeile, Karten mit Titel/Beschreibung, Formularfelder und Rueckmeldungen.
 */
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export const inputClass =
  "w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-white placeholder:text-white/30 outline-none transition focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--color-accent)_30%,transparent)] disabled:opacity-50";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-1 text-sm text-white/55">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, description, children, footer }: { title?: string; description?: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <section className="panel mb-5 overflow-hidden">
      {(title || description) && (
        <header className="border-b border-white/[0.06] px-5 py-4">
          {title && <h3 className="text-sm font-semibold">{title}</h3>}
          {description && <p className="mt-0.5 text-xs text-white/50">{description}</p>}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
      {footer && <footer className="flex items-center justify-end gap-2 border-t border-white/[0.06] bg-black/10 px-5 py-3">{footer}</footer>}
    </section>
  );
}

export function Field({ label, hint, children, className = "" }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block text-sm ${className}`}>
      <span className="mb-1.5 block text-white/70">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-white/40">{hint}</span>}
    </label>
  );
}

export function Button({
  children, onClick, variant = "secondary", type = "button", disabled, busy, title, className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  type?: "button" | "submit";
  disabled?: boolean;
  busy?: boolean;
  title?: string;
  className?: string;
}) {
  const styles = {
    primary: "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110",
    secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
    danger: "border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25",
    ghost: "text-white/60 hover:bg-white/[0.06] hover:text-white",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      title={title}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {busy && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  );
}

export type Notice = { kind: "ok" | "error"; text: string } | null;

export function NoticeLine({ notice }: { notice: Notice }) {
  if (!notice) return null;
  const ok = notice.kind === "ok";
  return (
    <p
      role={ok ? "status" : "alert"}
      className={`mb-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
        ok ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200"
      }`}
    >
      {ok ? <CheckCircle2 size={16} className="mt-0.5 flex-none" /> : <AlertCircle size={16} className="mt-0.5 flex-none" />}
      {notice.text}
    </p>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "accent" }) {
  const styles = {
    neutral: "bg-white/[0.08] text-white/70",
    good: "bg-emerald-500/15 text-emerald-300",
    warn: "bg-amber-500/15 text-amber-300",
    bad: "bg-red-500/15 text-red-300",
    accent: "bg-[color-mix(in_srgb,var(--color-accent)_20%,transparent)] text-white",
  }[tone];
  return <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ${styles}`}>{children}</span>;
}

/**
 * Zustand einer Erweiterung aus models/platform.py (enabled | disabled | error |
 * incompatible | uninstalling). "failed" bleibt als alter Name fuer "error" erhalten.
 * Frueher stand alles ausser "enabled" als "Aus" da, auch eine abgestuerzte
 * Erweiterung.
 */
export function ExtensionStateBadge({ state }: { state: string }) {
  switch (state) {
    case "enabled":
      return <Badge tone="good">Aktiv</Badge>;
    case "error":
    case "failed":
      return <Badge tone="bad">Fehler</Badge>;
    case "incompatible":
      return <Badge tone="warn">Nicht kompatibel</Badge>;
    case "uninstalling":
      return <Badge tone="warn">Wird entfernt</Badge>;
    default:
      return <Badge>Aus</Badge>;
  }
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 flex-none rounded-full transition disabled:opacity-50 ${checked ? "bg-[var(--color-accent)]" : "bg-white/15"}`}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${checked ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
