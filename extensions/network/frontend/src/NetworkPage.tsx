/** Wegweiser zu den Modulen „Pi-hole“ und „Nginx Proxy Manager“ (PageSpec `component="NetworkPage"`). */
import { Card, Page, buttonClass } from "../../../_shared/frontend/src/ui";

const MOVED = [
  { title: "Pi-hole", text: "Anfragen, Blockierung, aktivste Geräte und Sperrlisten.", href: "/ext/pihole/pihole", settings: "/settings/extensions/pihole" },
  {
    title: "Nginx Proxy Manager", text: "Proxy-Hosts, Zertifikate und der Adress-Check zu den Containern.",
    href: "/ext/npm/npm", settings: "/settings/extensions/npm",
  },
] as const;

export function NetworkPage(): JSX.Element {
  return (
    <Page title="Netzwerk" description="Pi-hole und Nginx Proxy Manager sind jetzt eigene Module. Einstellungen, Kacheln und Zugangsdaten sind mit umgezogen.">
      <div data-testid="moved" className="grid gap-4 md:grid-cols-2">
        {MOVED.map((m) => (
          <Card key={m.title} title={m.title}>
            <p className="mb-3 text-sm text-white/60">{m.text}</p>
            <div className="flex flex-wrap gap-2">
              <a href={m.href} className={buttonClass("primary", true)}>{m.title} öffnen</a>
              <a href={m.settings} className={buttonClass("ghost", true)}>Einstellungen</a>
            </div>
          </Card>
        ))}
      </div>
      <p className="mt-4 text-sm text-white/45">
        Ist ein Modul aus, lässt es sich unter Einstellungen → Erweiterungen einschalten. „Netzwerk“ selbst braucht es nicht mehr.
      </p>
    </Page>
  );
}
