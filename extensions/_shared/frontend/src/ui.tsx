/**
 * Gemeinsame Bausteine fuer die Seiten der mitgelieferten Erweiterungen, damit
 * Inventar, Dokumente, Skripte & Co. genauso aussehen wie der Kern (Einstellungen).
 * Wird von jedem Extension-Bundle per esbuild mit eingebunden -- deshalb bewusst
 * ohne Abhaengigkeiten ausser React (Icons als kleine Inline-SVGs).
 */
import type { ReactNode } from "react";

export const inputClass =
  "w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-white placeholder:text-white/30 outline-none transition focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--color-accent)_30%,transparent)] disabled:opacity-50";

type IconName =
  | "plus" | "search" | "trash" | "edit" | "download" | "upload" | "chevron-right" | "chevron-down" | "play"
  | "x" | "tag" | "folder" | "package" | "file" | "copy" | "eye" | "refresh" | "clock" | "code" | "map-pin";

const PATHS: Record<IconName, ReactNode> = {
  plus: <path d="M12 5v14M5 12h14" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  trash: <><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></>,
  edit: <path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" />,
  download: <path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" />,
  upload: <path d="M12 20V9m0 0-4 4m4-4 4 4M5 4h14" />,
  "chevron-right": <path d="m9 6 6 6-6 6" />,
  "chevron-down": <path d="m6 9 6 6 6-6" />,
  play: <path d="M7 4v16l13-8z" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  tag: <><path d="M3 12V3h9l9 9-9 9z" /><circle cx="7.5" cy="7.5" r="1.5" /></>,
  folder: <path d="M3 6h6l2 2h10v11H3z" />,
  package: <><path d="m12 3 9 5v8l-9 5-9-5V8z" /><path d="m3 8 9 5 9-5M12 13v8" /></>,
  file: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4M9 13h6M9 17h6" /></>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4H4v12h4" /></>,
  eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
  refresh: <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  code: <path d="m8 7-5 5 5 5M16 7l5 5-5 5" />,
  "map-pin": <><path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z" /><circle cx="12" cy="9" r="2.5" /></>,
};

export function Icon({ name, size = 16, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={`flex-none ${className}`}
    >
      {PATHS[name]}
    </svg>
  );
}

export function Page({ title, description, actions, children }: { title: string; description?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-1 text-sm text-white/55">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

export function Card({ title, description, actions, children, className = "", padded = true }: {
  title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={`panel overflow-hidden ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-5 py-3.5">
          <div>
            {title && <h3 className="text-sm font-semibold">{title}</h3>}
            {description && <p className="mt-0.5 text-xs text-white/50">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={padded ? "px-5 py-4" : ""}>{children}</div>
    </section>
  );
}

const BUTTON_STYLES = {
  primary: "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110",
  secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
  danger: "border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25",
  ghost: "text-white/60 hover:bg-white/[0.06] hover:text-white",
};

export const buttonClass = (variant: keyof typeof BUTTON_STYLES = "secondary", small = false) =>
  `inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
    small ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm"
  } ${BUTTON_STYLES[variant]}`;

export function Button({
  children, onClick, variant = "secondary", type = "button", disabled, small, title, ariaLabel, pressed,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: keyof typeof BUTTON_STYLES;
  type?: "button" | "submit";
  disabled?: boolean;
  small?: boolean;
  title?: string;
  ariaLabel?: string;
  pressed?: boolean;
}) {
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title} aria-label={ariaLabel} aria-pressed={pressed} className={buttonClass(variant, small)}>
      {children}
    </button>
  );
}

export type Tone = "neutral" | "good" | "warn" | "bad" | "info" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-white/[0.08] text-white/70",
  good: "bg-emerald-500/15 text-emerald-300",
  warn: "bg-amber-500/15 text-amber-300",
  bad: "bg-red-500/15 text-red-300",
  info: "bg-sky-500/15 text-sky-300",
  accent: "bg-[color-mix(in_srgb,var(--color-accent)_20%,transparent)] text-white",
};

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`}>{children}</span>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  const valueTone = tone === "warn" ? "text-amber-300" : tone === "bad" ? "text-red-300" : tone === "good" ? "text-emerald-300" : "text-white";
  return (
    <div className="panel px-4 py-3">
      <p className="text-xs text-white/50">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${valueTone}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-white/40">{hint}</p>}
    </div>
  );
}

export function EmptyState({ icon, title, text, action }: { icon: IconName; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="panel flex flex-col items-center px-6 py-14 text-center">
      <span className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.06] text-[var(--color-accent)]">
        <Icon name={icon} size={22} />
      </span>
      <p className="text-sm font-medium">{title}</p>
      {text && <p className="mt-1 max-w-md text-sm text-white/50">{text}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Notice({ text, kind = "error", onClose }: { text: string; kind?: "error" | "ok"; onClose?: () => void }) {
  const cls = kind === "ok" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200";
  return (
    <div role={kind === "ok" ? "status" : "alert"} className={`mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${cls}`}>
      <span>{text}</span>
      {onClose && (
        <button type="button" aria-label="Hinweis schließen" onClick={onClose} className="opacity-60 hover:opacity-100"><Icon name="x" size={14} /></button>
      )}
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder, label }: { value: string; onChange: (v: string) => void; placeholder: string; label: string }) {
  return (
    <span className="relative block min-w-[14rem] flex-1">
      <Icon name="search" size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/35" />
      <input aria-label={label} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} className={`${inputClass} pl-9`} />
    </span>
  );
}

export function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block text-sm ${className}`}>
      <span className="mb-1.5 block text-white/70">{label}</span>
      {children}
    </label>
  );
}

export function Loading() {
  return <div className="p-6 text-sm text-white/50">Lade …</div>;
}
