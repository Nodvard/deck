# Root-Rechte ohne Passwort und Gruppe docker

> English version: [en/ROOT-RIGHTS.md](en/ROOT-RIGHTS.md)

Die Kurzfassung steht in der
[Erst-Einrichtung, Anhang F](11-ERST-EINRICHTUNG.md#f-root-rechte-ohne-passwort-und-gruppe-docker). Hier steht alles
im Detail: wofür die Module root brauchen, was der Einrichtungsbefehl dafür auf dem Server einrichtet, was die
Hilfsskripte dürfen und wie du eine volle sudo-Regel wieder loswirst.

- [Wofür die Haken nötig sind](#wofür-die-haken-nötig-sind)
- [Was der Haken „Root-Rechte“ genau tut](#was-der-haken-root-rechte-genau-tut)
- [Die volle sudo-Regel](#die-volle-sudo-regel)
- [Das ist praktisch root](#das-ist-praktisch-root)
- [Hilfsskripte](#hilfsskripte) und [was sie dürfen](#was-die-hilfsskripte-dürfen)
- [Docker auf „Nur bedienen“ umstellen](#docker-auf-nur-bedienen-umstellen)
- [Proxmox-Knoten und vorhandene Benutzer](#proxmox-knoten-und-vorhandene-benutzer)
- [Was der Datei-Wächter nach der Einrichtung meldet](#was-der-datei-wächter-nach-der-einrichtung-meldet)

## Wofür die Haken nötig sind

Meldet sich Nodvard Deck auf einem Server **nicht als root** an (z. B. auf dem Pi), führt es
root-Befehle über `sudo -n` aus, also ohne Passwortabfrage. Verlangt sudo ein Passwort,
scheitert die Funktion mit „Keine root-Rechte: Das Dashboard ist auf diesem Server nicht
als root angemeldet und darf sudo nicht ohne Passwort nutzen.“

Den Einrichtungsbefehl ([Erst-Einrichtung, 3.3](11-ERST-EINRICHTUNG.md#33-einrichtungsbefehl-auf-dem-server-ausführen)) musst du dafür **nicht von Hand ändern**: Die Seite setzt die
passenden Haken selbst, weil die Erweiterungen melden, was sie brauchen. Dieselben Haken gelten, wenn du den Server
mit Passwort einrichtest (Nodvard Deck führt dann denselben Befehl aus).

- **„Root-Rechte ohne Passwort“** – nötig für (die Seite nennt es hinter dem Haken):
  - **Nodvard Shield, Virenschutz:** Datei in Quarantäne verschieben, wiederherstellen oder löschen,
    Lynis-Härtungsaudit, Werkzeuge installieren (ClamAV, Lynis, Signaturen, Fail2ban,
    automatische Updates). Scans laufen auch ohne root, prüfen dann aber nur Dateien, die der
    Benutzer lesen darf. Ob ClamAV und Lynis installiert sind, erkennt Nodvard Shield auch ohne root
    (Lynis liegt bei Debian und dem Betriebssystem des Pi unter `/usr/sbin`); nach einer Installation fragt es den
    Server gleich neu ab. Das **Härtungs-Audit** läuft auf dem Server im Hintergrund mit niedriger
    Priorität und kann je nach Server einige Minuten bis über eine Stunde dauern (Obergrenze 3 Stunden).
    Nach dem Klick steht sofort „Audit läuft seit …“ beim Server, ein zweiter Klick startet kein zweites
    Audit, und das Dashboard darf währenddessen neu starten. Die häufigsten Hinweise von Lynis stehen im Ergebnis mit
    deutscher Überschrift; ein Klick darauf zeigt „Was heißt das?“ und „Was kannst du tun?“ (vieles ist für ein Heimnetz
    bewusst nicht nötig, das steht dann dabei). Ganz oben stehen „Deine wichtigsten Baustellen“: höchstens fünf Hinweise
    mit der größten Wirkung auf den angezeigten Servern, mit den Servern, auf denen sie vorkommen. Die Protokolle liegen auf dem Server unter
    `/var/lib/nodvard-shield-audit/`. Scans und Echtzeit-Wächter laufen auf einem Server nie
    gleichzeitig; für diese Sperre legt Shield dort einen kleinen Ordner an: `/run/nodvard-shield` (als
    root) bzw. `~/.nodvard-shield`. In die Quarantäne kommt eine Datei nur, wenn ClamAV sie dabei selbst noch einmal
    als Fund meldet, erst an ihrem Ort und dann in einem Prüfordner, in den nur root schreibt; das gilt mit und ohne
    Hilfsskript. Fehlt ClamAV oder antwortet es nicht, bleibt die Datei, wo sie ist. Wiederhergestellt wird nur an den
    Ort und mit den Rechten, die Shield beim Verschieben auf dem Server hinterlegt hat (auch besondere wie `4755` oder
    `044`), ebenfalls mit und ohne Hilfsskript. Rückfrage und Freigabe nennen den Pfad aus dem Fund deshalb nur „laut
    Fund“; nach dem Zurücklegen nennt Shield den Ort, an den die Datei wirklich ging, und weicht er vom Fund ab, steht
    das auch am Fund. Fehlt der Eintrag oder ist er ungültig (etwa bei einer Datei, die mit Nodvard Deck 0.8 oder älter
    in die Quarantäne kam), bleibt die Datei dort, und du legst sie auf dem Server von Hand zurück oder löschst sie.
    Einen Fund, den du ignoriert oder wiederhergestellt hast, verschiebt der nächste Scan nicht noch einmal in die
    Quarantäne (außer ClamAV meldet dieselbe Datei mit einer anderen Signatur). Zurück geht das unter „Funde &
    Quarantäne“ mit dem Filter „Alle, inkl. erledigt“ und „Wieder beachten“: Der Fund ist dann wieder offen, und der
    nächste Scan meldet die Datei wieder.
  - **Schutzwert** (0 bis 100, auf der Übersicht): zählt den Virenschutz (ClamAV, Signaturen, Scans, offene Funde),
    die Härtung und außerdem offene Sicherheitsupdates, nötige Neustarts, Fail2ban, die SSH-Einstellungen (root oder
    Anmeldung mit Passwort, nach der Zahl der betroffenen Server) und offene kritische Sicherheitsereignisse (je 5 Punkte,
    höchstens 15). Darunter steht, was ihn gerade senkt und wie viele Punkte das kostet. Ist die letzte Update- oder
    Einbruchschutz-Prüfung eines Servers gescheitert oder älter als drei Tage, zählt sein alter Stand nicht; das steht
    dann als Grund „zuletzt nicht geprüft (ohne Abzug)“ da.
  - **Update-Zentrale:** Updates einspielen und neu starten. Das Neuladen der Paketlisten vor der
    Prüfung braucht root – ohne bleibt es beim zuletzt geladenen Stand. Updates laufen auf dem
    Server im Hintergrund (mit systemd als `lattice-upgrade-…`, ein
    [alter Name, der bleibt](11-ERST-EINRICHTUNG.md#m-alte-namen-bei-älteren-installationen), sonst per `setsid`) und laufen
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
    Läuft Fail2ban, zeigt jeder Server unter „Gesperrte Adressen“ die gesperrten IPs, nach Jail (der Schutzregel für
    einen Dienst, etwa `sshd` für SSH) sortiert, mit „Entsperren“ an jeder Adresse. Ab 20 Adressen gibt es eine Suche,
    gezeigt werden höchstens 200 auf einmal. Darunter sperrst du eine einzelne Adresse von Hand, im Jail `sshd` oder
    einem anderen, den der Server hat; welche das sind, weiß Shield erst nach einer Prüfung des Servers. Eine Sperre
    endet meist nach der Sperrzeit des Jails von selbst. Nicht gesperrt werden Adressen, die zu keinem anderen Rechner
    gehören (etwa `127.0.0.1`), die Adresse deines Browsers (hinter einem Reverse-Proxy sicher erst, wenn er unter
    „Vertrauenswürdiger Proxy“ steht; sonst vergleicht Shield zusätzlich die Angaben des Proxys in `X-Forwarded-For`)
    und die Adresse, über die Nodvard Deck den Server erreicht; sonst sperrt man sich selbst aus. Die letzte prüft auch der Server selbst, bevor er sperrt
    (aus `SSH_CLIENT` bzw. `SSH_CONNECTION`); kann er sie nicht feststellen, sperrt er nichts. Hat der Server keinen
    Jail für SSH, bietet Shield bei Angreifern kein „Sperren“ an. War eine Adresse beim Entsperren schon frei, sagt
    Shield das, statt einen Fehler zu melden.
    Einen neuen offenen Port meldet der Einbruchschutz einzeln („Neuer offener Port“). Programme, die bei jedem Start
    oder je Anfrage zufällige Ports wählen (NFS, Tailscale, Pi-hole und andere DNS-Dienste), zählen als ein Eintrag. Für
    ein Programm mit eigenem Portbereich, etwa einen Spieleserver, trägst du unter Einstellungen → Erweiterungen →
    „Nodvard Shield“ → „Erweitert“ → „Eigene Portbereiche je Programm“ Programm, Protokoll und Bereich ein. Neue Ports
    darin melden dann nicht mehr einzeln. Den Programmnamen kann sich jedes Programm selbst geben, auch eine
    Schadsoftware: nur Programme eintragen, denen du vertraust, und die Bereiche klein halten. Ports unter 1024 zählen
    nur mit dem Häkchen „Auch Ports unter 1024“. Ein Bereich umfasst höchstens 1000 Ports (ein größerer zählt nur bis
    dahin), mehr nur mit dem Häkchen „Breiter Bereich“; ein breiter Bereich beginnt immer frühestens bei 1024, SSH und
    andere feste Dienste bleiben also sichtbar. Es zählen höchstens 50 Bereiche.
  - **Server-Zustand:** Dienste (systemd) neu starten. Danach sieht Deck bis zu 45 Sekunden lang nach, ob der Dienst läuft
    und laufen bleibt (zweimal im Abstand von 10 Sekunden ohne neuen Start; das Nachsehen braucht kein root). Fällt er
    gleich wieder aus oder startet er immer wieder neu, gilt die Aktion als fehlgeschlagen und sagt das.
  - **Docker im Modus „Nur bedienen“** (Server nur mit der Markierung `docker-bedienen`): Container ansehen, Logs lesen,
    starten, stoppen und neu starten über das Hilfsskript `docker-root`, ohne die Gruppe docker.
- **„Zur Gruppe docker hinzufügen“** – nötig für das Modul Docker und die KI-Container-Wache von
  Nodvard Shield; sie rufen `docker` ohne sudo auf. Die Gruppe wird nur eingetragen, wenn es sie auf
  dem Server gibt. Einem Server nur mit der Markierung `docker-bedienen` bietet die Seite die Gruppe nicht an; dort
  reicht das Hilfsskript.

Ohne beide Haken bekommst du die **schlanke Variante**: eigener Benutzer, nur Schlüssel, keine
Root-Rechte. Die reicht für Terminal, Dateien und Skripte.

## Was der Haken „Root-Rechte“ genau tut

Das hängt davon ab, welche Module auf diesem Server root brauchen.

- **Nur die Hilfsskripte (Standard):** Braucht hier mindestens ein Modul root und hat jedes davon ein Hilfsskript
  (Nodvard Shield, Server-Zustand und Docker im Modus „Nur bedienen“ haben eins), richtet der Haken nur diese Skripte
  ein (siehe „Hilfsskripte“ unten). Jedes bekommt eine
  eigene Regel `<benutzer> ALL=(root) NOPASSWD: sha256:<prüfsumme> /usr/local/lib/nodvard-deck/<modul>-root`, eine
  volle sudo-Regel gibt es nicht. Mit der Prüfsumme startet sudo das Skript nur, solange es Byte für Byte das ist, für
  das die Regel eingerichtet wurde. Kennt sudo auf dem Server keine Prüfsummen in Regeln oder zeigt es sie in `sudo -l`
  nicht (sudo-rs, der Standard ab Ubuntu 25.10, und ältere sudo-Versionen), gilt die Regel ohne sie, und der Befehl sagt
  das. Zu tun ist dann nichts: Das Skript kann weiter nur root ändern. Schon eingerichtete Server bekommen die Prüfsumme,
  wenn der Einrichtungsbefehl das nächste Mal läuft. Der Benutzer darf per sudo genau diese Skripte starten, und was sie tun, steht in ihnen selbst (etwa Updates
  einspielen, Quarantäne, Dienste neu starten). Unter „Was macht der Befehl?“ kannst du jedes Skript lesen.
  Vorher prüft der Befehl, ob sich die Skripte sicher einrichten lassen: `sudo`, `base64`, `sha256sum` und `stat` müssen
  da sein, jeder Ordner über `/usr/local/lib/nodvard-deck` muss ein echter Ordner sein, der root gehört und in dem außer
  root niemand schreiben darf: keine Gruppe, auch nicht die Gruppe root, und kein anderer. Eine ACL kann dabei nur
  schreiben lassen, wenn die Gruppenbits es erlauben (sie sind dann die Maske), deshalb reicht der Blick auf sie. Gibt
  es `/usr/local/lib/nodvard-deck` schon, gilt für ihn dasselbe (sonst legt der Befehl ihn an). Klappt das nicht, ändert
  er **nichts**, nennt den Ordner, den Grund und die Abhilfe (etwa `chmod g-w /usr/local/lib` oder
  `mkdir -p /usr/local/lib`) und endet mit einem Fehler; „Mit Passwort einrichten“ zeigt dieselben Zeilen. Ältere
  Debian lassen die Gruppe staff in `/usr/local` und `/usr/local/lib` schreiben; dort hilft als root
  `chmod g-w /usr/local /usr/local/lib`. Dann den Grund beheben oder, außer wenn sudo fehlt, „Volle Rechte“ wählen und noch
  einmal einrichten. Docker im Modus „Nur bedienen“ hilft „Volle Rechte“ nicht: Es arbeitet nur über sein Hilfsskript,
  und das sagt der Befehl dann auch.
- **Volle Rechte:** Braucht ein Modul root und hat kein Hilfsskript, oder setzt du unter „Erweitert“ das Häkchen
  „Volle Rechte“, kommt die volle Regel dazu. Das Häkchen brauchst du nur, wenn du über Nodvard Deck eigene Befehle mit
  sudo startest, etwa in Skripten.
- **Kein Modul braucht root:** Dann richtet der Haken gar kein sudo ein, auch keine volle Regel. Die Seite sagt das unter
  dem Haken. Willst du trotzdem sudo, etwa für eigene Befehle in Skripten, setz unter „Erweitert“ „Volle Rechte“.

## Die volle sudo-Regel

Eine schon vorhandene volle Regel nimmt der Befehl nur zurück, wenn du es ausdrücklich willst: Ein Server, der sie hat,
läuft weiter wie bisher, auch wenn du den Befehl dort noch einmal ausführst. Willst du sie loswerden, führ den Befehl
zuerst noch einmal aus und drück „Verbindung prüfen“. Hat dann jedes Hilfsskript seine eigene Regel, steht bei
„Root-Rechte“ gelb „sudo ohne Passwort für alles ist hier nicht nötig. Die Hilfsskripte reichen.“, und unter „Braucht
Aufmerksamkeit“ in der Übersicht steht ein dezenter Hinweis (alle 6 Stunden geprüft, mit × ausblendbar). Der Hinweis
führt zum Einrichtungsbefehl, dort ist unter „Erweitert“ schon „Volle sudo-Regel entfernen“ angekreuzt. Führ den Befehl
aus (selbst einfügen oder „Mit Passwort einrichten“) und prüf noch einmal: Bei „Root-Rechte“ steht dann „sudo erlaubt
nur die Hilfsskripte der Module, keine volle Regel. Das reicht.“

So geht der Befehl dabei vor, damit du dich nicht aussperrst:

- Er richtet zuerst wie immer die Hilfsskripte und ihre Regeln ein. Geht dabei etwas schief, rührt er die volle Regel
  nicht an.
- Gibt es keine Regel von Nodvard Deck (nur Links, andere Inhalte oder gar keine), sagt er das und ändert nichts.
- Er löscht nur `/etc/sudoers.d/nodvard-<benutzer>` und `lattice-<benutzer>`, und nur, wenn die Datei genau die Zeile
  `<benutzer> ALL=(root) NOPASSWD: ALL` enthält. Links und Dateien mit anderem Inhalt bleiben stehen, mit Hinweis.
- Vorher fragt er jedes Skript über sudo nach seiner Version. Das zeigt nur, dass es läuft (noch mit voller Regel);
  antwortet eins nicht, bleibt die volle Regel stehen.
- Nach dem Löschen prüft er die sudo-Konfiguration mit `visudo -c` (wenn sie vorher in Ordnung war) und fragt jedes
  Skript noch einmal, jetzt ohne volle Regel. Klappt etwas davon nicht oder bricht der Lauf ab (etwa weil die Verbindung
  reißt), legt er die Regel sofort wieder an. Schlüssel und Anmeldung ändert er nie.
- Darf der Benutzer danach über eine fremde Regel weiter alles (etwa `010_pi-nopasswd` auf Raspberry Pi OS oder die
  Gruppe `sudo` mit NOPASSWD), sagt er das. Diese Regel bleibt stehen, ob sie weg kann, entscheidest du. Solange sie
  gilt, zeigt die Prüfung nicht, ob die Hilfsskripte allein reichen.

Danach gehen eigene Befehle mit sudo über Nodvard Deck nicht mehr. Bringt ein Update von Nodvard Deck neue
Hilfsskripte, haben die Module dort erst wieder root, wenn du den Server noch einmal einrichtest; die Übersicht sagt es
dir.

Willst du die volle Regel zurück, richte den Server mit „Volle Rechte“ noch einmal ein. Brauchst du sie für eigene
Befehle mit sudo, setz beim Server unter „Bearbeiten“, „Erweitert“ das Häkchen „Volle sudo-Regel gewollt“: Dann bleibt
„Root-Rechte“ grün, und der Hinweis kommt nicht. Braucht auf dem Server gar kein Modul root, bringt der Befehl keine
Hilfsskripte mit und kann die Regel darum nicht entfernen. Brauchst du sudo dann nicht für eigene Befehle über Nodvard
Deck, lösch sie selbst (`sudo rm /etc/sudoers.d/nodvard-<benutzer>`, auf älteren Servern `lattice-<benutzer>`).
„Verbindung prüfen“ zeigt danach bei „Root-Rechte“: „Kein eingeschaltetes Modul braucht hier root, sudo ohne Passwort
ist nicht nötig.“

Die volle Regel ist `/etc/sudoers.d/nodvard-<benutzer>` mit
der Zeile `<benutzer> ALL=(root) NOPASSWD: ALL`. Die Zeile wird erst in eine Probedatei geschrieben
und mit `visudo -c` geprüft; nur dann kommt sie mit den Rechten `0440` (root) an ihren Platz.
Danach prüft `visudo` die eingetragene Datei und die ganze Konfiguration noch einmal. Bei einem Fehler wird die Datei
sofort wieder entfernt („sudo-Regel zurückgenommen“), darunter steht, was `visudo` meldet. Meldet `visudo -c` schon
**vor** der Einrichtung einen Fehler in einer fremden Datei (z. B. „bad permissions, should be mode 0440“, weil eine
Datei in `/etc/sudoers.d` die Rechte `0644` hat), zählt nur die Prüfung der eigenen Dateien. Der Befehl nennt dann die
fremde Datei im Hinweis; mit `sudo chmod 0440 <Datei>` und `sudo chown root:root <Datei>` ist sie in Ordnung. Eine ältere Regel `lattice-<benutzer>` löscht der Befehl erst nach dieser
Prüfung und nur, wenn sie aus genau der Zeile oben besteht; sonst bleibt sie stehen, und du bekommst einen Hinweis.
Ist `sudo` gar nicht installiert, sagt der Befehl das
(als root `apt install sudo`; bei Proxmox am besten gleich als `root` anmelden).

## Das ist praktisch root

Die Seite sagt es dir auch: Wer in der Gruppe `docker` ist oder die volle sudo-Regel
hat, kann auf dem Server praktisch alles. Nodvard Deck startet ohne Hilfsskript einige root-Befehle
als `sudo -n sh -c '…'`, darum braucht es dann die volle sudo-Regel, und wer `sh` als root starten darf,
darf alles. Die Sicherheit kommt daher, dass **nur**
dieser eine Benutzer die Rechte bekommt, er sich **nur mit dem Schlüssel** anmelden kann und der
Schlüssel verschlüsselt in Nodvard Deck liegt.

## Hilfsskripte

Mit dem Haken „Root-Rechte“ legt der Befehl die Hilfsskripte der Module
(Nodvard Shield, Server-Zustand und Docker im Modus „Nur bedienen“) nach `/usr/local/lib/nodvard-deck` und erlaubt jedes mit einer
eigenen, engen sudo-Regel. Klappt das für ein Skript nicht, nennt der Befehl den Grund (Ordner oder Datei nicht sicher,
Werkzeug fehlt, Regel von `visudo` abgelehnt). Mit voller Regel ist das ein Hinweis, das Modul nimmt dann die volle
Regel; nur Docker nimmt sie nie, für `docker-root` ist es darum immer ein Fehler. Ohne volle Regel ist es ein Fehler,
denn dann hätte das Modul gar kein root: Ordner und Werkzeuge prüft dort schon
die Vorprüfung, bevor sich etwas ändert (siehe oben); ein beschädigtes Skript oder eine abgelehnte Regel meldet der
Befehl unterwegs mit „FEHLER: …“ und am Ende „Nicht alles hat geklappt“.
Die Module rufen ein Skript nur auf, wenn es auf dem Server Byte für Byte das mitgelieferte ist. Ausnahme ist Docker:
Es ruft auch eine ältere Fassung auf. Kann nur root sie ändern und erlaubt sudo sie, behält Docker damit bis zur neuen
Einrichtung root (bei „Veraltet“ steht dann „Docker ruft es trotzdem auf …“); die Regel mit Prüfsumme gehört ja zu
dieser älteren Fassung. Liegt dagegen ein anderer Inhalt da als der, für den die Regel gilt, startet sudo das Skript
nicht, und „Verbindung prüfen“ zählt die Regel nicht als eigene. „Verbindung prüfen“
zeigt je Skript, ob es passt, und bei „Root-Rechte“: „sudo erlaubt nur die Hilfsskripte der Module, keine volle Regel.
Das reicht.“, wenn alle passen. Fehlt ein Skript, steht dort „Fehlt auf dem Server“ (gelb), ohne volle Regel und bei
Docker immer mit dem Zusatz, dass das Modul hier kein root hat; fehlt `docker-root` neben der vollen Regel, ist auch
„Root-Rechte“ gelb. Nach einem Update, das ein neues Skript mitbringt, steht dort „Veraltet“: dann
den Einrichtungsbefehl noch einmal ausführen. Das musst du nicht selbst bemerken: Fehlt ein Skript oder ist es veraltet,
während sudo dort Skripte ausführen darf (volle Regel oder die Regel eines Skripts), steht unter „Braucht
Aufmerksamkeit“ in der Übersicht ein dezenter Hinweis mit Link zur Einrichtung. Nodvard Deck prüft das alle 6 Stunden und bei jedem „Verbindung prüfen“. Mit dem × blendest du den
Hinweis für diesen Server aus, bis ein Update neuere Skripte mitbringt.

## Was die Hilfsskripte dürfen

Diese Unterbefehle kann der Benutzer des Dashboards per sudo als root aufrufen, auch
am Dashboard vorbei. Jeder prüft seine Argumente selbst und bricht bei allem anderen ab, bevor er etwas tut. Ein Test
ruft jedes Skript in `sh` und `dash` mit über 200 ungültigen Eingaben auf (Optionen statt Namen, Leerzeichen,
Zeilenumbrüche, `;`, `$(…)`, `..`, relative Pfade, überlange Werte) und verlangt, dass dabei kein Programm mit
Nebenwirkung startet. Die volle sudo-Regel erlaubt dagegen alles.

| Skript | Unterbefehl | Was es als root tut | Grenzen |
|---|---|---|---|
| alle | `version` | nennt die Version | keine Argumente |
| `system-root` | `service-restart` | startet einen systemd-Dienst neu und meldet seinen Zustand | nur `.service`, `.socket`, `.timer`; nie SSH, D-Bus, `systemd-*`, `networking`, `NetworkManager`, `dhcpcd`, `wpa_supplicant` oder Nodvard Deck; andere Netzwerkdienste wie `firewalld` oder `wg-quick@…` schon. Docker und containerd auf dem Rechner des Dashboards sperrt nur das Dashboard, das Skript kann diesen Rechner nicht erkennen |
| `docker-root` | `ps`, `stats` | listet Container und ihre Last | keine Argumente |
| `docker-root` | `inspect`, `id`, `id-state`, `state`, `ports` | liest Angaben zu Containern in festen Formaten | nur gültige Namen bzw. IDs, höchstens 200 |
| `docker-root` | `logs` | liest das Protokoll eines Containers | zu Beginn höchstens 5000 Zeilen, auf Wunsch danach mitlaufend; Zeitraum nur in Minuten oder Stunden |
| `docker-root` | `start`, `stop`, `restart` | startet, stoppt oder startet einen Container neu | genau ein gültiger Name; Löschen gibt es nicht |
| `shield-root` | `guard` | liest Anmeldungen aus dem SSH-Protokoll, die wirksamen Einstellungen von sshd (Port, Anmeldung als root, Passwort- und Schlüssel-Anmeldung und ähnliche), Fail2ban, offene Ports samt Bereich für ausgehende Verbindungen sowie Prüfsumme, Rechte, Besitzer, Änderungszeit und Größe der überwachten Dateien und Programme | nur lesend; absolute Pfade ohne `..`, `*` nur bei Dateien |
| `shield-root` | `detect-templates`, `refresh-packages` | liest die Einstellungen von Samba, NFS und Nextcloud bzw. lädt die Paketliste neu | keine Argumente |
| `shield-root` | `quarantine`, `restore`, `delete`, `quarantine-state` | verschiebt eine von ClamAV gemeldete Datei in die Quarantäne, legt sie zurück, löscht sie dort oder nennt ihren Zustand | ClamAV prüft die Datei vorher noch einmal; zurück nur an den Ort, den das Skript selbst notiert hat; keine Links |
| `shield-root` | `ban`, `unban` | sperrt oder entsperrt eine Adresse in Fail2ban | Adresse nur aus Hex-Ziffern, `:` und `.`, höchstens 45 Zeichen; Jail nur aus Buchstaben, Ziffern, `_`, `.` und `-`, höchstens 64 Zeichen. Was keine gültige Adresse ist, lehnt Fail2ban ab |
| `shield-root` | `install` | installiert ClamAV, Lynis, Fail2ban oder unattended-upgrades, lädt neue Virensignaturen | nur diese fünf |
| `shield-root` | `reboot` | startet den Server in einer Minute neu (ohne `shutdown` nach 5 Sekunden) | keine Argumente |
| `shield-root` | `upgrade-start`, `upgrade-run`, `upgrade-poll` | spielt Updates mit apt, dnf oder apk ein und meldet den Stand | feste Arten; einzelne Pakete nur bei Sicherheitsupdates mit apt, Namen aus festen Zeichen |
| `shield-root` | `audit-start`, `audit-run`, `audit-poll`, `audit-stop` | startet, verfolgt oder beendet eine Prüfung mit Lynis | Lauf-ID in fester Form, feste Zeitgrenze, beendet nur den eigenen Lauf |
| `shield-root` | `scan`, `watch` (intern `scan-run`, `watch-run`, `odd-scan`) | prüft Ordner mit ClamAV | absolute Pfade, Zahlen mit Obergrenze, ein Lauf zur Zeit, Zeitgrenze |
| `shield-root` | `realtime-on`, `realtime-off`, `realtime-log`, `realtime-confirm` | schaltet den Echtzeit-Schutz von ClamAV ein oder aus, liest sein Protokoll, prüft gemeldete Dateien erneut | schreibt nur feste Dateien unter `/etc/clamav` und eine Ergänzung für systemd; Ordner absolut ohne `..`, höchstens 50 Dateien |

Welche Dateien `guard` liest, kommt aus den Einstellungen des Datei-Wächters von Nodvard Shield („Überwachte Dateien“).
Wer diese Einstellung ändern darf, bestimmt damit auch, von welchen Dateien root Prüfsumme, Rechte, Besitzer,
Änderungszeit und Größe liest. Den Inhalt dieser Dateien gibt das Skript nie aus.

## Docker auf „Nur bedienen“ umstellen

Bekommt ein Server, der schon mit der Gruppe docker oder der vollen
sudo-Regel eingerichtet ist, die Markierung `docker-bedienen` statt `docker`, bleibt beides erhalten. Der
Einrichtungsbefehl nimmt weder die Gruppe noch die Regel zurück, der Zugang ist dort also weiter praktisch root.
„Verbindung prüfen“ zeigt das in den Punkten „Nur bedienen: Zugang nicht in der Gruppe docker“ und „Nur bedienen:
keine volle sudo-Regel“ (gelb). Meldet sich der Zugang als `root` an, schränkt „Nur bedienen“ nichts ein; dann ist
„Nur bedienen: Zugang ist nicht root“ gelb, und der Zugang braucht einen eigenen Benutzer. Hat ein Server beide
Markierungen (`docker` und `docker-bedienen`), ist er ein voller Docker-Server: Die Punkte „Nur bedienen: …“ und das
Hilfsskript `docker-root` entfallen dann, Docker läuft über die Gruppe docker. So stellst du um:

1. Den Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ noch einmal ausführen. Er legt `docker-root` ab und bietet die
   Gruppe docker nicht mehr an.
2. „Verbindung prüfen“: Bei „Docker nur bedienen“ muss stehen, dass das Hilfsskript antwortet.
3. Den Zugang aus der Gruppe nehmen: `sudo gpasswd -d <benutzer> docker` (gilt ab der nächsten Anmeldung).
4. Braucht kein anderes Modul hier volle Rechte, die volle Regel entfernen wie oben beschrieben.
5. Noch einmal „Verbindung prüfen“: Die Prüfung meldet sich dafür neu an, die Punkte „Nur bedienen: …“ sind also gleich
   grün, und danach nutzt Nodvard Deck auch sonst nur neue Verbindungen.

Zurück zum vollen Docker-Server: Markierung `docker` wieder setzen und den Einrichtungsbefehl mit „Zur Gruppe docker
hinzufügen“ ausführen.

## Proxmox-Knoten und vorhandene Benutzer

**Proxmox-Knoten:** Dort meldet sich Nodvard Deck am einfachsten direkt als `root` an (Benutzer
`root` beim Erzeugen eintragen). Dann braucht es kein sudo und keine Haken. Der Schlüssel landet in
`/root/.ssh/authorized_keys`, das bei Proxmox ein Verweis in den Cluster-Ordner ist – er gilt dann
auf **allen Knoten des Clusters**.

**Vorhandenen Benutzer nehmen** (auf dem Pi z. B. `admin`): beim Erzeugen statt `nodvard` dessen
Namen eintragen. `sudo -n true && echo geht-schon` auf dem Server zeigt, ob er sudo schon ohne
Passwort darf (auf Raspberry Pi OS oft beim ersten Benutzer der Fall).

## Was der Datei-Wächter nach der Einrichtung meldet

Nicht wundern: Kopierst du den Befehl und führst ihn selbst aus, meldet der Datei-Wächter von Nodvard Shield die neue
sudo-Datei und die neue `authorized_keys` einmal – das warst du. Haben die Regeln der Hilfsskripte eine Prüfsumme,
meldet er sie wieder, wenn du den Befehl nach einem Update erneut ausführst: Die Prüfsumme ändert sich mit jeder neuen
Fassung eines Skripts. Mit **„Mit Passwort einrichten“** meldet der Befehl Nodvard
Deck genau, was er geändert hat: für die Benutzer- und Gruppendateien und die `authorized_keys` des Deck-Benutzers
Prüfsumme, Rechte und Besitzer vorher und nachher. Die eigenen sudo-Regeln (`nodvard-<benutzer>`, die Regeln der
Hilfsskripte) kennt Nodvard Deck sowieso, es schreibt sie selbst. Der Wächter übernimmt bei seiner nächsten Prüfung still
nur, was genau dazu passt: Der bestätigte Stand ist der von vorher, der jetzige der von nachher, und der Befehl hat die
passende Aktion gemeldet (Benutzer angelegt, in eine Gruppe eingetragen, Schlüssel eingetragen, alte Regel ersetzt). Das
steht im Verlauf als „Nach der Einrichtung durch Nodvard Deck um HH:MM übernommen“, schon erledigt und ohne Meldung.
Alles andere meldet er wie immer: andere Dateien, fremde sudo-Regeln (auch solche, die mit `nodvard` oder `lattice`
beginnen), nur geänderte Rechte oder Besitzer, ein anderer Inhalt als gemeldet und jede spätere Änderung. Jeder Lauf zählt
nur einmal, bei der ersten Prüfung danach. Dasselbe gilt, wenn du den Befehl mit Haken „Root-Rechte“ auf einem
Server erneut ausführst, der noch die alte Regel `lattice-<benutzer>` hat: Dann kommt `nodvard-<benutzer>` dazu,
und die alte fällt weg, wenn sie unverändert ist (siehe oben).
