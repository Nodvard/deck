# Erst-Einrichtung – Schritt für Schritt

Stand 01.10.2026. Für eine frische Installation, in dieser Reihenfolge. Wo Nodvard Deck noch
eine Lücke hat, steht das dabei. Ohne Oberfläche (nur mit der API) geht es über den
[Anhang](#anhang-ohne-oberfläche).

1. [Container starten](#1-container-starten)
2. [Einrichtungsassistent](#2-einrichtungsassistent-setup)
3. [Weitere Benutzer](#3-weitere-benutzer)
4. [Erweiterungen einschalten](#4-erweiterungen-einschalten)
5. [Server und SSH-Zugang](#5-server-und-ssh-zugang)
6. [Root-Rechte ohne Passwort und Gruppe docker](#6-root-rechte-ohne-passwort-und-gruppe-docker)
7. [Proxmox verbinden](#7-proxmox-verbinden) (nur mit Proxmox)
8. [Push-Nachrichten (ntfy)](#8-push-nachrichten-ntfy)
9. [Automatik und Wartungsfenster](#9-automatik-und-wartungsfenster)
10. [Das Dashboard selbst sichern](#10-das-dashboard-selbst-sichern)
11. [Wenn etwas hakt](#11-wenn-etwas-hakt)
12. [Ausgesperrt? Notfall-Befehle](#12-ausgesperrt-notfall-befehle)
13. [Nodvard Deck startet nicht: die Notseite](#13-nodvard-deck-startet-nicht-die-notseite)
14. [Anhang: Ohne Oberfläche](#anhang-ohne-oberfläche)

## 1. Container starten

### 1.1 Installation ohne das Repo

Wer nur Docker hat und das Repository nicht kennt, braucht eine einzige Datei:
[`deploy/compose.standalone.yml`](../deploy/compose.standalone.yml) als `compose.yml` in einen
leeren Ordner laden und dort starten. Das fertige Image kommt aus der GitHub-Container-Registry
(`ghcr.io/nodvard/deck`, für x86-Rechner und Raspberry Pi), es wird nichts gebaut:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

Kontrolle danach, auf demselben Rechner:

```bash
curl -s http://localhost:8080/api/v1/health      # erwartet: "status":"ok"
```

Die Datenbank wird beim Containerstart automatisch angelegt bzw. aktualisiert, dafür ist nichts
zu tun. Dann im Browser `http://<Adresse-des-Rechners>:8080` öffnen, z. B.
**http://192.168.1.10:8080** (anderer Port: Variable `NODVARD_DECK_PORT`). Die Daten liegen in
einem eigenen Docker-Speicher (Volume `nodvard-deck-data`). Wer stattdessen einen Ordner auf dem
NAS nehmen will (`./daten:/app/data`), muss nichts an den Rechten einstellen: Der Container setzt
beim Start die Besitzrechte an diesem Ordner selbst. Danach können ihn nur noch der Nutzer mit der Nummer 1000
und root lesen (sofern der Speicher Rechte unterstützt); Backup-Programme oder Dateimanager, die als anderer
Benutzer laufen, brauchen dafür `sudo`. Aktualisieren (im selben Ordner):
`docker compose pull && docker compose up -d`.
Den Einrichtungscode (nächster Abschnitt) zeigt der Assistent mit einer Anleitung, wo er steht
(Befehlszeile, Docker Desktop, Portainer, Synology, Unraid).

**Alle weiteren Befehle in dieser Anleitung** (Einrichtungscode, Protokoll, Notfall-Befehle,
Wiederherstellen) laufen im Ordner mit der `compose.yml`, hier also nach `cd ~/nodvard-deck`.
Die Beispiele schreiben `sudo docker …`; ist dein Benutzer in der Gruppe `docker` (oder nutzt du
Docker Desktop), lässt du `sudo` weg.

### 1.2 Aus dem Repository bauen (für Entwickler)

Wer das Repository klont und das Image selbst baut, startet im Ordner `deploy/` des Repositorys
(`docker compose up -d --build`). Bauen, auf einen Raspberry Pi ausliefern
(`scripts/deploy_pi.sh`), Rückfallweg und Rollback stehen in
[deploy/README.md](../deploy/README.md). Die Kontrolle danach ist dieselbe wie oben
(`curl -s http://localhost:8080/api/v1/health`).

Bei dieser Installation laufen die Befehle aus dieser Anleitung im Ordner `deploy/` des
Repositorys (z. B. `cd ~/deck/deploy`) statt in `~/nodvard-deck`. Abschnitte, die nur für diesen
Weg gelten, sind entsprechend markiert.

## 2. Einrichtungsassistent (/setup)

Solange es noch keinen Benutzer gibt, leitet die Anmeldeseite automatisch auf `/setup` um.

**Du hast schon eine Sicherung?** Im ersten Schritt gibt es **„Oder: Sicherung einspielen“**: Datei hochladen,
Passwort eingeben, Zusammenfassung prüfen, einspielen – danach meldest du dich mit dem Konto aus der Sicherung an
und musst nichts neu einrichten ([10.2](#102-wiederherstellen-sicherung-einspielen)). Auch dort wird der
Einrichtungscode (unten) verlangt.

**Einrichtungscode:** Damit nicht jeder im Netz, der die Seite zuerst öffnet, das erste Konto
(und damit den Inhaber) bekommt, verlangt `/setup` einen Code. Nodvard Deck würfelt ihn beim Start
und schreibt ihn ins Protokoll – bei **jedem** Start, bis das erste Konto angelegt ist:

```bash
cd ~/nodvard-deck && sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode
# Einrichtungscode: K7MQ-X2VD-H9PA – im Browser eingeben
```

- Groß-/Kleinschreibung, Striche und Leerzeichen sind egal.
- Der Code bleibt über Neustarts gleich (er liegt als `setup_code.txt` im Datenverzeichnis,
  nur für Nodvard Deck lesbar) und wird nach dem Anlegen des Kontos gelöscht.
- Falsch eingegebene Codes werden wie falsche Passwörter gebremst (nach 10 Fehlversuchen
  einige Minuten Pause) und stehen im Protokoll als „Einrichtungscode falsch“.
- Wer den Code lieber selbst festlegt (mindestens 8 Zeichen), trägt ihn in der Compose-Datei als
  **`NODVARD_DECK_SETUP_CODE: "…"`** unter `environment:` ein. Die `compose.yml` aus
  [1.1](#11-installation-ohne-das-repo) hat noch keinen `environment:`-Block; du ergänzt ihn
  unter `nodvard-deck:` (gleiche Einrückung wie `image:`) und übernimmst ihn mit
  `docker compose up -d`:

  ```yaml
      environment:
        NODVARD_DECK_SETUP_CODE: "mein-eigener-code"
  ```

  In `deploy/docker-compose.yml` des Repositorys gibt es den Block schon. Ohne diese Einstellung
  würfelt Nodvard Deck selbst.
- Bestehende Installationen mit Konto merken davon nichts.

Im Protokoll steht der Code zusätzlich groß und allein in einer Zeile (zwischen Leerzeilen, in einem
Rahmen aus `=`-Zeichen). Im Assistenten klappt „Wo finde ich den Code?“ eine Anleitung auf.

Der Assistent hat **sechs Schritte** und zeigt oben „Schritt X von 6“. Nur der erste ist Pflicht, alles
andere lässt sich überspringen und später in den Einstellungen nachholen. „Zurück“ gibt es ab Schritt 3.

**Schritt 1 von 6 — Administrator-Konto anlegen**

- **Einrichtungscode:** siehe oben.
- **Benutzername:** mindestens 3 Zeichen, nur Kleinbuchstaben, Ziffern sowie `.`, `-` und `_`, ohne
  Leerzeichen, vorne ein Buchstabe oder eine Ziffer (z. B. `admin`). Großbuchstaben werden automatisch
  umgewandelt.
- **Passwort** und **Passwort bestätigen:** mindestens 8 Zeichen.
- „Konto anlegen“. Passt eine Angabe nicht, steht gleich da, was (z. B. „Benutzername: Mindestens 3 Zeichen.“).

Dieses erste Konto ist der **Inhaber**. Der Inhaber darf immer alles, auch wenn später
Rollen verstellt werden – so kann man sich nicht aussperren.

**Schritt 2 von 6 — Zeitzone**

Zeitpläne (zum Beispiel das Morgen-Briefing um 7 Uhr) und Wartungsfenster richten sich nach dieser
Zeitzone. Vorausgewählt ist die Zeitzone des Geräts, mit dem du im Browser sitzt; kennt der Browser
sie nicht, bleibt die bisherige Einstellung stehen. Mit der Suche („Berlin“, „New York“, auch deutsche Namen wie
„Wien“ oder „Mitteleuropa“) oder der Liste
wählst du eine andere. „Weiter“ speichert, „Überspringen“ lässt die Einstellung, wie sie ist. Ändern
geht später unter Einstellungen → System. Ohne Suche steht die gewählte Zone oben, darunter die gängigen
(Berlin, Wien, Zürich …); bekannte Zonen zeigen daneben den deutschen Namen (etwa „Österreich, Wien“ bei
`Europe/Vienna`).

**Schritt 3 von 6 — Was willst du nutzen?**

Alle mitgelieferten Module als Kacheln mit Schalter (außer dem Beispiel-Modul „Hello World“ für Entwickler, das nur
unter Einstellungen → Erweiterungen steht), nach Themen sortiert: zuerst „Für deine Server“
(System, Terminal, Service-Matrix, Proxmox VE …), dann Sicherheit, Werkzeuge, Verbindungen zu anderen
Diensten. Ein Klick auf die Kachel oder den Schalter schaltet das Modul ein oder wieder aus. Kein Modul
ist Pflicht („Überspringen“). Module, die danach noch Angaben brauchen (Adresse, Zugangsdaten,
Token), tragen den Hinweis „Braucht danach noch Angaben“; diese Angaben kommen **erst jetzt nach dem
Assistenten** unter Einstellungen → Erweiterungen, und die Karte „Erste Schritte“ im Cockpit erinnert
daran (siehe [4.](#4-erweiterungen-einschalten)). Reihenfolge und Gruppe stammen aus dem Manifest des
Moduls (`category`, `sort_order`).

**Schritt 4 von 6 — Zwei-Faktor-Anmeldung (optional)**

„Jetzt einrichten“ fragt zur Sicherheit erst dein aktuelles Passwort ab („Weiter“) und zeigt dann einen
**QR-Code** für die Authenticator-App (er wird im Browser gezeichnet,
nichts geht an einen fremden Dienst) und zusätzlich den Schlüssel als Text, falls das Scannen nicht
klappt. Den sechsstelligen Code aus der App eingeben, „Bestätigen“. Danach erscheinen **einmalig die
Wiederherstellungs-Codes** („Rettungscodes“, siehe [2.1](#21-zwei-faktor-und-wiederherstellungs-codes)):
kopieren oder als Textdatei speichern, dann „Ich habe die Codes gesichert“. „Überspringen“ richtet
nichts ein; nachholen unter Einstellungen → Mein Konto.

**Schritt 5 von 6 — Aussehen (optional)**

Produktname, Untertitel (die kleine Zeile unter dem Namen; derselbe Text ist der Name der App auf dem
Handy-Startbildschirm, also kurz halten), Akzentfarbe → „Speichern“, oder „Überspringen“. Logo und die
übrigen Farben gibt es später unter Einstellungen → Aussehen.

**Schritt 6 von 6 — Fertig**

Hinweis auf die Karte „Erste Schritte“ im Cockpit und der Notfall-Befehl (siehe
[12.](#12-ausgesperrt-notfall-befehle)); derselbe Befehl steht auf der Anmeldeseite hinter „Passwort
vergessen?“. Wer die Zwei-Faktor-Anmeldung übersprungen hat, bekommt hier den Link zum Nachholen.
„Weiter zum Dashboard“ öffnet das Cockpit.

**Neu geladen mitten im Assistenten?** Das Konto gibt es dann schon, `/setup` führt deshalb eigentlich
zur Anmeldung. Der Assistent merkt sich den erreichten Schritt aber in diesem Browser-Tab: ist man noch
angemeldet, geht es genau dort weiter (Zeitzone, Module, Zwei-Faktor, Aussehen), sonst landet man auf
der Anmeldeseite, und danach übernimmt die Karte „Erste Schritte“. Die Wiederherstellungs-Codes lassen
sich nach einem Neuladen nicht noch einmal anzeigen – unter Mein Konto erzeugt man neue.

Danach ist `/setup` weg (führt nur noch zur Anmeldung).

### 2.1 Zwei-Faktor und Wiederherstellungs-Codes

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
- **Codes aus der App** gelten je Konto nur **einmal**; ist einer schon benutzt, auf den
  nächsten warten. Nach 10 falschen Codes in 15 Minuten (oder 20 in 24 Stunden), egal von
  welchem Gerät, ist die Code-Eingabe für das Konto eine Weile gesperrt, und Nodvard Deck
  schickt eine Meldung. Wer so oft rät, kennt vermutlich das Passwort – warst du es nicht,
  ändere es. Mit einem Wiederherstellungs-Code kommst du auch während der Sperre hinein.
- **Wie viele übrig sind**, steht unter Mein Konto. Bei wenigen oder keinen: „Neue
  Wiederherstellungs-Codes erzeugen“ (verlangt das aktuelle Passwort; die alten Codes sind
  danach sofort ungültig).
- Nodvard Deck speichert nur Prüfsummen, keine Codes – ein verlorener Satz lässt sich nicht
  nachschlagen, nur ersetzen.
- Wer schon vor dieser Funktion Zwei-Faktor eingerichtet hatte, hat noch keine Codes:
  unter Mein Konto einmal „Neue Wiederherstellungs-Codes erzeugen“.
- Verliert jemand Handy **und** Codes: siehe [12.](#12-ausgesperrt-notfall-befehle).

## 3. Weitere Benutzer

Einstellungen → Benutzer → **„Neuer Benutzer“**:

- **Benutzername:** dieselbe Regel wie beim ersten Konto (mindestens 3 Zeichen, nur Kleinbuchstaben,
  Ziffern, `.`, `-` und `_`, vorne ein Buchstabe oder eine Ziffer); Großbuchstaben werden automatisch
  umgewandelt. Bei der Anmeldung ist die Schreibweise egal. Ältere Konten, deren Name nicht zu dieser
  Regel passt (etwa mit Leerzeichen), melden sich weiter normal an.
- **Passwort:** mindestens 8 Zeichen. Der neue Benutzer kann es unter „Mein Konto“ ändern. (Das
  Passwort *anderer* Benutzer setzt man später unter „Bearbeiten“; das eigene und das des
  Inhabers lassen sich dort nicht setzen – eigenes unter „Mein Konto“.)
- **Anzeigename**, **E-Mail (optional)**; eine eingetragene Adresse braucht ein `@` (z. B. `name@beispiel.de`).
- **Rollen:**
  - **Administrator** (`admin`) – Volle Verwaltung inkl. Benutzer und Einstellungen
  - **Bediener** (`operator`) – Server bedienen, Aktionen bis mittleres Risiko freigeben
  - **Betrachter** (`viewer`) – Nur ansehen, nichts ändern. Ohne das Recht `hosts.execute` (wie für Befehle) sieht der Betrachter
    keine Dateien auf Servern und weder Befehle noch Ausgabe von Aktionen (Text vom Server), auch nicht im Protokoll.
    Meldungen kann `viewer` lesen, aber nicht als gelesen markieren (der Lesestatus gilt für alle Benutzer gemeinsam)
- „Anlegen“. Passt eine Angabe nicht, steht oben gleich, was (z. B. „Passwort: Mindestens 8 Zeichen.“).

**Zwei-Faktor eines Benutzers zurücksetzen:** Hat jemand Handy und Wiederherstellungs-Codes
verloren, schaltet ein Benutzer mit Recht `users.write` (Rolle `admin`) unter Einstellungen →
Benutzer → **„Zwei-Faktor zurücksetzen“** die Zwei-Faktor-Anmeldung dieser Person ab (zur
Sicherheit wird dabei das eigene Passwort des Admins abgefragt). Die
Person wird dabei überall abgemeldet; das Protokoll hält fest, wer es getan hat. Danach geht
die Anmeldung wieder nur mit Passwort, und sie kann Zwei-Faktor neu einrichten. **Beim
Inhaber geht das nicht** – seine Zwei-Faktor-Anmeldung kann nur er selbst (unter Mein Konto)
oder der Notfall-Befehl auf dem Server ([12.](#12-ausgesperrt-notfall-befehle)) abschalten.

**Benutzer deaktivieren:** Unter Einstellungen → Benutzer → **„Deaktivieren“** sperrt ein Benutzer
mit Recht `users.write` das Konto einer anderen Person (in der Liste steht dann „Gesperrt“; beim
Inhaber und beim eigenen Konto gibt es den Knopf nicht). Die Person wird dabei überall abgemeldet,
eine schon offene Seite bekommt nach spätestens einer halben Minute keine Live-Aktualisierung mehr.
Nach **„Aktivieren“** melden sich alte Geräte nicht von allein wieder an, sie muss sich neu anmelden.

## 4. Erweiterungen einschalten

Bei einer frischen Installation sind **alle Erweiterungen aus** – bis auf eine Ausnahme: **Terminal
schaltet sich von selbst ein**, sobald du dem ersten Server einen SSH-Zugang gibst (Schlüssel
erzeugen, Passwort oder eigener Schlüssel; siehe [5.2](#52-ssh-zugang-einrichten--drei-wege)). Das
Protokoll hält es fest („Erweiterung automatisch eingeschaltet“). Das passiert nur, solange
niemand Terminal je selbst ein- oder ausgeschaltet hat: Wer es bewusst ausschaltet, bei dem bleibt
es aus. Bereits bestehende Installationen sind davon nicht betroffen.

Im Einrichtungsassistenten (Schritt 3, siehe [2.](#2-einrichtungsassistent-setup)) schaltest du die Module,
die du nutzen willst, per Klick ein. Später geht das jederzeit unter Einstellungen →
Erweiterungen, mit dem Schalter rechts, „Konfigurieren“ öffnet die
Einstellungen der Erweiterung („Einrichtung nötig“ heißt: es fehlen noch Angaben oder der
letzte Verbindungstest ist fehlgeschlagen). Öffnest du die Seite eines ausgeschalteten Moduls (etwa über ein
Lesezeichen), sagt sie das, und **„Einschalten“** schaltet es dort gleich ein (ohne das Recht dazu steht da, dass ein
Administrator es einschalten kann). Ein guter Anfang für fast jedes Heimnetz: Terminal, System,
Nodvard Shield und ntfy-Benachrichtigungen, dazu Service-Matrix, wenn auf einem Server Docker läuft, und
Gameserver, wenn dort ein Spieleserver steht. **Proxmox VE und Backups brauchst du nur, wenn du Proxmox
nutzt** – sonst lass sie aus (siehe [4.1](#41-ohne-proxmox)). Der Rest nach Bedarf.

**Zugangsdaten gehören zur Adresse.** In den Einstellungen einer Erweiterung erst die Adresse eintragen
und „Speichern“ drücken, dann in der Karte „Verbindung“ Token oder Passwort hinterlegen – vorher nimmt
Nodvard Deck sie nicht an. Änderst du später die Adresse (etwa den ntfy-Server oder eine Proxmox-Adresse),
löscht Nodvard Deck beim Speichern die Zugangsdaten dazu, und du trägst sie neu ein; die Seite weist
vorher darauf hin. Eine andere Schreibweise derselben Adresse (Groß-/Kleinschreibung im Rechnernamen,
Standardport, `/` am Ende, fehlendes `http://`) zählt nicht als Änderung.

### 4.1 Ohne Proxmox

Nodvard Deck braucht kein Proxmox. Ein Raspberry Pi, eine Debian-VM, ein NAS oder ein paar Docker-Hosts
reichen. Was dann anders ist:

- **Server legst du von Hand an** ([5.1](#51-server-anlegen)) und gibst jedem einen SSH-Zugang
  ([5.2](#52-ssh-zugang-einrichten--drei-wege)). Die Module Proxmox VE und Backups bleiben aus; ihre Einträge
  erscheinen dann weder im Menü noch im Cockpit.
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
- **Auslastung und Verlauf** (CPU, Arbeitsspeicher, Platte, Netz, Temperatur) kommen vom Modul **System**
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
  Messwerte (Windows, ohne SSH-Zugang, Modul System aus, Messwerte für den Server abgeschaltet) bleiben eine Zeile,
  wo es passt mit einem Hinweis, warum.
- **Docker-Container:** Service-Matrix, für Server mit der Markierung `docker`.
- **Apps im Cockpit ohne Service-Matrix:** Im Bereich „Apps“ legst du mit **+ App hinzufügen** eigene Kacheln an –
  Name, Adresse (`http://192.168.1.1`), Symbol (aus einer Auswahl oder ein Emoji), Farbe, Gruppe und auf Wunsch der
  Server dazu. So stehen auch Router, NAS-Oberfläche oder Pi-hole im Cockpit, ohne Code und ohne Container-Erkennung;
  erkannte Container erscheinen daneben, wenn die Service-Matrix läuft. Über das Menü (⋮) an der Kachel bearbeitest,
  verschiebst oder löschst du sie, oben filterst du nach Gruppen. Anlegen und Ändern dürfen Inhaber und
  Administratoren (Recht `apps.write`); sehen dürfen es alle, die Server sehen. Nodvard Deck ruft die Adressen nie
  selbst auf (es zeigt also kein „läuft/läuft nicht“ an), erlaubt nur `http://` und `https://` und keine Zugangsdaten
  in der Adresse; Symbole sind nie Bild-Adressen.
- **Updates, Virenschutz, Einbruchschutz:** Nodvard Shield, für alle Linux-Server mit SSH-Zugang. Die KI-Container-Wache
  ist darin optional; ohne KI-Server zeigt sie abgestürzte Container trotzdem an. Mit KI schlägt sie höchstens den
  Neustart eines abgestürzten Containers vor, du findest ihn unter „Aktionen“; andere Ideen der KI stehen nur als
  Text im Lagebericht der Container-Wache. Schlägt sie einen anderen Befehl vor, steht im Lagebericht nur ein Hinweis
  darauf; den Befehl selbst siehst du mit Server-Rechten (`hosts.execute`) im Protokoll.
- **Konsole im Browser** (Bildschirm eines Gasts) gibt es nur für Proxmox-Gäste. Für alle anderen Server
  nimmst du das **Terminal**.
- **Sicherungen:** Das Modul Backups zeigt nur Proxmox-Backups. Das Dashboard selbst sicherst du unter
  Einstellungen → System ([10.](#10-das-dashboard-selbst-sichern)).

**Die Karte „Erste Schritte“:** Auf der Startseite (Cockpit) führt oben eine Checkliste durch
genau diese Schritte – Server anlegen, SSH-Zugang hinterlegen, Verbindung prüfen, Module
einrichten, Push-Nachrichten, erstes Widget – jeder mit einem Knopf zum richtigen Ort. Die
Häkchen setzt Nodvard Deck selbst aus dem, was schon eingerichtet ist. „SSH-Zugang hinterlegen“ zählt einen
Schlüssel erst, wenn sich Nodvard Deck damit wirklich angemeldet hat (ein Passwort sofort); bis dahin führt
**„Befehl ansehen“** zur Seite des Servers mit schon aufgeklapptem Einrichtungsbefehl. „Verbindung prüfen“ ist erst
abgehakt, wenn eine Anmeldung bestätigt ist: Dass der Server antwortet, reicht nicht. Es erscheinen nur Schritte,
für die du das Recht hast. Die Karte verschwindet von allein, wenn alles erledigt ist; mit
**„Ausblenden“** nimmst du sie früher weg (gilt für dein Konto auf jedem Gerät). Auch leere Seiten
(Terminal, Dateien, Server-Seite, Service-Matrix, Gameserver, Proxmox) sagen jetzt, was fehlt, und
bieten den Knopf zum nächsten Schritt an.

## 5. Server und SSH-Zugang

Alles dazu gibt es auf einer Seite: **Einstellungen → Server & Zugänge**. Dort legst du
Server an, richtest ihren SSH-Zugang ein und prüfst, ob die Verbindung klappt. Die Seite ist
für das Handy gedacht und nur für Benutzer mit dem Recht `hosts.write` (Inhaber und
`admin`) sichtbar.

**Woher die Server kommen:**

- Proxmox-Knoten, VMs und Container liest die Erweiterung „Proxmox VE“ **automatisch** ein
  (alle 5 Minuten, siehe [7.](#7-proxmox-verbinden)). Sie stehen mit dem Hinweis „Automatisch
  eingelesen“ in der Liste.
- Alles andere – vor allem der **Rechner, auf dem Nodvard Deck läuft** (z. B. ein Raspberry Pi),
  wenn er kein Proxmox-Gast ist – legst du von Hand an.
- Terminal, System, Service-Matrix, Nodvard Shield, Gameserver und Skripte brauchen für jeden
  Server einen **SSH-Zugang** (Benutzer + Schlüssel oder Passwort). Den speichert Nodvard Deck
  verschlüsselt; der private Schlüssel verlässt den Tresor nie und wird nirgends angezeigt.

**Noch keinen Server zur Hand?** Auf einer frischen Installation ohne Server gibt es im Cockpit (und
auf dieser Seite) den Knopf **Mit Beispieldaten ansehen**. Ein Klick legt fünf ausgedachte Server
(Adressen aus `192.0.2.x`, es wird nie etwas angefragt), ein paar Meldungen, drei Beispiel-Apps und – wenn Module Widgets
mitbringen – ein Beispiel-Dashboard an, damit du siehst, wie alles gefüllt aussieht. Oben steht dann
ein gelbes Band **„Du siehst Beispieldaten“**; **Beispieldaten löschen** (nach Rückfrage) entfernt genau
diese Dinge wieder, stellt dein Dashboard-Layout zurück und lässt alles Echte unberührt. Den Knopf
sehen nur Inhaber und Administratoren (`hosts.write` und `settings.write`). Sobald du einen echten
Server angelegt hast, gibt es den Knopf nicht mehr; die Beispieldaten kannst du trotzdem jederzeit
löschen.

### 5.1 Server anlegen

1. **Server hinzufügen** drücken (bei einer leeren Liste gibt es den Knopf auch in der Mitte). Kommst du über
   „Server hinzufügen“ im Cockpit oder in der Karte „Erste Schritte“, ist das Formular schon offen.
2. **Kurzname** – klein, ohne Leerzeichen, z. B. `bastel-pi`. Er lässt sich später **nicht**
   mehr ändern (Nodvard Shield und andere Erweiterungen erkennen den Server daran).
3. **Anzeigename** (so heißt der Server in den Listen; leer = Kurzname) und **Adresse**
   (IP oder Rechnername, ohne `http://` und ohne Port, z. B. `192.168.1.30`).
4. **Betriebssystem:** Linux oder Windows. Für Windows-Server gibt es keinen
   Einrichtungsbefehl und keine Rechte-Prüfung.
5. **Markierungen** (mit Komma getrennt): `docker`, wenn dort Docker läuft – Service-Matrix
   (Einstellung „Server-Markierung“) und die KI-Container-Wache von Nodvard Shield („Überwachte
   Server“) nehmen genau die Server mit dieser Markierung. Gameserver erscheinen nur mit der
   Markierung `gameserver`. Markierungen lassen sich jederzeit ändern, auch bei Proxmox-VMs
   (die von Proxmox gesetzten bleiben, man kann sie nicht ändern).
6. **Anlegen.** Die Seite des neuen Servers öffnet sich und bietet gleich den nächsten Schritt an.

Falsche Eingaben meldet die Seite direkt am Feld auf Deutsch (z. B. „Kurzname: nur
Kleinbuchstaben, Ziffern, - und _ …“).

**Adresse korrigieren:** Proxmox-VMs ohne laufenden Gast-Agenten bekommen als Platzhalter die
Adresse des Proxmox-Servers. Dann auf der Seite des Servers unter **Allgemein → Bearbeiten** die
echte IP eintragen – Nodvard Deck überschreibt eine von Hand gesetzte Adresse nicht mehr mit dem
Platzhalter, nur mit einer Adresse, die der Gast selbst meldet. Bei Proxmox-*Knoten* überschreibt
die Erweiterung die Adresse beim nächsten Abgleich wieder.

Hat der Server ein gespeichertes **SSH-Passwort**, wird es beim Ändern der Adresse gelöscht; du gibst es
danach neu ein. SSH-Schlüssel bleiben. Das gilt auch, wenn der Abgleich einer Erweiterung die Adresse
ändert (etwa bei einem Proxmox-Knoten); bei VMs und Containern, die ihre Adresse selbst melden und deren
Server-Schlüssel schon gemerkt ist, bleibt das Passwort.

### 5.2 SSH-Zugang einrichten – drei Wege

Auf der Seite des Servers, Karte **SSH-Zugang**:

- **SSH-Schlüssel erzeugen (empfohlen).** Nodvard Deck erzeugt einen eigenen Schlüssel nur für
  diesen Server. Du gibst nur den **Benutzer auf dem Server** an (Vorgabe `nodvard`, ein eigener
  Benutzer nur für Nodvard Deck; wird angelegt, falls es ihn noch nicht gibt – bei Proxmox-Knoten `root` nehmen) und den Port (22).
  Ältere Zugänge heißen vielleicht noch `lattice`, sie laufen unverändert weiter. Danach zeigt die Seite den
  **Einrichtungsbefehl** (5.3).
- **Passwort eingeben.** Für einen Benutzer, den es auf dem Server schon gibt. Das Passwort wird
  verschlüsselt gespeichert und nie wieder angezeigt. Ein Schlüssel ist sicherer.
- **Eigenen Schlüssel einfügen.** Den privaten Schlüssel (Text, der mit `-----BEGIN …` beginnt,
  **ohne Passphrase**) einfügen. Danach gibt es auch dazu den Einrichtungsbefehl für den Server.

Was du eintippst oder einfügst, wird beim Abschicken aus dem Formular gelöscht.

### 5.3 Einrichtungsbefehl auf dem Server ausführen

Der Befehl ist **ein einziger Einzeiler**. Mit **Kopieren** (geht auch auf `http://`, ohne
sicheren Kontext) in die Zwischenablage, auf dem Server anmelden (per `ssh`, am Bildschirm oder über die
Konsole deiner VM-Verwaltung), einfügen, Enter. Er

- legt den Benutzer an, falls nötig (ohne Passwort, Anmeldung nur mit dem Schlüssel),
- trägt den öffentlichen Schlüssel in `~/.ssh/authorized_keys` ein (mit `restrict,pty`: Terminal,
  Dateien und Befehle gehen, Weiterleitungen nicht),
- richtet auf Wunsch **Root-Rechte ohne Passwort** und die **Gruppe docker** ein (siehe [6.](#6-root-rechte-ohne-passwort-und-gruppe-docker)).

Unter „Was macht der Befehl?“ steht derselbe Ablauf lesbar, Zeile für Zeile. Der Befehl läuft als
root direkt, sonst über `sudo` – er funktioniert also auf Proxmox (kein sudo) ebenso wie auf
Debian oder dem Pi. Er lässt sich gefahrlos mehrfach ausführen: Einen schon eingetragenen Schlüssel erkennt er
auch mit dem älteren Kommentar `lattice@…` und trägt ihn nicht doppelt ein.

### 5.4 Verbindung prüfen

Karte **Verbindung prüfen** (bis zu 30 Sekunden). Die Seite zeigt Punkt für Punkt, was klappt
und was nicht – mit Hinweis, was zu tun ist:

1. **Server erreichbar** – Adresse, Port, Firewall, läuft SSH?
2. **Server-Schlüssel** – siehe unten.
3. **Anmeldung** – nimmt der Server den Schlüssel bzw. das Passwort?
4. **Root-Rechte** – geht `sudo` ohne Passwort? (Nur ein Hinweis, kein Fehler: Die schlanke
   Variante ohne Root-Rechte ist erlaubt.)
5. **Was Erweiterungen brauchen** – z. B. Docker ohne sudo für die Service-Matrix.
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
> **„Neue Server-Schlüssel“** ein. Bei neuen Installationen ist **„Neue Server-Schlüssel erst nach meiner
> Bestätigung merken“** an: Dann merkt sich Nodvard Deck einen Schlüssel nur über „Verbindung prüfen“,
> vorher geht kein Passwort und kein Schlüssel an den Server. Installationen, die schon vor dieser
> Einstellung eingerichtet waren, bleiben auf „aus“ und merken weiter von allein, bis du den Schalter
> einschaltest. Die Umgebungsvariable `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` (`true` oder `false`; in der
> Compose-Datei unter `environment:`, siehe das Beispiel in [2.](#2-einrichtungsassistent-setup); in
> `deploy/docker-compose.yml` des Repositorys ist die Zeile vorbereitet) übersteuert den Schalter: Dann
> zeigt die Karte deren Wert, und Speichern meldet einen Fehler.
> Nach **„Vergessen“** musst du den neuen Fingerabdruck immer bestätigen, egal wie das eingestellt ist –
> bis dahin verbindet sich Nodvard Deck nicht mehr mit dem Server.
> Bereits gemerkte Schlüssel sind nie betroffen. Sauberer ist immer: zuerst prüfen und bestätigen.

### 5.5 Zugang ersetzen, löschen, Gruppen

- **Ersetzen:** Karte SSH-Zugang → **Ersetzen** legt einen *neuen* Zugang an (Schlüssel erzeugen,
  Passwort oder eigener Schlüssel); der alte bleibt Standard. Führe den Befehl des neuen Zugangs
  auf dem Server aus, drücke **Neuen Zugang prüfen** und danach **Neuen Zugang verwenden**. Erst
  nach einer erfolgreichen, frischen Prüfung (höchstens 10 Minuten alt) wird der alte Zugang
  gelöscht – so sperrst du dich nicht aus. War der alte Zugang ein Schlüssel, **bleibt sein öffentlicher Schlüssel auf
  dem Server in `~/.ssh/authorized_keys` stehen**; dort bei Bedarf selbst entfernen. Bei einem Passwort ändert sich auf
  dem Server nichts. Die Prüfung des neuen Zugangs ändert nichts an der Bestätigung des alten (5.4); erst wenn du den
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

**Testen:** Im Menü „Terminal“ taucht der Server jetzt auf (die Terminal-Erweiterung schaltet sich mit dem
ersten SSH-Zugang ein, falls du sie nicht selbst ausgeschaltet hattest).
Eine Sitzung öffnen – klappt die Anmeldung, klappt sie auch für alle anderen Erweiterungen. Eine
Terminal-Sitzung ohne Eingabe und Ausgabe schließt sich nach 30 Minuten von selbst.

## 6. Root-Rechte ohne Passwort und Gruppe docker

Meldet sich Nodvard Deck auf einem Server **nicht als root** an (z. B. auf dem Pi), führt es
root-Befehle über `sudo -n` aus, also ohne Passwortabfrage. Verlangt sudo ein Passwort,
scheitert die Funktion mit „Keine root-Rechte: Das Dashboard ist auf diesem Server nicht
als root angemeldet und darf sudo nicht ohne Passwort nutzen.“

Den Einrichtungsbefehl (5.3) musst du dafür **nicht von Hand ändern**: Die Seite setzt die
passenden Haken selbst, weil die Erweiterungen melden, was sie brauchen.

- **„Root-Rechte ohne Passwort“** – nötig für (die Seite nennt es hinter dem Haken):
  - **Nodvard Shield, Virenschutz:** Datei in Quarantäne verschieben, wiederherstellen oder löschen,
    Lynis-Härtungsaudit, Werkzeuge installieren (ClamAV, Lynis, Signaturen, Fail2ban,
    automatische Updates). Scans laufen auch ohne root, prüfen dann aber nur Dateien, die der
    Benutzer lesen darf.
  - **Update-Zentrale:** Updates einspielen und neu starten. Das Neuladen der Paketlisten vor der
    Prüfung braucht root – ohne bleibt es beim zuletzt geladenen Stand. Updates laufen auf dem
    Server im Hintergrund (mit systemd als `lattice-upgrade-…`, sonst per `setsid`) und laufen
    weiter, auch wenn die Verbindung abreißt oder das Dashboard neu startet. Das Protokoll jedes
    Laufs liegt 30 Tage unter `/var/lib/nexus-updates/`. Das Dashboard wartet 45 Minuten auf das
    Ergebnis; dauert ein Lauf länger, steht er zunächst als „Keine Rückmeldung“ da (beendet wird
    er nie), das Dashboard fragt aber noch bis zu etwa 3 Stunden nach, berichtigt dann den Eintrag
    und meldet das Ergebnis per Push. Startet das Dashboard in dieser Zeit neu, bleibt der Eintrag
    stehen – dann im Protokoll auf dem Server nachsehen.
  - **Einbruchschutz und Datei-Wächter:** SSH-Log, Fail2ban-Status, Prozesse hinter offenen Ports,
    SSH-Einstellungen, Prüfsummen von Dateien wie `/etc/shadow`. Ohne root nur eingeschränkt –
    Nodvard Deck sagt das dann dazu, statt falsch Entwarnung zu geben. IP-Adressen per Fail2ban
    sperren/entsperren geht nur mit root.
  - **System:** Dienste (systemd) neu starten.
- **„Zur Gruppe docker hinzufügen“** – nötig für Service-Matrix und die KI-Container-Wache von
  Nodvard Shield; sie rufen `docker` ohne sudo auf. Die Gruppe wird nur eingetragen, wenn es sie auf
  dem Server gibt.

Ohne beide Haken bekommst du die **schlanke Variante**: eigener Benutzer, nur Schlüssel, keine
Root-Rechte. Die reicht für Terminal, Dateien und Skripte.

**Was der Haken „Root-Rechte“ genau tut:** Er legt `/etc/sudoers.d/nodvard-<benutzer>` an mit
der Zeile `<benutzer> ALL=(root) NOPASSWD: ALL`. Die Zeile wird erst in eine Probedatei geschrieben
und mit `visudo -c` geprüft; nur dann kommt sie mit den Rechten `0440` (root) an ihren Platz,
danach prüft `visudo -c` alles noch einmal, und bei einem Fehler wird die Datei sofort wieder
entfernt („sudo-Regel zurückgenommen“). Eine ältere Regel `lattice-<benutzer>` löscht der Befehl erst nach dieser
Prüfung und nur, wenn sie aus genau der Zeile oben besteht; sonst bleibt sie stehen, und du bekommst einen Hinweis.
Ist `sudo` gar nicht installiert, sagt der Befehl das
(als root `apt install sudo`; bei Proxmox am besten gleich als `root` anmelden).

**Das ist praktisch root – die Seite sagt es dir auch:** Wer in der Gruppe `docker` ist oder sudo
ohne Passwort darf, kann auf dem Server praktisch alles. Eine enger begrenzte sudo-Regel
funktioniert mit Nodvard Deck nicht: Es startet jeden root-Befehl als `sudo -n sh -c '…'`, und
wer `sh` als root starten darf, darf alles. Die Sicherheit kommt stattdessen daher, dass **nur**
dieser eine Benutzer die Rechte bekommt, er sich **nur mit dem Schlüssel** anmelden kann und der
Schlüssel verschlüsselt in Nodvard Deck liegt.

**Proxmox-Knoten:** Dort meldet sich Nodvard Deck am einfachsten direkt als `root` an (Benutzer
`root` beim Erzeugen eintragen). Dann braucht es kein sudo und keine Haken. Der Schlüssel landet in
`/root/.ssh/authorized_keys`, das bei Proxmox ein Verweis in den Cluster-Ordner ist – er gilt dann
auf **allen Knoten des Clusters**.

**Vorhandenen Benutzer nehmen** (auf dem Pi z. B. `admin`): beim Erzeugen statt `nodvard` dessen
Namen eintragen. `sudo -n true && echo geht-schon` auf dem Server zeigt, ob er sudo schon ohne
Passwort darf (auf Raspberry Pi OS oft beim ersten Benutzer der Fall).

Nicht wundern: Der Datei-Wächter von Nodvard Shield meldet die neue sudo-Datei und die neue
`authorized_keys` einmal – das warst du. Dasselbe gilt, wenn du den Befehl mit Haken „Root-Rechte“ auf einem
Server erneut ausführst, der noch die alte Regel `lattice-<benutzer>` hat: Dann kommt `nodvard-<benutzer>` dazu,
und die alte fällt weg, wenn sie unverändert ist (siehe oben).

## 7. Proxmox verbinden

**Nur wenn du Proxmox nutzt – sonst überspringst du diesen Abschnitt** (siehe [4.1](#41-ohne-proxmox)).
Ausführlich in **[docs/10-PROXMOX-TOKEN.md](10-PROXMOX-TOKEN.md)**. Kurzfassung:

1. Auf jedem Proxmox-Server (im Beispiel pve1 **und** pve2) einen Benutzer `nodvard@pve` mit
   Token `dashboard` und passender Rolle anlegen.
2. Einstellungen → Erweiterungen → „Proxmox VE“ → „Konfigurieren“ → „Server hinzufügen“
   (Kurzname `pve1`/`pve2`, Adresse `https://…:8006`, API-Token-ID
   `nodvard@pve!dashboard`) → „Speichern“ → unter „Zugangsdaten“ das Geheimnis eintragen.
3. Dasselbe unter „Backups“ mit denselben Kurznamen.
4. Nach spätestens 5 Minuten sind Knoten, VMs und Container da. VMs ohne Gast-Agent
   bekommen die Proxmox-Adresse als Platzhalter – die richtige IP unter Einstellungen →
   Server & Zugänge eintragen ([5.1](#51-server-anlegen)).

## 8. Push-Nachrichten (ntfy)

Einstellungen → Erweiterungen → **„ntfy-Benachrichtigungen“** einschalten →
„Konfigurieren“:

- **ntfy-Server:** z. B. `https://ntfy.sh` oder dein eigener.
- **Thema (Topic):** der Kanal, den du in der ntfy-App abonnierst. Bei ntfy.sh schwer
  erratbar wählen, denn dort kann jeder ein Thema mitlesen, der den Namen kennt.
- **Adresse des Dashboards (optional):** `http://192.168.1.10:8080`. Dann öffnet ein Tipp
  auf die Nachricht direkt die passende Seite. Muss mit `http://` oder `https://`
  anfangen, sonst wird sie ignoriert.
- „Speichern“.
- **Zugangsdaten → „Zugriffstoken (optional)“:** nur, wenn dein ntfy-Server eine Anmeldung
  verlangt.

In der ntfy-App auf dem Handy denselben Server und dasselbe Thema abonnieren.
**Testen:** Seite „Nodvard Shield“ → „Briefing senden“. Das Erstellen kann bis zu einer halben
Minute dauern, wenn ein Server nicht antwortet. Danach sagt dir die Seite, ob der Lagebericht
(„Lagebericht – …“) auch als Push-Nachricht zugestellt wurde. Wenn nicht, ist ntfy noch nicht
eingerichtet oder der ntfy-Server nicht erreichbar. Alle Nachrichten stehen außerdem im
Dashboard unter „Meldungen“ – auch dann, wenn die Push-Nachricht nicht ankommt.

## 9. Automatik und Wartungsfenster

Einstellungen → **Automatik & Sicherheit**:

- **Selbstständigkeit:** „Nur vorschlagen“ ist der Standard und für den Anfang richtig –
  jede Aktion wartet unter „Aktionen“ auf deine Freigabe. „Selbstständig handeln“ lässt
  Aktionen bis zur gewählten Risikostufe („Ohne Rückfrage erlaubt bis Risikostufe“) ohne
  Rückfrage laufen. Unter „Aktionen“ steht der Befehl einer Aktion direkt in ihrer Zeile (ohne das Recht
  `hosts.execute` und ohne Freigaberecht für ihr Risiko steht dort nur ein Hinweis); unsichtbare oder umlenkende
  Zeichen darin (etwa eine umgekehrte Schreibrichtung) erscheinen als sichtbare Marke wie `⟦U+202E⟧`. „Ausgewählte
  freigeben“ zeigt in der Rückfrage alle Befehle ungekürzt, gibt aber Aktionen mit hohem oder kritischem Risiko nicht
  mit frei; die bestätigst du einzeln.
- **Skripte ohne Klick:** Unabhängig davon kann ein aktives Skript mit Zeitplan eine Dauerfreigabe bekommen: auf der
  Skripte-Seite beim Skript den Schalter „Ohne Freigabe nach Zeitplan“ (nur Inhaber und `admin`). Vorher siehst du, für
  welche Server, unter welchem Konto und unter welcher Adresse sie gilt. Jede Änderung an Inhalt, Parametern, Ziel oder
  Zeitplan und ein anderes Konto, eine andere Adresse oder ein anderer SSH-Port eines dieser Server heben sie auf; neue
  Server in einer Gruppe oder unter „Alle Server“ fragen weiter nach. Ist die Person, die freigegeben hat, gesperrt oder
  hat sie die Rechte dafür nicht mehr, warten die Läufe ebenfalls wieder auf deinen Klick. Jeder Lauf steht weiter unter
  „Aktionen“ und im Protokoll, „Freigabe zurückziehen“ wirkt sofort.
- **Gesperrte Befehle:** eigene Muster (reguläre Ausdrücke), die nie ausgeführt werden. Die
  gefährlichen Standardfälle sind immer gesperrt.
- **Wartungsfenster:** „Fenster hinzufügen“ → Beginn, Dauer, „Gilt für“ (Alle Server /
  Ausgewählte Server) → „Speichern“. Während des Fensters werden Push-Nachrichten zu diesen
  Servern stummgeschaltet; unter „Meldungen“ stehen sie trotzdem. Die Zeit gilt in der
  eingestellten Zeitzone (Einstellungen → System). Stummgeschaltet werden nur Meldungen, die
  zu bestimmten Servern gehören: die von **Backups**, **Gameserver**, dem **Proxmox-Wächter** (Knoten, Datenträger,
  Verbindung) und von **Nodvard Shield** (Updates, Härtungs-Audit, Einbruchschutz-Warnungen,
  Lagebericht der Container-Wache). Ein Bericht über mehrere Server bleibt hörbar, solange
  auch nur einer davon nicht im Fenster liegt.
  Immer hörbar bleiben **Schadsoftware-Funde** und **kritische Einbruchschutz-Ereignisse**
  (der Tiefenscan läuft ab Werk sonntags um 03:30, mitten im vorgeschlagenen Fenster), das
  **Morgen-Briefing** und die Meldungen von Zertifikatswächter und Skripten.
  Gut zu wissen: Fällt bei Backups, Gameserver oder Proxmox-Wächter im Fenster etwas aus
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

**Automatische Updates** (Nodvard Shield): Einstellungen → Erweiterungen → „Nodvard Shield“ →
„Updates automatisch einspielen“ ist ab Werk **aus**, ebenso „Danach automatisch neu
starten, wenn nötig“. Erst einschalten, wenn die Update-Zentrale eine Weile sauber
gelaufen ist.

## 10. Das Dashboard selbst sichern

### 10.1 In der Oberfläche (empfohlen)

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
   NAS einbinden: In der Compose-Datei (`compose.yml` aus [1.1](#11-installation-ohne-das-repo),
   bei einer Installation aus dem Repository `deploy/docker-compose.yml`) steht unter `volumes:`
   schon die Zeile `# - ./sicherungen:/backups`. Das `#` davor entfernen (oder einen NAS-Pfad statt
   `./sicherungen` eintragen), im selben Ordner `mkdir sicherungen && sudo chown 1000:1000 sicherungen`
   ausführen (der Ordner muss dem Nutzer mit der Nummer 1000 gehören), mit `docker compose up -d`
   übernehmen und in der Oberfläche als Ordner `/backups` wählen.
4. **Prüfen** vergleicht eine Sicherung mit ihrer Prüfsumme (erkennt kaputte Dateien, ohne
   Passwort). **Herunterladen** holt eine Sicherung auf den PC, **Sicherung herunterladen**
   erstellt sofort eine frische – wahlweise mit dem Sicherungspasswort oder mit einem eigenen
   Einmal-Passwort nur für diese Datei.

Notfall ohne Nodvard Deck: Eine `.ndbak`-Datei ist eine normale [age](https://age-encryption.org)-Datei
mit zwei Zeilen davor.

```bash
tail -n +3 nodvard-deck-sicherung-20261001-023000.ndbak > sicherung.age
age -d -i wiederherstellungsschluessel.txt -o sicherung.tar.gz sicherung.age   # oder: age -d (Einmal-Passwort)
tar -xzf sicherung.tar.gz    # db/lattice.db, files/master.key, files/ext/ …
```

Einspielen: siehe [10.2](#102-wiederherstellen-sicherung-einspielen).

### 10.2 Wiederherstellen (Sicherung einspielen)

Zwei Wege, derselbe Ablauf: **Einstellungen → System → Wiederherstellen** (nur der Inhaber, mit
Anmeldepasswort) oder, auf einer **frischen Installation**, im Einrichtungsassistenten unter
**„Oder: Sicherung einspielen“** (dort statt eines Kontos mit dem **Einrichtungscode** aus dem
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
- Mit der Umgebungsvariable `NODVARD_DECK_JWT_SECRET` liegt kein Anmelde-Geheimnis in der Sicherung.
- Der Verlauf der Messwerte (`metrics.db`) gehört nicht zur Sicherung und bleibt, wie er ist.
- **Git-Einstellungen** in den Daten der Erweiterungen (etwa bei „Skripte“) gehören ebenfalls nicht zur Sicherung, der
  Verlauf deiner Skripte schon; die Einstellungen legt die Erweiterung selbst neu an. Enthält eine ältere Sicherung noch
  welche, werden sie beim Einspielen übersprungen, und die Zusammenfassung nennt ihre Zahl unter den Warnungen.

**Notfall ohne Oberfläche:** im Ordner mit der Compose-Datei

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin restore-backup /app/data/backups/nodvard-deck-sicherung-….ndbak
sudo docker compose restart nodvard-deck      # eingespielt wird beim Start
```

(Die Datei muss im Container sichtbar sein; der Befehl fragt das Passwort ab und zeigt die Zusammenfassung. `compose exec` läuft im
bestehenden Container; ein `compose run` daneben würde von der Sperre des Datenordners abgewiesen, siehe [10.4](#104-updates-die-kopie-vor-der-migration).)
Bei einer Installation aus dem Repository geht dasselbe kürzer mit `deploy/restore.sh sicherung.ndbak`
([10.3](#103-mit-dem-skript-nur-installation-aus-dem-repository)).
Geht gar nichts mehr, liegt der alte Stand unter `restore/replaced-…`: Container anhalten, dessen Inhalt
(`lattice.db`, `master.key`, `ext/` …) zurück in den Datenordner kopieren, starten.

### 10.3 Mit dem Skript (nur Installation aus dem Repository)

Die Skripte `deploy/backup.sh` und `deploy/restore.sh` gehören zur Installation aus dem Repository
([1.2](#12-aus-dem-repository-bauen-für-entwickler)): Sie rufen Compose mit dem Projektnamen von
`deploy/docker-compose.yml` auf und funktionieren mit der `compose.yml` aus
[1.1](#11-installation-ohne-das-repo) nicht. Dort sicherst du über die Oberfläche ([10.1](#101-in-der-oberfläche-empfohlen)).

`deploy/backup.sh` hält den Container kurz an und packt den kompletten Datenordner in eine Datei:
Datenbank, `master.key`, `jwt_secret.key`, Schlüsselbund, Daten der Erweiterungen,
Skript-Repository. Details und Wiederherstellen:
[deploy/README.md](../deploy/README.md#backup--restore).

Im Ordner `deploy/` des Repositorys (hier als Beispiel unter `~/deck`):

```bash
cd ~/deck/deploy
sudo ./backup.sh ~/nodvard-deck-backups
```

Wiederherstellen: `sudo ./restore.sh ~/nodvard-deck-backups/nodvard-deck-backup-<Zeitstempel>.tar.gz`
(fragt vorher nach).

Ist dein Benutzer in der Gruppe `docker`, lässt du `sudo` weg. Ohne Zugriff auf Docker brechen beide Skripte ab,
bevor sie etwas anhalten oder ändern (`backup.sh` meldet „Docker antwortet nicht …“). Mit `sudo` gestartet gehört
die Sicherung trotzdem dir, nicht root, und nur du kannst sie lesen.

So läuft das Wiederherstellen:

- `restore.sh` prüft die Datei, entpackt sie erst **neben** die bisherigen Daten und tauscht dann aus. Dafür braucht
  es kurz doppelt so viel Platz. Scheitert das Entpacken (Platte voll, Strg+C), läuft Nodvard Deck auf dem alten
  Stand weiter.
- Bricht der Austausch selbst ab (etwa bei einem Stromausfall), bleibt Nodvard Deck gestoppt. Dann denselben Befehl
  mit derselben Datei noch einmal aufrufen: Er macht den Austausch fertig und startet Nodvard Deck. Bis dahin nicht
  von Hand starten; `backup.sh` lehnt so lange ab.
- Reißt nach der Rückfrage die SSH-Verbindung ab, läuft das Einspielen weiter. Wie es ausging, steht in
  `deploy/restore.log`.
- Läuft schon eine Sicherung oder ein Einspielen, bricht ein zweiter Lauf ab, ohne etwas zu ändern.

Wichtig:

- **Die Sicherung enthält die Schlüssel** (`master.key`, `vault_keyring.json`). Damit
  lassen sich alle in Nodvard Deck gespeicherten Passwörter, SSH-Schlüssel und Tokens
  entschlüsseln – die Datei also so gut wegschließen wie die Passwörter selbst. Umgekehrt:
  Ohne diese Schlüsseldateien sind die Geheimnisse weg.
- Die Sicherung gehört **nicht nur auf denselben Rechner** (eine SD-Karte kann sterben). Vom PC
  aus abholen: `scp admin@192.168.1.10:~/nodvard-deck-backups/*.tar.gz .`
- Regelmäßig, z. B. sonntags 04:30 (per `sudo crontab -e`, eine Zeile):

  ```
  30 4 * * 0 cd /home/admin/deck/deploy && ./backup.sh /home/admin/nodvard-deck-backups >> /home/admin/nodvard-deck-backup.log 2>&1
  ```

  Die Datei gehört dann dem Besitzer des Zielordners, wenn das nicht root ist (hat ihn der erste `sudo ./backup.sh`
  angelegt, bist das du); so klappt das Abholen mit `scp` wie oben. Läuft gerade eine Sicherung oder ein Einspielen,
  bricht der Lauf ab, ohne etwas zu ändern (Meldung im Log).

### 10.4 Updates: die Kopie vor der Migration

Vor jedem Update, das die Datenbank umbaut (eine **Migration**), legt Nodvard Deck beim Start **automatisch eine Kopie der Datenbank** an:
`backups/vor-update/<Zeit>_<von>_<nach>.db` im Datenordner. Die **drei neuesten** bleiben. Zu sehen unter **Einstellungen → System → „Kopien vor Updates“**.

- **Ohne Kopie keine Migration.** Reicht der Platz nicht (nötig ist gut das 1,2-fache der Datenbank), startet Nodvard Deck nicht, sondern zeigt die
  Notseite mit der Zahl, wie viel fehlt (siehe [13](#13-nodvard-deck-startet-nicht-die-notseite)). Platz schaffen, „Neu versuchen“.
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
- **Nicht in der Kopie** sind Dateien außerhalb der Datenbank (Daten der Erweiterungen, Schlüssel). Migrationen fassen sie nicht an. Für alles andere gibt es die Sicherung ([10.1](#101-in-der-oberfläche-empfohlen)).
- **Notausgang** (nicht empfohlen): `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1` in der Compose-Datei schaltet die Kopie ab, damit auch bei zu wenig Platz migriert wird. Scheitert die Migration dann, gibt es keinen Rückweg.
- Was beiseitegelegt wurde (`restore/replaced-…`: der alte Stand einer Wiederherstellung oder eine Datenbank, die ein Rückweg ersetzt hat), wird nach 30 Tagen automatisch gelöscht.
- **Datenbank auf einem eigenen Laufwerk** (eigener `NODVARD_DECK_DATABASE_URL`, z. B. eine USB-SSD): Zum Beiseitelegen wird sie dann in den Datenordner kopiert.
  Dafür muss dort so viel Platz frei sein, wie die Datenbank groß ist; sonst zeigt die Notseite „zu wenig Platz“ mit den Zahlen, und es wurde nichts verändert.
  Nur den halben Stand einer abgebrochenen Migration verwirft der Start in diesem Fall, statt auf der Notseite hängen zu bleiben.
- **Eine Sicherung einspielen, deren Migration abbricht** (Strom weg mitten drin): Der nächste Start nimmt das Einspielen zurück, und es gilt wieder genau der alte
  Stand mit seinen Schlüsseln. Er wird danach nie durch die Sicherung ersetzt.
- Bei einer anderen Datenbank als SQLite gibt es keine Kopie (bitte vorher selbst sichern).

### 10.5 Neue Version finden und einspielen

Unter **Einstellungen → System → „Updates“** steht, welche Version läuft und welche die neueste ist. Nodvard Deck sieht dazu **einmal am Tag**
bei ghcr.io nach, wo die fertigen Images liegen; mit **„Jetzt suchen“** geht es sofort (höchstens einmal pro Minute).

- **Datenschutz:** ghcr.io gehört zu **GitHub**: Bei jeder Suche sieht GitHub die IP-Adresse deines Anschlusses und den Zeitpunkt; sonst wird
  nichts übertragen, auch nicht die installierte Version. **Du kannst die Suche abschalten:** „Täglich automatisch nach Updates suchen“ aus
  (Recht `settings.write`); dann fragt das Dashboard von sich aus nie nach. „Jetzt suchen“ fragt auch bei ausgeschaltetem Schalter nach – das
  kann jeder mit dem Recht `system.read`, und auch diese Anfrage sieht GitHub.
- **Welche Versionen:** „Nur fertige Versionen“ (Vorgabe) oder „Auch Vorabversionen (Beta)“. Vorabversionen (z. B. `0.7.0-rc1`) gibt es nicht
  unter `:latest`: Zum Einspielen trägst du bei `image:` genau `ghcr.io/nodvard/deck:0.7.0-rc1` ein.
- **Ohne Internet** steht dort „Konnte nicht prüfen (offline?)“ und der letzte bekannte Stand; das ist kein Fehler. „Keine gefunden“ heißt:
  Die Abfrage hat geklappt, aber im gewählten Kanal gibt es noch keine Version.
- **Einspielen** macht nicht das Dashboard selbst, sondern deine Umgebung. Die Karte hat Anleitungen als Reiter: Docker Compose, Docker Desktop,
  Portainer („Update the stack“ mit „Re-pull image“), Synology Container Manager, Unraid („Check for Updates“ → „Apply update“) und
  `scripts/deploy_pi.sh`. Mit Compose (Installation wie in [1.1](#11-installation-ohne-das-repo)) im Ordner mit der `compose.yml`:

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
  dabei verloren. Oder wieder die neue Version eintragen. Einzelheiten in [10.4](#104-updates-die-kopie-vor-der-migration) und
  [13](#13-nodvard-deck-startet-nicht-die-notseite).
- Läuft ein **selbst gebautes** Image (z. B. vom PC über `scripts/deploy_pi.sh`), sagt die Karte das; aktualisiert wird dann auf demselben Weg.

## 11. Wenn etwas hakt

**„HTTP 401“ oder „Nicht authentifiziert“ auf einer Erweiterungsseite** (Proxmox, Backups,
Skripte …): Im sichtbaren Tab erneuert Nodvard Deck die Anmeldung selbst. Lag der Tab lange im
Hintergrund, kann es noch passieren – Seite neu laden (F5).

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

- `All connection attempts failed` oder Zeitüberschreitung: Server aus, falsche Adresse
  oder Port. Die Adresse braucht `https://` und `:8006`.
- `CERTIFICATE_VERIFY_FAILED`: Haken „Selbstsigniertes Zertifikat erlauben“ fehlt
  ([docs/10](10-PROXMOX-TOKEN.md#zertifikat-was-selbstsigniertes-zertifikat-erlauben-macht)).
- `HTTP 401`: Token-ID oder Geheimnis falsch, oder Token in Proxmox gelöscht.
- `HTTP 403 … Permission check failed`: Recht fehlt – Tabelle in
  [docs/10](10-PROXMOX-TOKEN.md#nachschlagen-welcher-aufruf-welches-recht-braucht).
  Fehlen einzelne VMs ganz, fehlt ihnen `VM.Audit`.
- Testen vom Rechner mit Nodvard Deck aus: der `curl`-Befehl in
  [docs/10](10-PROXMOX-TOKEN.md#prüfen-bevor-du-es-in-nodvard-deck-einträgst).
- Ein ausgefallener Proxmox (z. B. pve2 aus) bremst die anderen nicht mehr, er steht nur
  als „nicht erreichbar“ da. Ist er länger aus: auf der Proxmox- und der Backups-Seite unter
  „Verbindungen verwalten“ auf „deaktiviert“ schalten, dann kommen auch keine Meldungen.

**Server fehlt im Terminal / „Noch kein Server prüfbar“ oder „Keine Linux-Server mit SSH-Zugang gefunden“ bei Nodvard Shield:** Kein
SSH-Zugang hinterlegt: Einstellungen → Server & Zugänge ([5.2](#52-ssh-zugang-einrichten--drei-wege)). Nodvard Shield nimmt außerdem nur Server, die als `linux`
eingetragen sind, und – wenn in seinen Einstellungen „Nur Server mit Markierung“ ausgefüllt ist – nur Server mit dieser Markierung.

**„Keine root-Rechte …“:** sudo ohne Passwort fehlt ([6.](#6-root-rechte-ohne-passwort-und-gruppe-docker)). Die Karte „Verbindung prüfen“ zeigt es unter „Root-Rechte“.

**„Der Server-Schlüssel von diesem Server ist noch nicht bestätigt.“:** Nodvard Deck kennt den
Fingerabdruck dieses Servers noch nicht (neuer Server, und „Neue Server-Schlüssel erst nach meiner
Bestätigung merken“ ist an) oder du hast ihn vergessen. Unter Server & Zugänge beim Server „Verbindung
prüfen“ drücken und den Fingerabdruck bestätigen ([5.4](#54-verbindung-prüfen)).

**„Host-Schlüssel … hat sich geändert“:** Server neu installiert? Dann unter Server & Zugänge
beim Server in der Karte „Server-Schlüssel“ den alten vergessen, erneut prüfen und den neuen
Fingerabdruck bestätigen. Wenn nicht: erst herausfinden, warum – das kann auch ein Angriff sein.

**„Server antwortet nicht (Zeitüberschreitung …)“, „Der Server lehnt die Verbindung ab …“, „Kein Weg zum Server …“
oder „Den Namen … kennt das Netz nicht.“** im Terminal oder Dateimanager (dort mit „Zugriff auf die Quelle
fehlgeschlagen:“ davor): Nodvard Deck erreicht den Server nicht. Der Satz nennt meist Adresse und Port. Prüfen, ob der
Server an ist, die Adresse unter Server & Zugänge stimmt und dort SSH läuft. Im Log steht dazu kein Traceback.

**„Server nicht erreichbar – bitte gleich noch einmal versuchen.“ bei der Anmeldung:** Das
Dashboard antwortet nicht. Auf dem Rechner mit Nodvard Deck
`curl -s http://localhost:8080/api/v1/health` und, im Ordner mit der Compose-Datei,
`sudo docker compose ps` prüfen.
Steht stattdessen **„Server meldet: …“** da (auch beim Neuladen der Seite), antwortet das
Dashboard und nennt den Grund, z. B. dass die Platte voll ist – dann diesen Grund beheben.
Abgemeldet wird man dabei nicht.

**„Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal.“ bei der
Anmeldung:** Nodvard Deck prüft nur wenige Passwörter gleichzeitig, damit viele Anmeldeversuche auf einmal das Dashboard
nicht ausbremsen. Ein paar Sekunden warten und noch einmal versuchen; wer schon angemeldet ist, arbeitet normal weiter.
Kommt die Meldung öfter, gibt es sehr viele Anmeldeversuche auf einmal; fehlgeschlagene stehen im Protokoll
(„Anmeldung fehlgeschlagen“). Bei einem Namen, den es nicht gibt, steht dort unter „Wer“ nur
„Nicht angemeldet · unbekannt“, nie die Eingabe selbst (es könnte ein ins Namensfeld getipptes Passwort sein). Nur der
Inhaber sieht beim Aufklappen der Zeile eine Kennung (`username_ref`) und die Länge der Eingabe; gleiche Eingaben haben
dieselbe Kennung.

## 12. Ausgesperrt? Notfall-Befehle

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
zurückgesetzt oder abgeschaltet, Notfall-Befehl), endet die Anmeldung sofort – wer nur noch
einen schon ausgestellten Zugangs-Schlüssel im Browser hat, kann damit aber noch **bis zu
15 Minuten** weiterarbeiten, bis dieser von selbst abläuft. Bei einem ernsten Verdacht also
zusätzlich Passwort ändern und ein paar Minuten warten. Terminal und Konsole warten diese Frist nicht ab:
Offene Sitzungen einer beendeten Anmeldung schließen sich nach spätestens 15 Sekunden, neue lassen
sich mit ihr nicht mehr nutzen.

## 13. Nodvard Deck startet nicht: die Notseite

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
   - **Zurück auf die alte Version:** Image-Version in der Compose-Datei zurückstellen und den Container neu starten (siehe [10.4](#104-updates-die-kopie-vor-der-migration)).
4. `GET /api/v1/health` antwortet auf der Notseite mit **503** `{"status":"rescue"}`; der Healthcheck des Containers zeigt deshalb „unhealthy“. Wer aus dem Repository mit `scripts/deploy_pi.sh`
   ausliefert, bekommt dadurch automatisch das alte Image zurück (ohne die volle Wartezeit).

Was die Notseite **nicht** kann: eine Kopie zum Herunterladen anbieten. Die Kopie liegt im Datenordner unter `backups/vor-update/` und lässt sich dort (oder per `docker cp`) sichern.
Fällt die Notseite selbst aus, wartet der Container, statt sich zu beenden – Ursache im Protokoll, dann von Hand neu starten.

Wichtig: Ein zweiter Container mit demselben Datenordner (`docker compose run` neben dem laufenden Dienst) ändert nichts; `nodvard_deck.boot` beendet sich dort mit Code 75, weil die laufende Anwendung (oder die Notseite)
die Sperre des Datenordners hält.

## Anhang: Ohne Oberfläche

Normalerweise nimmst du die Seite „Server & Zugänge“ (5.). Nur wenn die Oberfläche nicht
erreichbar ist (z. B. beim ersten Einrichten per SSH auf dem Rechner mit Nodvard Deck), geht
alles auch über die Nodvard-Deck-API mit diesem kleinen Hilfsskript. Es gibt keinen Einrichtungsbefehl und keine
Verbindungsprüfung – den Schlüssel erzeugst du selbst, und den öffentlichen Teil trägst du von
Hand in `~/.ssh/authorized_keys` ein.

**Schlüssel erzeugen** (einmal, auf dem Rechner mit Nodvard Deck):

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/nodvard_ed25519 -C nodvard
cat ~/.ssh/nodvard_ed25519.pub        # diese eine Zeile kommt auf die Server
```

Ohne Passphrase (`-N ""`), weil Nodvard Deck beim Verbinden keine Passphrase abfragt. Nodvard Deck
bekommt eine eigene, verschlüsselte Kopie des privaten Schlüssels; die Datei auf diesem Rechner
brauchst du nur noch zum Eintragen und Testen.

**Hilfsskript speichern** (einmal, auf demselben Rechner) – den ganzen Block in die Shell kopieren, er legt
`~/lattice-server.py` an:

```bash
cat > ~/lattice-server.py <<'EOF'
#!/usr/bin/env python3
"""Nodvard Deck: Server anlegen, Adresse korrigieren, SSH-Zugang hinterlegen (docs/11)."""
import getpass, json, os, urllib.error, urllib.request

API = os.environ.get("LATTICE_URL", "http://localhost:8080").rstrip("/") + "/api/v1"
token = None


class Fehler(Exception):
    pass


def call(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raise Fehler(f"HTTP {err.code}: {err.read().decode(errors='replace')[:300]}") from None


def ask(text, default=""):
    value = input(f"{text}{f' [{default}]' if default else ''}: ").strip()
    return value or default


try:
    status, data = call("POST", "/auth/login", {
        "username": ask("Nodvard-Deck-Benutzer").lower(),
        "password": getpass.getpass("Passwort: "),
        "client_type": "cli",
    })
    if status == 202:  # Zwei-Faktor-Anmeldung ist eingeschaltet
        status, data = call("POST", "/auth/mfa", {"mfa_token": data["mfa_token"], "code": ask("Code aus der App")})
except Fehler as exc:
    raise SystemExit(f"Anmeldung fehlgeschlagen: {exc}")
token = data["access_token"]

while True:
    try:
        _, hosts = call("GET", "/hosts")
    except Fehler as exc:
        raise SystemExit(f"{exc} -- nach 15 Minuten läuft die Anmeldung ab, dann Skript neu starten.")
    print()
    for h in sorted(hosts, key=lambda h: h["name"]):
        print(f"  {h['id']}  {h['name']:<26} {h['address']:<16} {h['os_family']}")
    print("\n1 = Server anlegen  2 = Adresse ändern  3 = SSH-Zugang hinterlegen  4 = SSH-Zugänge zeigen")
    print("5 = gemerkten SSH-Host-Schlüssel vergessen  q = Ende")
    choice = ask("Auswahl", "q")
    try:
        if choice == "1":
            name = ask("Kurzname, klein und ohne Leerzeichen (z. B. pi-host)")
            _, h = call("POST", "/hosts", {
                "name": name,
                "display_name": ask("Anzeigename", name),
                "address": ask("IP-Adresse"),
                "os_family": ask("Betriebssystem (linux/windows)", "linux"),
                "tags": [t.strip() for t in ask("Markierungen mit Komma (z. B. docker), leer = keine").split(",") if t.strip()],
            })
            print("Angelegt, ID:", h["id"])
        elif choice == "2":
            host_id = ask("ID des Servers")
            call("PATCH", f"/hosts/{host_id}", {"address": ask("Neue IP-Adresse")})
            print("Gespeichert.")
        elif choice == "3":
            host_id = ask("ID des Servers")
            user = ask("SSH-Benutzer", "nodvard")
            port = int(ask("SSH-Port", "22"))
            if ask("Anmeldung mit Schlüssel (s) oder Passwort (p)?", "s") == "p":
                kind, secret = "ssh_password", getpass.getpass("SSH-Passwort: ")
            else:
                with open(os.path.expanduser(ask("Privater Schlüssel", "~/.ssh/nodvard_ed25519"))) as f:
                    kind, secret = "ssh_key", f.read()
            call("POST", f"/hosts/{host_id}/credentials",
                 {"kind": kind, "username": user, "port": port, "secret_value": secret, "is_default": True})
            print("SSH-Zugang gespeichert und ab jetzt der Standard für diesen Server.")
        elif choice == "4":
            _, creds = call("GET", f"/hosts/{ask('ID des Servers')}/credentials")
            for c in creds:
                print(f"  {c['kind']:<13} {c['username']} Port {c['port']}{'  (Standard)' if c['is_default'] else ''}")
            if not creds:
                print("  Noch kein SSH-Zugang hinterlegt.")
        elif choice == "5":
            host_id = ask("ID des Servers")
            call("DELETE", f"/hosts/{host_id}/known-hosts/{ask('Schlüsseltyp aus der Fehlermeldung', 'ssh-ed25519')}")
            print("Vergessen. Jetzt in der Oberfläche „Verbindung prüfen“ drücken und den neuen Fingerabdruck bestätigen – vorher verbindet sich Nodvard Deck nicht mehr mit dem Server.")
        elif choice.lower() == "q":
            break
    except (Fehler, OSError, ValueError) as exc:
        print("Fehler:", exc)
EOF
```

**Benutzen:**

```bash
python3 ~/lattice-server.py
```

Anmelden mit einem Nodvard-Deck-Konto, das Server verwalten darf (Inhaber oder `admin`). Das Skript
zeigt alle Server mit ihrer **ID** (die steht auch in der Browser-Adresse einer Server-Seite:
`/hosts/<ID>`). Mit 1 legst du einen Server an, mit 2 änderst du die Adresse (ein gespeichertes
SSH-Passwort wird dabei gelöscht, danach mit 3 neu hinterlegen), mit 3 hinterlegst du
einen SSH-Zugang (Schlüssel `~/.ssh/nodvard_ed25519` oder Passwort), mit 4 zeigst du die Zugänge,
mit 5 vergisst du einen gemerkten Server-Schlüssel (nach einer Neuinstallation des Servers).
Fingerabdrücke bestätigt das Skript nicht. Nach „Vergessen“ (5) verbindet sich Nodvard Deck erst wieder, wenn du in
der Oberfläche „Verbindung prüfen“ gedrückt und den neuen Fingerabdruck bestätigt hast. Dasselbe gilt für neue Server,
solange „Neue Server-Schlüssel erst nach meiner Bestätigung merken“ an ist (bei neuen Installationen von Anfang an).

Der Schlüssel auf dem Server wird dann von Hand eingetragen, z. B. für einen eigenen Benutzer:

```bash
sudo adduser --disabled-password --gecos "Nodvard Deck" nodvard
sudo install -d -m 700 -o nodvard -g nodvard /home/nodvard/.ssh
echo 'HIER DIE ZEILE AUS nodvard_ed25519.pub' | sudo tee /home/nodvard/.ssh/authorized_keys
sudo chown nodvard:nodvard /home/nodvard/.ssh/authorized_keys
sudo chmod 600 /home/nodvard/.ssh/authorized_keys
sudo usermod -aG docker nodvard        # nur wo Docker läuft
```

Für Root-Rechte ohne Passwort (siehe [6.](#6-root-rechte-ohne-passwort-und-gruppe-docker) – praktisch root!):

```bash
echo 'nodvard ALL=(root) NOPASSWD: ALL' > /tmp/nodvard-sudo
sudo visudo -cf /tmp/nodvard-sudo && sudo install -m 0440 -o root -g root /tmp/nodvard-sudo /etc/sudoers.d/nodvard-nodvard
rm /tmp/nodvard-sudo
sudo visudo -c
```

Meldet das letzte `visudo -c` einen Fehler, die Datei **sofort** wieder löschen
(`sudo rm /etc/sudoers.d/nodvard-nodvard`) – eine kaputte sudo-Datei kann sudo lahmlegen. Der
Dateiname darf keinen Punkt enthalten, sonst ignoriert sudo die Datei. Prüfen, vom Rechner mit Nodvard Deck aus:

```bash
ssh -i ~/.ssh/nodvard_ed25519 nodvard@SERVER-IP 'sudo -n true && echo sudo-ok; docker ps >/dev/null && echo docker-ok'
```
