# Server über Tailscale an Nodvard Deck anbinden

> English summary: [en/TAILSCALE.md](en/TAILSCALE.md)

Mit Tailscale verbindest du den Rechner von Nodvard Deck und deine Server über ein verschlüsseltes VPN.
Die Geräte gehören zu deinem Tailscale-Netz, dem „Tailnet“. Du brauchst dafür keine öffentliche IPv4-Adresse
zuhause und normalerweise keine Portfreigabe im Router. Auch hinter DS-Lite oder CGNAT sind Verbindungen möglich;
wenn keine direkte Verbindung zustande kommt, kann Tailscale verschlüsselte Verbindungen über Relays vermitteln.
Siehe [Tailscale zu Verbindungen und Firewall](https://tailscale.com/docs/reference/faq/firewall-ports).

## 1. Tailscale installieren und anmelden

Diese Anleitung geht von Docker auf einem Linux-Rechner aus. Installiere Tailscale auf dem **Rechner, auf dem
Docker und Nodvard Deck laufen**, und auf jedem Server, den du erreichen möchtest. Läuft Docker in einer VM,
ist diese VM gemeint. Eine Proxmox-VM richtest du wie einen normalen Linux-Server ein.

Für unterstützte Linux-Systeme, auch Raspberry Pi OS:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Öffne die angezeigte Anmeldeseite und melde beide Geräte im selben Tailnet an. Das Installationsskript kommt
von Tailscale und wird direkt ausgeführt. Die [Linux-Anleitung von Tailscale](https://tailscale.com/docs/install/linux)
verlinkt auch die Installation über die Paketquellen deiner Distribution.

Auf jedem Gerät kannst du danach mit der [Tailscale-Kommandozeile](https://tailscale.com/docs/reference/tailscale-cli)
nachsehen, ob es angemeldet ist und welche IPv4-Adresse es hat:

```bash
tailscale status
tailscale ip -4
```

Die [Tailscale-IPv4-Adressen](https://tailscale.com/docs/concepts/tailscale-ip-addresses) liegen im Bereich
`100.64.0.0/10`, also zwischen `100.64.0.0` und `100.127.255.255`.
Nimm die tatsächlich angezeigte Adresse deines Servers, nicht irgendeine Adresse, die mit `100` beginnt.

### Proxmox und LXC

Ein unprivilegierter LXC-Container braucht für den normalen Tailscale-Betrieb Zugriff auf `/dev/net/tun`.
Wenn deine Proxmox-Oberfläche die Gerätefreigabe anbietet, öffne beim Container **Ressourcen → Hinzufügen →
Device Passthrough**, trage `/dev/net/tun` als Gerätepfad ein und fahre den Container danach vollständig herunter
und starte ihn wieder. Installiere Tailscale anschließend im LXC-Container.

Für ältere Proxmox-Versionen beschreibt die [LXC-Anleitung von Tailscale](https://tailscale.com/docs/features/containers/lxc/lxc-unprivileged)
die passende Konfiguration. Die Freigabe betrifft den LXC-Container, in dem Tailscale läuft.

## 2. Verbindung aus dem Deck-Container prüfen

Die mitgelieferten Dateien [compose.standalone.yml](../deploy/compose.standalone.yml),
[compose.standalone-mit-helfer.yml](../deploy/compose.standalone-mit-helfer.yml) und
[docker-compose.yml](../deploy/docker-compose.yml) verwenden für Nodvard Deck das normale Docker-Bridge-Netz.
Deck teilt sich also nicht das Host-Netz und bringt in diesen Dateien keinen eigenen Tailscale-Dienst mit.
`network_mode: none` in der Variante mit Helfer betrifft nur den Update-Helfer.

Docker ermöglicht ausgehende Verbindungen aus dem Bridge-Netz über den Host. Damit Deck darüber einen
Tailscale-Server erreicht, müssen die Route und die Firewall auf dem Host diese Verbindung zulassen.
Dass Tailscale auf dem Host läuft, bestätigt allein noch nicht die Erreichbarkeit aus dem Container.
Siehe [Docker zu Bridge-Netzen](https://docs.docker.com/engine/network/drivers/bridge/).

Prüfe deshalb direkt aus dem laufenden Deck-Container, ob der SSH-Port des Servers erreichbar ist.
Führe den Befehl im Ordner deiner Compose-Datei aus. Ersetze `100.100.10.20` durch die Tailscale-Adresse
des Servers und `22` durch dessen SSH-Port, falls er abweicht:

```bash
docker compose exec -T nodvard-deck python -c "import socket; socket.create_connection(('100.100.10.20', 22), timeout=5).close(); print('SSH-Port erreichbar')"
```

Bei Erfolg erscheint „SSH-Port erreichbar“. Das prüft die Netzwerkverbindung, die SSH-Anmeldung kommt im nächsten
Schritt. Bei einem Fehler prüfe zuerst mit `tailscale status` auf dem Host, ob beide Geräte angemeldet sind.
Prüfe danach die Tailscale-Zugriffsregeln, den SSH-Dienst auf dem Zielserver sowie Routing und Firewall des Hosts.
Ein erfolgreicher Test auf dem Host ersetzt den Test aus dem Container nicht.

## 3. Server in Deck anlegen

1. Öffne **Einstellungen → Server & Zugänge** und drücke **Server hinzufügen**.
2. Trage als **Adresse** die Tailscale-IP des Servers ein, zum Beispiel `100.100.10.20`.
3. Richte den SSH-Zugang mit Benutzer, Port und SSH-Schlüssel wie gewohnt ein. Auf dem Server muss der passende
   öffentliche Schlüssel für diesen Benutzer hinterlegt sein.
4. Drücke **Verbindung prüfen**. Vergleiche bei einem neuen Server den angezeigten Fingerabdruck mit dem
   Server-Schlüssel, bevor du ihn bestätigst.

Die einzelnen Schritte für Schlüssel und Einrichtungsbefehl stehen in der
[Erst-Einrichtung, Abschnitt 5](11-ERST-EINRICHTUNG.md#5-server-und-ssh-zugang).
Tailscale stellt hier den Netzwerkweg bereit. Deck braucht weiterhin seinen normalen SSH-Zugang.

Du kannst auch einen [MagicDNS-Namen](https://tailscale.com/docs/features/magicdns) als Adresse verwenden,
wenn er **im Deck-Container** aufgelöst werden kann. Verwende dafür den vollständigen Namen aus der
Tailscale-Verwaltung. Dass der Name auf dem Host funktioniert, reicht nicht als Prüfung für den Container.
Funktioniert die IP-Adresse, der Name aber nicht, verwende zunächst die IP-Adresse und prüfe die
DNS-Einstellungen des Containers.

## 4. Zugriff und Anmeldung

- Beschränke in der Tailscale-Verwaltung den Zugriff vom Deck-Rechner auf die benötigten Server und Ports.
  Tailscale beschreibt das unter [Zugriffskontrolle](https://tailscale.com/docs/features/access-control).
- Für die Verbindung zu deinen Servern brauchst du keine Exit Node. Ein
  [Subnet Router](https://tailscale.com/docs/features/subnet-routers) ist eine Alternative, wenn du nicht auf jedem
  Zielgerät Tailscale installieren möchtest. Dafür müssen Routen und Zugriffsregeln eingerichtet sein; in Deck
  verwendest du dann die erreichbare Adresse des Zielgeräts in dessen Netz.
- Läuft ein Server dauerhaft, beachte den Ablauf seines Tailscale-Geräteschlüssels. Die
  [Tailscale-Anleitung für Server](https://tailscale.com/docs/how-to/set-up-servers) erklärt die Möglichkeiten dafür.

Wenn du auch **Deck selbst über Tailscale aufrufst**, behandelt Deck erkannte Client-Adressen aus `100.64.0.0/10`
bei der Anmeldung als intern. Für diese Adressen gilt die zusätzliche, wachsende Wartezeit wegen des
Durchprobierens eines Benutzernamens nicht. Die Grenzen je Adresse und die Grenzen für falsche Zwei-Faktor-Codes
gelten weiterhin. Entscheidend ist die Adresse, die Deck tatsächlich sieht; Docker oder ein Proxy können sie ändern.
Belegt ist das in [den Anmelderegeln](../backend/src/nodvard_deck/core/login_limit.py).

Eine Tailscale-Adresse gilt dadurch **nicht automatisch als vertrauenswürdiger Proxy**. Nur wenn du tatsächlich
einen Reverse-Proxy benutzt, trägst du dessen von Deck gesehene Adresse unter **Einstellungen → System →
Vertrauenswürdiger Proxy** ein. Trag nicht das ganze Tailscale-Netz ein: Jedes eingetragene Gerät dürfte sonst
Absenderadressen vorgeben. Die Anleitung [Reverse-Proxy](12-REVERSE-PROXY.md) erklärt die Einrichtung.

Deck lässt den Bereich `100.64.0.0/10` bei der Prüfung von Proxy-Einträgen als internen Bereich zu. Auch der Hinweis
auf einen noch nicht eingetragenen Proxy kann bei einer solchen Adresse erscheinen. Das ist keine automatische
Freigabe. Belegt ist das in [der Prüfung der Proxy-Einträge](../backend/src/nodvard_deck/core/trusted_proxies.py)
und [dem Proxy-Hinweis](../backend/src/nodvard_deck/core/untrusted_proxies.py).

## 5. Wenn etwas nicht klappt

| Was du siehst | Was du prüfen kannst |
|---|---|
| Der Host erreicht den Server, der Test aus dem Deck-Container scheitert | Weiterleitung vom Docker-Netz zum Tailscale-Netz, Routen und Firewall des Hosts |
| Der SSH-Port ist nicht erreichbar | Tailscale-Anmeldung beider Geräte, Zugriffsregeln, SSH-Port und Firewall des Zielservers |
| Die IP-Adresse funktioniert, der MagicDNS-Name nicht | MagicDNS und Namensauflösung im Deck-Container; zunächst die IP-Adresse verwenden |
| Der Port ist erreichbar, „Verbindung prüfen“ scheitert | SSH-Benutzer, Schlüssel, Port und Bestätigung des Server-Schlüssels in Deck |
| Eine bisher funktionierende Verbindung fällt aus | `tailscale status`, Anmeldung und Ablauf des Geräteschlüssels prüfen |

Eine pauschale Freigabe von UDP-Port 41641 ist nicht nötig. Wenn direkte Verbindungen nicht möglich sind, kann
Tailscale Relays verwenden. Bei stark eingeschränkten Netzen hilft die
[Firewall-Anleitung von Tailscale](https://tailscale.com/docs/reference/faq/firewall-ports).
