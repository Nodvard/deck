# Server über Tailscale an Nodvard Deck anbinden

> English version: [en/TAILSCALE.md](en/TAILSCALE.md)

Mit Tailscale verbindest du den Rechner von Nodvard Deck und deine Server über ein verschlüsseltes VPN.
Die Geräte gehören zu deinem Tailscale-Netz, dem „Tailnet“. Du brauchst dafür keine öffentliche IPv4-Adresse
zuhause und normalerweise keine Portfreigabe im Router. Auch hinter DS-Lite oder CGNAT sind Verbindungen möglich;
wenn keine direkte Verbindung zustande kommt, kann Tailscale verschlüsselte Verbindungen über Relays vermitteln.
Siehe [Tailscale zu Verbindungen und Firewall](https://tailscale.com/docs/reference/faq/firewall-ports).

Die folgenden Schritte verbinden einen **Linux-Server** über seinen normalen SSH-Dienst mit Deck.
Tailscale stellt den Netzwerkweg bereit, Deck verwendet weiterhin Benutzer und SSH-Schlüssel.
Eine Aktivierung von Tailscale SSH mit `tailscale up --ssh` ist dafür nicht nötig.
Alle Namen mit `example.ts.net` sind Platzhalter. Ersetze sie durch die Namen aus deiner Tailscale-Verwaltung.

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

## 2. MagicDNS und Verbindung aus dem Deck-Container prüfen

1. Öffne in der Tailscale-Verwaltung **DNS** und aktiviere
   [MagicDNS](https://tailscale.com/docs/features/magicdns), falls es noch ausgeschaltet ist.
2. Kopiere den vollständigen MagicDNS-Namen des **Zielservers** aus der Geräteübersicht, zum Beispiel
   `server.example.ts.net`. Verwende den Namen des Servers, nicht den des Deck-Hosts.
3. Prüfe auf dem Deck-Host, ob der Server im Tailnet erreichbar ist:

   ```bash
   tailscale ping server.example.ts.net
   ```

   Das prüft den Tailscale-Weg. Ob der SSH-Dienst erreichbar ist und der Container den Namen auflösen kann,
   prüfst du getrennt mit den folgenden Befehlen.

Die mitgelieferten Dateien [compose.standalone.yml](../deploy/compose.standalone.yml),
[compose.standalone-mit-helfer.yml](../deploy/compose.standalone-mit-helfer.yml) und
[docker-compose.yml](../deploy/docker-compose.yml) verwenden für Nodvard Deck das normale Docker-Bridge-Netz.
Deck teilt sich also nicht das Host-Netz und bringt in diesen Dateien keinen eigenen Tailscale-Dienst mit.
`network_mode: none` in der Variante mit Helfer betrifft nur den Update-Helfer.

Docker ermöglicht ausgehende Verbindungen aus dem Bridge-Netz über den Host. Damit Deck darüber einen
Tailscale-Server erreicht, müssen die Route und die Firewall auf dem Host diese Verbindung zulassen.
Dass Tailscale auf dem Host läuft, bestätigt allein noch nicht die Erreichbarkeit aus dem Container.
Siehe [Docker zu Bridge-Netzen](https://docs.docker.com/engine/network/drivers/bridge/).

Prüfe deshalb direkt aus dem laufenden Deck-Container zuerst die Namensauflösung und dann den SSH-Port.
Führe die Befehle im Ordner deiner Compose-Datei aus. Ersetze `server.example.ts.net` durch den vollständigen
MagicDNS-Namen des Servers und `22` durch dessen SSH-Port, falls er abweicht:

```bash
docker compose exec -T nodvard-deck python -c "import socket; print(socket.getaddrinfo('server.example.ts.net', 22, type=socket.SOCK_STREAM))"
```

Bei Erfolg erscheinen die aufgelösten Adressen. Prüfe danach den Port:

```bash
docker compose exec -T nodvard-deck python -c "import socket; socket.create_connection(('server.example.ts.net', 22), timeout=5).close(); print('SSH-Port erreichbar')"
```

Bei Erfolg erscheint „SSH-Port erreichbar“. Das prüft die Netzwerkverbindung, die SSH-Anmeldung kommt im nächsten
Schritt. Bei einem Fehler prüfe zuerst mit `tailscale status` auf dem Host, ob beide Geräte angemeldet sind.
Prüfe danach die Tailscale-Zugriffsregeln, den SSH-Dienst auf dem Zielserver sowie Routing und Firewall des Hosts.
Ein erfolgreicher Test auf dem Host ersetzt den Test aus dem Container nicht.

Scheitert nur die Namensauflösung, wiederhole den Porttest mit der tatsächlichen Tailscale-IP des Servers.
Führe dazu `tailscale ip -4` **auf dem Zielserver** aus. Funktioniert die IP, prüfe MagicDNS und die
DNS-Einstellungen des Containers.
Du kannst die IP vorübergehend auch als Adresse in Deck eintragen. Verwende den MagicDNS-Namen erst,
wenn dessen Auflösung im Container klappt. Bei `docker run` lautet der Aufruf stattdessen
`docker exec <Containername> python -c "…"`, mit demselben Python-Code.

## 3. Server in Deck anlegen

### Serveradresse eintragen

1. Öffne als Inhaber oder Administrator **Einstellungen → Server & Zugänge** und drücke **Server hinzufügen**.
2. Fülle **Kurzname** mit einem Namen wie `server` und bei Bedarf **Anzeigename** aus.
3. Trage unter **Adresse (IP oder Name)** den geprüften MagicDNS-Namen ein: `server.example.ts.net`.
   Verwende nur den Namen, ohne `http://`, `https://`, Benutzer, Pfad oder `:22`. Den SSH-Port gibst du später
   beim Zugang an.
4. Wähle **Linux** als **Betriebssystem** und drücke **Anlegen**. Die Seite des Servers öffnet sich.

### SSH-Schlüssel hinterlegen

1. Wähle in der Karte **SSH-Zugang** den Weg **SSH-Schlüssel erzeugen**.
2. Gib den **Benutzer auf dem Server** an, zum Beispiel `nodvard`, und den **SSH-Port**, normalerweise `22`.
3. Drücke **Schlüssel erzeugen**. Deck speichert den privaten Schlüssel verschlüsselt und zeigt den
   **Einrichtungsbefehl** für den Server.
4. Melde dich über einen vorhandenen administrativen Zugang oder die Serverkonsole am **Zielserver** an.
   Kopiere den Einrichtungsbefehl aus Deck und führe ihn dort aus. Er läuft als root oder über `sudo`, legt bei
   Bedarf den Benutzer an und hinterlegt dessen öffentlichen Schlüssel in `~/.ssh/authorized_keys`.
   Prüfe vorher die angebotenen Optionen für Root-Rechte und die Gruppe docker. Einzelheiten stehen in der
   [Erst-Einrichtung zum Einrichtungsbefehl](11-ERST-EINRICHTUNG.md#33-einrichtungsbefehl-auf-dem-server-ausführen).

Wenn du schon einen Schlüssel verwenden möchtest, wähle stattdessen **Eigenen Schlüssel einfügen**.
Trage Benutzer und Port ein und füge den vollständigen **privaten Schlüssel ohne Passphrase** ein,
nicht die `.pub`-Datei. Führe danach ebenfalls den angezeigten Einrichtungsbefehl auf dem Zielserver aus,
damit der passende öffentliche Schlüssel beim richtigen Benutzer hinterlegt ist.

Deck liest weder `~/.ssh` noch `ssh_config` des Deck-Hosts und verwendet dessen `ssh-agent` nicht.
Ein funktionierender SSH-Aufruf auf dem Host richtet den Zugang in Deck deshalb noch nicht ein.

### Verbindung und Server-Schlüssel prüfen

1. Drücke auf der Serverseite **Verbindung prüfen**.
2. Bei einem unbekannten Server-Schlüssel hält die Prüfung an und zeigt den Fingerabdruck.
   Führe den angezeigten Vergleichsbefehl über die Serverkonsole oder einen bereits geprüften Zugang aus,
   zum Beispiel für den Ed25519-Schlüssel:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

3. Vergleiche den Fingerabdruck und bestätige ihn in Deck nur bei Übereinstimmung. Danach läuft die Prüfung weiter.
4. Prüfe, ob **Anmeldung** erfolgreich ist. Hinweise auf fehlende Root-Rechte oder Rechte für Erweiterungen
   hängen davon ab, was du auf diesem Server nutzen möchtest.

Meldet Deck einen **geänderten** Server-Schlüssel, kläre zuerst die Ursache. Nach einer bestätigten
Neuinstallation kannst du den alten Eintrag in der Karte **Server-Schlüssel → Vergessen** entfernen und erneut
prüfen. Eine unerwartete Änderung solltest du nicht übergehen.

Alle Zugangswege und Rechte beschreibt die
[Erst-Einrichtung, Abschnitt 3](11-ERST-EINRICHTUNG.md#3-den-ersten-server-verbinden).

## 4. Zugriff und Anmeldung

- Beschränke in der Tailscale-Verwaltung den Zugriff vom Deck-Rechner auf die benötigten Server und Ports.
  Tailscale beschreibt das unter [Zugriffskontrolle](https://tailscale.com/docs/features/access-control).
- Erlaube in den Tailscale-Zugriffsregeln den SSH-Port des Zielservers vom Deck-Host aus, normalerweise TCP `22`.
  Auch die lokale Firewall des Zielservers muss diesen Verkehr über Tailscale zulassen, und der SSH-Dienst
  muss auf einer über Tailscale erreichbaren Adresse lauschen. Auf Linux ist die Tailscale-Schnittstelle
  üblicherweise `tailscale0`. Auf dem Deck-Host muss zusätzlich die Weiterleitung aus dem Docker-Netz ins
  Tailnet erlaubt sein. Für diesen Weg brauchst du keine öffentliche Freigabe des SSH-Ports am Router.
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
| `Name or service not known`, `Temporary failure in name resolution` oder die IP funktioniert, der MagicDNS-Name nicht | MagicDNS in der Tailscale-Verwaltung, vollständigen Namen und DNS im Container prüfen; vorübergehend die tatsächliche Tailscale-IP verwenden |
| `Connection timed out` oder der SSH-Port ist nicht erreichbar | Tailscale-Anmeldung beider Geräte, Zugriffsregeln für TCP und den SSH-Port, Routing und Firewalls prüfen |
| `Connection refused` | Adresse und Port prüfen; läuft der normale SSH-Dienst und lauscht er auf einer über Tailscale erreichbaren Adresse? |
| Der Port ist erreichbar, aber „Der Server hat den Schlüssel nicht akzeptiert“ erscheint | Benutzer und Zugang in Deck prüfen; Einrichtungsbefehl auf dem Zielserver ausführen und passenden öffentlichen Schlüssel beim richtigen Benutzer hinterlegen |
| Die Prüfung hält beim Server-Schlüssel an | Fingerabdruck unabhängig auf dem Server vergleichen und bei Übereinstimmung bestätigen; bei einer Änderung zuerst die Ursache klären |
| Eine bisher funktionierende Verbindung fällt aus | `tailscale status`, Anmeldung und Ablauf des Geräteschlüssels prüfen |

Eine pauschale Freigabe von UDP-Port 41641 ist nicht nötig. Wenn direkte Verbindungen nicht möglich sind, kann
Tailscale Relays verwenden. Bei stark eingeschränkten Netzen hilft die
[Firewall-Anleitung von Tailscale](https://tailscale.com/docs/reference/faq/firewall-ports).
