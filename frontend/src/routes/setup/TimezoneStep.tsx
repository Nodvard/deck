/**
 * Assistent, Schritt „Zeitzone“: vorbelegt mit der Zone dieses Geräts, gespeichert über die
 * Einstellungen (`PUT /settings/system.timezone`). Die Auswahl ist dieselbe wie unter
 * Einstellungen -> System (`TimezonePicker`).
 */
import { useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";
import { browserTimeZone, setDeckTimezone } from "../../lib/deckTimezone";
import { TimezonePicker, timezoneOptions } from "../settings/TimezonePicker";
import { Button, NoticeLine, errorText, type Notice } from "../settings/ui";
import { StepNav } from "./StepNav";

export function TimezoneStep({ onNext }: { onNext: () => void }) {
  /** Zone, die der Server gerade hat; `null` solange sie nicht geladen ist. */
  const [current, setCurrent] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [zone, setZone] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<{ timezone?: string }>("/me")
      .then((me) => (typeof me.timezone === "string" && me.timezone ? me.timezone : null))
      .catch(() => null)
      .then((server) => {
        if (!alive) return;
        const device = browserTimeZone();
        setCurrent(server);
        // Zone des Geräts, wenn sie in der Liste der Zeitzonen vorkommt, sonst die jetzige Einstellung.
        setZone(timezoneOptions().includes(device) ? device : (server ?? "UTC"));
        setLoaded(true);
      });
    return () => { alive = false; };
  }, []);

  async function save() {
    setNotice(null);
    if (zone === current) return onNext();
    setBusy(true);
    try {
      await api.put("/settings/system.timezone", { value: zone });
      setDeckTimezone(zone);
      onNext();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        // Der Server kennt die Zone des Geräts nicht (anderer Name in seiner Zonen-Datenbank).
        setNotice({ kind: "error", text: "Diese Zeitzone kennt der Server nicht. Bitte eine andere aus der Liste wählen." });
        if (current) setZone(current);
      } else {
        setNotice({ kind: "error", text: errorText(err) });
      }
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return <p className="text-sm text-white/50">Lade …</p>;

  const fromDevice = zone === browserTimeZone() && zone !== current;
  return (
    <div data-testid="setup-timezone">
      <p className="mb-3 text-sm text-white/70">
        Zeitpläne (zum Beispiel das Morgen-Briefing um 7 Uhr) und Wartungsfenster richten sich nach dieser Zeitzone.
        {fromDevice && " Vorausgewählt ist die Zeitzone dieses Geräts."}
        {" "}Du kannst sie später unter Einstellungen → System ändern.
      </p>
      <NoticeLine notice={notice} />
      <TimezonePicker value={zone} savedZone={current ?? zone} onChange={setZone} />
      <StepNav>
        <Button onClick={onNext}>Überspringen</Button>
        <Button variant="primary" busy={busy} onClick={() => void save()}>Weiter</Button>
      </StepNav>
    </div>
  );
}
