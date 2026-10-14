/**
 * Bild von einem geschützten API-Endpunkt (z. B. Inventar-Fotos): Ein `<img src>` kann keinen `Authorization`-Header
 * schicken und bekäme 401, darum per fetch mit Token laden und als Blob-URL zeigen. `src` ist der volle Pfad (mit /api/v1).
 */
// Eigene Datei statt ui.tsx: ui.tsx bleibt ohne React-Hooks, sonst benennt esbuild sie in jedem Bundle um. So ändern
// sich nur die Bundles, die AuthImage benutzen.
import { useEffect, useState } from "react";

import { authedFetch } from "./api";
import { Icon } from "./ui";

export function AuthImage({ src, alt, className = "" }: { src: string; alt: string; className?: string }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setObjectUrl(null);
    setFailed(false);
    // authedFetch hängt /api/v1 selbst an und erneuert bei 401 das Token.
    authedFetch(src.replace(/^\/api\/v1(?=\/)/, ""))
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [src]);

  if (objectUrl) return <img src={objectUrl} alt={alt} className={className} />;
  if (failed) {
    return (
      <span
        role="img" aria-label={alt || "Foto konnte nicht geladen werden"} title="Foto konnte nicht geladen werden"
        className={`grid place-items-center bg-white/[0.05] text-white/35 ${className}`}
      >
        <Icon name="x" size={14} />
      </span>
    );
  }
  return <span aria-hidden="true" className={`block animate-pulse bg-white/[0.05] ${className}`} />;
}
