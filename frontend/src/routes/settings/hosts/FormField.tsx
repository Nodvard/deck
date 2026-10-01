/**
 * Formularfeld fuer „Server & Zugaenge“: Beschriftung, Eingabe, Hinweis und Fehlermeldung
 * getrennt, damit die Beschriftung allein das Feld benennt (Screenreader, Tests) und der
 * Hinweis nicht mit dazugehoert.
 */
import { useId, type ReactNode } from "react";

export function FormField({
  label, hint, error, className = "", children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  className?: string;
  /** Bekommt die `id` und `aria-describedby`, die auf die Eingabe gehoeren. */
  children: (props: { id: string; "aria-describedby": string | undefined; "aria-invalid": boolean }) => ReactNode;
}) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`text-sm ${className}`}>
      <label htmlFor={id} className="mb-1.5 block text-white/70">{label}</label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": Boolean(error) })}
      {hint && <p id={`${id}-hint`} className="mt-1 text-xs text-white/40">{hint}</p>}
      {error && <p id={`${id}-error`} role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
    </div>
  );
}
