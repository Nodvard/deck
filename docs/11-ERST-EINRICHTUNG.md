# Erst-Einrichtung – Schritt für Schritt

> English version: [en/FIRST-SETUP.md](en/FIRST-SETUP.md)

Diese Anleitung beschreibt die aktuelle Einrichtung von Nodvard Deck.

In wenigen Minuten zum laufenden Dashboard. Du brauchst nur einen Rechner mit Docker, der durchläuft: einen Raspberry Pi,
ein NAS, einen kleinen Server oder eine VM. Die Abschnitte 1 bis 7 führen in dieser Reihenfolge durch die Einrichtung.
Technik, Sonderfälle und alles zum Nachschlagen stehen im [Anhang](#anhang) und in eigenen Anleitungen.

Im Dashboard öffnet **„Anleitung“** diese Seite in einem neuen Tab: unten in der Navigation der Einstellungen und über
die Suche (Strg+K, „Anleitung“, „Hilfe“ oder „Doku“). An vielen Stellen steht ein Link direkt am passenden Abschnitt.

<!-- Die Marken <a id="…"></a> halten Links älterer Versionen gültig (sie verlinken diese Datei auf main). Nicht entfernen. -->

**Einrichten**

1. [Schnellstart](#1-schnellstart): Nodvard Deck mit drei Befehlen starten
2. [Einrichtungsassistent](#2-einrichtungsassistent-setup): Konto anlegen, Zeitzone, Module
3. [Den ersten Server verbinden](#3-den-ersten-server-verbinden): Server anlegen, SSH-Zugang, Verbindung prüfen
4. [Erweiterungen einschalten](#4-erweiterungen-einschalten): ein guter Anfang, mit oder ohne
   [Proxmox](#42-proxmox-verbinden)
5. [Push-Nachrichten (ntfy)](#5-push-nachrichten-ntfy): Meldungen aufs Handy
6. [Das Dashboard selbst sichern](#6-das-dashboard-selbst-sichern): verschlüsselt und nach Zeitplan
7. [Typische Stolpersteine](#7-typische-stolpersteine), auch [Ausgesperrt? Notfall-Befehle](#73-ausgesperrt-notfall-befehle)

**Nachschlagen:** [Anhang](#anhang) A bis M und die eigenen Anleitungen
[Installation je Plattform](13-INSTALLATION.md), [Root-Rechte und Hilfsskripte](14-ROOT-RECHTE.md),
[Synology DSM](15-SYNOLOGY-DSM.md), [Sicherung im Detail](16-SICHERUNG.md),
[Ohne Oberfläche](17-OHNE-OBERFLAECHE.md), [Proxmox-Token](10-PROXMOX-TOKEN.md) und
[Reverse-Proxy](12-REVERSE-PROXY.md).

## 1. Schnellstart

<a id="1-container-starten"></a>

Nodvard Deck läuft als Docker-Container. Das fertige Image kommt aus der GitHub-Container-Registry
(`ghcr.io/nodvard/deck`, für x86-Rechner und Raspberry Pi), es wird nichts gebaut. Auf einem Rechner mit Docker und
Docker Compose sind es drei Befehle:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

Danach im Browser **`http://<Adresse-des-Rechners>:8080`** öffnen, z. B. **http://192.168.1.10:8080** (auf demselben
Rechner **http://localhost:8080**). Dort wartet der Einrichtungsassistent ([Abschnitt 2](#2-einrichtungsassistent-setup)).
Er fragt als Erstes nach einem Einrichtungscode aus dem Protokoll des Containers; wo der steht, zeigt
[2.1](#21-den-einrichtungscode-finden).

- **Erster Start:** Das Image (knapp 400 MB) wird geladen, danach legt der Container die Datenbank an. Bis zu fünf
  Minuten ist das normal. Kontrolle auf demselben Rechner: `curl -s http://localhost:8080/api/v1/health` zeigt
  `"status":"ok"`.
- **Noch kein Docker?** Wie du es installierst, zeigt die [Anleitung von Docker](https://docs.docker.com/engine/install/)
  für dein System; Docker Compose gehört dort dazu.
- **„permission denied“ unter Linux:** Dann `sudo` vor `docker` schreiben. Ist dein Benutzer in der Gruppe `docker`, geht
  es ohne. Die Beispiele in dieser Anleitung schreiben `sudo docker …`.
- **Windows oder macOS zum Ausprobieren:** [Docker Desktop](https://docs.docker.com/desktop/) installieren und starten,
  dann die drei Befehle in einem Terminal eingeben (Windows: PowerShell, macOS: Terminal). In PowerShell `curl.exe` statt
  `curl` schreiben und die Befehle einzeln ausführen. Für den Dauerbetrieb ist ein Rechner besser, der immer läuft.
- **Port 8080 schon belegt?** (Meldung „port is already allocated“ oder „address already in use“): Neben die
  `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DECK_PORT=8081` legen und `docker compose up -d` noch einmal
  ausführen. Die Adresse ist dann `…:8081`, auch bei der Kontrolle oben.
- **Aktualisieren:** im selben Ordner `docker compose pull && docker compose up -d`. Die Daten bleiben erhalten
  ([Anhang I](#i-updates)).

Alle weiteren Befehle in dieser Anleitung (Einrichtungscode, Protokoll, Notfall-Befehle, Wiederherstellen) laufen im
Ordner mit der `compose.yml`, hier also nach `cd ~/nodvard-deck`.

### 1.1 Andere Wege: NAS, Portainer und mehr

<a id="11-installation-ohne-das-repo"></a>
<a id="im-terminal"></a>
<a id="mit-einer-web-oberfläche"></a>
<a id="auf-einem-nas"></a>
<a id="raspberry-pi-proxmox-und-docker-desktop"></a>

Kein Terminal zur Hand, oder läuft bei dir schon eine Oberfläche für Container? Für jeden dieser Wege gibt es eine eigene
Schritt-für-Schritt-Anleitung in **[Installation je Plattform](13-INSTALLATION.md)**, jeweils mit Öffnen, anderem Port,
Einrichtungscode und Aktualisieren:

| Du hast … | Anleitung |
|---|---|
| eine Web-Oberfläche für Container | [Portainer](13-INSTALLATION.md#portainer-weg-1-aus-dem-repository), [Dockge](13-INSTALLATION.md#dockge) |
| ein NAS | [Synology](13-INSTALLATION.md#synology-container-manager-dsm-72-oder-neuer), [UGREEN](13-INSTALLATION.md#ugreen-ugos-pro-app-docker), [QNAP](13-INSTALLATION.md#qnap-container-station-3), [Unraid](13-INSTALLATION.md#unraid), [TrueNAS SCALE](13-INSTALLATION.md#truenas-scale-ab-2410), [OpenMediaVault](13-INSTALLATION.md#openmediavault-plugin-compose), [ZimaOS und CasaOS](13-INSTALLATION.md#zimaos-und-casaos) |
| ein bestimmtes Gerät | [Raspberry Pi](13-INSTALLATION.md#raspberry-pi), [Proxmox (VM oder LXC)](13-INSTALLATION.md#proxmox-vm-oder-lxc-container), [Docker Desktop](13-INSTALLATION.md#docker-desktop-windows-macos-zum-ausprobieren) |
| Docker ohne Compose oder Podman | [`docker run`](13-INSTALLATION.md#ein-einzelner-docker-run-befehl-ohne-compose), [Podman](13-INSTALLATION.md#podman) |

Mit Portainer oder einer NAS-Oberfläche gibt es den Ordner mit der `compose.yml` nicht: Protokoll und Neustart findest du
beim Container in der Oberfläche, Befehle wie die Notfall-Befehle gibst du in seiner Konsole ein
([7.3](#73-ausgesperrt-notfall-befehle)). Daten als eigener Ordner und selbst bauen: [Anhang A](#a-installation-im-detail).

### Verbindung absichern

Beim Aufruf über `http://<Rechner-IP>:8080` ist die Verbindung unverschlüsselt. Im Heimnetz kann dein
Sitzungs-Cookie mitgelesen werden. Deshalb erscheint in Schritt 1 der Einrichtung und unter
**Einstellungen → System → Vertrauenswürdiger Proxy** ein Warnbalken. Du kannst ihn für die Sitzung
ausblenden. Bei der nächsten Anmeldung erscheint er wieder, solange du HTTP verwendest. Bei HTTPS
und beim Zugriff über `localhost`, `127.0.0.1` oder `::1` erscheint er nicht.

Du kannst die Verbindung auf zwei Wegen schützen:

- **HTTPS über einen Reverse-Proxy:** Richte mit **Nginx Proxy Manager** oder **Caddy** ein Zertifikat
  und HTTPS ein. Rufe Deck danach nur über die HTTPS-Adresse auf. Trage die Adresse deines Proxys unter
  **Einstellungen → System → Vertrauenswürdiger Proxy** (`system.trusted_proxies`) ein, damit Deck
  dessen HTTPS-Angabe erkennt. Die [Proxy-Anleitung](12-REVERSE-PROXY.md) erklärt beide Wege.
  Beschränke den direkten Zugriff auf Port 8080 auf den Proxy, damit der unverschlüsselte Weg nicht
  weiter für andere Geräte offensteht.
- **Zugriff nur über Tailscale oder ein VPN:** Erlaube den Zugriff auf Deck ausschließlich über das
  verschlüsselte Netz und sperre Port 8080 für direkte Verbindungen aus dem Heimnetz und Internet.
  Verwende anschließend die Tailscale- oder VPN-Adresse. Bei HTTP innerhalb des Tunnels bleibt der
  Warnbalken sichtbar, weil Deck den Schutz durch das VPN nicht erkennen kann.

## 2. Einrichtungsassistent (/setup)

Solange es noch keinen Benutzer gibt, leitet die Anmeldeseite automatisch auf `/setup` um. Der Assistent hat sechs
Schritte; nur der erste ist Pflicht, alles andere lässt sich überspringen und später in den Einstellungen nachholen.

**Du hast schon eine Sicherung?** Im ersten Schritt gibt es **„Sicherung einspielen“**: Datei hochladen,
Passwort eingeben, Zusammenfassung prüfen, einspielen – danach meldest du dich mit dem Konto aus der Sicherung an
und musst nichts neu einrichten ([6.2](#62-wiederherstellen-sicherung-einspielen)). Auch dort wird der
Einrichtungscode (unten) verlangt.

### 2.1 Den Einrichtungscode finden

Damit nicht jeder im Netz, der die Seite zuerst öffnet, das erste Konto (und damit den Inhaber) bekommt, verlangt
`/setup` einen **Einrichtungscode**. Nodvard Deck würfelt ihn beim Start und schreibt ihn ins **Protokoll (Log) des
Containers**: in einer klar umrahmten Box aus `+`, `=` und `|`, der Code zusätzlich allein in einer Zeile,
etwa `K7MQ-X2VD-H9PA`.
Groß-/Kleinschreibung, Striche und Leerzeichen sind beim Eintippen egal. Im Assistenten klappt „Wo finde ich den Code?“
dieselbe Hilfe auf.

- **Im Terminal** (im Ordner mit der `compose.yml`):

  ```bash
  sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode
  # Einrichtungscode: K7MQ-X2VD-H9PA (im Browser eingeben)
  ```

  Windows PowerShell hat kein `grep`, dort:
  `docker compose logs nodvard-deck | Select-String -Context 0,4 Einrichtungscode`. Es geht auch ganz ohne Filter:
  `docker compose logs nodvard-deck` zeigt das ganze Protokoll, der Code steht im Rahmen.
- **Docker Desktop:** links **Containers** → den Container von Nodvard Deck auswählen → Reiter **Logs**.
- **Portainer:** **Containers** → `nodvard-deck-nodvard-deck-1` (allgemein `<Stack-Name>-nodvard-deck-1`) → **Logs**.
- **Synology Container Manager:** **Container** → den Container von Nodvard Deck auswählen → **Action** → **Details** →
  **Log**.
- **Unraid:** Symbol des Containers → **Logs**.
- **Andere NAS und Oberflächen:** steht beim jeweiligen Weg in [Installation je Plattform](13-INSTALLATION.md).

Der automatisch erzeugte Code bleibt über Neustarts gleich. Solange kein Konto existiert, steht er bei jedem Start
erneut in der Box. Sobald das erste Konto angelegt ist, wird der Code ungültig und seine Datei gelöscht.
Alte Einträge im Container-Protokoll bleiben sichtbar.

**Den Code selbst festlegen (optional):** Die Compose-Dateien unter `deploy/` reichen
`NODVARD_DECK_SETUP_CODE` an den Container weiter. Vor dem ersten Start legst du neben deiner `compose.yml` eine
Textdatei mit dem genauen Namen **`.env`** an (nicht `.env.txt`). Darin steht eine Zeile:

```dotenv
NODVARD_DECK_SETUP_CODE=mein-eigener-code-7392
```

Wähle einen eigenen, schwer zu erratenden Code statt des Beispiels. Er muss insgesamt mindestens **8 Buchstaben
oder Ziffern** enthalten. Es zählen nur `A` bis `Z`, `a` bis `z` und `0` bis `9`. Andere Zeichen, auch Umlaute,
Striche und Leerzeichen, werden entfernt; Kleinbuchstaben werden zu Großbuchstaben. Starte anschließend mit
`docker compose up -d`.
Bei der Installation aus dem Repository liegt die `.env` neben `deploy/docker-compose.yml`.

**Portainer:** Beim Anlegen des Stacks unter **Environment variables** den Namen `NODVARD_DECK_SETUP_CODE` und
deinen Code als Wert eintragen, dann den Stack starten. Die Compose-Datei enthält die passende Weitergabe bereits.
Auch ein selbst vorgegebener Code steht in der Box im Container-Protokoll und wird im Assistenten eingegeben.

Ohne Variable oder mit leerem Wert verwendet Nodvard Deck einen zufälligen Code. Ein zu kurzer Wert wird mit
einem Hinweis im Protokoll ignoriert; dann gilt der bereits gespeicherte oder ein neu erzeugter Zufallscode.
Die Variable wirkt **nur, solange noch kein Konto existiert**. Nach der Einrichtung wird sie ignoriert und beim
Start ein Hinweis ohne den Wert protokolliert. Sie kann kein weiteres Konto anlegen und kein Passwort zurücksetzen.
Entferne die Zeile danach aus der `.env` bzw. aus den Stack-Variablen.
Mehr zum Code (Fehlversuche, wo er gespeichert ist): [Anhang B.1](#b1-der-einrichtungscode).

### 2.2 Die sieben Schritte

1. **Administrator-Konto anlegen** (Pflicht): Einrichtungscode, Benutzername (mindestens 3 Zeichen, nur Kleinbuchstaben,
   Ziffern sowie `.`, `-` und `_`, z. B. `admin`) und ein Passwort mit mindestens 8 Zeichen. Dieses erste Konto ist der
   **Inhaber**. Der Inhaber darf immer alles, auch wenn später Rollen verstellt werden – so kann man sich nicht
   aussperren.
2. **Zeitzone:** Vorausgewählt ist die Zeitzone deines Geräts. Danach richten sich Zeitpläne (etwa das Morgen-Briefing)
   und Wartungsfenster.
3. **Was willst du nutzen?** Module per Klick einschalten; keins ist Pflicht. Welche sich für den Anfang lohnen, steht in
   [Abschnitt 4](#4-erweiterungen-einschalten). Module mit dem Hinweis „Braucht danach noch Angaben“ richtest du nach dem
   Assistenten unter Einstellungen → Erweiterungen ein.
4. **Zwei-Faktor-Anmeldung** (optional, aber empfohlen): „Jetzt einrichten“, dein Passwort bestätigen, den QR-Code mit
   der Authenticator-App scannen und den sechsstelligen Code eingeben. Danach erscheinen **einmalig die
   Wiederherstellungs-Codes**: kopieren oder als Textdatei speichern, am besten im Passwortmanager oder ausgedruckt.
   Nachholen geht unter Einstellungen → Mein Konto ([Anhang C](#c-zwei-faktor-und-wiederherstellungs-codes)).
5. **Aussehen** (optional): Produktname, Untertitel und Akzentfarbe. Logo, Theme und mehr gibt es später unter
   Einstellungen → Darstellung.
6. **Nutzungsdaten** (optional): Drei unabhängige Stufen, alles aus. „Weiter ohne Teilen“ überspringt die Freigabe. [Was gesendet wird](18-NUTZUNGSDATEN.md).
7. **Fertig:** Der Schritt nennt den Notfall-Befehl für ein vergessenes Passwort ([7.3](#73-ausgesperrt-notfall-befehle)).
   „Weiter zum Dashboard“ öffnet das Cockpit.

Jeder Schritt im Einzelnen, auch eigenes CSS und sein Notausgang: [Anhang B](#b-einrichtungsassistent-im-detail).

**Die Karte „Erste Schritte“:** Auf der Startseite (Cockpit) führt oben eine Checkliste durch
genau diese Schritte – Server anlegen, SSH-Zugang hinterlegen, Verbindung prüfen, Module
einrichten, Push-Nachrichten, erstes Widget – jeder mit einem Knopf zum richtigen Ort. Die
Häkchen setzt Nodvard Deck selbst aus dem, was schon eingerichtet ist. „SSH-Zugang hinterlegen“ zählt einen
Schlüssel erst, wenn sich Nodvard Deck damit wirklich angemeldet hat (ein Passwort sofort); bis dahin führt
**„Befehl ansehen“** zur Seite des Servers mit schon aufgeklapptem Einrichtungsbefehl. „Verbindung prüfen“ ist erst
abgehakt, wenn eine Anmeldung bestätigt ist: Dass der Server antwortet, reicht nicht. Es erscheinen nur Schritte,
für die du das Recht hast. Die Karte verschwindet von allein, wenn alles erledigt ist; mit
**„Ausblenden“** nimmst du sie früher weg (gilt für dein Konto auf jedem Gerät). Auch leere Seiten
(Terminal, Dateien, Server-Seite, Docker, Gameserver, Proxmox) sagen jetzt, was fehlt, und
bieten den Knopf zum nächsten Schritt an.

## 3. Den ersten Server verbinden

<a id="5-server-und-ssh-zugang"></a>

Damit Nodvard Deck einen Server überwachen und bedienen kann, legst du ihn an und gibst ihm einen SSH-Zugang. Alles dazu
steht auf einer Seite: **Einstellungen → Server & Zugänge**. Dort legst du Server an, richtest ihren SSH-Zugang ein und
prüfst, ob die Verbindung klappt. Die Seite ist für das Handy gedacht und nur für Benutzer mit dem Recht `hosts.write`
(Inhaber und `admin`) sichtbar.

- Den **Rechner, auf dem Nodvard Deck läuft** (z. B. ein Raspberry Pi), legst du genauso von Hand an, wenn er kein
  Proxmox-Gast ist.
- **Proxmox-Knoten, VMs und Container** liest das Modul „Proxmox VE“ von selbst ein ([4.2](#42-proxmox-verbinden)).
- Terminal, Server-Zustand, Docker, Nodvard Shield, Gameserver und Skripte brauchen für jeden Server einen
  **SSH-Zugang** (Benutzer + Schlüssel oder Passwort). Den speichert Nodvard Deck verschlüsselt; der private Schlüssel
  verlässt den Tresor nie und wird nirgends angezeigt.

**Noch keinen Server zur Hand?** Im Cockpit zeigt **„Mit Beispieldaten ansehen“** ausgedachte Server, damit du siehst, wie
alles gefüllt aussieht ([Anhang E.2](#e2-beispieldaten)).

### 3.1 Server anlegen

1. **Server hinzufügen** drücken (bei einer leeren Liste gibt es den Knopf auch in der Mitte). Kommst du über
   „Server hinzufügen“ im Cockpit oder in der Karte „Erste Schritte“, ist das Formular schon offen.
2. **Kurzname** – klein, ohne Leerzeichen, z. B. `bastel-pi`. Er lässt sich später **nicht**
   mehr ändern (Nodvard Shield und andere Erweiterungen erkennen den Server daran).
3. **Anzeigename** (so heißt der Server in den Listen; leer = Kurzname) und **Adresse**
   (IP oder Rechnername, ohne `http://` und ohne Port, z. B. `192.168.1.30`).
4. **Betriebssystem:** Linux oder Windows. Für Windows-Server gibt es keinen
   Einrichtungsbefehl und keine Rechte-Prüfung. Einen Windows-Server (etwa einen Gameserver) wirklich als Windows
   eintragen: Lange Befehle schickt Nodvard Deck sonst auf Linux-Art, und die scheitern unter Windows.
5. **Markierungen:** Die festen Auswahlkästchen erklären, wofür die jeweilige Markierung verwendet wird:
   - **Docker verwalten** (`docker`): voller Zugang zum Modul Docker, etwa für Container, Stacks und
     Image-Updates. Auch die Container-Wache von Nodvard Shield nimmt ab Werk diese Server.
   - **Docker nur bedienen** (`docker-bedienen`): Container und ihre Logs ansehen sowie Container starten,
     stoppen und neu starten. Die Einrichtung verwendet dafür ein begrenztes Hilfsskript statt der Gruppe `docker`.
     Sind beide Docker-Haken gesetzt, gilt der volle Zugang.
   - **Gameserver** (`gameserver`): Der Server erscheint im Modul Gameserver.
   - **Nodvard Deck läuft hier** (`dashboard`): Das Modul System sperrt hier Neustarts von Docker und
     containerd. Nodvard Shield plant diesen Server bei automatischen Updates zuletzt ein und verschiebt
     seinen Neustart hinter die Zusammenfassung.
   - **NAS** (`nas`): Kennzeichnet den Server als NAS und schaltet keine zusätzlichen Funktionen frei.

   Unter **Eigene Markierungen** bleiben eigene Namen möglich, mit Komma oder Leerzeichen getrennt.
   Die Kästchen setzen die Standard-Markierungen. Hast du die Server-Markierung in einem Modul geändert,
   trägst du den passenden Namen dort als eigene Markierung ein. Die Markierungen lassen sich später ändern,
   auch bei Proxmox-VMs. Von Erweiterungen verwaltete Markierungen bleiben erhalten und sind nicht änderbar;
   ein passendes Auswahlkästchen ist bereits angehakt und gesperrt.
6. **Anlegen.** Die Seite des neuen Servers öffnet sich und bietet gleich den nächsten Schritt an.

Falsche Eingaben meldet die Seite direkt am Feld auf Deutsch (z. B. „Kurzname: nur
Kleinbuchstaben, Ziffern, - und _ …“).

Adresse später korrigieren (etwa bei Proxmox-VMs ohne Gast-Agent): [Anhang E.3](#e3-adresse-korrigieren).

### 3.2 SSH-Zugang einrichten – drei Wege

Auf der Seite des Servers, Karte **SSH-Zugang**:

- **SSH-Schlüssel erzeugen (empfohlen).** Nodvard Deck erzeugt einen eigenen Schlüssel nur für
  diesen Server. Du gibst nur den **Benutzer auf dem Server** an (Vorgabe `nodvard`, ein eigener
  Benutzer nur für Nodvard Deck; wird angelegt, falls es ihn noch nicht gibt – bei Proxmox-Knoten `root` nehmen) und den Port (22).
  Danach zeigt die Seite den **Einrichtungsbefehl** (3.3).
- **Passwort eingeben.** Für einen Benutzer, den es auf dem Server schon gibt. Das Passwort wird
  verschlüsselt gespeichert und nie wieder angezeigt. Ein Schlüssel ist sicherer.
- **Eigenen Schlüssel einfügen.** Den privaten Schlüssel (Text, der mit `-----BEGIN …` beginnt,
  **ohne Passphrase**) einfügen. Danach gibt es auch dazu den Einrichtungsbefehl für den Server.

Was du eintippst oder einfügst, wird beim Abschicken aus dem Formular gelöscht.

### 3.3 Einrichtungsbefehl auf dem Server ausführen

Der Befehl ist **ein einziger Einzeiler**. Mit **Kopieren** (geht auch auf `http://`, ohne
sicheren Kontext) in die Zwischenablage, auf dem Server anmelden (per `ssh`, am Bildschirm oder über die
Konsole deiner VM-Verwaltung), einfügen, Enter. Er

- legt den Benutzer an, falls nötig (ohne Passwort, Anmeldung nur mit dem Schlüssel),
- trägt den öffentlichen Schlüssel in `~/.ssh/authorized_keys` ein (mit `restrict,pty`: Terminal, Dateien und Befehle
  gehen, Weiterleitungen nicht; Dropbear bekommt passende Optionen, siehe [Anhang E.4](#e4-was-der-einrichtungsbefehl-genau-tut)),
- richtet auf Wunsch **Root-Rechte ohne Passwort** und die **Gruppe docker** ein (siehe [Anhang F](#f-root-rechte-ohne-passwort-und-gruppe-docker)).

Unter „Was macht der Befehl?“ steht derselbe Ablauf lesbar, Zeile für Zeile. Der Befehl läuft als root direkt, sonst
über `sudo`. Er funktioniert auf Proxmox (kein sudo) ebenso wie auf Debian oder dem Pi und lässt sich gefahrlos
mehrfach ausführen. Ab Version 0.13.0 verankert der Befehl bei klassischem sudo Prüfsummen für die Hilfsskripte.
Auf Systemen mit `sudo-rs` (Standard ab Ubuntu 25.10) wird die Absicherung per Prüfsumme nicht unterstützt.

**Root-Rechte und Gruppe docker:** Am Befehl musst du dafür nichts von Hand ändern. Die Seite setzt die Haken „Root-Rechte
ohne Passwort“ und „Zur Gruppe docker hinzufügen“ selbst, weil die eingeschalteten Module melden, was sie brauchen.
Ohne beide Haken bekommst du die **schlanke Variante**: eigener Benutzer, nur Schlüssel, keine Root-Rechte. Die reicht für
Terminal, Dateien und Skripte. Was die Haken erlauben: [Anhang F](#f-root-rechte-ohne-passwort-und-gruppe-docker).

#### Mit Passwort einrichten (Nodvard Deck führt den Befehl aus)

Du musst den Befehl nicht selbst einfügen: Im Kasten **„Server für diesen Schlüssel einrichten“** wählst du statt
„Befehl selbst einfügen“ den zweiten Weg, **„Mit Passwort einrichten (Nodvard Deck führt den Befehl aus)“**. Das gibt
es für Zugänge mit Schlüssel (erzeugt oder eigener) auf Linux-Servern. Es ist etwas anderes als der Zugang „Passwort
eingeben“ aus [3.2](#32-ssh-zugang-einrichten--drei-wege): Hier brauchst du das Passwort nur für diese eine
Einrichtung, und es wird nie gespeichert. Danach meldet sich Nodvard Deck mit dem Schlüssel an.

- **Was du eingibst:** einmal das Konto (Vorgabe `root`) und sein Passwort. Das Konto muss sich per SSH mit Passwort
  anmelden dürfen: `root` oder ein Konto mit sudo. Debian und Ubuntu lassen `root` meist nicht mit Passwort herein; nimm
  dann ein Konto mit sudo (das Formular sagt das am Feld). Bei einem Konto ohne `root` nimmt sudo dasselbe Passwort;
  nimmst du den Haken „Für sudo dasselbe Passwort nehmen“ weg, muss das Konto sudo ohne Passwort dürfen.
- **Was passiert:** Nodvard Deck meldet sich damit **einmal** an, führt **genau denselben Befehl** aus wie in 3.3 (die
  Haken „Root-Rechte ohne Passwort“ und „Gruppe docker“ darüber gelten auch hier), schließt die Verbindung und prüft
  danach mit dem neuen Schlüssel wie bei „Verbindung prüfen“ ([3.4](#34-verbindung-prüfen)). Darunter stehen die
  Meldungen des Befehls und das Ergebnis der Prüfung. Hatte der Server noch keinen Standard-Zugang, wird dieser Zugang
  der Standard. Beim Ersetzen eines Zugangs ([Anhang E.7](#e7-zugang-ersetzen-löschen-gruppen)) zählt die Prüfung wie „Neuen
  Zugang prüfen“; danach kannst du „Neuen Zugang verwenden“ drücken.
- **Das Passwort wird nie gespeichert:** nicht in der Datenbank, nicht im Tresor, nicht im Protokoll und nicht im Log.
  Das Feld wird beim Absenden sofort geleert, und der Browser soll es nicht merken. Im Protokoll steht, dass es einen
  Versuch gab („Server mit Passwort einrichten“, `host.setup_with_password`), mit Server, Konto und Ergebnis – nie das
  Passwort.
- **Der Server-Schlüssel muss bekannt sein.** Das Passwort geht nie an einen Server, dessen Fingerabdruck Nodvard Deck
  noch nicht kennt oder der sich geändert hat. Kennt Nodvard Deck ihn noch nicht, zeigt das Formular den Fingerabdruck
  zum Vergleichen und Bestätigen (wie in [3.4](#34-verbindung-prüfen)); danach gibst du das Passwort noch einmal ein.
  Bei einem geänderten Schlüssel gibt es nur die Warnung, ohne Bestätigen-Knopf.

Grenzen, Warnung ohne HTTPS und was noch nicht geht: [Anhang E.5](#e5-mit-passwort-einrichten-grenzen-und-sicherheit).

### 3.4 Verbindung prüfen

Karte **Verbindung prüfen** (bis zu 30 Sekunden). Die Seite zeigt Punkt für Punkt, was klappt
und was nicht – mit Hinweis, was zu tun ist:

1. **Server erreichbar** – Adresse, Port, Firewall, läuft SSH?
2. **Server-Schlüssel** – siehe unten.
3. **Anmeldung** – nimmt der Server den Schlüssel bzw. das Passwort?
4. **Root-Rechte** – geht `sudo` ohne Passwort? (Nur ein Hinweis, kein Fehler: Die schlanke
   Variante ohne Root-Rechte ist erlaubt.)
5. **Was Erweiterungen brauchen** – z. B. Docker ohne sudo für das Modul Docker.
6. **Betriebssystem** – z. B. „Debian 12 · aarch64 · Raspberry Pi 4 Model B“.

**Der Server-Schlüssel (Fingerabdruck):** Jeder SSH-Server hat einen eigenen Fingerabdruck.

- **Neu:** Beim ersten Mal zeigt die Prüfung den Fingerabdruck und hält an. Bis dahin ging **kein
  Passwort und kein Schlüssel** an den Server. Vergleiche den Fingerabdruck mit dem auf dem Server
  (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`, die Seite zeigt den Befehl zum
  Kopieren) und drücke **„Fingerabdruck stimmt – bestätigen“**. Danach prüft die Seite gleich weiter. Im eigenen
  Heimnetz und bei einem frisch eingerichteten Server ist Bestätigen in Ordnung; bei einem Server im Internet
  vergleiche den Fingerabdruck wirklich.
- **Geändert:** Zeigt der Server später einen anderen Fingerabdruck, erscheint eine deutliche
  Warnung – **ohne** Bestätigen-Knopf. Das passiert nach einer Neuinstallation des Servers, aber
  auch, wenn sich jemand dazwischenschaltet. War es eine Neuinstallation: Karte **Server-Schlüssel**
  → **Vergessen**, dann erneut prüfen und den neuen Fingerabdruck bestätigen. Wenn nicht: erst
  herausfinden, warum.
- Zu viele Prüfungen hintereinander bremst die Seite („Zu viele Prüfungen. Bitte in N Minuten …“).

Ab der Liste geht es auch schneller: **Prüfen** neben jedem Server zeigt das Ergebnis kurz
(„4 von 5 in Ordnung“), und die Server-Seite (`/hosts/<ID>`) hat eine Karte **Zugang** mit
„Verbindung prüfen“.

Wann das Abzeichen des Zugangs grün wird und wie neue Server-Schlüssel gemerkt werden: [Anhang E.6](#e6-server-schlüssel-und-grüner-zugang).
Zugang ersetzen oder löschen, Gruppen, Server löschen: [Anhang E.7](#e7-zugang-ersetzen-löschen-gruppen).

**Testen:** Im Menü „Terminal“ taucht der Server jetzt auf (die Terminal-Erweiterung schaltet sich mit dem
ersten SSH-Zugang ein, falls du sie nicht selbst ausgeschaltet hattest).
Eine Sitzung öffnen – klappt die Anmeldung, klappt sie auch für alle anderen Erweiterungen. Eine
Terminal-Sitzung ohne Eingabe und Ausgabe schließt sich nach 30 Minuten von selbst.

## 4. Erweiterungen einschalten

Bei einer frischen Installation sind **alle Erweiterungen aus** – bis auf eine Ausnahme: **Terminal
schaltet sich von selbst ein**, sobald du dem ersten Server einen SSH-Zugang gibst (Schlüssel
erzeugen, Passwort oder eigener Schlüssel; siehe [3.2](#32-ssh-zugang-einrichten--drei-wege)). Das
Protokoll hält es fest („Erweiterung automatisch eingeschaltet“). Das passiert nur, solange
niemand Terminal je selbst ein- oder ausgeschaltet hat: Wer es bewusst ausschaltet, bei dem bleibt
es aus. Bereits bestehende Installationen sind davon nicht betroffen.

Im Einrichtungsassistenten (Schritt 3, siehe [2.](#2-einrichtungsassistent-setup)) schaltest du die Module,
die du nutzen willst, per Klick ein. Später geht das jederzeit unter Einstellungen →
Erweiterungen, mit dem Schalter rechts, „Konfigurieren“ öffnet die
Einstellungen der Erweiterung („Einrichtung nötig“ heißt: es fehlen noch Angaben oder der
letzte Verbindungstest ist fehlgeschlagen). Öffnest du die Seite eines ausgeschalteten Moduls (etwa über ein
Lesezeichen), sagt sie das, und **„Einschalten“** schaltet es dort gleich ein (ohne das Recht dazu steht da, dass ein
Administrator es einschalten kann). Ein guter Anfang für fast jedes Heimnetz: Terminal, Server-Zustand,
Nodvard Shield und Push-Nachrichten, dazu das Modul Docker, wenn auf einem Server Docker läuft, und
Gameserver, wenn dort ein Spieleserver steht. **Proxmox VE und Proxmox-Backups brauchst du nur, wenn du Proxmox
nutzt** – sonst lass sie aus (siehe [4.1](#41-ohne-proxmox)). Der Rest nach Bedarf.

**Serverrechte nachziehen und Prüfsummen setzen:** Das Einschalten allein ergänzt keine Rechte auf dem Server.
Schaltest du Docker oder Nodvard Shield erst nach der SSH-Einrichtung ein, können Docker-Gruppe und Hilfsskripte fehlen.
Zudem führt sudo die Hilfsskripte ab Version 0.13.0 nur noch mit geprüfter Prüfsumme aus. Wiederhole die
Server-Einrichtung nach der Aktualisierung auf 0.13.0 einmal auf jedem Server: Öffne unter
**Einstellungen → Server & Zugänge** den vorhandenen Zugang und führe für jeden betroffenen Server mit bestehendem Schlüsselzugang
den neu angezeigten Einrichtungsbefehl erneut aus ([3.3](#33-einrichtungsbefehl-auf-dem-server-ausführen)). Bis zur
Wiederholung laufen die Server wie bisher. Prüfe danach die Verbindung erneut ([3.4](#34-verbindung-prüfen)).

**„Beta“:** Gameserver, Home Assistant, Jellyfin, Netzwerk, Nginx Proxy Manager, Pi-hole, Plex, qBittorrent, Synology DSM,
Uptime Kuma und Vaultwarden tragen das Kennzeichen „Beta“ (in den Einstellungen, im Assistenten, im Menü und über der Seite
des Moduls). Sie funktionieren, sind aber noch nicht so gründlich erprobt wie der Rest. Rückmeldungen helfen.
Netzwerk kommt nur in eigenen Builds als optionaler Wegweiser zu Pi-hole und Nginx Proxy Manager mit.

**Synology DSM** (Beta, nur lesend) zeigt Zustand, Speicher, Festplatten und Hyper Backup einer oder mehrerer Synology.
Lege dafür in DSM ein eigenes Konto nur für Nodvard Deck an (für die meisten Angaben in der Gruppe „administrators“) und
trag jedes Gerät unter Einstellungen → Erweiterungen → Synology DSM mit Kurzname, Adresse und Konto ein; das Passwort kommt
in den Tresor. Alles Weitere (Zwei-Faktor, Zertifikat, mehrere Geräte, Meldungen, Hyper Backup):
[Synology DSM](15-SYNOLOGY-DSM.md).

**Zugangsdaten gehören zur Adresse.** In den Einstellungen einer Erweiterung erst die Adresse eintragen
und „Speichern“ drücken, dann in der Karte „Verbindung“ Token oder Passwort hinterlegen – vorher nimmt
Nodvard Deck sie nicht an. Änderst du später die Adresse (etwa den ntfy-Server oder eine Proxmox-Adresse),
löscht Nodvard Deck beim Speichern die Zugangsdaten dazu, und du trägst sie neu ein; die Seite weist
vorher darauf hin. Eine andere Schreibweise derselben Adresse (Groß-/Kleinschreibung im Rechnernamen,
Standardport, `/` am Ende, fehlendes `http://`) zählt nicht als Änderung.

Mehr zu einzelnen Modulen (Docker, Nodvard Shield, Apps, Dateien, Adress-Check im Nginx Proxy Manager, Widgets) steht in
[Anhang G](#g-module-im-detail).

### 4.1 Ohne Proxmox

Nodvard Deck braucht kein Proxmox. Ein Raspberry Pi, eine Debian-VM, ein NAS oder ein paar Docker-Hosts
reichen. Was dann anders ist:

- **Server legst du von Hand an** ([3.1](#31-server-anlegen)) und gibst jedem einen SSH-Zugang
  ([3.2](#32-ssh-zugang-einrichten--drei-wege)). Die Module Proxmox VE und Proxmox-Backups bleiben aus; ihre Einträge
  erscheinen dann weder im Menü noch im Cockpit.
- **Online oder offline:** Nodvard Deck prüft von Hand angelegte Server von selbst alle 2 Minuten. Fällt einer aus oder
  kommt wieder, gibt es eine Meldung, auf Wunsch auch als Push.
- **Auslastung und Verlauf** (CPU, Arbeitsspeicher, Platte, Netz, Temperatur) kommen vom Modul **Server-Zustand** per
  SSH, für Linux-Server mit SSH-Zugang.
- **Docker-Container** startest, stoppst und aktualisierst du mit dem Modul **Docker**, für Server mit der Markierung
  `docker`.
- **Updates, Virenschutz, Einbruchschutz:** Nodvard Shield, für alle Linux-Server mit SSH-Zugang.
- **Eigene Kacheln** für Router, NAS-Oberfläche oder Pi-hole legst du im Cockpit im Bereich „Apps“ mit
  **+ App hinzufügen** an.
- **Konsole im Browser** (Bildschirm eines Gasts) gibt es nur für Proxmox-Gäste. Für alle anderen Server nimmst du das
  **Terminal**.

Alles im Detail: [Anhang G.1](#g1-ohne-proxmox-im-detail).

### 4.2 Proxmox verbinden

<a id="7-proxmox-verbinden"></a>

**Nur wenn du Proxmox nutzt – sonst überspringst du diesen Abschnitt** (siehe [4.1](#41-ohne-proxmox)).

1. Erweiterung **„Proxmox VE“** einschalten.
2. Auf der Seite **„Proxmox“** auf **„Mit Passwort verbinden“** klicken. Dafür brauchst du in Nodvard Deck Admin-Rechte.
3. Adresse (`https://…:8006`) und Passwort eines Proxmox-Administrators eingeben (Vorgabe `root@pam`). Hat Proxmox ein
   selbst ausgestelltes Zertifikat, vergleichst du vorher dessen Fingerabdruck und bestätigst ihn.

Nodvard Deck legt Benutzer, Rolle und Token in Proxmox selbst an, liest gleich danach Knoten, VMs und Container ein und
vergisst das Passwort sofort. Den Fingerabdruck merkt es sich und prüft ihn danach bei jeder Anfrage. Ist
„Proxmox-Backups“ eingeschaltet, bekommt es dabei gleich einen eigenen Zugang (Schalter „Auch Proxmox-Backups
verbinden“). Je Proxmox-Server einmal.

Schritt für Schritt, mit Zwei-Faktor oder lieber mit einem eigenen Token mit genau festgelegten Rechten:
**[docs/10-PROXMOX-TOKEN.md](10-PROXMOX-TOKEN.md)**. Der Weg von Hand in Kürze und was die Module von selbst melden:
[Anhang G.2](#g2-proxmox-im-detail).

## 5. Push-Nachrichten (ntfy)

Unter **Einstellungen → Benachrichtigungen** findest du alle Meldungsarten als Tabelle. Dort kannst du jede Art
ein- oder ausschalten und ihre ntfy-Priorität auf `min`, `low`, `default`, `high` oder `urgent` setzen.
**„Wie bisher“** übernimmt die bisherige Priorität nach Schweregrad. Standardmäßig sind alle Arten eingeschaltet.
Nach einer Änderung **„Speichern“** drücken. Ausgeschaltete Push-Meldungen bleiben unter „Meldungen“ sichtbar;
die Testnachricht wird weiterhin gesendet. Mindest-Stufen und Wartungsfenster gelten weiterhin.

<a id="8-push-nachrichten-ntfy"></a>

Einstellungen → Erweiterungen → **„Push-Nachrichten“** (früher „ntfy-Benachrichtigungen“) einschalten →
„Konfigurieren“. Für ntfy:

- **ntfy-Server:** z. B. `https://ntfy.sh` oder dein eigener. Nodvard Deck verbindet sich immer direkt damit, nie über
  einen Proxy aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`). Kommt der Rechner mit Nodvard Deck nur über einen Proxy ins
  Internet, ist `ntfy.sh` nicht erreichbar; nimm dann einen ntfy-Server im eigenen Netz.
- **Thema (Topic):** der Kanal, den du in der ntfy-App abonnierst. Bei ntfy.sh schwer
  erratbar wählen, denn dort kann jeder ein Thema mitlesen, der den Namen kennt. Der Knopf
  **„Zufällig wählen“** neben dem Feld trägt einen zufälligen Namen ein.
- **Adresse des Dashboards (optional):** `http://192.168.1.10:8080`. Dann öffnet ein Tipp
  auf die Nachricht direkt die passende Seite. Muss mit `http://` oder `https://`
  anfangen, sonst wird sie ignoriert.
- **Mindest-Stufe für Push-Nachrichten:** „Alles (Info, Warnung, Kritisch)“ ist der Standard. Mit
  „Ab Warnung“ oder „Nur Kritisch“ kommen kleinere Meldungen nicht mehr aufs Handy; sie stehen
  weiter unter „Meldungen“. Die Testnachricht kommt immer an.
- **Lautstärke bei Info, Warnung und Kritisch:** wie auffällig eine Meldung der jeweiligen Stufe
  auf dem Handy ankommt, von „1 – ohne Ton“ bis „5 – sehr laut“. Ob Stufe 5 auch bei „Nicht
  stören“ durchkommt, entscheidet dein Handy.
- „Speichern“.
- **Zugangsdaten → „Zugriffstoken (optional)“:** nur, wenn dein ntfy-Server eine Anmeldung
  verlangt.

In der ntfy-App auf dem Handy denselben Server und dasselbe Thema abonnieren. Das Abtippen
sparst du dir: Nach dem Speichern zeigt die Seite unter den Einstellungen einen **QR-Code**. Halte
die Kamera deines Handys darauf, dann öffnet sich die ntfy-App und bietet an, das Thema zu
abonnieren. Der Code enthält nur Server und Thema, nie das Zugriffstoken.

**Testen:** Seite „Nodvard Shield“ → „Briefing senden“. Das Erstellen kann bis zu einer halben
Minute dauern, wenn ein Server nicht antwortet. Danach sagt dir die Seite, ob der Lagebericht
(„Lagebericht – …“) auch als Push-Nachricht zugestellt wurde. Wenn nicht, ist ntfy noch nicht
eingerichtet oder der ntfy-Server nicht erreichbar. Alle Nachrichten stehen außerdem im
Dashboard unter „Meldungen“ – auch dann, wenn die Push-Nachricht nicht ankommt. Dort kannst du eine Meldung mit dem
Papierkorb löschen oder mit „Alle gelesenen löschen“ aufräumen. Von selbst löscht Nodvard Deck jede Nacht Meldungen, die
älter als 90 Tage sind (ungelesene kritische Meldungen bleiben stehen, bis du sie liest); die Zahl stellst du unter
Einstellungen → System → „Zeit & Protokoll“ um.

**Was im Lagebericht steht:** Server, Schutzwert, Bedrohungen, Härtung, Updates, Einbruchschutz und – falls vorhanden – wie
viele Vorschläge (aus allen Modulen) unter „Aktionen“ auf deine Bestätigung warten und welche Container in den letzten
24 Stunden abgestürzt sind. Eine wartende Freigabe oder ein noch offener Absturz macht ihn zu „es gibt etwas zu tun“. Er
kommt auch, wenn alles in Ordnung ist; bleibt er aus, ist Deck selbst vermutlich ausgefallen.

Hast du die Mindest-Stufe auf „Ab Warnung“ oder „Nur Kritisch“ gestellt, teste lieber mit „Testnachricht
senden“ (Karte „Verbindung“ unter den ntfy-Einstellungen): Ein Briefing ohne Probleme ist nur eine
Info-Meldung und kommt dann nicht aufs Handy.

### Weitere Kanäle (Telegram, Discord, Gotify, Pushover, Matrix, Slack, Mattermost)

Statt ntfy oder zusätzlich (bis zu 10 Kanäle): Unter **„Weitere Kanäle“** auf „Kanal hinzufügen“ klicken, einen
**Kurznamen** wählen (z. B. `telegram`), die **Mindest-Stufe** (Vorgabe „Ab Warnung“) und ob er **aktiv** ist →
„Speichern“. Danach unten unter **Zugangsdaten** beim Eintrag „Adresse des Kanals“ mit diesem Kurznamen die Adresse
eintragen. Sie enthält Zugangsdaten (Bot-Token, Webhook-Schlüssel) und liegt deshalb nur im Tresor. Benennst du einen
Kanal um, ist seine Adresse weg und muss neu eingetragen werden. Geschrieben wird sie wie bei Apprise:

| Dienst | Adresse |
|---|---|
| Telegram | `tgram://BOT-TOKEN/CHAT-ID` (mehrere Chats mit `/` getrennt; Kanäle als `@name`) |
| Discord | `discord://WEBHOOK-ID/WEBHOOK-TOKEN` oder die Webhook-Adresse, die Discord anzeigt |
| Gotify | `gotifys://SERVER/APP-TOKEN` (`gotify://` ohne HTTPS, Port mit `:`) |
| Pushover | `pover://USER-KEY@APP-TOKEN` (optional `/GERÄT`) |
| Matrix | `matrixs://ZUGRIFFSTOKEN@SERVER/!RAUM-ID:SERVER` oder `…/#raumname` (nur mit Zugriffstoken, nicht mit Passwort) |
| Slack | `slack://T…/B…/X…` oder die Webhook-Adresse von Slack |
| Mattermost | `mmosts://SERVER/WEBHOOK-ID` |
| alles andere | `apprises://SERVER/SCHLÜSSEL` – eine eigene [Apprise-API](https://github.com/caronc/apprise-api) (eigener Container), die dann an jeden von Apprise unterstützten Dienst weitergibt |

Nodvard Deck schickt die Nachrichten selbst und direkt an den Dienst (keine Zusatzsoftware, kein Proxy aus der
Umgebung, keine Umleitungen). Telegram, Discord, Slack und Pushover brauchen dafür direkten Zugang ins Internet, und der
Text jeder Meldung (z. B. Servernamen, Dateipfade, Adressen) geht an diesen Dienst. Sammel-Pings wie `@everyone`,
`@channel`, `@all` oder `@room` aus einer Meldung bleiben nur Text. Ist die „Adresse des Dashboards“ oben
eingetragen, steht unter jeder Nachricht ein Link dorthin.

- **„Testnachricht senden“** schickt an ntfy und alle aktiven Kanäle gleichzeitig und nennt jeden, bei dem es nicht
  geklappt hat.
- **„Verbindung testen“** fragt jeden Dienst, ohne etwas zu senden: Bei Telegram, Discord, Pushover, Matrix und einer
  Apprise-API prüft das Token bzw. den Schlüssel, bei Telegram auch, ob der Bot in jede Gruppe und jeden Kanal
  schreiben darf, bei Matrix, ob das Konto in jedem Raum ist. Slack, Gotify und Mattermost erlauben das nicht (ebenso
  private Telegram-Chats); dort steht ein Hinweis, und ein Fehler beim letzten Senden zählt mit.
- Den Kurznamen „ntfy“ gibt es nicht, er steht für den ntfy-Server oben.
- Fällt bei einer normalen Meldung ein Kanal (oder ein Chat eines Kanals) aus, bekommen die anderen sie trotzdem.

## 6. Das Dashboard selbst sichern

<a id="10-das-dashboard-selbst-sichern"></a>

Nodvard Deck sichert sich selbst: verschlüsselt, nach Zeitplan und auf Wunsch auf ein anderes Laufwerk oder ein NAS.
Richte das gleich am Anfang ein; ohne Passwort und Wiederherstellungsschlüssel lässt sich keine Sicherung mehr öffnen.

### 6.1 In der Oberfläche (empfohlen)

<a id="101-in-der-oberfläche-empfohlen"></a>

**Einstellungen → System → Sicherung.** Nur der Inhaber (Owner) kann dort etwas ändern.

1. **Sicherungspasswort festlegen** (mindestens 12 Zeichen, zweimal eingeben, mit dem
   Anmeldepasswort bestätigen). Nodvard Deck speichert das Passwort nicht, nur einen
   öffentlichen Schlüssel. Danach erscheint **einmal** der *Wiederherstellungsschlüssel*
   (`AGE-SECRET-KEY-1…`): kopieren oder als Datei speichern und gut aufbewahren.
   **Passwort und Schlüssel weg = Sicherungen wertlos.** Niemand kann sie dann noch öffnen.
2. **Automatisch sichern** einschalten, Uhrzeit wählen und festlegen, wie viele Sicherungen
   bleiben sollen (1 bis 60, ältere werden gelöscht).
3. **Ordner:** Standard ist `/app/data/backups` im Datenordner. Das schützt vor Fehlbedienung,
   aber nicht vor einer kaputten SD-Karte. Besser einen Ordner von einem anderen Laufwerk oder
   NAS einbinden: In der Compose-Datei (`compose.yml` aus [Abschnitt 1](#1-schnellstart),
   bei einer Installation aus dem Repository `deploy/docker-compose.yml`) steht unter `volumes:`
   schon die Zeile `# - ./sicherungen:/backups`. Das `#` davor entfernen (oder einen NAS-Pfad statt
   `./sicherungen` eintragen), im selben Ordner `mkdir sicherungen && sudo chown 1000:1000 sicherungen`
   ausführen (der Ordner muss dem Nutzer mit der Nummer 1000 gehören), mit `docker compose up -d`
   übernehmen und in der Oberfläche als Ordner `/backups` wählen. Für ein NAS oder NFS siehe
   [Sicherungen auf ein NAS oder NFS](#sicherungen-auf-ein-nas-oder-nfs).
4. **Prüfen** vergleicht eine Sicherung mit ihrer Prüfsumme (erkennt kaputte Dateien, ohne
   Passwort). **Herunterladen** holt eine Sicherung auf den PC, **Sicherung herunterladen**
   erstellt sofort eine frische – wahlweise mit dem Sicherungspasswort oder mit einem eigenen
   Einmal-Passwort nur für diese Datei.

#### Sicherungen auf ein NAS oder NFS

Als Ziel nimmt Nodvard Deck nur `/app/data/backups` im Datenordner oder den eingebundenen Ordner `/backups` samt
Unterordnern (etwa `/backups/deck`). Gemeint ist immer der Pfad **im Container**, nicht der auf dem Rechner oder dem NAS.
In Kürze: den Ordner vom NAS auf dem Rechner einhängen (bei NFS so, dass er vor Docker bereitsteht), in der
Compose-Datei als `/backups` einbinden, dem Nutzer 1000 Schreibrechte geben, testen und in der Oberfläche `/backups`
wählen. Schritt für Schritt, mit der Zeile für `/etc/fstab`, den Rechten und den Fehlermeldungen beim Speichern:
[Sicherung im Detail](16-SICHERUNG.md#sicherungen-auf-ein-nas-oder-nfs).

### 6.2 Wiederherstellen (Sicherung einspielen)

<a id="102-wiederherstellen-sicherung-einspielen"></a>

Zwei Wege, derselbe Ablauf: **Einstellungen → System → Wiederherstellen** (nur der Inhaber, mit
Anmeldepasswort) oder, auf einer **frischen Installation**, im Einrichtungsassistenten unter
**„Sicherung einspielen“** (dort statt eines Kontos mit dem **Einrichtungscode** aus dem
Protokoll des Containers – wie beim Anlegen des ersten Kontos).

1. **Datei wählen** (`.ndbak`) und hochladen. Es gibt eine Fortschrittsanzeige; die Datei geht als Strom
   auf die Platte, nicht in den Speicher. Obergrenze 4 GiB (`NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`).
2. **Passwort oder Wiederherstellungsschlüssel** eingeben (bei einer Datei „mit eigenem Einmal-Passwort“ nur
   das Einmal-Passwort). Nodvard Deck entschlüsselt und prüft alles in einem getrennten Zwischenordner;
   die Datenbank der Sicherung wird dabei nur gelesen und nie ausgeführt.
3. **Zusammenfassung ansehen:** erstellt am, Version, Kennung der Installation, Name des Inhabers, Zahl der
   Nutzer und Server, Erweiterungen, Warnungen. **age prüft nicht, wer eine Sicherung erstellt hat** – wer das
   Passwort kennt, kann eine bauen. Spiele nur Sicherungen ein, die du selbst erstellt hast.
4. **„Einspielen und neu starten“.** Das ersetzt **alles, auch die Konten**; danach melden sich alle neu an (mit
   den Konten aus der Sicherung). Nodvard Deck beendet sich und startet neu; beim Start wird eingespielt, danach
   migriert es wie immer. Die Seite wartet und führt dann zur Anmeldung.

Gut zu wissen:

- **Neustart-Regel nötig.** Der Neustart funktioniert nur, wenn der Container eine Neustart-Regel hat
  (`restart: unless-stopped`; die mitgelieferten Compose-Dateien haben sie). Mit `docker run` ohne `--restart`
  bleibt er danach aus: von Hand starten (`docker start …`), die Sicherung wird beim Start eingespielt.
- **Der alte Stand bleibt liegen** unter `restore/replaced-<Zeit>` im Datenordner (mit alten Konten und
  Schlüsseln), genau einer, und wird **nach 30 Tagen automatisch gelöscht** (die Karte nennt den Tag). Unter *Wiederherstellen* lässt er sich früher
  mit dem Anmeldepasswort löschen.
- **Läuft die alte Installation noch**, etwa auf einem anderen Gerät, haben beide dieselben Schlüssel und
  Zugangsdaten. Danach nur eine betreiben.
- **Scheitert etwas** (kaputte oder zu neue Sicherung, Fehler beim Verschieben, Migration schlägt fehl), kommt der
  alte Stand zurück und die Karte nennt den Grund. Ein Absturz mitten im Einspielen wird beim nächsten Start
  zurückgenommen. Eine Sicherung aus einer **neueren** Version oder mit einer Erweiterung, die hier fehlt, wird
  mit Hinweis abgelehnt. Eine Vormerkung gilt eine Stunde, ein aufgegebener Zwischenstand wird nach einer Stunde
  gelöscht.
- Was nicht in der Sicherung steckt (Messwerte, Git-Einstellungen der Erweiterungen, ein Anmelde-Geheimnis aus der
  Umgebung): [Sicherung im Detail](16-SICHERUNG.md#was-nicht-in-der-sicherung-steckt).

**Notfall ohne Oberfläche:** im Ordner mit der Compose-Datei

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin restore-backup /app/data/backups/nodvard-deck-sicherung-….ndbak
sudo docker compose restart nodvard-deck      # eingespielt wird beim Start
```

(Die Datei muss im Container sichtbar sein; der Befehl fragt das Passwort ab und zeigt die Zusammenfassung. `compose exec` läuft im
bestehenden Container; ein `compose run` daneben würde von der Sperre des Datenordners abgewiesen, siehe [Anhang I.3](#i3-die-kopie-vor-der-migration).)
Bei einer Installation aus dem Repository geht dasselbe kürzer mit `deploy/restore.sh sicherung.ndbak`
([Sicherung mit dem Skript](16-SICHERUNG.md#mit-dem-skript-nur-installation-aus-dem-repository)).
Geht gar nichts mehr, liegt der alte Stand noch unter `restore/replaced-…`:
[Sicherung im Detail](16-SICHERUNG.md#wenn-gar-nichts-mehr-geht).
Eine `.ndbak`-Datei lässt sich im Notfall auch ganz ohne Nodvard Deck öffnen:
[Sicherung im Detail](16-SICHERUNG.md#notfall-eine-sicherung-ohne-nodvard-deck-öffnen).

## 7. Typische Stolpersteine

Die häufigsten Hürden beim Start, jeweils mit dem nächsten Schritt. Alle weiteren Fehlermeldungen (Proxmox, Module,
Anmeldung) stehen in [Anhang J](#j-wenn-etwas-hakt).

### 7.1 Das Dashboard ist nicht erreichbar

- **Gleich nach dem Start:** Der erste Start dauert bis zu fünf Minuten (Image laden, Datenbank anlegen). So lange zeigen
  Oberflächen wie Portainer oder der Container-Bereich eines NAS den Container als „starting“ oder noch nicht als
  „healthy“.
- **„port is already allocated“ oder „address already in use“:** Port 8080 ist schon belegt. Nimm einen anderen
  ([Abschnitt 1](#1-schnellstart); bei Oberflächen und NAS steht es beim jeweiligen Weg in
  [Installation je Plattform](13-INSTALLATION.md)). Auf einem QNAP nutzt die Oberfläche des NAS selbst Port 8080.
- **„unhealthy“ oder der Container startet nicht:** Den Grund nennt das Protokoll, im Ordner mit der `compose.yml`:
  `sudo docker compose logs --since 15m nodvard-deck` (bei Oberflächen beim Container unter „Logs“). Zeigt der Browser
  statt des Dashboards eine **Notseite**, hilft [Anhang K](#k-nodvard-deck-startet-nicht-die-notseite).
- **Läuft es überhaupt?** Auf dem Rechner mit Nodvard Deck zeigt `curl -s http://localhost:8080/api/v1/health`
  `"status":"ok"`, und `sudo docker compose ps` nennt den Zustand des Containers. Geht das dort, aber nicht von einem
  anderen Gerät aus, stimmen meist Adresse oder Port nicht.
- **„Server nicht erreichbar – bitte gleich noch einmal versuchen.“ bei der Anmeldung:** Das Dashboard antwortet nicht;
  dieselben Prüfungen wie eben. Steht stattdessen **„Server meldet: …“** da (auch beim Neuladen der Seite), antwortet das
  Dashboard und nennt den Grund, z. B. dass die Platte voll ist – dann diesen Grund beheben. Abgemeldet wird man dabei
  nicht.
- **Der Einrichtungscode wird nicht angenommen:** Er steht im Protokoll des Containers ([2.1](#21-den-einrichtungscode-finden)).
  Falsch eingegebene Codes werden gebremst: Nach 10 Fehlversuchen gibt es einige Minuten Pause.
- **„Die Seite konnte nicht geladen werden“** kommt meist direkt nach einem Update: Seite neu laden (F5).

### 7.2 Der Server lässt sich nicht verbinden

Was genau hakt, zeigt **„Verbindung prüfen“** auf der Seite des Servers Punkt für Punkt ([3.4](#34-verbindung-prüfen)).
Die häufigsten Meldungen:

**„Der Server hat den Schlüssel nicht akzeptiert.“:** Meist fehlt auf dem Server noch der Einrichtungsbefehl
([3.3](#33-einrichtungsbefehl-auf-dem-server-ausführen)); ihn auszuführen ist gefahrlos, auch mehrmals. Verlangt der Server
Zwei-Faktor-Authentifizierung (2FA): [Server mit Zwei-Faktor-Authentifizierung anbinden](SSH-MIT-2FA.md). Läuft dort
Dropbear und wurde der Server mit einer älteren Version eingerichtet: [Anhang E.4](#e4-was-der-einrichtungsbefehl-genau-tut).

**Server fehlt im Terminal / „Noch kein Server prüfbar“ oder „Keine Linux-Server mit SSH-Zugang gefunden“ bei Nodvard Shield:** Kein
SSH-Zugang hinterlegt: Einstellungen → Server & Zugänge ([3.2](#32-ssh-zugang-einrichten--drei-wege)). Nodvard Shield nimmt außerdem nur Server, die als `linux`
eingetragen sind, und – wenn in seinen Einstellungen „Nur Server mit Markierung“ ausgefüllt ist – nur Server mit dieser Markierung.

**„Keine root-Rechte …“:** sudo ohne Passwort fehlt ([Anhang F](#f-root-rechte-ohne-passwort-und-gruppe-docker)). Die Karte „Verbindung prüfen“ zeigt es unter „Root-Rechte“.

**„Der Server-Schlüssel von diesem Server ist noch nicht bestätigt.“:** Nodvard Deck kennt den
Fingerabdruck dieses Servers noch nicht (neuer Server, und „Neue Server-Schlüssel erst nach meiner
Bestätigung merken“ ist an) oder du hast ihn vergessen. Unter Server & Zugänge beim Server „Verbindung
prüfen“ drücken und den Fingerabdruck bestätigen ([3.4](#34-verbindung-prüfen)).

**„Host-Schlüssel … hat sich geändert“:** Server neu installiert? Dann unter Server & Zugänge
beim Server in der Karte „Server-Schlüssel“ den alten vergessen, erneut prüfen und den neuen
Fingerabdruck bestätigen. Wenn nicht: erst herausfinden, warum – das kann auch ein Angriff sein.

**„Server antwortet nicht (Zeitüberschreitung …)“, „Der Server lehnt die Verbindung ab …“, „Kein Weg zum Server …“
oder „Den Namen … kennt das Netz nicht.“** im Terminal oder Dateimanager (dort mit „Zugriff auf die Quelle
fehlgeschlagen:“ davor): Nodvard Deck erreicht den Server nicht. Der Satz nennt meist Adresse und Port. Prüfen, ob der
Server an ist, die Adresse unter Server & Zugänge stimmt und dort SSH läuft. Im Log steht dazu kein Traceback.

### 7.3 Ausgesperrt? Notfall-Befehle

<a id="12-ausgesperrt-notfall-befehle"></a>

Wenn jemand – vor allem der Inhaber – Passwort oder Zwei-Faktor verloren hat und auch nicht
mehr über die Weboberfläche hineinkommt, gibt es Befehle **auf dem Rechner, auf dem Nodvard Deck
läuft** (z. B. dem Raspberry Pi), im Ordner mit der Compose-Datei. Wer sie ausführen kann, hat
ohnehin Zugriff auf alles im Datenverzeichnis; deshalb braucht es keine Anmeldung, aber jede
Änderung steht im Protokoll (Akteur `system/cli`).

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin list-users
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password admin
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa admin
```

(`admin` steht hier für den Benutzernamen, den `list-users` zeigt; ein älterer Name mit Leerzeichen gehört in
Anführungszeichen. Bei einer Installation aus dem Repository statt `cd ~/nodvard-deck` in den Ordner `deploy/` des
Repositorys wechseln.)

- **`list-users`** zeigt Benutzername, Inhaber/Benutzer, gesperrt oder aktiv und ob
  Zwei-Faktor an ist.
- **`reset-password <benutzername>`** setzt ein neues, zufälliges Passwort und **gibt es aus**
  (z. B. `k7mq-x2vd-h9pa-tn4c`). Alle bestehenden Anmeldungen dieses Benutzers werden beendet.
  Sonst ändert sich nichts (Zwei-Faktor bleibt). Nach der Anmeldung unter Mein Konto ein
  eigenes Passwort setzen.
- **`disable-2fa <benutzername>`** schaltet die Zwei-Faktor-Anmeldung ab, löscht die
  Wiederherstellungs-Codes und beendet alle Anmeldungen. Danach geht es nur mit Passwort,
  Zwei-Faktor lässt sich unter Mein Konto neu einrichten.

Bei Fehlern (Benutzer gibt es nicht, Datenbank nicht erreichbar) steht eine verständliche
Meldung da. Der Befehl ändert nur Benutzer – keine Server, keine Schlüssel, keine Einstellungen.

**Ohne Befehlszeile** (Portainer, Docker Desktop, NAS): die Konsole des Containers von Nodvard Deck öffnen und den
Befehl dort **ohne** `sudo docker compose exec nodvard-deck` davor eingeben, also etwa
`python -m nodvard_deck.admin reset-password admin`. Den Container erkennst du an „nodvard-deck“ im Namen, meist
heißt er `nodvard-deck-nodvard-deck-1`. Die Konsole findest du so:

- **Portainer:** Containers → Container von Nodvard Deck → Symbol „Exec Console“ → „Connect“.
- **Docker Desktop:** „Containers“ → die Gruppe „nodvard-deck“ aufklappen → den Container darin anklicken → Reiter „Exec“.
- **Synology Container Manager:** Container → den von Nodvard Deck auswählen → „Details“ → Reiter „Terminal“ → „Erstellen“.
- **Unraid:** Reiter „Docker“ → Symbol des Containers von Nodvard Deck → „Console“.

Dieselbe Anleitung steht auf der Anmeldeseite unter „Passwort vergessen?“.

**Gut zu wissen:** Wenn Nodvard Deck jemanden „überall abmeldet“ (Passwort geändert, Zwei-Faktor
zurückgesetzt oder abgeschaltet, Notfall-Befehl) oder du dich abmeldest, endet die Sitzung sofort:
Das Anmelde-Cookie im Browser wird ungültig und die nächste Anfrage damit wird abgelehnt. Offene
Terminal- und Konsolen-Sitzungen einer beendeten Anmeldung schließen sich nach spätestens 15 Sekunden,
die Live-Aktualisierung nach spätestens 30 Sekunden.

## Anhang

Zum Nachschlagen: Technik, Sonderfälle und Einzelheiten, die du für den Start nicht brauchst.

| | Abschnitt | Worum es geht |
|---|---|---|
| A | [Installation im Detail](#a-installation-im-detail) | Daten als eigener Ordner, aus dem Repository bauen |
| B | [Einrichtungsassistent im Detail](#b-einrichtungsassistent-im-detail) | Einrichtungscode, jeder Schritt, eigenes CSS |
| C | [Zwei-Faktor und Wiederherstellungs-Codes](#c-zwei-faktor-und-wiederherstellungs-codes) | benutzen, abschalten, Sperre nach Fehlversuchen |
| D | [Weitere Benutzer](#d-weitere-benutzer) | Rollen, Zwei-Faktor zurücksetzen, Konten sperren |
| E | [Server und Zugänge im Detail](#e-server-und-zugänge-im-detail) | kleine SSH-Server, Beispieldaten, Server-Schlüssel, Zugang ersetzen |
| F | [Root-Rechte ohne Passwort und Gruppe docker](#f-root-rechte-ohne-passwort-und-gruppe-docker) | was die Haken erlauben |
| G | [Module im Detail](#g-module-im-detail) | ohne Proxmox, Proxmox von Hand, Adress-Check, Widgets |
| H | [Automatik und Wartungsfenster](#h-automatik-und-wartungsfenster) | Freigaben, Skripte ohne Klick, automatische Updates |
| I | [Updates](#i-updates) | neue Version, Update per Knopf, Kopie vor der Migration, Meldungen der Update-Zentrale |
| J | [Wenn etwas hakt](#j-wenn-etwas-hakt) | Fehlermeldungen von Proxmox, Modulen und Anmeldung |
| K | [Nodvard Deck startet nicht: die Notseite](#k-nodvard-deck-startet-nicht-die-notseite) | Notfallcode, Stand vor dem Update |
| L | [Ohne Oberfläche](#l-ohne-oberfläche) | Server über die API einrichten |
| M | [Alte Namen bei älteren Installationen](#m-alte-namen-bei-älteren-installationen) | warum hier und da noch „lattice“ steht |

## A. Installation im Detail

Alle Wege je Plattform (Terminal, Web-Oberfläche, NAS, Raspberry Pi, Proxmox, Docker Desktop) und die kurze
Compose-Fassung für Oberflächen: [Installation je Plattform](13-INSTALLATION.md).

### A.1 Daten: Speicher oder eigener Ordner

Standard ist der Docker-Speicher (Volume) `nodvard-deck-data`. Ein frisches Volume erhält die passenden Rechte aus
dem Image. Mit der Compose-Datei unten (`user: "1000:1000"`) startet Deck als `1000:1000`, auch Migration, Notseite und Healthcheck laufen ohne root. Ohne `user:` startet das Image als root und gibt nach dem Setzen der Besitzrechte an `1000:1000` ab. Ein eigener
Ordner (`./daten:/app/data`) muss vorher diesem Nutzer gehören: `mkdir -p daten && sudo chown 1000:1000 daten`.
Auf NAS-Freigaben müssen die Rechte für diese UID/GID am NAS gesetzt werden, wenn dort `chown` nicht erlaubt ist.
Die Datenbank wird beim Containerstart automatisch angelegt bzw. aktualisiert.

Die Compose-Vorgaben, hier als vollständiges Beispiel:

```yaml
name: nodvard-deck
services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    user: "1000:1000"
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,nodev,mode=1777
    security_opt:
      - "no-new-privileges:true"
    cap_drop:
      - ALL
    ports:
      - "8080:8080"
    volumes:
      - nodvard_deck_data:/app/data
    healthcheck:
      test: ["CMD", "python", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=2)"]
      interval: 30s
      timeout: 3s
      start_period: 300s
      retries: 3
volumes:
  nodvard_deck_data:
    name: nodvard-deck-data
```

Schreibbar bleiben der Datenordner, `/tmp` und ausdrücklich eingebundene Ziele wie `/backups` oder der Kanal
`/app/updater`. Zusätzliche Capabilities braucht Deck nicht: Uvicorn lauscht auf Port 8080 (`deploy/Dockerfile`),
Serverzugriffe laufen über SSH (`backend/src/nodvard_deck/core/ssh.py`), OCR als normaler Prozess. Es gibt keine
Spracheingabe im Frontend; Kamera, Mikrofon und Standort bleiben im Browser gesperrt. HSTS wird nur bei erkanntem
HTTPS gesetzt. Proxy-Angaben zählen nur von einem eingetragenen vertrauenswürdigen Proxy. Über HTTP bleibt die
Warnung vor unverschlüsseltem Zugriff bestehen. Die Notseite sendet HSTS immer; Browser beachten es nur über HTTPS.
CORP `same-origin` gilt für API-Antworten. Ausgenommen ist `/api/v1/health` mit CORP `cross-origin`, damit Startseiten
wie Homarr oder Homepage den Zustand von Deck direkt aus dem Browser abfragen können.
Öffentliche Logos, Favicons und App-Symbole erhalten bei GET/HEAD CORP `cross-origin` und können auf fremden Webseiten
eingebunden werden. Das betrifft `/api/v1/branding/logo`, `/api/v1/branding/favicon` und die App-Symbole unter
`/api/v1/app/icon-*.png` (180, 192 und 512 Pixel, auch die Varianten mit `-maskable`). API-Antworten erhalten weiterhin
`no-store`, die vorhandenen eigenen Cache-Regeln für Symbole, Bundles und andere statische Ressourcen bleiben erhalten.

**Bestehende Installation umstellen:** Erst eine Sicherung erstellen. Den bisherigen Volume-Namen und alle
Bind-Mounts behalten: Im Repository heißt das Volume `deploy_lattice_data`, in der Schnellinstallation
`nodvard-deck-data`. Alte Starts haben die Daten bereits UID/GID 1000 zugewiesen; dann ist kein Besitzwechsel nötig.
Vor dem Neustart die Sicherheitsvorgaben oben in die vorhandene Compose-Datei übernehmen und alte `user: root`-Angaben
entfernen. Bei abweichenden Besitzrechten nach dem Laden oder Bauen des neuen Images im Ordner dieser Compose-Datei:

```bash
docker compose stop nodvard-deck
docker compose run --rm --no-deps --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --entrypoint /usr/local/bin/migrate-data nodvard-deck
docker compose up -d nodvard-deck
```

Nur dieser einmalige Aufruf läuft als root. `CHOWN` erlaubt den Besitzerwechsel, `DAC_OVERRIDE` das Durchlaufen
alter geschützter Ordner. Das Skript ändert ausschließlich Besitzrechte unter `/app/data`, folgt keinen Links und
bleibt auf dessen Dateisystem. Währenddessen darf kein anderer Container diesen Datenordner benutzen.
Die normalen Starts brauchen keine dieser Capabilities. Ein zusätzlicher Sicherungsordner unter `/backups`
braucht ebenfalls Schreibrechte für UID 1000.

Der Update-Helfer übernimmt den Nutzer des neuen Images, auch bei alten gleichwertigen root-Angaben. Andere eigene
Nutzer bleiben erhalten. Er ändert keine Dateirechte und schreibt die Compose-Datei nicht um; abweichende
Besitzrechte deshalb vor dem Update wie oben korrigieren und den Helfer vorher aktualisieren. Die Einstellungen
`read_only`, `tmpfs`, `security_opt` und `cap_drop` übernimmt er vom laufenden Container. Sie werden bei bestehenden
Installationen durch Anpassen der Compose-Datei und `docker compose up -d` wirksam. Scheitert der neue Start,
greift die vorhandene Rückkehr zum alten Container.

### A.2 Aus dem Repository bauen (für Entwickler)

Wer das Repository klont und das Image selbst baut, startet im Ordner `deploy/` des Repositorys
(`docker compose build --pull && docker compose up -d`; `--pull` holt frische Basis-Images). Bauen, auf einen Raspberry Pi ausliefern
(`scripts/deploy_pi.sh`), Rückfallweg und Rollback stehen in [deploy/README.md](../deploy/README.md). Beim
Ausliefern benennt `deploy_pi.sh` alte `LATTICE_*`-Variablen in `deploy/.env` auf dem Zielrechner automatisch
in `NODVARD_DECK_*` um und behält eine Sicherungskopie. Ausnahme: Stehen in `deploy/.env.deploy` DNS-Einstellungen
(`NODVARD_DECK_DNS_1/2`), schreibt `deploy_pi.sh` die `.env` auf dem Zielrechner komplett neu; eigene Zeilen dort gehen
verloren. Wer von Hand ausliefert, nimmt diese Umbenennung in der `.env`-Datei selbst vor. Liegt auf dem Zielrechner noch eine Version vor 0.6, bricht der Vorgang mit einem Hinweis ab.
Die Kontrolle danach ist dieselbe wie oben (`curl -s http://localhost:8080/api/v1/health`, Antwort `{"status":"ok"}`).

Bei dieser Installation laufen die Befehle aus dieser Anleitung im Ordner `deploy/` des
Repositorys (z. B. `cd ~/deck/deploy`) statt in `~/nodvard-deck`. Abschnitte, die nur für diesen
Weg gelten, sind entsprechend markiert.

## B. Einrichtungsassistent im Detail

### B.1 Der Einrichtungscode

- Groß-/Kleinschreibung, Striche und Leerzeichen sind egal.
- Der zufällig erzeugte Code bleibt über Neustarts gleich (er liegt als `setup_code.txt` im Datenverzeichnis,
  nur für Nodvard Deck lesbar) und wird nach dem Anlegen des Kontos gelöscht. Ein vorgegebener Code wird nicht in
  diese Datei geschrieben; dann gilt bis zur Einrichtung der Wert von `NODVARD_DECK_SETUP_CODE`.
- Falsch eingegebene Codes werden wie falsche Passwörter gebremst (nach 10 Fehlversuchen
  einige Minuten Pause) und stehen im Protokoll als „Einrichtungscode falsch“.
- Sobald ein Konto existiert, bleibt `NODVARD_DECK_SETUP_CODE` wirkungslos. Beim Start weist das Protokoll darauf
  hin, ohne den Wert auszugeben.

### B.2 Die Schritte im Einzelnen

Der Assistent hat **sieben Schritte** und zeigt oben „Schritt X von 7“. Nur der erste ist Pflicht, alles
andere lässt sich überspringen und später in den Einstellungen nachholen. „Zurück“ gibt es ab Schritt 3.

**Schritt 1 von 7 — Administrator-Konto anlegen**

- **Einrichtungscode:** siehe [2.1](#21-den-einrichtungscode-finden).
- **Benutzername:** mindestens 3 Zeichen, nur Kleinbuchstaben, Ziffern sowie `.`, `-` und `_`, ohne
  Leerzeichen, vorne ein Buchstabe oder eine Ziffer (z. B. `admin`). Großbuchstaben werden automatisch
  umgewandelt.
- **Passwort** und **Passwort bestätigen:** mindestens 8 Zeichen.
- „Konto anlegen“. Passt eine Angabe nicht, steht gleich da, was (z. B. „Benutzername: Mindestens 3 Zeichen.“).

Dieses erste Konto ist der **Inhaber**. Der Inhaber darf immer alles, auch wenn später
Rollen verstellt werden – so kann man sich nicht aussperren.

**Schritt 2 von 7 — Zeitzone**

Zeitpläne (zum Beispiel das Morgen-Briefing um 7 Uhr) und Wartungsfenster richten sich nach dieser
Zeitzone. Vorausgewählt ist die Zeitzone des Geräts, mit dem du im Browser sitzt; kennt der Browser
sie nicht, bleibt die bisherige Einstellung stehen. Mit der Suche („Berlin“, „New York“, auch deutsche Namen wie
„Wien“ oder „Mitteleuropa“) oder der Liste
wählst du eine andere. „Weiter“ speichert, „Überspringen“ lässt die Einstellung, wie sie ist. Ändern
geht später unter Einstellungen → System. Ohne Suche steht die gewählte Zone oben, darunter die gängigen
(Berlin, Wien, Zürich …); bekannte Zonen zeigen daneben den deutschen Namen (etwa „Österreich, Wien“ bei
`Europe/Vienna`).

**Schritt 3 von 7 — Was willst du nutzen?**

Alle mitgelieferten Module als Kacheln mit Schalter, nach Themen sortiert: zuerst „Für deine Server“
(Server-Zustand, Terminal, Docker, Proxmox VE …), dann Sicherheit, Werkzeuge, Verbindungen zu anderen
Diensten. Ein Klick auf die Kachel oder den Schalter schaltet das Modul ein oder wieder aus. Kein Modul
ist Pflicht („Überspringen“). Module, die danach noch Angaben brauchen (Adresse, Zugangsdaten,
Token), tragen den Hinweis „Braucht danach noch Angaben“; diese Angaben kommen **erst jetzt nach dem
Assistenten** unter Einstellungen → Erweiterungen, und die Karte „Erste Schritte“ im Cockpit erinnert
daran (siehe [4.](#4-erweiterungen-einschalten)). Reihenfolge und Gruppe stammen aus dem Manifest des
Moduls (`category`, `sort_order`).

**Schritt 4 von 7 — Zwei-Faktor-Anmeldung (optional)**

„Jetzt einrichten“ fragt zur Sicherheit erst dein aktuelles Passwort ab („Weiter“) und zeigt dann einen
**QR-Code** für die Authenticator-App (er wird im Browser gezeichnet,
nichts geht an einen fremden Dienst) und zusätzlich den Schlüssel als Text, falls das Scannen nicht
klappt. Den sechsstelligen Code aus der App eingeben, „Bestätigen“. Danach erscheinen **einmalig die
Wiederherstellungs-Codes** („Rettungscodes“, siehe [Anhang C](#c-zwei-faktor-und-wiederherstellungs-codes)):
kopieren oder als Textdatei speichern, dann „Ich habe die Codes gesichert“. „Später einrichten“ steht bei der
Auswahl, bei der Passwortabfrage und neben „Bestätigen“, richtet nichts ein und geht zu Schritt 5; nachholen
unter Einstellungen → Mein Konto. „Zurück“ führt innerhalb des Schritts zur Auswahl.

**Schritt 5 von 7 — Aussehen (optional)**

Produktname, Untertitel (die kleine Zeile unter dem Namen; derselbe Text ist der Name der App auf dem
Handy-Startbildschirm, also kurz halten), Akzentfarbe → „Speichern“, oder „Überspringen“. Logo, Favicon und Theme
(Klassisch hell, Schlicht dunkel, Profi dunkel, Bisher) gibt es später unter Einstellungen → Darstellung. Logo und
Favicon werden dort **hochgeladen**; Bilder von fremden Adressen lädt Nodvard Deck nicht. Stand früher eine fremde
Adresse drin, zeigt die Karte „Marke“ einen Hinweis, bis du das Bild hochlädst oder entfernst.
Dort können Admins (Recht `branding.write`) auch **eigenes CSS** für alle hinterlegen, am einfachsten als
Variablen der Themes, z. B. `:root { --radius: 0px; }`. Erlaubt sind eingebettete Bilder (`data:`) und Pfade auf
Nodvard Deck selbst, kein `@import` und keine fremden Adressen. **Notausgang:** Macht das eigene CSS die Oberfläche
unbedienbar, hänge `?ohne-css=1` an die Adresse (z. B. `http://deck:8080/settings/appearance?ohne-css=1`). Dann lädt
der Tab ohne eigenes CSS, auch beim Weiterklicken; die Karte „Eigenes CSS“ zeigt das an und hat den Knopf „Wieder
einschalten“. Für alle anderen gilt das CSS weiter, bis du es korrigierst und speicherst.

**Schritt 6 von 7: Nutzungsdaten (optional)**

Drei unabhängige Schalter, standardmäßig aus. „Das wird gesendet“ zeigt das echte JSON.
„Weiter ohne Teilen“ geht ohne Zustimmung weiter. „Teilen“ speichert die ausdrückliche Auswahl.
Bis Empfänger und Datenschutzfreigabe konfiguriert sind, bleibt das Teilen gesperrt.
[Alle Angaben und der Löschknopf](18-NUTZUNGSDATEN.md).
Bei Nodvard Dock entfällt die Modul-Auswahl, der Assistent hat dort sechs Schritte.

**Schritt 7 von 7: Fertig**

Hinweis auf die Karte „Erste Schritte“ im Cockpit und der Notfall-Befehl (siehe
[7.3](#73-ausgesperrt-notfall-befehle)); derselbe Befehl steht auf der Anmeldeseite hinter „Passwort
vergessen?“. Wer die Zwei-Faktor-Anmeldung übersprungen hat, bekommt hier den Link zum Nachholen.
„Weiter zum Dashboard“ öffnet das Cockpit.

**Neu geladen mitten im Assistenten?** Das Konto gibt es dann schon, `/setup` führt deshalb eigentlich
zur Anmeldung. Der Assistent merkt sich den erreichten Schritt aber in diesem Browser-Tab: ist man noch
angemeldet, geht es genau dort weiter (Zeitzone, Module, Zwei-Faktor, Aussehen), sonst landet man auf
der Anmeldeseite, und danach übernimmt die Karte „Erste Schritte“. Die Wiederherstellungs-Codes lassen
sich nach einem Neuladen nicht noch einmal anzeigen – unter Mein Konto erzeugt man neue.

Danach ist `/setup` weg (führt nur noch zur Anmeldung).

## C. Zwei-Faktor und Wiederherstellungs-Codes

<a id="21-zwei-faktor-und-wiederherstellungs-codes"></a>

Im Assistenten (Schritt 4) oder später unter Einstellungen → Mein Konto → „Zwei-Faktor-Anmeldung“ →
„Einrichten“: zur Sicherheit das aktuelle Passwort eingeben („Weiter“), dann den QR-Code mit der
Authenticator-App scannen (oder den Schlüssel von Hand eintragen),
den sechsstelligen Code bestätigen. Sobald die Zwei-Faktor-
Anmeldung aktiv ist, zeigt Nodvard Deck **einmalig zehn Wiederherstellungs-Codes** (Form
`ABCDE-FGHJK`). Mit „Kopieren“ oder „Als Textdatei speichern“ sichern – am besten im
Passwortmanager oder ausgedruckt, **nicht** nur auf dem Handy, das ja verloren gehen soll.

- **Benutzen:** Bei der Anmeldung im Schritt „Bestätigung“ auf „Handy nicht zur Hand?
  Wiederherstellungs-Code verwenden“ klicken und einen Code eintippen. Jeder Code gilt
  **genau einmal**; Nodvard Deck trägt die Benutzung ins Protokoll ein und schickt eine
  Meldung (Meldungen-Seite, bei eingerichtetem ntfy auch als Push).
- **Codes aus der App** gelten je Konto nur **einmal** (auch der, mit dem du die Einrichtung
  bestätigt hast); ist einer schon benutzt, auf den nächsten warten. Nach 10 falschen Codes in
  15 Minuten (oder 20 in 24 Stunden), egal von welchem Gerät, ist die Code-Eingabe für das Konto
  eine Weile gesperrt, und Nodvard Deck schickt eine Meldung. Die Sperre gilt auch nach einem
  Neustart von Nodvard Deck weiter. Wer so oft rät, kennt vermutlich das Passwort. Warst du es
  nicht, ändere gleich dein Passwort. Dafür und fürs Anmelden brauchst du in der Zeit einen
  Wiederherstellungs-Code, denn die Codes aus der App sind gesperrt. Bestätigungen mit dem Code aus der
  App (etwa für ein Update) gehen erst wieder, wenn die Sperre vorbei ist.
- **Wie viele übrig sind**, steht unter Mein Konto. Bei wenigen oder keinen: „Neue
  Wiederherstellungs-Codes erzeugen“. Dafür brauchst du dein Passwort und einen Code: den aktuellen
  aus der App oder einen deiner bisherigen Wiederherstellungs-Codes. Die alten Codes sind danach
  sofort ungültig.
- **Abschalten** (Mein Konto → „Abschalten“) geht nur mit Passwort **und** Code: dem aktuellen aus
  der App oder einem Wiederherstellungs-Code, der dabei verbraucht wird. Wer nur dein Passwort kennt,
  kann die Zwei-Faktor-Anmeldung so nicht abschalten. Ältere Apps fragen den Code noch nicht ab; dort
  gehen Abschalten, neue Codes, Passwort ändern und Sicherungen erst nach einem Update der App, im Browser sofort.
- **Passwort ändern** (Mein Konto → „Passwort“) verlangt bei eingeschalteter Zwei-Faktor-Anmeldung ebenfalls
  den Code aus der App oder einen Wiederherstellungs-Code; das Feld dafür erscheint gleich mit. Sonst könnte
  jemand, der nur dein altes Passwort kennt, ein neues setzen und dich aussperren. Eine ältere Handy-App
  zeigt dafür den Hinweis, sich zuerst zu aktualisieren.
- Wird beim Abschalten, beim Ändern des Passworts oder beim Erzeugen neuer Codes ein
  Wiederherstellungs-Code benutzt, steht das wie bei der Anmeldung im Protokoll, und Nodvard Deck
  schickt eine Meldung.
- **Sicherung herunterladen oder einspielen** verlangt bei eingeschalteter Zwei-Faktor-Anmeldung
  ebenfalls den Code aus der App (hier kein Wiederherstellungs-Code): Eine Sicherung enthält alle
  Schlüssel, auch den der Zwei-Faktor-Anmeldung. Das Feld dafür erscheint gleich mit. Vertippst du
  dich, bleiben die Passwörter stehen, nur der Code muss neu.
- Nodvard Deck speichert nur Prüfsummen, keine Codes – ein verlorener Satz lässt sich nicht
  nachschlagen, nur ersetzen.
- Wer schon vor dieser Funktion Zwei-Faktor eingerichtet hatte, hat noch keine Codes:
  unter Mein Konto einmal „Neue Wiederherstellungs-Codes erzeugen“ (mit Passwort und dem Code aus
  der App).
- Verliert jemand Handy **und** Codes: siehe [7.3](#73-ausgesperrt-notfall-befehle).

## D. Weitere Benutzer

<a id="3-weitere-benutzer"></a>

Einstellungen → Benutzer → **„Neuer Benutzer“**:

- **Benutzername:** dieselbe Regel wie beim ersten Konto (mindestens 3 Zeichen, nur Kleinbuchstaben,
  Ziffern, `.`, `-` und `_`, vorne ein Buchstabe oder eine Ziffer); Großbuchstaben werden automatisch
  umgewandelt. Bei der Anmeldung ist die Schreibweise egal. Ältere Konten, deren Name nicht zu dieser
  Regel passt (etwa mit Leerzeichen), melden sich weiter normal an.
- **Passwort:** mindestens 8 Zeichen. Der neue Benutzer kann es unter „Mein Konto“ ändern. (Das
  Passwort *anderer* Benutzer setzt man später unter „Bearbeiten“; das eigene und das des
  Inhabers lassen sich dort nicht setzen – eigenes unter „Mein Konto“.)
- **Anzeigename**, **E-Mail (optional)**; eine eingetragene Adresse braucht ein `@` (z. B. `name@beispiel.de`). Nodvard Deck
  verschickt keine E-Mails: Die Adresse ist nur eine Angabe zum Konto (Benachrichtigungen kommen als Push über ntfy).
- **Rollen:**
  - **Administrator** (`admin`) – Volle Verwaltung inkl. Benutzer und Einstellungen
  - **Bediener** (`operator`) – Server bedienen, Aktionen bis mittleres Risiko freigeben
  - **Betrachter** (`viewer`) – Nur ansehen, nichts ändern. Ohne das Recht `hosts.execute` (wie für Befehle) sieht der Betrachter
    keine Dateien auf Servern und weder Befehle noch Ausgabe von Aktionen (Text vom Server), auch nicht im Protokoll.
    Meldungen kann `viewer` lesen, aber nicht als gelesen markieren oder löschen (beides gilt für alle Benutzer gemeinsam)
- „Anlegen“. Passt eine Angabe nicht, steht oben gleich, was (z. B. „Passwort: Mindestens 8 Zeichen.“).

**Zwei-Faktor eines Benutzers zurücksetzen:** Hat jemand Handy und Wiederherstellungs-Codes
verloren, schaltet ein Benutzer mit Recht `users.write` (Rolle `admin`) unter Einstellungen →
Benutzer → **„Zwei-Faktor zurücksetzen“** die Zwei-Faktor-Anmeldung dieser Person ab (zur
Sicherheit wird dabei das eigene Passwort des Admins abgefragt und, wenn er selbst Zwei-Faktor an hat, sein
eigener Code aus der App). Dasselbe gilt, wenn ein Admin beim Bearbeiten ein neues Passwort für jemand anderen
setzt, ein Konto mit Verwaltungsrechten (Rolle `admin`) anlegt oder einem Konto diese Rolle gibt. Die Person wird dabei überall abgemeldet; das Protokoll hält fest, wer es getan hat, und unter
„Meldungen“ steht „Zugang von … geändert“. Danach geht
die Anmeldung wieder nur mit Passwort, und sie kann Zwei-Faktor neu einrichten. **Beim
Inhaber geht das nicht** – seine Zwei-Faktor-Anmeldung kann nur er selbst (unter Mein Konto)
oder der Notfall-Befehl auf dem Server ([7.3](#73-ausgesperrt-notfall-befehle)) abschalten.

**Benutzer deaktivieren:** Unter Einstellungen → Benutzer → **„Deaktivieren“** sperrt ein Benutzer
mit Recht `users.write` das Konto einer anderen Person (in der Liste steht dann „Gesperrt“; beim
Inhaber und beim eigenen Konto gibt es den Knopf nicht). Die Person wird dabei überall abgemeldet,
eine schon offene Seite bekommt nach spätestens einer halben Minute keine Live-Aktualisierung mehr.
Nach **„Aktivieren“** melden sich alte Geräte nicht von allein wieder an, sie muss sich neu anmelden.

## E. Server und Zugänge im Detail

### E.1 Woher die Server kommen

- Proxmox-Knoten, VMs und Container liest die Erweiterung „Proxmox VE“ **automatisch** ein
  (alle 5 Minuten, siehe [4.2](#42-proxmox-verbinden)). Sie stehen mit dem Hinweis „Automatisch
  eingelesen“ in der Liste.
- Alles andere – vor allem der **Rechner, auf dem Nodvard Deck läuft** (z. B. ein Raspberry Pi),
  wenn er kein Proxmox-Gast ist – legst du von Hand an.
- Terminal, Server-Zustand, Docker, Nodvard Shield, Gameserver und Skripte brauchen für jeden
  Server einen **SSH-Zugang** (Benutzer + Schlüssel oder Passwort). Den speichert Nodvard Deck
  verschlüsselt; der private Schlüssel verlässt den Tresor nie und wird nirgends angezeigt.
- **Kleine SSH-Server** wie Dropbear ab Version 2015.68 (etwa auf DietPi, Routern und manchen NAS) gehen auch: Lange
  Befehle (über 8000 Byte, z. B. die Einrichtung, Updates, Härtungs-Audit und Einbruchschutz von Nodvard Shield)
  schickt Nodvard Deck nicht als Befehlszeile, sondern über die Standardeingabe. Dafür muss die Login-Shell des Kontos
  eine POSIX-Shell sein (sh, bash, dash oder die ash von BusyBox); mit fish oder tcsh als Login-Shell klappen diese
  langen Befehle nicht. Wo die Standardeingabe nicht geht (Konsole, Live-Logs), sagt Nodvard Deck bei einem zu langen
  Befehl vorher in Klartext Bescheid und schickt ihn nicht los. Ältere Dropbear-Versionen (etwa auf OpenWrt 15.05)
  nehmen nur Befehle bis 1024 Byte an, dort scheitern viele Befehle.

### E.2 Beispieldaten

**Noch keinen Server zur Hand?** Auf einer frischen Installation ohne Server gibt es im Cockpit (und
auf dieser Seite) den Knopf **Mit Beispieldaten ansehen**. Ein Klick legt fünf ausgedachte Server
(Adressen aus `192.0.2.x`, es wird nie etwas angefragt), ein paar Meldungen, drei Beispiel-Apps und – wenn Module Widgets
mitbringen – ein Beispiel-Dashboard an, damit du siehst, wie alles gefüllt aussieht. Oben steht dann
ein gelbes Band **„Du siehst Beispieldaten“**; **Beispieldaten löschen** (nach Rückfrage) entfernt genau
diese Dinge wieder, stellt dein Dashboard-Layout zurück und lässt alles Echte unberührt. Den Knopf
sehen nur Inhaber und Administratoren (`hosts.write` und `settings.write`). Sobald du einen echten
Server angelegt hast, gibt es den Knopf nicht mehr; die Beispieldaten kannst du trotzdem jederzeit
löschen.

### E.3 Adresse korrigieren

**Adresse korrigieren:** Proxmox-VMs ohne laufenden Gast-Agenten bekommen als Platzhalter die
Adresse des Proxmox-Servers. Dann auf der Seite des Servers unter **Allgemein → Bearbeiten** die
echte IP eintragen – Nodvard Deck überschreibt eine von Hand gesetzte Adresse nicht mehr mit dem
Platzhalter, nur mit einer Adresse, die der Gast selbst meldet. Bei Proxmox-*Knoten* überschreibt
die Erweiterung die Adresse beim nächsten Abgleich wieder.

Hat der Server ein gespeichertes **SSH-Passwort**, wird es beim Ändern der Adresse gelöscht; du gibst es
danach neu ein. SSH-Schlüssel bleiben. Das gilt auch, wenn der Abgleich einer Erweiterung die Adresse
ändert (etwa bei einem Proxmox-Knoten); bei VMs und Containern, die ihre Adresse selbst melden und deren
Server-Schlüssel schon gemerkt ist, bleibt das Passwort.

### E.4 Was der Einrichtungsbefehl genau tut

Läuft auf dem Server Dropbear (etwa bei DietPi), trägt der Befehl den Schlüssel statt mit `restrict,pty` mit
`no-port-forwarding,no-agent-forwarding,no-X11-forwarding` ein: Dropbear kennt `pty` nicht und verwirft sonst die ganze
Zeile.

Einen schon eingetragenen Schlüssel erkennt der Befehl auch mit dem älteren Kommentar `lattice@…`
([Anhang M](#m-alte-namen-bei-älteren-installationen)) und
trägt ihn nicht doppelt ein. Eine auskommentierte Zeile (`# …`) zählt dabei nicht: Wer Nodvard Deck so gesperrt hat,
bekommt beim nächsten Einrichten wieder eine gültige Zeile, die auskommentierte bleibt stehen. Steht der Schlüssel
ohne Einschränkung in der Datei (etwa von Hand eingetragen), setzt der Befehl `restrict,pty` bzw. die Optionen für
Dropbear davor und meldet „Der schon eingetragene Schlüssel für Nodvard Deck ist jetzt eingeschränkt …“. Zeilen mit
eigenen Optionen, etwa `from="…"`, bleiben, wie sie sind. Zum Prüfen des Geschriebenen braucht er dabei `cmp` (Paket
diffutils); fehlt es, ändert er nichts und nennt die Abhilfe.

Hast du einen Server mit Dropbear schon mit einer älteren Version eingerichtet und „Verbindung prüfen“ meldet „Der
Server hat den Schlüssel nicht akzeptiert“, dann richte ihn einfach noch einmal ein (mit Passwort oder mit dem
Befehl). Der Befehl ersetzt dabei genau seine eigene alte Zeile `restrict,pty …` durch die Zeile für Dropbear
(Meldung „Schlüssel für Dropbear angepasst.“); andere Zeilen mit demselben Schlüssel und eigenen Optionen bleiben,
wie sie sind.

### E.5 Mit Passwort einrichten: Grenzen und Sicherheit

- **Grenzen:** Du brauchst die Rechte `hosts.write` **und** `hosts.execute`, denn der Befehl läuft auf dem Server als
  root, wie beim Terminal. Nodvard Deck bremst Versuche: je Adresse und Port höchstens 5, je Benutzer 10 in 10 Minuten
  (dazu 30 Aufrufe je Benutzer in 5 Minuten), und je Server läuft nur eine Einrichtung gleichzeitig; dann steht „Zu
  viele Versuche mit Passwort …“ bzw. „Für diesen Server läuft gerade schon eine Einrichtung …“. Die Zähler liegen im
  Speicher und beginnen nach einem Neustart von vorn. Der Befehl bekommt bis zu 90 Sekunden. Reißt die Verbindung ab
  oder läuft die Zeit aus, kann er auf dem Server trotzdem teilweise gelaufen sein: „Verbindung prüfen“ zeigt, was
  schon da ist, und ein neuer Versuch ist gefahrlos, weil sich der Befehl mehrfach ausführen lässt.
- **Ohne HTTPS** warnt dich das Formular, bevor du das Passwort abschickst: Es geht dann unverschlüsselt von deinem
  Browser zu Nodvard Deck. Im eigenen Heimnetz ist das meist in Ordnung, in einem fremden Netz nicht; sicherer ist dann
  „Befehl selbst einfügen“. Beim Aufruf direkt auf dem Rechner (`localhost`) erscheint die Warnung nicht. Gesperrt wird
  nichts.
- **Noch nicht möglich:** Proxmox per Passwort, Windows-Server (dafür gibt es keinen Einrichtungsbefehl) und der
  Einrichtungsassistent.

### E.6 Server-Schlüssel und grüner Zugang

**Wann der Zugang grün ist:** In der Liste und auf der Server-Seite wird das Abzeichen des SSH-Zugangs erst grün, wenn
der Server antwortet und sich Nodvard Deck dort mit diesem Zugang wirklich angemeldet hat, etwa bei „Verbindung prüfen“
oder wenn ein Modul den Server per SSH abfragt. Dass der Server antwortet, heißt nur, dass sein SSH-Port offen ist. Bis
dahin bleibt das Abzeichen grau, und daneben steht gelb, was fehlt: „noch nicht geprüft“, „noch keine Verbindung“ (der
Server hat nie geantwortet), „Anmeldung noch nicht bestätigt“ (bei einem Schlüssel fehlt meist noch der
Einrichtungsbefehl), „keine Antwort“ oder „Anmeldung klappt nicht“. Nach einer neuen Adresse, einer abgelehnten
Anmeldung, einem geänderten Server-Schlüssel oder einem neuen Standard-Zugang ohne frische Prüfung gilt die Anmeldung
wieder als nicht bestätigt, bis sie das nächste Mal klappt.

> **Hinweis:** Ob Nodvard Deck einen neuen Server-Schlüssel beim allerersten Verbinden (Terminal,
> Überwachung, …) von allein merkt, stellst du unter **Einstellungen → Server & Zugänge** in der Karte
> **„Neue Server-Schlüssel“** ein. **„Neue Server-Schlüssel erst nach meiner Bestätigung merken“** ist an,
> auch bei Installationen, die schon vor dieser Einstellung eingerichtet waren: Dann merkt sich Nodvard Deck einen
> Schlüssel nur über „Verbindung prüfen“, vorher geht kein Passwort und kein Schlüssel an den Server. Kennt Deck
> von einem Server noch keinen Schlüssel, drückst du dort einmal „Verbindung prüfen“ und bestätigst den
> Fingerabdruck. „Aus“ (nicht empfohlen) merkt neue Schlüssel wieder von allein; das gilt nur, wenn du es
> ausdrücklich so einstellst. Die Umgebungsvariable `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` (`true` oder `false`; in der
> Compose-Datei unter `environment:`, siehe das Beispiel in [2.1](#21-den-einrichtungscode-finden); in
> `deploy/docker-compose.yml` des Repositorys ist die Zeile vorbereitet) übersteuert den Schalter: Dann
> zeigt die Karte deren Wert, und Speichern meldet einen Fehler.
> Nach **„Vergessen“** musst du den neuen Fingerabdruck immer bestätigen, egal wie das eingestellt ist –
> bis dahin verbindet sich Nodvard Deck nicht mehr mit dem Server.
> Bereits gemerkte Schlüssel sind nie betroffen. Sauberer ist immer: zuerst prüfen und bestätigen.

### E.7 Zugang ersetzen, löschen, Gruppen

- **Ersetzen:** Karte SSH-Zugang → **Ersetzen** legt einen *neuen* Zugang an (Schlüssel erzeugen,
  Passwort oder eigener Schlüssel); der alte bleibt Standard. Führe den Befehl des neuen Zugangs
  auf dem Server aus, drücke **Neuen Zugang prüfen** und danach **Neuen Zugang verwenden**. Erst
  nach einer erfolgreichen, frischen Prüfung (höchstens 10 Minuten alt) wird der alte Zugang
  gelöscht – so sperrst du dich nicht aus. War der alte Zugang ein Schlüssel, **bleibt sein öffentlicher Schlüssel auf
  dem Server in `~/.ssh/authorized_keys` stehen**; dort bei Bedarf selbst entfernen. Bei einem Passwort ändert sich auf
  dem Server nichts. Die Prüfung des neuen Zugangs ändert nichts an der Bestätigung des alten (3.4); erst wenn du den
  neuen verwendest, gilt seine Prüfung.
- **Zugang löschen:** Terminal, Updates und Überwachung erreichen den Server dann nicht mehr. Ein
  Schlüssel bleibt auf dem Server eingetragen; bei einem Passwort ändert sich dort nichts.
- **Gruppen:** Auf der Liste unter **Gruppen** anlegen, umbenennen, löschen; auf der Seite des
  Servers per Haken zuordnen. „Skripte“ kann ein Skript auf allen Servern einer Gruppe laufen lassen.
- **Server löschen:** Karte „Gefahrenbereich“ (oder „Löschen“ auf der Server-Seite). Zur Sicherheit
  den Kurznamen eintippen. Gelöscht werden auch SSH-Zugang und gemerkter Server-Schlüssel;
  Verläufe in Nodvard Shield bleiben mit dem alten Namen stehen. Von Proxmox eingelesene Server
  tauchen beim nächsten Abgleich wieder auf – dann ohne SSH-Zugang. Läuft gerade eine Aktion auf
  dem Server, geht das Löschen nicht.
  Ist das Modul, das den Server eingelesen hat, ausgeschaltet, sagt der Dialog
  stattdessen, dass der Server nicht wiederkommt, solange das Modul aus bleibt.

## F. Root-Rechte ohne Passwort und Gruppe docker

<a id="6-root-rechte-ohne-passwort-und-gruppe-docker"></a>

Meldet sich Nodvard Deck auf einem Server **nicht als root** an (z. B. auf dem Pi), führt es
root-Befehle über `sudo -n` aus, also ohne Passwortabfrage. Verlangt sudo ein Passwort,
scheitert die Funktion mit „Keine root-Rechte: Das Dashboard ist auf diesem Server nicht
als root angemeldet und darf sudo nicht ohne Passwort nutzen.“

Den Einrichtungsbefehl ([3.3](#33-einrichtungsbefehl-auf-dem-server-ausführen)) musst du dafür **nicht von Hand ändern**: Die Seite setzt die
passenden Haken selbst, weil die Erweiterungen melden, was sie brauchen. Dieselben Haken gelten, wenn du den Server
mit Passwort einrichtest (Nodvard Deck führt dann denselben Befehl aus).

- **„Root-Rechte ohne Passwort“** – nötig für (die Seite nennt es hinter dem Haken): Teile von Nodvard Shield (Dateien in
  Quarantäne verschieben, wiederherstellen oder löschen, Härtungs-Audit mit Lynis, Werkzeuge wie ClamAV und Fail2ban
  installieren, Updates einspielen und neu starten, Adressen per Fail2ban sperren; Einbruchschutz und Datei-Wächter lesen
  ohne root nur eingeschränkt), Dienste neu starten (Server-Zustand) und Docker im Modus „Nur bedienen“ (Server mit der
  Markierung `docker-bedienen`).
- **„Zur Gruppe docker hinzufügen“** – nötig für das Modul Docker und die KI-Container-Wache von
  Nodvard Shield; sie rufen `docker` ohne sudo auf. Die Gruppe wird nur eingetragen, wenn es sie auf
  dem Server gibt. Einem Server nur mit der Markierung `docker-bedienen` bietet die Seite die Gruppe nicht an; dort
  reicht das Hilfsskript.

Ohne beide Haken bekommst du die **schlanke Variante**: eigener Benutzer, nur Schlüssel, keine
Root-Rechte. Die reicht für Terminal, Dateien und Skripte.

**Was der Haken „Root-Rechte“ einrichtet:** Standardmäßig nur die Hilfsskripte der Module (Nodvard Shield,
Server-Zustand und Docker im Modus „Nur bedienen“), jedes mit einer eigenen, engen sudo-Regel; eine volle sudo-Regel
gibt es dann nicht. Ab Version 0.13.0 führt sudo diese Hilfsskripte nur noch in der geprüften Fassung mit fester
SHA-256-Prüfsumme aus. Bei bestehenden Servern wird diese Bindung aktiv, sobald der Einrichtungsbefehl einmal neu
ausgeführt wurde. Mit `sudo-rs`, dem Standard ab Ubuntu 25.10, funktioniert die Prüfsumme nicht. Die volle Regel
(`<benutzer> ALL=(root) NOPASSWD: ALL`) kommt nur dazu, wenn ein Modul root braucht und kein Hilfsskript hat, oder wenn
du unter „Erweitert“ „Volle Rechte“ setzt. Braucht auf dem Server kein Modul root, richtet der Haken gar kein sudo ein.

**Das ist praktisch root – die Seite sagt es dir auch:** Wer in der Gruppe `docker` ist oder die volle sudo-Regel
hat, kann auf dem Server praktisch alles. Nodvard Deck startet ohne Hilfsskript einige root-Befehle
als `sudo -n sh -c '…'`, darum braucht es dann die volle sudo-Regel, und wer `sh` als root starten darf,
darf alles. Die Sicherheit kommt daher, dass **nur**
dieser eine Benutzer die Rechte bekommt, er sich **nur mit dem Schlüssel** anmelden kann und der
Schlüssel verschlüsselt in Nodvard Deck liegt.

**Proxmox-Knoten:** Dort meldet sich Nodvard Deck am einfachsten direkt als `root` an (Benutzer
`root` beim Erzeugen eintragen). Dann braucht es kein sudo und keine Haken. Der Schlüssel landet in
`/root/.ssh/authorized_keys`, das bei Proxmox ein Verweis in den Cluster-Ordner ist – er gilt dann
auf **allen Knoten des Clusters**.

**Vorhandenen Benutzer nehmen** (auf dem Pi z. B. `admin`): beim Erzeugen statt `nodvard` dessen
Namen eintragen. `sudo -n true && echo geht-schon` auf dem Server zeigt, ob er sudo schon ohne
Passwort darf (auf Raspberry Pi OS oft beim ersten Benutzer der Fall).

Alles im Detail – was jedes Modul als root tut, was die Hilfsskripte dürfen (mit Tabelle), wie die sudo-Regel geprüft
wird, wie du eine volle Regel wieder loswirst, Docker auf „Nur bedienen“ umstellen und was der Datei-Wächter nach der
Einrichtung meldet: **[Root-Rechte und Hilfsskripte](14-ROOT-RECHTE.md)**.

## G. Module im Detail

### G.1 Ohne Proxmox im Detail

- **Zustand (online/offline):** Nodvard Deck prüft von Hand angelegte Server **von selbst alle 2 Minuten**
  (Einstellungen → Server & Zugänge → „Erreichbarkeit prüfen“; Schalter und Abstand von 1 bis 60 Minuten).
  Geprüft wird nur, ob der SSH-Port des Servers (Standard 22, sonst der Port des Zugangs) eine Verbindung annimmt:
  ohne Anmeldung, auch für Server ohne Zugang. Ein Server gilt erst nach **zwei Fehlversuchen hintereinander** als
  „nicht erreichbar“, „wieder da“ gilt sofort. Fällt ein Server aus (oder kommt wieder), gibt es **eine Meldung**
  mit Link zur Server-Seite, auf Wunsch auch als Push; das Cockpit zeigt ihn unter „Braucht Aufmerksamkeit“. Im
  **Wartungsfenster** gibt es keinen Push (nur den Verlauf); dauert der Ausfall nach dem Fenster an, kommt die Meldung
  einmal nach. Ein Server, der **noch nie geantwortet hat** (zum Beispiel ein Tippfehler in der Adresse), steht
  einfach auf „nicht erreichbar“, ohne Meldung. Nicht geprüft werden Server, die ein Modul selbst einliest
  (etwa Proxmox-Gäste), Beispiel-Server, Server im Zustand „Wartung“ und Server, deren Standard-Zugang kein SSH ist.
  Ein Server ohne SSH-Dienst (zum Beispiel ein Gerät, auf dem SSH aus ist) zeigt deshalb dauerhaft „nicht
  erreichbar“; wer das nicht will, schaltet ihn auf seiner Seite aus. „Verbindung prüfen“ gibt es weiter für den
  genauen Test mit Anmeldung. Das Cockpit zeigt bei ungeprüften Servern „noch nicht geprüft“.
- **Auslastung und Verlauf** (CPU, Arbeitsspeicher, Platte, Netz, Temperatur) kommen vom Modul **Server-Zustand** (bis Version 0.8.0 hieß es „System“)
  per SSH, nur für Linux-Server mit SSH-Zugang. Ohne dieses Modul zeigt die Server-Seite einen Hinweis statt
  der Ringe und Kurven. Der Verlauf wird alle 30 Sekunden gemessen und ist die ersten Minuten noch leer. Hat der
  Server noch nie geantwortet oder ist die Anmeldung noch nicht bestätigt, sagt die Server-Seite das, statt auf Kurven
  warten zu lassen (wer Server ändern darf, bekommt den Link „Zum Zugang“). Im Cockpit steht bei einem Server mit
  SSH-Zugang, der „nicht erreichbar“ ist und noch nie geantwortet hat, „Noch keine Verbindung: prüfe zuerst den Zugang“.
- **Auslastung im Cockpit:** Im Bereich „Infrastruktur“ bekommt jeder Linux-Server mit Messwerten eine Karte mit
  Ringen für CPU, Arbeitsspeicher und Platte, so wie die Proxmox-Knoten. Die Zahlen stammen aus dem schon geführten
  Verlauf (letzte Messung, alle 30 Sekunden); das Cockpit löst beim Laden **keine** SSH-Verbindung aus und fragt alle
  Server in einer einzigen Abfrage (`GET /hosts/metrics/latest`). Ist die letzte Messung älter als 2 Minuten, steht
  „veraltet“ mit dem Alter darunter und die Ringe sind gedämpft; nach 10 Minuten ohne Messung wird der Server wieder
  eine schlichte Zeile. Ein Server, der „nicht erreichbar“ ist, zeigt keine alten Werte als aktuell. Server ohne
  Messwerte (Windows, ohne SSH-Zugang, Modul Server-Zustand aus, Messwerte für den Server abgeschaltet) bleiben eine Zeile,
  wo es passt mit einem Hinweis, warum. Laufende Proxmox-Gäste (VMs und LXC) zeigen CPU, Arbeitsspeicher und „Läuft
  seit“. Die Platte zeigen LXC-Container und Proxmox-Knoten. Bei VMs bleibt sie leer, weil Proxmox für sie keinen
  belegten Platz meldet. Gestoppte Gäste bleiben eine Zeile. Die Werte holt der Sammler alle 30 Sekunden mit einer
  Abfrage je Proxmox-Verbindung.
- **Docker-Container und Stacks aus Git:** das Modul Docker, für Server mit der Markierung `docker`. Dort startest,
  stoppst und löschst du Container (gelöscht werden nur gestoppte; Volumes und Image bleiben), liest ihre Logs live,
  öffnest mit „Konsole“ eine Shell im Container und spielst neuere Images ein („Einspielen“, nur bei Docker-Compose-Diensten).
  Stacks lassen sich auch direkt aus Git laden und nach Zeitplan auf Änderungen prüfen. Deck lädt Stacks aus Git standardmäßig
  nur von öffentlichen Adressen. Für Gitea oder ähnliche Server im Heimnetz schaltest du in den Einstellungen des Moduls Docker
  unter „Erweitert“ den Schalter „Git-Server im eigenen Netz erlauben“ ein. Deck verbindet sich nur mit der vorab geprüften
  Zieladresse. Hinter einem Proxy (`HTTPS_PROXY`) holt Deck nur von Ziffern-Adressen; Git-Server mit Domainnamen gehören in
  `NO_PROXY`. Stürzt eine neue Version beim Start ab, geht das Modul Docker in der Regel von selbst auf die alte zurück (nie bei
  Datenbanken; abschaltbar unter „Konfigurieren“).
  Nach „Starten“, „Neustart“ und „Stack neu starten“ sieht Deck bis zu einer Minute lang nach, ob die Container
  wirklich laufen und laufen bleiben (ohne Healthcheck: zweimal im Abstand von 15 Sekunden ohne neuen Start). Stürzt
  ein Container gleich wieder ab, gilt die Aktion als fehlgeschlagen und sagt, warum (etwa „beendet, Exit-Code 1“).
  Beim Stack zählen nur die Container, die vorher liefen. Ein einzelner Neustart (etwa weil eine Abhängigkeit noch
  nicht bereit war) und ein Ende mit Exit-Code 0 (Container für einmalige Aufgaben) gelten nicht als Fehler, sie stehen
  nur als Hinweis dabei; „ungesund“ zählt erst, wenn es bis zum Ende so bleibt.
- **Apps im Cockpit ohne das Modul Docker:** Im Bereich „Apps“ legst du mit **+ App hinzufügen** eigene Kacheln an –
  Name, Adresse (`http://192.168.1.1`), Symbol (aus einer Auswahl oder ein Emoji), Farbe, Gruppe und auf Wunsch der
  Server dazu. So stehen auch Router, NAS-Oberfläche oder Pi-hole im Cockpit, ohne Code und ohne Container-Erkennung;
  erkannte Container erscheinen daneben, wenn das Modul Docker läuft. Über das Menü (⋮) an der Kachel bearbeitest,
  verschiebst oder löschst du sie, oben filterst du nach Gruppen. Anlegen und Ändern dürfen Inhaber und
  Administratoren (Recht `apps.write`); sehen dürfen es alle, die Server sehen. Nodvard Deck ruft die Adressen nie
  selbst auf (es zeigt also kein „läuft/läuft nicht“ an), erlaubt nur `http://` und `https://` und keine Zugangsdaten
  in der Adresse; Symbole sind nie Bild-Adressen.
- **Seite „Apps“** (links im Menü): alle Kacheln an einem Ort, eigene Apps in **Ordnern** (ihre Gruppe), die du
  zuklappen kannst. Mit `apps.write` ziehst du eine Kachel auf eine andere (davor einsortiert) oder auf die Überschrift
  eines Ordners (dort ans Ende); ohne Maus über das Menü (⋮). Unter **„Vorschläge“** stehen Adressen, die deine Module
  schon kennen – zum Beispiel die Proxy-Hosts aus dem Nginx Proxy Manager – und die es als App noch nicht gibt;
  **„Übernehmen“** füllt den Dialog vor, angelegt wird erst mit „Speichern“. Erkannte Container mit Web-Oberfläche
  stehen nicht dort, sondern unter **„Erkannte Dienste“**; dort macht **„Als App übernehmen“** (nur bei laufenden
  Containern) eine eigene Kachel daraus. Beide Listen kommen aus demselben Lesen der Docker-Server. Nur wenn die Kachel
  eines Containers gerade nicht zu sehen ist (etwa weil die Seite sie noch nicht geladen hat), steht er kurz unter
  „Vorschläge“.
- **Updates, Virenschutz, Einbruchschutz:** Nodvard Shield, für alle Linux-Server mit SSH-Zugang. Die KI-Container-Wache
  ist darin optional; ohne KI-Server zeigt sie abgestürzte Container trotzdem an, mit der Ursache laut System (etwa
  Speichermangel oder von außen beendet) und zwei festen Sätzen: was das heißt und was du tun kannst. Mit KI schlägt sie höchstens den
  Neustart eines abgestürzten Containers vor, du findest ihn unter „Aktionen“; andere Ideen der KI stehen nur als
  Text im Lagebericht der Container-Wache. Schlägt sie einen anderen Befehl vor, steht im Lagebericht nur ein Hinweis
  darauf; den Befehl selbst siehst du mit Server-Rechten (`hosts.execute`) im Protokoll.
  Ein Klick auf die Push-Meldung des Lageberichts öffnet genau diesen Vorschlag unter „Aktionen“ (hervorgehoben, auch
  wenn er schon erledigt ist); ohne Vorschlag öffnet er den Reiter „Container-Wache“. Anmelden musst du dich trotzdem.
  Läuft ein Container nach einem Absturz wieder fünf Minuten ruhig, setzt die Container-Wache seinen Vorfall von selbst
  auf „Erledigt“ („Läuft wieder seit 04:12 – von selbst erledigt.“, ohne Push-Meldung, mit Eintrag im Protokoll).
  Wartet dazu noch ein vorgeschlagener Neustart unter „Aktionen“, steht dabei, dass er nicht mehr nötig ist; ablehnen
  musst du ihn dort selbst. Stürzt der Container innerhalb der Ruhezeit wieder ab, ist derselbe Vorfall wieder offen,
  sofern ihn noch niemand bearbeitet hatte und kein Neustart dazu schon gelaufen ist; sonst gibt es einen neuen Vorfall
  mit Meldung, auch in der Ruhezeit (zuletzt hieß es ja „läuft wieder“). Die fünf
  ruhigen Minuten zählt die Container-Wache nur, solange das Dashboard läuft: Nach einem Neustart des Dashboards bleibt
  ein offener Vorfall offen, bis der Container wieder eine Störung hatte und danach ruhig lief, oder bis du ihn abhakst.
  Stürzt ein Container innerhalb von sechs Stunden nach einem freigegebenen Neustart genau dieses Containers wieder ab
  (und lief er dazwischen nicht ruhig), sagt die nächste Meldung: „nginx auf docker ist nach dem freigegebenen Neustart
  um 03:10 wieder abgestürzt.“ – auch innerhalb der Ruhezeit, in der sonst keine neue Meldung kommt.
- **Server wählen, Dateien:** Auf den Seiten „Server-Zustand“ und „Terminal“ stehen die Server als Kacheln mit Name, Adresse,
  System und Status (Ampelpunkt). Auf „Server-Zustand“ zeigen sie auch die Auslastung („CPU 12 % · RAM 48 %“, bis zu 5 Minuten
  alt) und einen Befund wie eine fast volle Platte als Hinweis; der zuletzt gewählte Server ist beim nächsten Besuch
  vorgewählt (das merkt sich der Browser). Im Dateimanager steht jeder Server mit SSH-Zugang als Quelle (nur mit dem
  Recht `hosts.execute`); die Leiste zeigt daneben, ob er erreichbar ist und welches System darauf läuft. Quellen ohne
  Server, etwa Nextcloud, haben beides nicht. Ohne das Recht, Server zu sehen (`hosts.read`), fehlen nur Punkt und System.
  Textdateien (Konfiguration, Logs, Skripte) und Bilder öffnest du mit **„Ansehen“** direkt über der Liste; bei großen
  Dateien zeigt die Vorschau die ersten 256 KB. HTML erscheint als Text, SVG- und PDF-Dateien holst du mit „Laden“.
- **Konsole im Browser** (Bildschirm eines Gasts) gibt es nur für Proxmox-Gäste. Für alle anderen Server
  nimmst du das **Terminal**.
- **Sicherungen:** Das Modul „Proxmox-Backups“ zeigt nur die Backups deiner Proxmox-Server. Das Dashboard selbst sicherst du unter
  Einstellungen → System ([6.](#6-das-dashboard-selbst-sichern)).
  Mit „Als Kopie wiederherstellen“ holst du dort einen Gast aus einer Sicherung zurück: als neuen Gast mit neuer Nummer,
  der nicht von selbst startet; das Original bleibt unberührt. Jede Kopie entsteht im Pool `nodvard-kopien` (nur dort
  braucht der Zugang das Recht zum Anlegen; ab Proxmox VE 7.2 für VMs). Die Netzwerkkarten trennt Nodvard Deck, sobald
  Proxmox fertig ist (sonst holt es der Backup-Wächter nach und meldet sich). Pool und Token-Rechte stehen in
  [docs/10](10-PROXMOX-TOKEN.md#wiederherstellen-als-kopie-backups).

### G.2 Proxmox im Detail

Ausführlich in **[docs/10-PROXMOX-TOKEN.md](10-PROXMOX-TOKEN.md)**, der Weg mit Passwort dort unter
[Schneller Weg](10-PROXMOX-TOKEN.md#schneller-weg-mit-passwort-verbinden). Hast du mit Passwort verbunden und dabei
„Auch Proxmox-Backups verbinden“ angelassen, entfällt Schritt 3 unten. Von Hand geht es so:

1. Auf jedem Proxmox-Server (im Beispiel pve1 **und** pve2) einen Benutzer `nodvard@pve` mit
   Token `dashboard` und passender Rolle anlegen.
2. Einstellungen → Erweiterungen → „Proxmox VE“ → „Konfigurieren“ → „Server hinzufügen“
   (Kurzname `pve1`/`pve2`, Adresse `https://…:8006`, API-Token-ID
   `nodvard@pve!dashboard`) → „Speichern“ → unter „Zugangsdaten“ das Geheimnis eintragen.
3. Dasselbe unter „Proxmox-Backups“ mit denselben Kurznamen.
4. Nach spätestens 5 Minuten sind Knoten, VMs und Container da. VMs ohne Gast-Agent
   bekommen die Proxmox-Adresse als Platzhalter – die richtige IP unter Einstellungen →
   Server & Zugänge eintragen ([3.1](#31-server-anlegen)).
5. Von selbst melden die Module außerdem, wenn ein Speicher fast voll ist (Proxmox VE, ab 90 %, einstellbar) und wenn
   die letzte erfolgreiche Sicherung einer VM älter als 3 Tage ist (Backups, einstellbar, mindestens 2 Tage); beides
   unter „Konfigurieren“ des jeweiligen Moduls. Läuft der Backup-Job einer VM seltener (z. B. nur samstags), gilt
   automatisch dessen Abstand, wenn er größer ist; dazu kommen 6 Stunden Luft, und solange gerade eine Sicherung
   läuft, kommt keine Warnung. Proxmox VE meldet außerdem einen ZFS-Pool, der nicht mehr ONLINE ist (eingeschränkt
   als Warnung, ausgefallen als „kritisch“) oder ganz fehlt (etwa nach einem Neustart nicht eingebunden), und einen
   Datenträger, der 10 bis 15 Minuten lang zu heiß ist (Festplatten ab 55 °C, SSD ab 65 °C, NVMe ab 70 °C, einstellbar
   unter „Erweitert“). Die ZFS-Pools stehen auch beim Knoten unter
   „Knotendetails“.

### G.3 Erweiterungen verwalten

Unter Einstellungen → Erweiterungen stehen die Module in denselben Gruppen wie im Assistenten.
Hat ein Modul Server eingelesen (etwa Proxmox),
nennt die Rückfrage beim Ausschalten ihre Zahl: Die Server bleiben in der Liste stehen, im Cockpit tragen sie
„Modul aus“, und ihr Zustand wird nicht mehr aktualisiert. Löschen kannst du sie unter Einstellungen → Server & Zugänge.

**Für Entwickler:** Das ausführbare Beispiel [hello-world](02-EXTENSION-API.md) ist in der SDK-Dokumentation erklärt
und gehört nicht zum Standardimage. Selbst installierte Beispiel-Module stehen unter „Für Entwickler“.

### G.4 Adress-Check (Nginx Proxy Manager)

Auf der Seite „Nginx Proxy Manager“ steht unter jedem Proxy-Host, zu welchem
Docker-Container er führt und ob der läuft, etwa „Führt zum Container immich_server auf docker – der ist gestoppt.“
Ist er gestoppt, startet ihn der Knopf „Container starten“ über die Freigabe (wer `hosts.execute` hat). Zugeordnet wird
über die Server-Adresse mit Port (auch bei gestoppten Containern; nur an `127.0.0.1` gebundene Ports zählen nicht), über
`host.docker.internal` auf dem Server, auf dem der Proxy Manager selbst läuft (`localhost` nur, wenn er im Host-Netz
läuft, sonst ist das der Proxy Manager selbst), und über den Containernamen bzw. Compose-Dienst. Namen zählen nur auf dem Server des Proxy
Managers (Docker kennt sie nur im eigenen Netz); ist der unbekannt, heißt es „vermutlich“, ohne Rot und ohne Knopf.
Dafür muss das Modul Docker eingeschaltet sein, es kennt die Container. Ein Ziel, das zu keinem Container passt
(etwa die Proxmox-Oberfläche), steht grau da und ist kein Fehler. Nicht erkannt werden Container im Host-Netz (sie
zeigen keine Ports), Netzwerk-Aliase und zusätzliche Orte (`locations`) eines Proxy-Hosts.

### G.5 Synology DSM

Einrichten, mehrere Geräte, Meldungen und Hyper Backup: [Synology DSM](15-SYNOLOGY-DSM.md).

### G.6 Die Widgets auf der Startseite

Die Module bringen ihre Widgets mit; sie stehen in Gruppen (Server und Proxmox,
Sicherheit, Docker und Apps, Backups, Dienste). Ein Klick auf den Namen klappt eine Gruppe zu; ein farbiger Punkt daneben
meldet dann eine Warnung, eine Störung oder ein Widget, das gerade nicht lädt. Mit **„Bearbeiten“** über den Widgets
ordnest du alles an: Karten am Griff verschieben, Höhe (Automatisch, Klein, Mittel, Groß), Breite (Schmal, Breit) und
Gruppe wählen, Widgets entfernen und Gruppen umbenennen. Erst **„Fertig“** speichert alles auf einmal, **„Abbrechen“**
verwirft die Änderungen; ein entferntes Widget holst du über **„Widgets verwalten“** zurück. Am Handy stehen die Karten
untereinander: Höhe, Gruppe und Entfernen gehen dort auch, Verschieben und Breite nur auf einem größeren Bildschirm.

### G.7 Jellyfin (Beta, nur lesend)

#### Datenschutz

Für jede Verbindung hinterlegst du einen API-Schlüssel, den Nodvard Deck verschlüsselt im Tresor speichert.
Er gehört zur eingetragenen Serveradresse. Änderst du das Ziel in den Einstellungen, wird der hinterlegte Schlüssel
beim Speichern gelöscht und muss neu eingetragen werden. Laut den Hinweisen in den Moduleinstellungen haben
Jellyfin-Schlüssel Verwaltungsrechte und lassen sich nicht auf Lesen beschränken. Das Modul verwendet den Schlüssel
nur zum Lesen. Lösche ihn auch in Jellyfin, wenn du das Modul nicht mehr brauchst.

Das Modul liest Name und Version des Servers sowie die Anzahl der Medien je Bibliotheksart. Dazu kommen bis zu zehn
zuletzt hinzugefügte Filme und Folgen mit Titel, Angaben zum Medium und Hinzufügedatum. Laufende Wiedergaben werden
mit Benutzernamen, Gerätenamen, verwendeter App, Medientitel, Abspielmethode, Fortschritt und Pausenzustand abgefragt.
Die Anfragen gehen nur an die eingetragene Serveradresse; Weiterleitungen werden abgewiesen.

Alle Benutzer mit Leserecht für Server (`hosts.read`), darunter die Rolle Viewer, können im Dashboard sehen,
wer welchen Titel auf welchem Gerät abspielt, und die zuletzt hinzugefügten Titel lesen. Vergib dieses Recht nur
an Personen, die diese Angaben sehen dürfen.

### G.8 Plex (Beta, nur lesend)

#### Datenschutz

Für jede Verbindung hinterlegst du einen Plex-Token, den Nodvard Deck verschlüsselt im Tresor speichert.
Er gehört zur eingetragenen Serveradresse. Änderst du das Ziel in den Einstellungen, wird der hinterlegte Token
beim Speichern gelöscht und muss neu eingetragen werden. Laut den Hinweisen in den Moduleinstellungen gehört ein
Plex-Token zu deinem Plex-Konto und hat dessen volle Rechte; er lässt sich nicht auf Lesen beschränken.
Das Modul verwendet ihn nur zum Lesen. Gib den Token nicht weiter.

Das Modul liest Name und Version des Servers sowie Namen, Art, Aktualisierungszustand und Eintragszahl der
Bibliotheken. Für die Eintragszahl fragt es nur Zähler ab. Zusätzlich ruft es bis zu zehn zuletzt hinzugefügte Medien
mit Titel, Angaben zum Medium und Hinzufügedatum ab. Laufende Wiedergaben werden mit Benutzernamen, Gerät,
verwendeter App, Medientitel, Abspielmethode, Fortschritt und Pausenzustand abgefragt. Die Anfragen gehen nur an die
eingetragene Serveradresse; Weiterleitungen werden abgewiesen. Zusätzliche Abfragen an plex.tv führt das Modul nicht aus.

Alle Benutzer mit Leserecht für Server (`hosts.read`), darunter die Rolle Viewer, können im Dashboard sehen,
wer welchen Titel auf welchem Gerät abspielt, und die zuletzt hinzugefügten Titel lesen. Vergib dieses Recht nur
an Personen, die diese Angaben sehen dürfen.

### G.9 qBittorrent (Beta, nur lesend)

#### Datenschutz

Wenn qBittorrent eine Anmeldung verlangt, hinterlegst du den Benutzernamen in den Moduleinstellungen und das
Passwort verschlüsselt im Tresor. Das Passwort gehört zur eingetragenen Serveradresse. Änderst du das Ziel in den
Einstellungen, wird es beim Speichern gelöscht und muss neu eingetragen werden. Erlaubt qBittorrent den Zugriff
aus dem Netz des Dashboards ohne Anmeldung, können Benutzername und Passwort leer bleiben.

Das Modul liest Programmversion und Schnittstellenversion sowie Download- und Upload-Tempo, Verbindungsstatus,
Tempolimits und freien Festplattenplatz. Für die Liste aktiver Torrents fragt es bis zu 51 aktive Torrents ab und
zeigt höchstens 50 mit Namen, Zustand, Fortschritt, Größe der ausgewählten Dateien, Tempo und Restzeit an.
Für die Anzahl je Zustand ruft es zusätzlich eine Liste aller Torrents ab, auch der inaktiven, und zählt deren
Zustände. Die Kommunikation geht nur an die eingetragene Adresse der Weboberfläche; Weiterleitungen werden
abgewiesen. Das Modul kontaktiert selbst keine Tracker. Es verändert keine Torrents und sendet nur zur Anmeldung
und Abmeldung schreibende Anfragen.

Alle Benutzer mit Leserecht für Server (`hosts.read`), darunter die Rolle Viewer, können im Dashboard die Namen
der angezeigten aktiven Torrents sehen. Vergib dieses Recht nur an Personen, die diese Angaben sehen dürfen.
Wiederholte falsche Anmeldungen können die Adresse des Dashboards in qBittorrent sperren. Nach einer abgelehnten
Anmeldung wartet Nodvard Deck deshalb 30 Minuten bis zur nächsten Abfrage; „Verbindung testen“ hebt die Pause auf.

## H. Automatik und Wartungsfenster

<a id="9-automatik-und-wartungsfenster"></a>

Einstellungen → **Automatik & Sicherheit**:

- **Selbstständigkeit:** „Nur vorschlagen“ ist der Standard und für den Anfang richtig –
  jede Aktion, die Nodvard Deck von sich aus vorschlägt, wartet unter „Aktionen“ auf deine Freigabe. „Selbstständig handeln“ lässt
  Aktionen bis zur gewählten Risikostufe („Ohne Rückfrage erlaubt bis Risikostufe“) ohne
  Rückfrage laufen. Unter „Aktionen“ steht der Befehl einer Aktion direkt in ihrer Zeile (ohne das Recht
  `hosts.execute` und ohne Freigaberecht für ihr Risiko steht dort nur ein Hinweis); unsichtbare oder umlenkende
  Zeichen darin (etwa eine umgekehrte Schreibrichtung) erscheinen als sichtbare Marke wie `⟦U+202E⟧`. „Ausgewählte
  freigeben“ zeigt in der Rückfrage alle Befehle ungekürzt, gibt aber Aktionen mit hohem oder kritischem Risiko nicht
  mit frei; die bestätigst du einzeln.
- **Selbst ausgelöst:** Klickst du selbst einen Knopf wie „Endgültig löschen“ (Nodvard Shield) oder „Neustart“
  (Docker) und hast das Freigaberecht für das Risiko der Aktion, ist deine Bestätigung im Dialog schon die
  Freigabe: Die Aktion läuft sofort. Sie steht trotzdem unter „Aktionen“ und im Protokoll, nur nicht unter „Wartet auf
  Bestätigung“, sondern unter „Alle“ bzw. „Läuft“, „Erfolgreich“, „Fehlgeschlagen“ oder (Sperrliste) „Abgelehnt“. Ohne
  dieses Recht wartet sie unter „Wartet auf Bestätigung“ auf jemanden, der es hat. Ausnahmen: „Alle Updates
  vorschlagen“ schlägt bewusst nur vor (die Vorschläge warten), beim Einspielen eines Image-Updates ist die Übersicht die
  Bestätigung, und „In Quarantäne“, „Ignorieren“ und „Wieder beachten“ in Nodvard Shield laufen ohne Aktion (nur im
  Protokoll).
- **Skripte ohne Klick:** Unabhängig davon kann ein aktives Skript mit Zeitplan eine Dauerfreigabe bekommen: auf der
  Skripte-Seite beim Skript den Schalter „Ohne Freigabe nach Zeitplan“ (nur Inhaber und `admin`). Vorher siehst du, für
  welche Server, unter welchem Konto und unter welcher Adresse sie gilt. Jede Änderung an Inhalt, Parametern, Ziel oder
  Zeitplan und ein anderes Konto, eine andere Adresse oder ein anderer SSH-Port eines dieser Server heben sie auf; neue
  Server in einer Gruppe oder unter „Alle Server“ fragen weiter nach. Ist die Person, die freigegeben hat, gesperrt oder
  hat sie die Rechte dafür nicht mehr, warten die Läufe ebenfalls wieder auf deinen Klick. Jeder Lauf steht weiter unter
  „Aktionen“ und im Protokoll, „Freigabe zurückziehen“ wirkt sofort.
- **Fehlgeschlagene Läufe nach Zeitplan:** Scheitert ein geplanter Skript-Lauf (auf dem Server mit einem Fehler, vom
  Dashboard abgelehnt oder gar nicht erst gestartet), steht er im Verlauf des Skripts als fehlgeschlagen, und du
  bekommst eine Meldung (Stufe Warnung, mit Link zur Skripte-Seite) – **einmal je Fehler**: Dieselbe Meldung kommt
  erst wieder, wenn sich der Fehler ändert oder das Skript zwischendurch durchgelaufen ist, nicht bei jedem Lauf.
  Der Text nennt die Server, aber nie die Ausgabe; die steht unter „Aktionen“. Wer ein Skript von Hand startet, sieht das
  Ergebnis selbst, dafür gibt es keine Meldung. Lief ein Skript nirgends, weil alle Server übersprungen wurden, sagt das
  schon die Meldung zu den übersprungenen Servern.
- **Gesperrte Befehle:** eigene Muster (reguläre Ausdrücke), die nie ausgeführt werden. Die
  gefährlichen Standardfälle sind immer gesperrt.
- **Wartungsfenster:** „Fenster hinzufügen“ → Beginn, Dauer, „Gilt für“ (Alle Server /
  Ausgewählte Server) → „Speichern“. Während des Fensters werden Push-Nachrichten zu diesen
  Servern stummgeschaltet; unter „Meldungen“ stehen sie trotzdem. Die Zeit gilt in der
  eingestellten Zeitzone (Einstellungen → System). Stummgeschaltet werden nur Meldungen, die
  zu bestimmten Servern gehören: die von **Proxmox-Backups**, **Gameserver**, dem **Proxmox-Wächter** (Knoten, Datenträger,
  Verbindung) und von **Nodvard Shield** (Updates, Härtungs-Audit, Einbruchschutz-Warnungen,
  Lagebericht der Container-Wache). Ein Bericht über mehrere Server bleibt hörbar, solange
  auch nur einer davon nicht im Fenster liegt.
  Immer hörbar bleiben **Schadsoftware-Funde** und **kritische Einbruchschutz-Ereignisse**
  (der Tiefenscan läuft ab Werk sonntags um 03:30, mitten im vorgeschlagenen Fenster), das
  **Morgen-Briefing** und die Meldungen von Zertifikatswächter und Skripten.
  Gut zu wissen: Fällt bei Proxmox-Backups, Gameserver oder Proxmox-Wächter im Fenster etwas aus
  und ist es nach dem Fenster immer noch so, kommt die Meldung **einmal als Push nach**,
  mit „(seit dem Wartungsfenster)“ im Titel. Das gilt auch für einen neuen Join-Code des
  Gameservers, der sich etwa bei einem nächtlichen Neustart ändert. Ist alles schon im Fenster
  wieder in Ordnung, kommt nichts nach; unter „Meldungen“ stehen dann Ausfall und
  Entwarnung still. Wird die Entwarnung erst nach dem Fenster bemerkt, kommt sie als Push
  mit dem Hinweis, dass die Störung im Wartungsfenster lag. Kam der Ausfall als Push und
  fällt nur das „wieder in Ordnung“ ins Fenster, bleibt es im Fenster still und kommt
  danach einmal als Push, damit die Meldung auf dem Handy nicht offen bleibt. Einbruchschutz-Warnungen von
  Nodvard Shield kommen nicht nach – sie bleiben als offene Ereignisse im Einbruchschutz stehen,
  und das Morgen-Briefing nennt sie.

**Wochenbericht** (Nodvard Shield): Im Reiter „Wochenbericht“ findest du die Berichte der letzten zwölf Wochen.
Ab Werk erstellt Shield jeden Montag um 08:00 Uhr einen Bericht für die ganze Installation, in der Zeitzone unter
Einstellungen → System. Unter Einstellungen → Erweiterungen → „Nodvard Shield“ kannst du den Wochenbericht
ausschalten oder seinen Zeitplan ändern.

Jeder Bericht hält den damaligen Stand je Server fest: offene Updates und Sicherheitsupdates, das letzte
Virenscan-Ergebnis, die zuletzt gespeicherten Fail2ban-Sperren, den letzten erfolgreichen Lynis-Härtungswert sowie
neue und erledigte Schadsoftware-Funde im Berichtszeitraum. Fehlende Ergebnisse stehen als unbekannt im Bericht.
Shield liest dafür nur seine gespeicherten Daten und startet weder neue Scans noch Server-Abfragen.
Zertifikatsabläufe sind bisher nicht in Shield gespeichert; dafür steht ausdrücklich „Keine gespeicherten
Zertifikatsdaten“ im Bericht. Daraus folgt keine Aussage, ob Zertifikate bald ablaufen.

Über deine vorhandenen Meldungskanäle kommt eine Kurzfassung mit Link zum vollständigen Bericht.
Ein alter Bericht verändert sich nicht, wenn später neue Ergebnisse eingehen. Vor dem ersten geplanten Lauf ist der
Verlauf leer; Berichte für frühere Wochen werden nicht nachträglich erzeugt.

**Automatische Updates** (Nodvard Shield): Einstellungen → Erweiterungen → „Nodvard Shield“ →
„Updates automatisch einspielen“ ist ab Werk **aus**, ebenso „Danach automatisch neu
starten, wenn nötig“. Erst einschalten, wenn die Update-Zentrale eine Weile sauber
gelaufen ist. Den Server, auf dem Nodvard Deck selbst läuft, nimmt die Runde als letzten; sein Neustart
kommt erst, nachdem die Zusammenfassung verschickt ist (mit einer eigenen kurzen Meldung).
Einzelne Server kannst du vom automatischen Neustart ausnehmen: Direkt unter „Danach automatisch neu starten, wenn
nötig“ wählst du bei „Diese Server nie automatisch neu starten“ die Server aus (zum Beispiel ein NAS oder einen
Spieleserver). Sie bekommen ihre Updates trotzdem, nur der Neustart entfällt. Braucht so ein Server danach einen
Neustart, sagt das die Zusammenfassung („Neustart nötig, für diesen Server aber ausgeschaltet – bitte selbst neu
starten“), und du startest ihn selbst neu. Die Update-Zentrale nennt die Ausnahmen am Abzeichen („Neustart danach:
automatisch, außer …“). Steht der Server von Nodvard Deck selbst in der Liste, startet auch er nicht neu.
Ist das letzte Update auf einem Server fehlgeschlagen, abgebrochen oder noch nicht fertig, startet Deck ihn ebenfalls
nicht neu, auch in den folgenden Nächten nicht: Halb eingerichtete Pakete und ein Neustart vertragen sich schlecht. Die
Zusammenfassung sagt dann „Neustart nötig, aber ausgelassen“. Als Warnung, also im Cockpit unter „Braucht
Aufmerksamkeit“, kommt sie nur einmal je gescheitertem Lauf; in den Nächten danach steht der Satz weiter in der
Zusammenfassung, aber als Info. Ist der Lauf fehlgeschlagen, zeigt auch die Übersicht von Nodvard Shield dort „Neustart wartet auf ein
sauberes Update“, bis ein Lauf wieder klappt. Sieh in der Update-Zentrale nach, was schiefging; nach dem nächsten
sauberen Lauf startet Deck wieder selbst neu. Ist nichts mehr einzuspielen, starte den Server selbst neu.

**Alle Updates auf einmal vorschlagen:** In der Update-Zentrale legen „Alle Sicherheitsupdates
vorschlagen“ bzw. „Alle Updates vorschlagen“ für jeden passenden Server einen Vorschlag an; vorher
nennt eine Rückfrage die Server. Server mit einem offenen Update-Vorschlag oder einem laufenden
Einspielen werden übersprungen. Freigegeben wird danach unter „Aktionen“, gesammelt mit „Ausgewählte
freigeben“; Vorschläge mit hohem Risiko (bei „Proxmox: Alle Updates als dist-upgrade“) einzeln. Steht
die Selbstständigkeit auf „Selbstständig handeln“, laufen die Vorschläge bis zur eingestellten
Risikostufe ohne Rückfrage an.

**Durch Phasing verzögerte Updates (Ubuntu):** Ubuntu verteilt manche Updates schrittweise, erst an einen Teil der
Rechner, nach und nach an alle. Bis ein Server an der Reihe ist, hält apt das Update zurück, auch bei „Alle einspielen“.
Die Update-Zentrale zählt solche Updates nicht als einspielbar und schreibt dazu „X durch Phasing verzögert“; ein Klick darauf erklärt
es, in der Paketliste stehen sie als „verzögert“. Das ist gewollt und kein Fehler: Sobald der Server an der Reihe ist,
lassen sie sich wie jedes andere Update einspielen. Erkannt wird das ohne root und unabhängig von der Sprache des Servers; auf Debian und
Proxmox gibt es kein Phasing, dort fehlt der Hinweis. Stammt der angezeigte Stand noch von einer Version ohne diese
Prüfung, steht dort „Phasing noch nicht geprüft“, bis „Prüfen“ oder die Tagesprüfung ihn erneuert. Kann apt die
Probe-Installation nicht durchrechnen (etwa bei fehlenden Abhängigkeiten), steht dort „Phasing-Prüfung nicht verfügbar“
statt einer Zahl.

**Zurückgehaltene und gesperrte Updates:** „Alle einspielen“ spielt auch Updates mit neuen Abhängigkeiten ein, entfernt
aber nie ein Paket. Braucht ein Update das (etwa wenn ein Paket ein anderes ersetzt, wie bei den Kamera-Paketen von
Raspberry Pi OS), hält apt es zurück. Die Update-Zentrale zeigt solche Updates als „zurückgehalten, braucht full-upgrade“
und zählt sie nicht als einspielbar. Einspielen lassen sie sich auf dem Server mit `sudo apt full-upgrade`; lies vorher die
Liste, die apt zeigt, denn was dort unter „REMOVED“ (deutsch „ENTFERNT“) steht, wird entfernt. Auf Proxmox mit der Einstellung für
`dist-upgrade` kommen sie mit „Alle einspielen“ mit. Mit `apt-mark hold` gesperrte Pakete stehen als „gesperrt“ da;
`sudo apt-mark unhold <Paketname>` hebt die Sperre auf. „Sicherheitsupdates einspielen“ lässt alle drei Arten aus, auch
die durch Phasing verzögerten: Gezielt eingespielt würde ein zurückgehaltenes Sicherheitsupdate ein anderes Paket
entfernen, ein gesperrtes den ganzen Lauf abbrechen und ein verzögertes das Phasing übergehen. Vor dem Lauf prüft die
Update-Zentrale den Server dafür noch einmal. Steht dort „Phasing-Prüfung nicht verfügbar“, ist unbekannt, was apt
zurückhält; „Sicherheitsupdates einspielen“ gibt es dann nicht, bis die nächste Prüfung wieder durchkommt. Ist nichts
einspielbar und sind mehrere Gründe gemischt, steht im Abzeichen „nicht einspielbar“, daneben jeder Grund einzeln.

**Updates auf dem Server, auf dem Nodvard Deck läuft:** Ist unter den Updates ein Paket, das Docker neu startet
(`docker-ce`, `docker.io`, `containerd` und ähnliche), sieht die Prüfung zusätzlich nach, ob dort Nodvard Deck läuft.
Die Rückfrage vor dem Einspielen sagt es dann: Nodvard Deck ist danach kurz nicht erreichbar und lädt von selbst neu.
Das Einspielen läuft auf dem Server weiter, auch wenn Nodvard Deck dabei neu startet. Die Seite merkt sich den Lauf und
zeigt sein Ergebnis, sobald er fertig ist, auch nach dem Neuladen. Unter „Aktionen“ steht die Aktion dann als „durch
Neustart unterbrochen“; was wirklich passiert ist, steht im Verlauf der Update-Zentrale und in der Push-Nachricht.
Ist Nodvard Deck gerade nicht erreichbar, sagt der Reiter das und versucht es alle paar Sekunden selbst wieder.

## I. Updates

### I.1 Neue Version finden und einspielen

<a id="105-neue-version-finden-und-einspielen"></a>

Unter **Einstellungen → System → „Updates“** steht, welche Version läuft und welche die neueste ist. Nodvard Deck sieht dazu **einmal am Tag**
bei ghcr.io nach, wo die fertigen Images liegen; mit **„Jetzt suchen“** geht es sofort (höchstens einmal pro Minute).

- **Datenschutz:** ghcr.io gehört zu **GitHub**, ebenso github.com, wo die Suche die Freigabe einer Version abholt (Downloads über
  `objects.githubusercontent.com` bzw. `release-assets.githubusercontent.com`; wer filtert, lässt diese Adressen durch). Bei jeder Suche
  sieht GitHub die IP-Adresse deines Anschlusses und den Zeitpunkt; sonst wird
  nichts übertragen, auch nicht die installierte Version. **Du kannst die Suche abschalten:** „Täglich automatisch nach Updates suchen“ aus
  (Recht `settings.write`); dann fragt das Dashboard von sich aus nie nach. „Jetzt suchen“ fragt auch bei ausgeschaltetem Schalter nach – das
  kann jeder mit dem Recht `system.read`, und auch diese Anfrage sieht GitHub.
- **Welche Versionen:** „Nur fertige Versionen“ (Vorgabe) oder „Auch Vorabversionen (Beta)“. Vorabversionen (z. B. `0.7.0-rc1`) gibt es nicht
  unter `:latest`: Zum Einspielen trägst du bei `image:` genau `ghcr.io/nodvard/deck:0.7.0-rc1` ein.
- **Ohne Internet** steht dort „Konnte nicht prüfen (offline?)“ und der letzte bekannte Stand; das ist kein Fehler. „Keine gefunden“ heißt:
  Die Abfrage hat geklappt, aber im gewählten Kanal gibt es noch keine Version.
- **Einspielen** macht ohne Update-Helfer nicht das Dashboard selbst, sondern deine Umgebung (mit Helfer per Knopf, siehe
  [Anhang I.2](#i2-update-per-knopf-update-helfer)). Die Karte hat Anleitungen als Reiter: Docker Compose, Docker Desktop,
  Portainer („Update the stack“ mit „Re-pull image“), Synology Container Manager, Unraid („Check for Updates“ → „Apply update“) und
  `scripts/deploy_pi.sh`. Mit Compose (Installation wie in [Abschnitt 1](#1-schnellstart)) im Ordner mit der `compose.yml`:

  ```bash
  docker compose pull
  docker compose up -d
  ```

  Unter Linux ggf. `sudo` davor. Heißt deine Datei anders (z. B. `compose.standalone.yml` aus einer älteren Anleitung), häng `-f` und den
  Dateinamen an (`docker compose -f compose.standalone.yml pull`). Steht bei
  `image:` eine feste Version (z. B. `:0.5.0`) oder eine Reihe wie `:0.5` statt `:latest`, trag dort zuerst die neue Version ein.
- **Zurück zur Vorversion:** Notiere dir vor dem Update die jetzige Version (steht in der Karte). Zum Zurückgehen trägst du sie wieder bei
  `image:` ein und startest neu (`docker compose up -d`). Vor einem Umbau der Datenbank legt Nodvard Deck beim Start
  automatisch eine Kopie an (nur bei SQLite, der Standard-Datenbank). Hat die neue Version noch nie richtig gestartet, spielt die alte diese
  Kopie von selbst wieder ein. Lief die neue Version schon und hat sie dabei die Datenbank umgebaut, zeigt die alte eine **Notseite**: Dort mit
  dem Notfallcode (steht im Protokoll des Containers) „Stand vor dem Update wiederherstellen“ wählen; was seit dem Update geändert wurde, geht
  dabei verloren. Oder wieder die neue Version eintragen. Einzelheiten in [Anhang I.3](#i3-die-kopie-vor-der-migration) und
  [Anhang K](#k-nodvard-deck-startet-nicht-die-notseite).
- Läuft ein **selbst gebautes** Image (z. B. vom PC über `scripts/deploy_pi.sh`), sagt die Karte das; aktualisiert wird dann auf demselben Weg.
- **Nach dem Update auf 0.13.0 beachten:**
  1. **Neu anmelden:** Die Weboberfläche nutzt nur noch das Cookie von Nodvard Deck. Wer von einer Version vor 0.6.2 aktualisiert oder Deck seitdem nicht im Browser neu geladen hat, meldet sich nach dem Start einmal neu an.
  2. **Server-Einrichtung einmal wiederholen:** Führe den Einrichtungsbefehl auf jedem Server unter **Einstellungen → Server & Zugänge** einmal neu aus, damit sudo die Hilfsskripte mit Prüfsumme absichert. Auf Rechnern mit `sudo-rs` (ab Ubuntu 25.10) greift die Prüfsumme nicht.

### I.2 Update per Knopf (Update-Helfer)

<a id="106-update-per-knopf-update-helfer"></a>

Mit dem **Update-Helfer** spielst du Updates unter Einstellungen → System → „Updates“ per Knopf ein und kannst danach
7 Tage lang einmal per Knopf zurück. Der Helfer ist ein kleiner eigener Dienst in derselben Compose-Datei, den du bewusst
einschaltest. Er hat Zugriff auf Docker, das ist so viel wie root auf diesem Rechner. Einrichten, aktualisieren und
entfernen: [deploy/README.md, Abschnitt „Update-Helfer“](../deploy/README.md#update-helfer).

- **Zustand:** Die Karte „Updates“ zeigt „Update-Helfer: nicht eingerichtet“ (mit Link zur Anleitung), „bereit“ (mit
  Version und letztem Lebenszeichen), „nicht bereit“ (mit dem Grund und was hilft) oder „antwortet nicht“.
- **Voraussetzungen für den Knopf:** Helfer bereit, eine neuere fertige Version ist da, es läuft das offizielle Image
  `ghcr.io/nodvard/deck` mit `:latest` oder einer passenden Reihe wie `:0.7` (keine feste Version), und du bist Inhaber
  der Installation. Der Helfer nimmt erst Versionen ab 0.7.0 an; das erste Update per Knopf geht also von 0.7.0 auf die
  Version danach. Fehlt etwas, sagt die Karte, warum, wo es hilft.
- **Freigabe:** Per Knopf kommt nur eine Version, die am Release freigegeben ist (signiert mit dem Schlüssel des
  Projekts); bis dahin sagt die Karte „Für Version X liegt noch keine gültige Freigabe vor …“, von Hand geht das Update
  trotzdem. Ein aktueller Helfer prüft die Signatur selbst, die Karte zeigt dann „Freigabe liegt vor (Schlüssel …)“. Bei
  einem älteren Helfer bittet sie, ihn zu erneuern.
- **Update:** „Jetzt aktualisieren auf X“, dann Passwort und, wenn Zwei-Faktor an ist, den Code aus der App eingeben.
  Baut die neue Version die Datenbank um, legt Nodvard Deck vorher wie immer eine Kopie an ([Anhang I.3](#i3-die-kopie-vor-der-migration)).
  Die Karte zeigt die Schritte; du kannst die Seite dabei schließen, der Helfer arbeitet ohne sie weiter. Beim Umschalten
  ist das Dashboard ein paar Minuten weg; die Seite fragt weiter nach und lädt nach dem Erfolg neu. Dauert es länger als
  30 Minuten (langsamer Download), fragt die Karte weiter nach und zeigt das Ergebnis, sobald es da ist.
- **Klappt es nicht** (die neue Version beendet sich, startet immer wieder neu, zeigt die Notseite oder ist nach 15
  Minuten nicht bereit), schaltet der Helfer von selbst auf die alte Version zurück. Die Karte sagt, warum und was du tun
  kannst; wichtige Ergebnisse kommen zusätzlich als Meldung.
- **Rückweg:** In der Karte „Kopien vor Updates“ steht 7 Tage lang „Zurück zu Version X“, einmal. Hat die neue Version
  die Datenbank umgebaut, gehen die Daten mit zurück: Die Karte nennt vorher den Zeitpunkt, seit dem alles verloren geht,
  und verlangt ein Häkchen. Der ersetzte Stand bleibt 30 Tage unter `restore/replaced-…`. Ist unklar, was mit den Daten
  passiert, gibt es keinen Knopf, nur die Erklärung. Nach einem Rückweg spielt der Helfer die verlassene Version 24 Stunden
  lang nicht wieder ein; die Karte sagt, ab wann es wieder geht. Ist ein Rückweg gescheitert, der die Daten mitnehmen
  sollte, steht in der Meldung (Glocke), ob die Datenbank schon zurückgesetzt war.
- Update und Rückweg zusammen gehen höchstens einmal in 10 Minuten. Ohne Helfer gelten die Anleitungen aus
  [Anhang I.1](#i1-neue-version-finden-und-einspielen).

### I.3 Die Kopie vor der Migration

<a id="104-updates-die-kopie-vor-der-migration"></a>

Vor jedem Update, das die Datenbank umbaut (eine **Migration**), legt Nodvard Deck beim Start **automatisch eine Kopie der Datenbank** an:
`backups/vor-update/<Zeit>_<von>_<nach>.db` im Datenordner. Die **drei neuesten** bleiben. Zu sehen unter **Einstellungen → System → „Kopien vor Updates“**.

- **Ohne Kopie keine Migration.** Reicht der Platz nicht (nötig ist gut das 1,2-fache der Datenbank), startet Nodvard Deck nicht, sondern zeigt die
  Notseite mit der Zahl, wie viel fehlt (siehe [Anhang K](#k-nodvard-deck-startet-nicht-die-notseite)). Platz schaffen, „Neu versuchen“.
- **Klappt die Migration nicht,** setzt Nodvard Deck die Datenbank von selbst auf die Kopie zurück. Danach läuft die Notseite; die Daten sind wie vor dem Update.
  Der halbe Stand wird dabei verworfen; er enthält nichts, was nicht auch in der Kopie steht.
- **Bricht die Migration mittendrin ab** (Strom weg, Container neu gestartet), setzt der nächste Start die Datenbank zuerst auf die Kopie zurück und migriert dann neu.
  Der halbe Stand bleibt dabei 30 Tage unter `restore/replaced-…` liegen (verworfen wird er nur, wenn die Datenbank auf einem eigenen Laufwerk liegt und im
  Datenordner der Platz dafür fehlt). Bricht die **allererste** Migration einer neuen Installation ab
  (noch ohne Konto), legt der nächste Start den halben Stand dort ab und beginnt mit einer frischen Datenbank.
- **Zurück auf die alte Version** (Image-Version in der Compose-Datei zurückstellen, Container neu starten):
  - Hat die neue Version **nie erfolgreich gestartet**, spielt die alte die Kopie **selbst** wieder ein. Es gingen ja keine Daten verloren; die neueren Daten
    bleiben trotzdem 30 Tage unter `restore/replaced-…` liegen (neuere Daten werden beim Zurücksetzen nie gelöscht).
  - Hat die neue Version **schon gearbeitet**, zeigt die alte die Notseite („Die Daten sind neuer als diese Version“). Mit dem Notfallcode lässt sich dort
    **„Stand vor dem Update wiederherstellen“** wählen: Alles seit dem Update geht verloren (die neueren Daten bleiben 30 Tage unter `restore/replaced-…`).
- **Der Rückweg funktioniert erst ab einer Version mit dieser Funktion.** Eine alte Version (bis 0.5.x) kann eine Kopie nicht selbst einspielen und kennt die Notseite nicht. Vorversion und
  neue Version müssen also beide mindestens die Version mit „Kopie vor jeder Migration“ sein, damit der Weg zurück offen ist. Das erste Update **auf** diese Version legt die Kopie schon an.
- **Nicht in der Kopie** sind Dateien außerhalb der Datenbank (Daten der Erweiterungen, Schlüssel). Migrationen fassen sie nicht an. Für alles andere gibt es die Sicherung ([6.1](#61-in-der-oberfläche-empfohlen)).
- **Notausgang** (nicht empfohlen): `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1` in der Compose-Datei schaltet die Kopie ab, damit auch bei zu wenig Platz migriert wird. Scheitert die Migration dann, gibt es keinen Rückweg.
- Was beiseitegelegt wurde (`restore/replaced-…`: der alte Stand einer Wiederherstellung oder eine Datenbank, die ein Rückweg ersetzt hat), wird nach 30 Tagen automatisch gelöscht.
- **Datenbank auf einem eigenen Laufwerk** (eigener `NODVARD_DECK_DATABASE_URL`, z. B. eine USB-SSD): Zum Beiseitelegen wird sie dann in den Datenordner kopiert.
  Dafür muss dort so viel Platz frei sein, wie die Datenbank groß ist; sonst zeigt die Notseite „zu wenig Platz“ mit den Zahlen, und es wurde nichts verändert.
  Nur den halben Stand einer abgebrochenen Migration verwirft der Start in diesem Fall, statt auf der Notseite hängen zu bleiben.
- **Eine Sicherung einspielen, deren Migration abbricht** (Strom weg mitten drin): Der nächste Start nimmt das Einspielen zurück, und es gilt wieder genau der alte
  Stand mit seinen Schlüsseln. Er wird danach nie durch die Sicherung ersetzt.
- Bei einer anderen Datenbank als SQLite gibt es keine Kopie (bitte vorher selbst sichern).

### I.4 Was die Update-Zentrale meldet und was es heißt

<a id="was-die-update-zentrale-meldet-und-was-es-heisst"></a>
<a id="i4-was-die-update-zentrale-meldet-und-was-es-heisst"></a>

Unter **Einstellungen → System → „Updates“** zeigt die Karte den Zustand der Container-Aktualisierung von Nodvard Deck. Für Paket-Updates des Betriebssystems auf Servern ist das Modul Nodvard Shield zuständig (Reiter „Updates“). Die folgende Übersicht führt die belegten Zustände auf, erklärt ihre Bedeutung und nennt den nächsten Schritt:

| Meldung oder Zustand | Was es heißt | Nächster Schritt |
|---|---|---|
| **„keine gefunden“** oder **„Es gibt noch keine fertige Version zum Herunterladen.“** | Die Suche war erfolgreich, aber im gewählten Kanal (Stable oder Beta) gibt es keine passende Version, zum Beispiel wenn die Registry nur Vorabversionen wie `0.6.0-rc1` anbietet. Ein Abruffehler ist das nicht; der steht als eigene Meldung in der Karte. | Nichts zu tun: Die nächste fertige Version erscheint beim nächsten Suchlauf von selbst. Wer Vorabversionen will, stellt den Kanal auf Beta um. |
| **„Bei der letzten Update-Suche kam keine Prüfsumme für diese Version an. Such erneut nach Updates.“** | Die Registry hat keinen Digest (`latest_digest`) übermittelt, den der Update-Helfer zur kryptografischen Prüfung vor dem Einspielen verlangt. | Erneut auf **„Jetzt suchen“** klicken. Bleibt der Digest weiterhin aus, das Update manuell über die Anleitung (zum Beispiel per Docker Compose) einspielen. |
| **„Für Version X liegt noch keine gültige Freigabe vor, oder sie ließ sich bei der Suche nicht abrufen. Such später erneut nach Updates; von Hand geht es schon mit der Anleitung unten.“** | Der Abruf der Freigabe-Signatur von github.com ist fehlgeschlagen (Netzwerkfehler oder Zeitüberschreitung) oder die Freigabe am Release fehlt noch. | Netzwerkverbindung zu github.com prüfen. Bei fortdauerndem Fehler das Update manuell über Docker Compose durchführen. |
| **Sicherheitsupdate gefunden, aber Benachrichtigung bleibt aus** | Bei der Suche wurde ein Sicherheitsupdate erkannt, beim Erstellen oder Übermitteln der Benachrichtigung (`notify_security`) trat jedoch ein Fehler auf. Deck fängt die Ausnahme ab und protokolliert `security_notice_failed` im Server-Log. | Benachrichtigungskanäle in den Einstellungen kontrollieren und Server-Protokolle auf Zustellungsfehler prüfen. |
| **Paketmanager-Fehler auf dem Host (`dpkg returned an error code`, `apt lock`, fehlende Root-Rechte, Zeitüberschreitung)** | Diese Fehler betreffen Linux-Paketmanager auf den verbundenen Servern und treten in den Systemeinstellungen von Deck nicht auf. | Für Paket-Updates von Servern das Shield-Modul (Reiter „Updates“) nutzen. In den Deck-Systemeinstellungen werden ausschließlich Container-Images von Nodvard Deck selbst aktualisiert. |

## J. Wenn etwas hakt

<a id="11-wenn-etwas-hakt"></a>

Die häufigsten Hürden beim Start (Dashboard nicht erreichbar, Server verbindet sich nicht, ausgesperrt) stehen in
[Abschnitt 7](#7-typische-stolpersteine). Hier steht der Rest.

**„HTTP 401“ oder „Nicht authentifiziert“ auf einer Erweiterungsseite** (Proxmox, Proxmox-Backups,
Skripte …): Im sichtbaren Tab erneuert Nodvard Deck die Anmeldung selbst. Erweiterungsseiten, Herunter- und
Hochladen auf der Dateien-Seite, Sicherung hochladen und der Export des Protokolls erneuern sie bei einer
abgelaufenen Anmeldung auch selbst und versuchen es einmal neu. Kommt die Meldung trotzdem, Seite neu laden (F5).

**Irgendwo „HTTP 500“:** Log ansehen (im Ordner mit der Compose-Datei), dort steht der Grund:

```bash
cd ~/nodvard-deck && sudo docker compose logs --since 15m nodvard-deck | grep -iE -A5 "error|traceback"
```

**„Seite nicht gefunden“:** Die Adresse gibt es nicht (mehr), etwa ein altes Lesezeichen; „Zur Übersicht“ führt ins
Cockpit. **„Die Seite konnte nicht geladen werden“** kommt meist direkt nach einem Update: Seite neu laden (F5). Bei
**„Hier ist etwas schiefgegangen“** ebenfalls neu laden; bleibt es, nennt „Technische Einzelheiten“ den Fehler, und
das Log (siehe oben) hilft weiter.

**Proxmox nicht erreichbar** (Push „Proxmox 'pve1' nicht erreichbar“, Kacheln mit „nicht
erreichbar“ oder „Nicht abrufbar: …“) – die Fehlermeldung verrät meist den Grund:

- „Proxmox ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin). Läuft der Server, und stimmen Adresse und
  Port?“ oder „Proxmox antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port.“: Server aus, falsche Adresse
  oder Port. Die Adresse braucht `https://` und `:8006`.
- „Das Zertifikat von Proxmox wird nicht akzeptiert …“: Fingerabdruck noch nicht gemerkt – Seite „Proxmox“, Reiter
  „Verbindungen“ → „Zertifikat“ ([docs/10](10-PROXMOX-TOKEN.md#zertifikat-fingerabdruck-merken)).
- „Der Server zeigt ein anderes Zertifikat als das gemerkte …“: Das Zertifikat in Proxmox hat sich geändert. Neuen
  Fingerabdruck vergleichen und im Reiter „Verbindungen“ → „Zertifikat“ bestätigen – nur, wenn du den Wechsel
  selbst kennst.
- „HTTP 401: Proxmox hat den Zugang abgelehnt. Prüfe Token-ID und Geheimnis in den Einstellungen.“: Token-ID oder
  Geheimnis falsch, oder Token in Proxmox gelöscht.
- „HTTP 403: Dem Token fehlt ein Recht für diese Abfrage. Grund laut Proxmox: Permission check failed (…)“: Recht
  fehlt – Tabelle in [docs/10](10-PROXMOX-TOKEN.md#nachschlagen-welcher-aufruf-welches-recht-braucht). Den Zusatz
  „Grund laut Proxmox“ gibt es nur, wenn Proxmox einen Grund nennt. „Verbindung testen“ sagt dazu allgemein „Zugriff
  verweigert – dem Konto oder Token fehlt ein Recht auf dem Server …“. Fehlen einzelne VMs ganz, fehlt ihnen
  `VM.Audit`.
- „Der Server hat die Verbindung auf eine andere Adresse umgeleitet …“: Meist steht `http://` statt `https://` in der
  Adresse, oder ein Proxy davor leitet um. Nodvard Deck folgt Weiterleitungen nicht – die Adresse des Knotens direkt
  eintragen: `https://<adresse>:8006`.
- „Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?“: Unter der Adresse antwortet
  etwas anderes als die Proxmox-API, etwa die Anmeldeseite eines Proxys. Adresse prüfen. „Verbindung testen“ zeigt
  beide Sätze genauso an.
- Testen vom Rechner mit Nodvard Deck aus: der `curl`-Befehl in
  [docs/10](10-PROXMOX-TOKEN.md#prüfen-bevor-du-es-in-nodvard-deck-einträgst).
- Ein ausgefallener Proxmox (z. B. pve2 aus) bremst die anderen nicht mehr, er steht nur
  als „nicht erreichbar“ da. Auf der Startseite zählt sein Knoten dann nicht als online: beim ersten Abgleich ohne
  Antwort steht er als „unbekannt“ da (ein kurzer Neustart bleibt so ohne Fehlalarm). Als „nicht erreichbar“ gilt er
  erst, wenn er mindestens 4 Minuten am Stück bei keinem Abgleich antwortet, in der Regel also beim nächsten geplanten
  Abgleich (nach etwa 5 bis 10 Minuten); ein von Hand gestarteter Abgleich verkürzt diese Frist nicht. Eine Antwort
  oder eine Ablehnung dazwischen beginnt sie neu. Steht hinter der Verbindung ein Cluster oder lehnt Proxmox nur ab
  (etwa ein abgelaufenes Token), bleiben die Knoten „unbekannt“, denn die Maschinen können noch laufen. Seine VMs und
  Container stehen als „unbekannt“ da, bis er wieder antwortet. Ist er länger aus: auf der Seite „Proxmox“ (Reiter
  „Verbindungen“) und „Proxmox-Backups“ (unter „Verbindungen verwalten“) auf „deaktiviert“ schalten, dann kommen auch
  keine Meldungen; seine Server stehen danach als „unbekannt“ da, bis du die Verbindung wieder einschaltest.

**„… leitet auf eine andere Adresse um“** (Pi-hole, Nginx Proxy Manager, ntfy, Nextcloud): Der Server hat
statt der Antwort eine Weiterleitung geschickt (HTTP 301, 302, 303, 307 oder 308). Nodvard Deck folgt dem nie, weil sonst
Passwort oder Token an eine Adresse gingen, die niemand eingetragen hat, und nennt auch die Zieladresse nicht. Meist
steht in den Einstellungen `http://` statt `https://` (oder umgekehrt), oder ein Proxy davor leitet um. Abhilfe: in den
Einstellungen die endgültige Adresse eintragen, so wie sie im Browser nach der Weiterleitung steht. Andere Fehler der
Nextcloud erscheinen als fester Satz, etwa „Nextcloud ist gerade nicht bereit (HTTP 503), zum Beispiel im
Wartungsmodus.“ oder „In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507).“

**Git-Server im eigenen Netz wird abgewiesen oder Git-Stack schlägt fehl:** Deck lädt Stacks aus Git nur von öffentlichen Adressen. Für lokale Server (wie Gitea im Heimnetz) musst du unter **Einstellungen → Erweiterungen → Docker → Erweitert** den Schalter **„Git-Server im eigenen Netz erlauben“** aktivieren. Läuft Deck hinter einem Proxy (`HTTPS_PROXY`), dürfen Git-Server mit Domainnamen nicht über den Proxy laufen; trage ihren Namen in die Umgebungsvariable `NO_PROXY` ein.

**„Der Zeitplan „…“ ist ungültig“** beim Speichern (Einstellungen einer Erweiterung, Skripte): Ein eigener Zeitplan
(Auswahl „Eigener“) braucht genau fünf Angaben: Minute (0–59), Stunde (0–23), Tag (1–31), Monat (1–12) und Wochentag
(0–7), zum Beispiel `0 2 * * *` für täglich um 2 Uhr. Kürzel wie `@daily` gehen nicht. Stammt ein kaputter Zeitplan
noch aus einer älteren Version, läuft Nodvard Shield mit seinem Standard-Zeitplan weiter bzw. das Skript vorerst nicht
nach Zeitplan; eine Meldung „Zeitplan … ist ungültig“ sagt, welcher es ist.

**Backup fehlgeschlagen** (Proxmox-Backups): Klapp auf der Seite „Proxmox-Backups“ beim Job den „Verlauf“ auf. Oben
steht in zwei Sätzen, was passiert ist und was du tun kannst, darunter die Zeile, die Proxmox geschrieben hat. Bekannt
sind die häufigsten Gründe: Ziel voll, Verbindung zum Netzlaufwerk abgerissen, Ziel nicht eingehängt, Gast gesperrt,
Backup-Server lehnt die Anmeldung ab, Gast-Agent antwortet nicht, fehlende Rechte (auf dem Ziel oder in Proxmox),
Datenträgerfehler, Gast gelöscht, Lauf abgebrochen, Ziel nicht erreichbar. Bei einem Job für mehrere Gäste steht auch da,
wenn dieser Gast gesichert wurde und nur ein anderer fehlschlug. Bei einem älteren Fehlschlag zeigt „Warum?“ neben dem Lauf seinen Grund. Die
Meldung des Backup-Wächters nennt dieselben zwei Sätze, die Zeile von Proxmox aber nicht (sie kann Pfade und Adressen
enthalten). Ohne das Recht `Sys.Audit` liest Nodvard Deck das Protokoll des Laufs nicht und erklärt nur dessen
Ergebnis. Ein Lauf mit Warnungen („WARNINGS“ in Proxmox) gilt als gelungen, die Sicherung ist da; im Verlauf steht „OK,
mit Warnungen“.

**Virenscan von Nodvard Shield fehlgeschlagen:** Die Liste der Scans nennt einen verständlichen Grund: ob der Server
nicht erreichbar war, die Verbindung mitten im Scan abgebrochen ist, der Scan sein Zeitlimit überschritten hat (mit
Dauer) oder welche Meldung ClamAV geschrieben hat, etwa „Input/output error“ bei einer defekten Platte. Wurden neue
Dateien vor dem Scan des Echtzeit-Wächters wieder gelöscht, gilt das nicht als Fehler. Läuft auf einem Server gerade ein
Scan, setzt der Wächter still aus und prüft beim nächsten Lauf die ausgelassene Zeit mit (bis zu 6 Stunden zurück); kann
er länger nicht prüfen, steht das als Fehler in der Liste. Ein Scan wartet bis zu 6 Minuten auf einen laufenden
Wächter, und ein hängender Lauf wird vom Server kurz vor dem Zeitlimit beendet. Das braucht auf dem Server `flock` und
`timeout` (util-linux, coreutils); fehlen sie, läuft alles wie früher.

**Ordner vom Virenscan ausnehmen** (Nodvard Shield, etwa ein großes Medienarchiv, das den Scan bremst): Einstellungen →
Erweiterungen → „Nodvard Shield“ → „Erweitert“ → „Ausgenommene Ordner“, dort volle Pfade wie `/srv/medien`. Sie gelten
für den Schnell- und den Tiefenscan **und** für den Echtzeit-Wächter, der dort weder nach neuen Dateien sucht noch etwas
prüft. Ein Eintrag gilt genau für diesen Ordner samt Inhalt: `/srv/backup` nimmt nicht nebenbei `/srv/backups-wichtig`
aus, und der eingebaute Ausschluss von `/dev` trifft nicht `/devel`. Platzhalter wie `*` gibt es nicht, sie zählen als
normale Zeichen; nur ein `/*` oder `/**` am Ende ist erlaubt und meint denselben Ordner (`/srv/backup/*` wirkt wie
`/srv/backup`). Doppelte Schrägstriche, `/./` und ein Schrägstrich am Ende fallen weg. Einträge, die nicht gelten,
werden übergangen: Pfade, die nicht mit `/` beginnen, Pfade mit einem Zeilenumbruch, Pfade mit `..` (der Eintrag
würde sonst einen ganz anderen Ordner treffen) und `/` allein; beim Virenscan außerdem Pfade über 511 Bytes. Ist ein
überwachter Ordner selbst ein Link (etwa `/home` → `/data/home`), trag den Ausschluss mit dem Pfad
darunter ein (`/home/…`), nicht mit dem Ziel des Links. Liegt ein ausgenommener Ordner über einem geprüften Ordner,
fällt dessen Prüfung ganz weg.

**Freigaben und Nextcloud vom Echtzeit-Wächter prüfen lassen** (Nodvard Shield → Reiter „Scans“ → „Wächter-Vorlagen“):
Der Wächter prüft von sich aus nur Orte, an denen Schadsoftware typischerweise abgelegt wird (`/tmp`, `/home` …).
Was andere hochladen, landet oft woanders. „Auf den Servern suchen“ liest auf jedem prüfbaren Server die Samba-Freigaben
(`path =` bzw. `directory =` in `/etc/samba/smb.conf`, ohne Drucker und `[homes]`), die NFS-Freigaben (`/etc/exports`,
auch mit Anführungszeichen oder `\040`) und den Datenordner einer Nextcloud (aus ihrer `config.php` oder ein
Docker-Volume mit „nextcloud“ im Namen, auch Nextcloud AIO) und ändert dabei nichts. Ohne root bzw. `sudo -n` fehlt
meist die Nextcloud. „Zum Wächter hinzufügen“ hängt den Ordner an „Überwachte Ordner“ an (braucht das Recht,
Einstellungen zu ändern; steht im Protokoll). Die Liste gilt für alle Server, auf einem Server ohne diesen Ordner kostet
sie nichts. Systemordner (`/etc`, `/var` …, `/proc`, `/sys`, `/dev`, `/run`, `/boot` samt allem darunter) und
Samba-Platzhalter wie `%U` werden nicht vorgeschlagen. Liegt ein Fund in einem „ausgenommenen Ordner“, steht er als
„ausgenommen“ da und lässt sich nicht hinzufügen (dort prüft der Wächter nicht). Ein sehr großer Ordner kostet bei jedem
Wächterlauf etwas Zeit; entfernen geht in den Einstellungen unter „Überwachte Ordner“. Der Wächter prüft neue und
geänderte Dateien, auch solche, die mit alter Änderungszeit hineinkopiert wurden (Samba, Nextcloud-Sync, `cp -p`,
`rsync -a`): Er schaut außer auf die Änderungszeit auch darauf, wann die Datei dort angelegt wurde.

**„Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal.“ bei der
Anmeldung:** Nodvard Deck prüft nur wenige Passwörter gleichzeitig, damit viele Anmeldeversuche auf einmal das Dashboard
nicht ausbremsen. Ein paar Sekunden warten und noch einmal versuchen; wer schon angemeldet ist, arbeitet normal weiter.
Kommt die Meldung öfter, gibt es sehr viele Anmeldeversuche auf einmal; fehlgeschlagene stehen im Protokoll
(„Anmeldung fehlgeschlagen“). Bei einem Namen, den es nicht gibt, steht dort unter „Wer“ nur
„Nicht angemeldet · unbekannt“, nie die Eingabe selbst (es könnte ein ins Namensfeld getipptes Passwort sein). Nur der
Inhaber sieht beim Aufklappen der Zeile eine Kennung (`username_ref`) und die Länge der Eingabe; gleiche Eingaben haben
dieselbe Kennung.

## K. Nodvard Deck startet nicht: die Notseite

<a id="13-nodvard-deck-startet-nicht-die-notseite"></a>

Scheitert der Start (die Migration, zu wenig Platz für die Kopie, Daten neuer als die Version, ein unerwarteter Fehler), startet Nodvard Deck **nicht**, sondern eine kleine **Notseite**
auf demselben Port (z. B. `http://192.168.1.10:8080`). Sie braucht nichts außer Python selbst und läuft deshalb auch, wenn im neuen Image etwas fehlt. Der Container bleibt oben und
startet **nicht endlos neu**. Sie lauscht auf derselben Adresse wie die Anwendung (`--host` im Startbefehl, sonst `UVICORN_HOST`); ist keine erkennbar (kein `--host`, ein
Hostname wie `localhost`), nur lokal auf `127.0.0.1` – so wie uvicorn selbst. Das Image startet mit `--host 0.0.0.0`, im Normalfall ist sie also im Heimnetz erreichbar.

1. **Ohne Code** zeigt die Seite nur, dass Nodvard Deck nicht gestartet ist, und die allgemeinen Wege zurück.
2. **Notfallcode holen:** Er steht im Protokoll des Containers, als Zeile „Notfallcode: XXXX-XXXX-XXXX“ in einem auffälligen Block, und in der Datei `.boot/rescue_code.txt` im Datenordner:

   ```bash
   cd ~/nodvard-deck && sudo docker compose logs nodvard-deck | grep -A3 Notfallcode
   ```

   Er bleibt bis zum nächsten erfolgreichen Start gleich. Falsche Eingaben werden gebremst (5 pro Rechner, insgesamt 25 in 10 Minuten, danach „Zu viele Fehlversuche“). Der richtige Code geht trotzdem
   immer sofort durch: Du musst nicht warten, auch wenn vorher jemand anderes im Netz oft falsch getippt hat.
3. **Mit Code** erscheinen Grund und bereinigtes Protokoll (ohne Pfade, Passwörter, SQL-Parameter) und die Schritte, die zum Grund passen:
   - **Neu versuchen:** beendet den Container-Prozess; die Neustart-Regel (`restart: unless-stopped`) startet ihn neu, und der Start läuft noch einmal. Ohne Neustart-Regel bitte von Hand starten.
   - **Stand vor dem Update wiederherstellen** (nur wenn die Daten neuer sind und eine heile Kopie da ist): merkt den Rückweg vor und startet neu; beim Start wird die Kopie eingespielt.
     Änderungen seit dem Update gehen verloren.
   - **Zurück auf die alte Version:** Image-Version in der Compose-Datei zurückstellen und den Container neu starten (siehe [Anhang I.3](#i3-die-kopie-vor-der-migration)).
4. `GET /api/v1/health` antwortet auf der Notseite mit **503** `{"status":"rescue"}`; der Healthcheck des Containers zeigt deshalb „unhealthy“. Wer aus dem Repository mit `scripts/deploy_pi.sh`
   ausliefert, bekommt dadurch automatisch das alte Image zurück (ohne die volle Wartezeit).

Was die Notseite **nicht** kann: eine Kopie zum Herunterladen anbieten. Die Kopie liegt im Datenordner unter `backups/vor-update/` und lässt sich dort (oder per `docker cp`) sichern.
Fällt die Notseite selbst aus, wartet der Container, statt sich zu beenden – Ursache im Protokoll, dann von Hand neu starten.

Wichtig: Ein zweiter Container mit demselben Datenordner (`docker compose run` neben dem laufenden Dienst) ändert nichts; `nodvard_deck.boot` beendet sich dort mit Code 75, weil die laufende Anwendung (oder die Notseite)
die Sperre des Datenordners hält.

## L. Ohne Oberfläche

<a id="anhang-ohne-oberfläche"></a>

Normalerweise nimmst du die Seite „Server & Zugänge“ ([Abschnitt 3](#3-den-ersten-server-verbinden)). Ist die
Oberfläche nicht erreichbar (z. B. beim ersten Einrichten per SSH auf dem Rechner mit Nodvard Deck), geht alles auch
über die API mit einem kleinen Hilfsskript: Server anlegen, Adresse ändern, SSH-Zugang hinterlegen, Zugänge zeigen und
einen gemerkten Server-Schlüssel vergessen. Skript, Schlüssel und die Einrichtung des Servers von Hand:
**[Ohne Oberfläche](17-OHNE-OBERFLAECHE.md)**.

## M. Alte Namen bei älteren Installationen

**Übergangsübersicht bis 1.0:** Nodvard Deck hieß früher „Lattice“. Diese Tabelle bleibt für den Übergang bis 1.0
erhalten. Einige technische Namen verwendet der aktuelle Code weiterhin, auch für neue Update-Läufe und neue Dateien
in Quarantäne. Andere kommen nur auf älteren Installationen vor. Benenne diese Namen und Ordner nicht von Hand um,
weil bestehende Einrichtungen und der Weg zurück auf eine ältere Version daran hängen. Ausgenommen sind
`LATTICE_*`-Variablen in `.env`: Stelle sie wie in der Tabelle beschrieben auf `NODVARD_DECK_*` um.

| Name | Wo | Aktueller Stand und Übergang |
|---|---|---|
| Benutzer `lattice` | SSH-Zugänge, die eine ältere Version angelegt hat | Die Server sind so eingerichtet, die Zugänge laufen unverändert weiter. Für neue Schlüssel schlägt die Seite `nodvard` vor |
| Kommentar `lattice@…` | ältere Schlüssel in `~/.ssh/authorized_keys` | Der Einrichtungsbefehl erkennt ihn und trägt den Schlüssel nicht doppelt ein. Neue Schlüssel bekommen `nodvard@…` |
| `/etc/sudoers.d/lattice-<benutzer>` | ältere volle sudo-Regel auf Servern | Der Einrichtungsbefehl ersetzt sie durch `nodvard-<benutzer>`, wenn sie genau die früher von Nodvard Deck angelegte ist ([Root-Rechte](14-ROOT-RECHTE.md#die-volle-sudo-regel)) |
| systemd-Einheiten `lattice-upgrade-…` | Updates von Nodvard Shield auf deinen Servern | Weiterhin der aktuelle Name, auch für neue Läufe. Fortsetzen und Zurückrollen von Updates hängen daran |
| Ordner `/var/lib/nexus-updates` und `/var/lib/nexus-quarantine` | Updates und Quarantäne von Nodvard Shield auf deinen Servern | Weiterhin die aktuellen Pfade. Die Pfade stehen in gespeicherten Funden, und laufende Updates werden über den Ordner fortgesetzt |
| Datenbankdatei `lattice.db` (in Sicherungen `db/lattice.db`) | Datenordner | Umbenennen bräuchte eine Migration; nach einem Rückweg öffnete ein älteres Image sonst eine leere Datenbank |
| `LATTICE_PORT` und die anderen `LATTICE_*`-Variablen | Compose-Datei, `.env` | Rückfall, wenn der neue Name `NODVARD_DECK_*` nicht gesetzt ist, nur noch übergangsweise (spätestens bis 1.0). `deploy_pi.sh` benennt `LATTICE_*` in `deploy/.env` auf dem Zielrechner automatisch in `NODVARD_DECK_*` um und behält eine Sicherungskopie (nicht bei DNS-Einstellungen in `deploy/.env.deploy`, dann wird die `.env` neu geschrieben). Wer von Hand ausliefert, benennt die Werte in `.env` selbst um. Ausliefern, Sichern und Einspielen brechen ab, wenn auf dem Zielrechner noch eine Version vor 0.6 liegt. Nodvard Deck weist den Inhaber in der Übersicht auf alte Namen hin |

Die Namen für Update-Läufe und Quarantäne sind im aktuellen Code in
[`detached.py`](../extensions/shield/src/nodvard_deck_ext_shield/detached.py),
[`antivirus.py`](../extensions/shield/src/nodvard_deck_ext_shield/antivirus.py) und im
[Hilfsskript für Root-Rechte](../extensions/shield/root-helper/shield-root.sh) festgelegt.
Die vollständige Liste mit allen Gründen steht in
[docs/01-ARCHITECTURE.md, „Alte und neue Namen“](01-ARCHITECTURE.md#alte-und-neue-namen).
