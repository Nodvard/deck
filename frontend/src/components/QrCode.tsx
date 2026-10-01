/**
 * QR-Code als SVG, komplett im Browser erzeugt (Bibliothek `qrcode-generator`, MIT) -- der Text
 * (z. B. der Schluessel fuer die Authenticator-App) verlaesst den Rechner nie, es gibt keinen
 * externen Dienst. Immer dunkel auf weissem Grund mit Ruhezone: Scanner-Apps erkennen helle
 * Codes auf dunklem Grund nicht zuverlaessig, auch wenn die Oberflaeche dunkel ist.
 */
import qrcode from "qrcode-generator";
import { useMemo } from "react";

const QUIET_ZONE = 4;

/** Pfad aller dunklen Module (ein Quadrat je Modul), oder `null`, wenn der Text nicht hineinpasst. */
export function qrPath(text: string): { path: string; size: number } | null {
  try {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    const count = qr.getModuleCount();
    let path = "";
    for (let row = 0; row < count; row++) {
      for (let col = 0; col < count; col++) {
        if (qr.isDark(row, col)) path += `M${col + QUIET_ZONE} ${row + QUIET_ZONE}h1v1h-1z`;
      }
    }
    return { path, size: count + QUIET_ZONE * 2 };
  } catch {
    return null; // Text zu lang fuer einen QR-Code
  }
}

export function QrCode({ value, label, size = 200, className = "" }: { value: string; label: string; size?: number; className?: string }) {
  const code = useMemo(() => qrPath(value), [value]);
  if (!code) return null;
  return (
    <svg
      role="img"
      aria-label={label}
      data-testid="qr-code"
      viewBox={`0 0 ${code.size} ${code.size}`}
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className={`max-w-full rounded-md ${className}`}
    >
      <rect width={code.size} height={code.size} fill="#ffffff" />
      <path d={code.path} fill="#000000" />
    </svg>
  );
}
