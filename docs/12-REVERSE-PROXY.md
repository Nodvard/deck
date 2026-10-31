# Nodvard Deck hinter einem Reverse-Proxy

> English version: [en/REVERSE-PROXY.md](en/REVERSE-PROXY.md)

Diese Anleitung ist für dich, wenn du Nodvard Deck nicht direkt über `http://<Rechner>:8080` aufrufst, sondern über
einen Reverse-Proxy wie **Nginx Proxy Manager**, **Traefik** oder **Caddy**, meist mit HTTPS und einer eigenen Adresse
wie `https://deck.example.org`. Nodvard Deck bringt selbst keinen Proxy und keine Zertifikate mit. Das übernimmt dein
Proxy, hier geht es nur darum, dass beide gut zusammenspielen.

Ohne Proxy brauchst du nichts davon. Lass die Einstellung dann einfach leer.

## Inhalt

1. [Wozu die Einstellung da ist](#1-wozu-die-einstellung-da-ist)
2. [Proxy einrichten](#2-proxy-einrichten)
3. [Adresse des Proxys herausfinden](#3-adresse-des-proxys-herausfinden)
4. [Adresse eintragen](#4-adresse-eintragen)
5. [Prüfen, ob alles stimmt](#5-prüfen-ob-alles-stimmt)
6. [Sicherheit: was du beachten solltest](#6-sicherheit-was-du-beachten-solltest)
7. [Wenn etwas nicht klappt](#7-wenn-etwas-nicht-klappt)

## 1. Wozu die Einstellung da ist

Sitzt ein Proxy vor Nodvard Deck, kommt jede Verbindung vom Proxy. Nodvard Deck sieht dann nur dessen Adresse:

- Die **Sperre nach zu vielen falschen Anmeldungen** zählt alle Geräte als ein einziges. Vertippt sich jemand oft,
  sind womöglich alle anderen auch für ein paar Minuten ausgesperrt.
- Das **Protokoll** zeigt bei jeder Anmeldung dieselbe Adresse, die des Proxys.
- Nodvard Deck weiß nicht, dass du über **HTTPS** gekommen bist. Das Anmelde-Cookie bekommt dann nicht die Markierung
  „nur über HTTPS senden“ (`Secure`).

Ein Proxy schreibt deshalb in jede Anfrage, von wem sie wirklich kommt (`X-Forwarded-For`) und ob der Browser HTTPS
benutzt hat (`X-Forwarded-Proto`). Diese Angaben kann aber jeder auch selbst mitschicken. Nodvard Deck glaubt sie
deshalb nur von Adressen, die du ausdrücklich als **vertrauenswürdigen Proxy** einträgst. Von allen anderen werden sie
nicht beachtet.

## 2. Proxy einrichten

Richte zuerst deinen Proxy ein, so dass du Nodvard Deck über die neue Adresse erreichst. Ziel ist immer der Container
von Nodvard Deck auf Port **8080** (ohne HTTPS, das macht der Proxy).

Am einfachsten und sichersten ist es, wenn Proxy und Nodvard Deck im **selben Docker-Netz** laufen. Dann erreicht der
Proxy Nodvard Deck über den Namen des Containers, und die Adresse des Proxys ist eindeutig (siehe Abschnitt 3).

### Nginx Proxy Manager

1. „Hosts“ → „Proxy Hosts“ → „Add Proxy Host“.
2. „Domain Names“: deine Adresse, z. B. `deck.example.org`.
3. „Scheme“: `http`, „Forward Hostname / IP“: `nodvard-deck` (Name des Containers, wenn beide im selben Docker-Netz
   sind) oder die Adresse des Rechners, „Forward Port“: `8080`.
4. **„Websockets Support“ einschalten.** Das ist Pflicht: Terminal, Konsole und die Live-Anzeige laufen darüber.
5. „Block Common Exploits“ darf an sein. Scheitert etwas Ungewöhnliches (z. B. eine Suche mit Sonderzeichen), schalte
   es testweise aus.
6. Reiter „SSL“: Zertifikat anfordern (Let's Encrypt), „Force SSL“ einschalten.

Nginx Proxy Manager setzt `X-Forwarded-For` und `X-Forwarded-Proto` von selbst, du musst dafür nichts eintragen.
Lädst du große Sicherungen über den Proxy hoch (Einstellungen → System → Wiederherstellen), kann eine Größengrenze im
Proxy greifen. Dann hilft im Reiter „Advanced“ die Zeile `client_max_body_size 0;`.

Beispiel mit beiden im selben Compose-Projekt und einer festen Adresse für den Proxy:

```yaml
name: nodvard-deck

services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    # Kein "ports:" nötig: der Proxy erreicht Nodvard Deck im gemeinsamen Netz.
    environment:
      NODVARD_DECK_TRUSTED_PROXIES: "172.18.0.5"
    volumes:
      - nodvard_deck_data:/app/data
    networks: [proxy]

  npm:
    image: jc21/nginx-proxy-manager:latest
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "81:81"   # Verwaltung von Nginx Proxy Manager
    volumes:
      - ./npm/data:/data
      - ./npm/letsencrypt:/etc/letsencrypt
    networks:
      proxy:
        ipv4_address: 172.18.0.5

networks:
  proxy:
    ipam:
      config:
        - subnet: 172.18.0.0/16

volumes:
  nodvard_deck_data:
    name: nodvard-deck-data
```

### Traefik

Traefik liest seine Einstellungen aus Labels am Container von Nodvard Deck:

```yaml
services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    labels:
      - traefik.enable=true
      - traefik.http.routers.deck.rule=Host(`deck.example.org`)
      - traefik.http.routers.deck.entrypoints=websecure
      - traefik.http.routers.deck.tls.certresolver=letsencrypt
      - traefik.http.services.deck.loadbalancer.server.port=8080
    networks: [proxy]
```

Traefik setzt `X-Forwarded-For` und `X-Forwarded-Proto` selbst und reicht WebSockets ohne weitere Einstellung durch.
Eingetragen wird die Adresse des Traefik-Containers im Netz `proxy`.

### Caddy

Im `Caddyfile` reicht eine Zeile je Adresse:

```
deck.example.org {
    reverse_proxy nodvard-deck:8080
}
```

Caddy holt das Zertifikat selbst, setzt `X-Forwarded-For` und `X-Forwarded-Proto` und reicht WebSockets durch.
Eingetragen wird die Adresse des Caddy-Containers (oder des Rechners, auf dem Caddy läuft).

## 3. Adresse des Proxys herausfinden

**Der einfachste Weg:** Öffne Nodvard Deck über die neue Adresse des Proxys und geh zu **Einstellungen → System →
Vertrauenswürdiger Proxy**. Oben steht, wie deine Anfrage gerade ankommt, zum Beispiel:

> Deine Anfrage kommt gerade von 172.18.0.5 (direkt) über http.

Schickt der Proxy schon seine Angaben mit, ist aber noch nicht eingetragen, zeigt die Karte einen Hinweis und den
Knopf **„Diese Adresse eintragen“**. Er übernimmt genau diese eine Adresse in das Feld, danach „Speichern“.

Hast du dich als Inhaber in den letzten 7 Tagen über einen Proxy aus deinem Heimnetz angemeldet, der nicht eingetragen
ist, nennen die Karte und „Braucht Aufmerksamkeit“ in der Übersicht seine Adresse: „Anmeldungen kommen über einen Proxy
(172.18.0.5), der nicht eingetragen ist.“ Den Hinweis siehst nur du, denn nur der Inhaber darf die Liste ändern, und
nur deine Anmeldungen zählen. Nodvard Deck trägt den Proxy nie selbst ein. Prüf, ob das wirklich dein Proxy ist, und
trag ihn dann wie oben ein. Kein Hinweis kommt, wenn der Proxy Nodvard Deck über eine öffentliche Adresse erreicht.
Ist das eine einzelne Adresse, etwa ein Proxy über eine IPv6-Adresse deines Anschlusses, trägst du sie selbst ein.
Cloudflare ohne Tunnel lässt sich nicht eintragen: Die Anfragen kommen aus großen Netzen (IPv4 bis /13), und
öffentliche Netze nimmt die Liste nur bis /24 (IPv6 bis /48), sonst dürfte fast jeder Rechner seine Adresse fälschen.
Nimm dann Cloudflare Tunnel (der Proxy läuft in deinem Netz, der Hinweis erscheint) oder einen eigenen Proxy davor.
Ebenso kein Hinweis, wenn der Proxy nur die Kopfzeile `Forwarded` schickt; die versteht Nodvard Deck nicht, nur
`X-Forwarded-For` und `X-Forwarded-Proto`.

Was du dort siehst, hängt davon ab, wie Proxy und Nodvard Deck verbunden sind:

- **Proxy im selben Docker-Netz** (z. B. im selben Compose-Projekt): Du siehst die Adresse des Proxy-Containers, etwa
  `172.18.0.5`. Nachsehen kannst du sie auch so:
  `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' <name-des-proxy-containers>`.
  Diese Adresse kann sich ändern, wenn der Container neu angelegt wird. Gib dem Proxy deshalb eine feste Adresse
  (`ipv4_address`, siehe Beispiel oben) oder trag das Netz ein (z. B. `172.18.0.0/16`), aber nur, wenn in diesem Netz
  ausschließlich deine eigenen Container hängen. Das Netz enthält auch sein Gateway (nächster Punkt); trag es deshalb
  nur ein, wenn Port 8080 nicht nach außen freigegeben ist (Abschnitt 6).
- **Proxy in einem anderen Docker-Projekt auf demselben Rechner**, der Nodvard Deck über den Port 8080 des Rechners
  erreicht: Du siehst meist das Gateway des Docker-Netzes von Nodvard Deck, etwa `172.18.0.1`
  (`docker network inspect <netz>` zeigt es unter „Gateway“). Über diese Adresse kommen aber auch andere Verbindungen
  an: alles, was auf dem Rechner selbst läuft, und bei Docker Desktop (Windows/macOS) oder Rootless-Docker sogar jede
  Verbindung auf Port 8080, auch von anderen Geräten im Heimnetz. Trägst du das Gateway ein, dürften diese Geräte
  eine falsche Absenderadresse vorgeben. Besser: Häng den Proxy zusätzlich in das Netz von Nodvard Deck
  (`docker network connect <netz> <proxy-container>`) und trag dann seine Adresse in diesem Netz ein.
- **Proxy auf einem anderen Rechner** im Heimnetz, z. B. `192.168.2.10`: Du siehst dessen Adresse. Trag genau diese
  eine Adresse ein, nicht das ganze Heimnetz.

## 4. Adresse eintragen

Es gibt zwei Wege:

- **In der Oberfläche:** Einstellungen → System → Vertrauenswürdiger Proxy. Eine Adresse oder ein kleines Netz pro
  Zeile, dann „Speichern“. Die Änderung gilt sofort, ohne Neustart. Sie steht mit altem und neuem Wert im Protokoll.
  Die Karte siehst du mit den Rechten für Einstellungen und System (Rolle „admin“ oder Inhaber). Ändern kann die
  Liste nur der Inhaber (Owner), andere Admins sehen sie nur (`PUT /settings/system.trusted_proxies` sonst 403).
  Das ist eine Leitplanke, keine harte Grenze: Wer auf dem Server, auf dem Nodvard Deck läuft, Befehle ausführen
  darf, kann auch die Compose-Datei ändern.
- **Über die Umgebungsvariable** `NODVARD_DECK_TRUSTED_PROXIES` in deiner Compose-Datei, mehrere Einträge durch Kommas
  getrennt, z. B. `NODVARD_DECK_TRUSTED_PROXIES: "172.18.0.5, fd00:1::5"`. Ist sie gesetzt, gilt nur sie. Die Karte
  zeigt die Liste dann nur an, ändern geht nur in der Compose-Datei (danach `docker compose up -d`). Ungültige
  Einträge darin werden übersprungen und stehen als Warnung im Container-Protokoll.

Erlaubt sind IPv4- und IPv6-Adressen und Netze in der Schreibweise `172.18.0.0/16`, höchstens 20 Einträge. Zu große
Netze lehnt Nodvard Deck ab: bei öffentlichen IPv4-Adressen alles größer als `/24`, in den privaten Bereichen
(`10.x`, `172.16.x` bis `172.31.x`, `192.168.x`, `100.64.x` bis `100.127.x`, `127.x`, `169.254.x`) alles, was größer
ist als der Bereich selbst (höchstens `/8`), bei IPv6 alles größer als `/48`. Ein Docker-Netz mit IPv6 ist meist ein
`/64`, das geht. `0.0.0.0/0` („allen vertrauen“) geht also nicht. Schreibst du eine Adresse mit Netzmaske wie
`192.168.2.10/24`, fragt Nodvard Deck nach, ob du nur diesen Rechner (`192.168.2.10`) oder das ganze Netz
(`192.168.2.0/24`) meinst.

Meint ein Eintrag ein Netz mit mehr als 16 Adressen (IPv4 größer als `/28`, IPv6 größer als `/124`), zeigt die Karte
eine gelbe Warnung, auch in privaten Netzen. Speichern geht trotzdem, am sichersten ist aber die einzelne Adresse
deines Proxys (Abschnitt 6).

## 5. Prüfen, ob alles stimmt

1. Öffne Nodvard Deck über den Proxy (`https://deck.example.org`) und sieh in die Karte. Dort sollte jetzt stehen:
   > Deine Anfrage kommt gerade von 192.168.2.40 über https, weitergeleitet von 172.18.0.5.

   Die erste Adresse ist die deines Geräts, „https“ heißt: Nodvard Deck weiß, dass die Verbindung verschlüsselt ist.
2. Melde dich ab und wieder an, dann lade die Seite neu. Du bleibst angemeldet.
3. Unter Einstellungen → Protokoll steht bei deiner Anmeldung die Adresse deines Geräts, nicht die des Proxys.
4. Öffne einmal das Terminal eines Servers. Kommt keine Verbindung zustande, fehlt im Proxy die Unterstützung für
   WebSockets.

## 6. Sicherheit: was du beachten solltest

- **Ist Nodvard Deck aus dem Internet erreichbar**, bremst es das Durchprobieren von Passwörtern: Ab 10 falschen
  Anmeldungen für einen Benutzernamen innerhalb von 24 Stunden wartet jeder weitere Versuch aus einem Netz doppelt so
  lange wie der vorige aus demselben Netz, höchstens 5 Minuten. Du bekommst dann die Meldung „Passwort wird
  durchprobiert“.
  - Aussperren kann dich so niemand: Aus deinem Heimnetz, über ein VPN wie Tailscale und aus jedem Netz, aus dem
    nicht geraten wird, kommst du sofort hinein. Vertippst du dich dort, wartest du 2 Sekunden.
  - Teilst du das Netz mit dem Angreifer (etwa dasselbe öffentliche WLAN), wartest du dort wie er, auch mit dem
    richtigen Passwort. Nimm dann ein anderes Netz oder das VPN.
  - Wer für jeden Versuch eine neue Adresse nimmt, etwa über ein Botnetz, wird so kaum aufgehalten. Dagegen helfen ein
    langes Passwort und Zwei-Faktor; die Meldung sagt dir, dass jemand rät.
- **Ohne Eintrag unter „Vertrauenswürdiger Proxy“** sieht Nodvard Deck nur die Adresse des Proxys. Die ist meist
  lokal, und dann greift diese Bremse nicht. Es bleiben die Grenzen je Adresse, und die gelten dann für alle zusammen:
  Wer von außen ein paar falsche Passwörter schickt, sperrt damit alle immer wieder für einige Minuten aus. Trag deinen
  Proxy deshalb unter Einstellungen → System → Vertrauenswürdiger Proxy ein. Dasselbe passiert, wenn Docker die echte
  Adresse gar nicht weitergibt, etwa bei IPv6 über den Userland-Proxy, bei Docker Desktop oder bei rootless Docker.
- **Trag nie ein ganzes Netz ein, aus dem auch normale Geräte kommen**, also nicht dein Heimnetz wie
  `192.168.2.0/24`. Jedes eingetragene Gerät darf Nodvard Deck eine beliebige Absenderadresse vorgeben. Damit ließe
  sich die Sperre nach falschen Anmeldungen umgehen, und im Protokoll stünden falsche Adressen. Trag nur deinen Proxy
  ein, am besten mit seiner einzelnen Adresse.
- **Nodvard Deck bleibt auch direkt erreichbar**, per HTTP auf Port 8080, am Proxy vorbei. Willst du das nicht, gib den
  Port nicht nach außen frei:
  - Proxy im selben Docker-Netz: die Zeilen `ports:` beim Dienst `nodvard-deck` ganz weglassen (siehe Beispiel oben).
  - Proxy direkt auf dem Rechner: den Port nur an den Rechner selbst binden:

    ```yaml
    services:
      nodvard-deck:
        ports:
          - "127.0.0.1:8080:8080"
    ```
- **Gateway oder ganzes Docker-Netz eingetragen?** Dann gib Port 8080 nicht nach außen frei (keine `ports:`-Zeilen
  oder nur `127.0.0.1:8080:8080` wie oben). Sonst kommen je nach Docker-Einrichtung auch Verbindungen anderer Geräte
  über das Gateway an und dürften ihre Adresse fälschen. Bindest du den Port an `127.0.0.1`, erscheinen Verbindungen
  vom Rechner selbst ebenfalls mit der Adresse des Gateways; das betrifft dann nur Programme auf diesem Rechner.
- Der Proxy sollte `X-Forwarded-For` selbst setzen bzw. seine Adresse anhängen. Das tun Nginx Proxy Manager, Traefik
  und Caddy von sich aus. Schickt ein Gerät selbst eine erfundene Adresse mit, steht sie in der Kette links von der
  echten. Nodvard Deck liest die Kette von rechts und nimmt die erste Adresse, die nicht selbst eingetragen ist; was
  links davon steht, wird nicht gelesen. Das gilt, solange nur dein Proxy eingetragen ist. Trägst du ein ganzes Netz
  ein, kann jedes Gerät darin eine Adresse vorgeben, denn Nodvard Deck liest die Kette dann über alle Geräte dieses
  Netzes hinweg weiter nach links.
- Das Anmelde-Cookie bekommt `Secure` automatisch, sobald die Anfrage über HTTPS kam. Die Umgebungsvariable
  `NODVARD_DECK_COOKIE_SECURE` (`true`/`false`) erzwingt es weiter, falls du das brauchst.
- Das Image startet uvicorn mit `--no-proxy-headers`. Hast du früher `FORWARDED_ALLOW_IPS` gesetzt, wirkt das nicht
  mehr. Trag die Adresse deines Proxys stattdessen unter Einstellungen → System → Vertrauenswürdiger Proxy ein
  (Abschnitt 4). Startest du den Container mit einem eigenen `command:`, nimm `--no-proxy-headers` mit, sonst wertet
  uvicorn die Angaben selbst aus und die Karte zeigt Verwirrendes.

## 7. Wenn etwas nicht klappt

| Was du siehst | Woran es liegt | Was hilft |
|---|---|---|
| Die Karte zeigt „über http“, obwohl du per HTTPS kommst, oder im Protokoll steht bei jeder Anmeldung die Adresse des Proxys | Die Adresse des Proxys ist nicht eingetragen (dann fehlt dem Anmelde-Cookie `Secure`, und die Sperre nach falschen Anmeldungen zählt alle Geräte als eines), oder er schickt kein `X-Forwarded-Proto` | Adresse eintragen (Abschnitt 3), Proxy prüfen |
| Die Karte zeigt „(direkt)“ mit der Adresse des Proxys, aber keinen Hinweis | Der Proxy schickt keine `X-Forwarded-*`-Angaben | Bei Nginx Proxy Manager, Traefik und Caddy ist das Standard; bei eigenen Konfigurationen ergänzen |
| Die Karte zeigt ein Gateway wie `172.18.0.1` statt des Proxys | Der Proxy erreicht Nodvard Deck über den Port des Rechners | Proxy in das Docker-Netz von Nodvard Deck hängen (Abschnitt 3) |
| Nach einem Update des Proxys stimmt nichts mehr | Der Proxy-Container hat eine neue Adresse bekommen | Feste Adresse vergeben oder das eigene Docker-Netz eintragen |
| Terminal und Live-Anzeige verbinden sich nicht | WebSockets werden nicht durchgereicht | Bei Nginx Proxy Manager „Websockets Support“ einschalten |
| „ist zu groß“ beim Speichern | Netz zu groß (Grenzen in Abschnitt 4) | Nur die Adresse des Proxys oder ein kleineres Netz eintragen |
