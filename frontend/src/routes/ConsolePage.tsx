/**
 * Grafische Konsole einer VM/eines Containers -- damit dafuer niemand in die Proxmox-
 * Oberflaeche wechseln muss. Kern-Seite ohne PageSpec wie Dateien/
 * Aktionen: sie kennt keinen Hypervisor, nur `POST /console/sessions` (die
 * `ConsoleTarget`-Capability dahinter liefert z. B. die proxmox-Extension).
 *
 * Ablauf: `POST /console/sessions` oeffnet die Konsole serverseitig und liefert URL +
 * Einmal-Kennwort; noVNC oeffnet danach selbst den WebSocket zu Nodvard Deck (nie direkt
 * zum Hypervisor) und authentifiziert sich per RFB mit dem Einmal-Kennwort. Der
 * Browser sieht damit weder einen Proxmox-Login noch das API-Token.
 *
 * noVNC wird erst hier dynamisch geladen: das Paket nutzt Top-Level-`await` und ist
 * gross, das Haupt-Bundle soll dafuer nicht zahlen.
 *
 * "Text senden": QEMUs VNC kennt keine Zwischenablage -- der Dialog tippt Text als
 * Tastendruecke, passend zum Tastaturlayout des GASTS (lib/keyboardLayouts.ts).
 * Zeilenumbrueche druecken Enter nur, wenn das ausdruecklich angehakt ist.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import type RFB from "@novnc/novnc";

import { api, ApiError } from "../lib/api";
import { LAYOUT_LABEL, textToKeySteps, type LayoutId } from "../lib/keyboardLayouts";

interface ConsoleSessionOut {
  session_id: string;
  ws_url: string;
  protocol: string;
  password: string | null;
}

interface HostOut {
  id: string;
  display_name: string;
  name: string;
  kind: string | null;
  status: string;
}

type Status = "connecting" | "connected" | "disconnected" | "error";

const STATUS_LABEL: Record<Status, string> = {
  connecting: "Verbinde …",
  connected: "Verbunden",
  disconnected: "Getrennt",
  error: "Fehler",
};

const STATUS_CLASS: Record<Status, string> = {
  connecting: "bg-amber-500/20 text-amber-300",
  connected: "bg-emerald-500/20 text-emerald-300",
  disconnected: "bg-white/10 opacity-80",
  error: "bg-red-500/20 text-red-300",
};

/** Neuer Name; der alte (`lattice.console.layout`) wird beim Start uebernommen (lib/legacyStorage.ts). */
export const LAYOUT_STORAGE_KEY = "nodvard-deck.console.layout";

function loadLayout(): LayoutId {
  try {
    const stored = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    return stored === "us" || stored === "de" ? stored : "de";
  } catch {
    return "de";
  }
}

function saveLayout(layout: LayoutId) {
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, layout);
  } catch {
    /* privates Fenster o. ae. -- dann eben nicht merken */
  }
}

function TypeTextDialog({ open, onOpenChange, rfb }: { open: boolean; onOpenChange: (open: boolean) => void; rfb: RFB | null }) {
  const [text, setText] = useState("");
  const [layout, setLayout] = useState<LayoutId>(loadLayout);
  const [enter, setEnter] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function send() {
    if (!rfb) return;
    const { steps, unsupported } = textToKeySteps(text, layout, { enter });
    if (unsupported.length > 0) {
      setError(
        unsupported.includes("↵") && unsupported.length === 1
          ? "Der Text enthält Zeilenumbrüche – „Zeilenumbruch = Enter“ anhaken oder entfernen."
          : `Nicht tippbar im Layout ${LAYOUT_LABEL[layout]}: ${unsupported.join(" ")}`,
      );
      return;
    }
    setError(null);
    setSending(true);
    try {
      for (let i = 0; i < steps.length; i += 1) {
        const step = steps[i];
        rfb.sendKey(step.keysym, step.code, step.down);
        // Kurze Pause nach jedem Loslassen: manche Gaeste (Windows-Anmeldemaske)
        // verschlucken sonst Zeichen, wenn sie zu dicht kommen.
        if (!step.down) await new Promise((r) => setTimeout(r, 8));
      }
      setText("");
      onOpenChange(false);
      rfb.focus();
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 shadow-xl">
          <Dialog.Title className="mb-1 text-lg font-semibold">Text senden</Dialog.Title>
          <Dialog.Description className="mb-3 text-sm opacity-70">
            Wird als Tastendrücke in die Konsole getippt – es gibt dort keine Zwischenablage. Das Layout muss zu dem im Gast passen.
          </Dialog.Description>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            aria-label="Zu sendender Text"
            className="mb-2 w-full rounded bg-white/10 p-2 font-mono text-sm"
          />
          <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-1">
              Layout im Gast
              <select
                value={layout}
                onChange={(e) => {
                  const next = e.target.value as LayoutId;
                  setLayout(next);
                  saveLayout(next);
                }}
                className="rounded bg-white/10 px-1 py-0.5"
              >
                {(Object.keys(LAYOUT_LABEL) as LayoutId[]).map((id) => (
                  <option key={id} value={id}>{LAYOUT_LABEL[id]}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={enter} onChange={(e) => setEnter(e.target.checked)} />
              Zeilenumbruch = Enter
            </label>
          </div>
          {error && <p className="mb-2 text-sm text-red-400">{error}</p>}
          <div className="flex justify-end gap-2">
            <Dialog.Close asChild>
              <button type="button" className="rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20">Abbrechen</button>
            </Dialog.Close>
            <button
              type="button"
              disabled={!text || sending || !rfb}
              onClick={() => void send()}
              className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-[var(--color-background)] disabled:opacity-40"
            >
              {sending ? "Tippe …" : "Tippen"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Absolute WS-URL zu Nodvard Deck selbst (gleicher Host/Port wie die Seite, D-03). */
export function consoleSocketUrl(wsPath: string, loc: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}${wsPath}`;
}

export function ConsolePage() {
  const { hostId } = useParams();
  const navigate = useNavigate();
  const screenRef = useRef<HTMLDivElement | null>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [host, setHost] = useState<HostOut | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [message, setMessage] = useState<string | null>(null);
  const [scale, setScale] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [typeOpen, setTypeOpen] = useState(false);

  useEffect(() => {
    if (!hostId) return;
    api.get<HostOut>(`/hosts/${hostId}`).then(setHost).catch(() => setHost(null));
  }, [hostId]);

  useEffect(() => {
    if (!hostId) return;
    let cancelled = false;
    setStatus("connecting");
    setMessage(null);

    (async () => {
      try {
        const session = await api.post<ConsoleSessionOut>("/console/sessions", { host_id: hostId });
        const { default: RFBClass } = await import("@novnc/novnc");
        if (cancelled || !screenRef.current) return;
        if (session.protocol !== "vnc") {
          throw new Error(`Unbekanntes Konsolenprotokoll "${session.protocol}".`);
        }
        // VNC-Auth braucht nur das Kennwort; die Typen verlangen alle drei Felder.
        const credentials = session.password ? { username: "", password: session.password, target: "" } : undefined;
        const rfb = new RFBClass(screenRef.current, consoleSocketUrl(session.ws_url), {
          credentials,
          wsProtocols: ["binary"],
        });
        rfbRef.current = rfb;
        rfb.scaleViewport = scale;
        rfb.background = "#000";
        // Nur die AKTUELLE Sitzung darf den Status setzen: nach "Neu verbinden"
        // meldet die alte ihr `disconnect` erst asynchron, wenn die neue schon laeuft.
        const isCurrent = () => rfbRef.current === rfb;
        rfb.addEventListener("connect", () => {
          if (!isCurrent()) return;
          setStatus("connected");
          rfb.focus();
        });
        rfb.addEventListener("disconnect", (e) => {
          if (!isCurrent()) return;
          setStatus(e.detail.clean ? "disconnected" : "error");
          if (!e.detail.clean) setMessage("Verbindung unerwartet getrennt.");
        });
        rfb.addEventListener("securityfailure", (e) => {
          if (!isCurrent()) return;
          setStatus("error");
          setMessage(`Anmeldung an der Konsole fehlgeschlagen${e.detail.reason ? `: ${e.detail.reason}` : "."}`);
        });
        rfb.addEventListener("credentialsrequired", () => {
          if (credentials) rfb.sendCredentials(credentials);
        });
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setMessage(err instanceof ApiError || err instanceof Error ? err.message : String(err));
      }
    })();

    return () => {
      cancelled = true;
      rfbRef.current?.disconnect();
      rfbRef.current = null;
    };
    // `scale` absichtlich NICHT: Umschalten darf die Sitzung nicht neu aufbauen,
    // siehe eigener Effekt unten.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hostId, attempt]);

  useEffect(() => {
    if (rfbRef.current) rfbRef.current.scaleViewport = scale;
  }, [scale]);

  const reconnect = useCallback(() => setAttempt((n) => n + 1), []);

  const fullscreen = useCallback(() => {
    void screenRef.current?.requestFullscreen?.();
  }, []);

  const title = host?.display_name || host?.name || hostId;
  const connected = status === "connected";

  return (
    <div className="flex h-full flex-col p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => navigate(-1)} className="text-sm opacity-60 hover:opacity-100">
          ← Zurück
        </button>
        <h2 className="text-lg font-semibold">Konsole: {title}</h2>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[status]}`}>{STATUS_LABEL[status]}</span>
        <div className="ml-auto flex flex-wrap gap-1.5">
          <button
            type="button"
            disabled={!connected}
            onClick={() => rfbRef.current?.sendCtrlAltDel()}
            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40"
          >
            Strg+Alt+Entf
          </button>
          <button
            type="button"
            disabled={!connected}
            onClick={() => setTypeOpen(true)}
            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40"
          >
            Text senden …
          </button>
          <button
            type="button"
            onClick={() => setScale((s) => !s)}
            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20"
            aria-pressed={scale}
          >
            {scale ? "Originalgröße" : "An Fenster anpassen"}
          </button>
          <button type="button" onClick={fullscreen} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20">
            Vollbild
          </button>
          <button
            type="button"
            onClick={reconnect}
            disabled={status === "connecting"}
            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40"
          >
            Neu verbinden
          </button>
        </div>
      </div>
      {message && <p className="mb-2 text-sm text-red-400">{message}</p>}
      <div ref={screenRef} className="min-h-0 flex-1 overflow-auto rounded border border-white/10 bg-black" data-testid="console-screen" />
      <TypeTextDialog open={typeOpen} onOpenChange={setTypeOpen} rfb={rfbRef.current} />
    </div>
  );
}
