/**
 * Änderungsprotokoll: Daten vom Server (GET /app/changelog), die Anzeige-Namen der
 * Arten und die Merkhilfe "schon gesehen?" für die kleine Neu-Markierung neben der
 * Versionsnummer in der Seitenleiste.
 *
 * Gemerkt wird ein Fingerabdruck aus laufender Version und Zahl der noch nicht
 * veröffentlichten Einträge im localStorage dieses Browsers. Der Speicher kann fehlen
 * oder werfen (privates Fenster, gesperrte Website-Daten) -- dann gibt es einfach nie
 * eine Markierung, nichts stürzt ab.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";

import { api } from "./api";

export type ChangeKind = "neu" | "verbessert" | "behoben" | "sicherheit";

export interface ChangelogEntry {
  kind: ChangeKind;
  text: string;
  prs: number[];
}

export interface ChangelogVersion {
  version: string;
  /** JJJJ-MM-TT */
  date: string;
  title: string | null;
  entries: ChangelogEntry[];
}

export interface Changelog {
  current: string;
  /** Build-Kennung des Servers, falls er eine kennt. */
  build: string | null;
  unreleased: ChangelogEntry[];
  versions: ChangelogVersion[];
}

/** Anzeigereihenfolge und deutsche Namen (dieselbe Reihenfolge wie im Backend). */
export const KIND_ORDER: ChangeKind[] = ["neu", "verbessert", "behoben", "sicherheit"];
export const KIND_LABELS: Record<ChangeKind, string> = {
  neu: "Neu",
  verbessert: "Verbessert",
  behoben: "Behoben",
  sicherheit: "Sicherheit",
};

/** GET /api/v1/app/changelog -- für jeden angemeldeten Nutzer. Ändert sich nur mit einem Update. */
export function useChangelog() {
  return useQuery({
    queryKey: ["changelog"],
    queryFn: () => api.get<Changelog>("/app/changelog"),
    staleTime: 60_000,
  });
}

/** `2026-09-30` -> `30.09.2026` (ohne Date, damit keine Zeitzone das Datum verschiebt). */
export function formatDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : iso;
}

/**
 * Wann diese Oberfläche gebaut wurde. Die Build-Kennung (vite.config.ts) ist der
 * Bauzeitpunkt in Millisekunden, als Zahl zur Basis 36 geschrieben; in Tests fehlt sie.
 */
export function frontendBuildDate(): Date | null {
  if (typeof __BUILD_ID__ === "undefined") return null;
  const ms = parseInt(__BUILD_ID__, 36);
  // Plausibel = zwischen 2020 und 2100 -- sonst ist es keine Zeit (z. B. eine Kennung von Hand).
  if (!Number.isFinite(ms) || ms < 1_577_836_800_000 || ms > 4_102_444_800_000) return null;
  return new Date(ms);
}

// --- "Schon gesehen?" -----------------------------------------------------------------

/** Neuer Name; der alte (`lattice.changelogSeen`) wird beim Start uebernommen (lib/legacyStorage.ts). */
export const SEEN_KEY = "nodvard-deck.changelogSeen";

const listeners = new Set<() => void>();

function readSeen(): string | null {
  try {
    return window.localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

/** Fingerabdruck des Protokolls: neue Version oder neue unveröffentlichte Einträge ändern ihn. */
export function changelogFingerprint(data: Pick<Changelog, "current" | "unreleased">): string {
  return `${data.current}|${data.unreleased?.length ?? 0}`;
}

/** Merkt sich, dass der Nutzer dieses Protokoll kennt (Seite "Über Nodvard Deck" geöffnet). */
export function markChangelogSeen(fingerprint: string): void {
  try {
    if (window.localStorage.getItem(SEEN_KEY) === fingerprint) return;
    window.localStorage.setItem(SEEN_KEY, fingerprint);
  } catch {
    // Kein Speicher: dann gibt es eben keine Neu-Markierung.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Ein anderer Tab hat die Seite geöffnet und den Stand gemerkt.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** Der zuletzt gemerkte Fingerabdruck (null = nie gemerkt oder kein Speicher). */
export function useChangelogSeen(): string | null {
  return useSyncExternalStore(subscribe, readSeen, () => null);
}

/**
 * Für die Seitenleiste: laufende Version und ob es etwas Ungesehenes gibt. Beim allerersten
 * Besuch (nichts gemerkt) wird der Stand still gemerkt, ohne Markierung -- sonst wäre
 * gleich jede frische Installation "neu".
 */
export function useChangelogNotice(): { version: string | null; isNew: boolean } {
  const { data } = useChangelog();
  const seen = useChangelogSeen();
  const fingerprint = data && typeof data.current === "string" ? changelogFingerprint(data) : null;

  useEffect(() => {
    if (fingerprint !== null && seen === null) markChangelogSeen(fingerprint);
  }, [fingerprint, seen]);

  return {
    version: fingerprint !== null ? data!.current : null,
    isNew: fingerprint !== null && seen !== null && seen !== fingerprint,
  };
}
