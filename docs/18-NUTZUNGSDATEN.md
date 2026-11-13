# Freiwillige Nutzungsdaten in Deck und Dock

Unter Einstellungen → Nutzungsdaten entscheidet ausschließlich der Inhaber, ob Daten geteilt werden.
Alle drei Schalter sind standardmäßig aus, auch im Einrichtungsassistenten. „Weiter ohne Teilen“ und
„Teilen“ sind dort gleich groß. Ohne Zustimmung gibt es keinen Bericht und keinen Start-Ping.

Die drei Stufen sind unabhängig wählbar. Jede gewählte Stufe erscheint als eigener Block im Bericht.
Nicht gewählte Blöcke fehlen vollständig:

| Stufe | Inhalt |
|---|---|
| 1: Basis | Programmversion, Produkt `deck` oder `dock`, Installationsart `docker`, `pi` oder `sonstiges`, Architektur `amd64` oder `arm64`, Sprache des Inhabers `de` oder `en` |
| 2: Nutzung | Kennungen eingeschalteter mitgelieferter Module, Klasse für die Anzahl eingeschalteter fremder Module, Serverklasse, Klasse aktiver Benutzer, ob mindestens ein aktiver Benutzer Zwei-Faktor eingerichtet hat und ob automatische Sicherungen von Deck selbst eingeschaltet sind |
| 3: Fehler | Zähler für `ssh.timeout` beim SSH-Verbindungsaufbau und `backup.ziel_nicht_beschreibbar` bei fehlendem Schreibzugriff für eine Deck-Sicherung, jeweils auf 0 bis 99 begrenzt |

Serverklassen: `0`, `1`, `2-5`, `6-20`, `21+`. Benutzerklassen: `1`, `2-5`, `6+`.
Fremde Module heißen im JSON nur `fremd`, mit der Klasse `0`, `1`, `2-5` oder `6+`.
Ihre Namen werden nicht übernommen. „Modul-Nutzung“ bedeutet hier eingeschaltete Module, keine Aufzeichnung einzelner Klicks.
Die Fehlerzähler beginnen erst mit eingeschalteter Fehlerstufe, werden nach jedem Sendeversuch geleert und beim
Ausschalten dieser Stufe verworfen. Es gibt keine Auswertung alter Fehlertexte.

Jeder Bericht enthält außerdem `schema: 1` und eine zufällige UUIDv4. Die Kennung entsteht lokal,
ist nicht aus Hardware abgeleitet und bleibt bis zum Löschen gleich. Das ermöglicht dem Empfänger,
Berichte derselben Installation zusammenzuführen. Es handelt sich also nicht um vollständig unverbundene Berichte.

„Das wird gesendet“ zeigt das echte JSON für die Auswahl, auch vor dem Speichern. „Auswahl speichern“
speichert die Einwilligung. Erst dann ist „Jetzt senden“ verfügbar. Automatische Berichte verwenden
denselben Aufbau mit den zum Sendezeitpunkt aktuellen Klassen und Zählern.
Das [Datenformat und der API-Vertrag](04-API.md#nutzungsdaten) sind dokumentiert.

Das JSON enthält keine Namen, Hostnamen, IP-Adressen, Zugangsdaten, Dateipfade, Fehlermeldungstexte,
Zeitstempel, Zeitzonen oder Laufzeiten. Der Empfänger sieht beim Verbindungsaufbau technisch die öffentliche
IP-Adresse. Ob und wie er diese verarbeitet oder Berichte aufbewahrt, muss seine Datenschutzerklärung erklären;
der Deck-Code kann das nicht garantieren.

## Versand und lokale Aufbewahrung

Es gibt höchstens einen Sendeversuch pro 24 Stunden, einschließlich „Jetzt senden“. Die Sperre bleibt
bei Neustarts, Fehlern und dem Wechsel der Kennung erhalten. Der erste automatische Termin liegt zufällig
innerhalb der nächsten 24 Stunden, weitere frühestens 24 Stunden nach dem letzten Versuch mit bis zu einer
Stunde zusätzlichem Zufallsabstand. Der Hintergrunddienst prüft jede Minute, ob dieser Termin erreicht ist.
Eine Anfrage endet spätestens nach zehn Sekunden. Bei fehlender Verbindung oder abgelehntem Bericht wird
bis zum nächsten Termin gewartet. Es gibt keine zusätzliche Internet-Prüfanfrage und keine sofortige Wiederholung.

Die letzten 30 Sendeversuche und Löschwünsche stehen mit Datum, Ergebnis und JSON ausschließlich lokal
in der Datenbank. „Sendeversuch begonnen“ ohne Abschluss kann nach einem abgebrochenen Prozess stehen bleiben.
Das bedeutet nicht, dass der Empfänger den Bericht sicher angenommen hat.

„Meine Daten löschen“ schaltet alle Stufen aus und erzeugt eine neue Kennung und einen neuen lokalen
Löschschlüssel. Für die bisherige Kennung wird ein Löschwunsch an den konfigurierten Empfänger gesendet.
Scheitert das, bleiben alte Kennung und Schlüssel lokal für einen erneuten Klick auf „Meine Daten löschen“
erhalten. Die Oberfläche meldet offene Löschwünsche ausdrücklich. Es erfolgt keine automatische Wiederholung.
Das lokale Sendeprotokoll bleibt erhalten. Eine bestätigte Anfrage bedeutet, dass der Empfänger sie erfolgreich
beantwortet hat; die tatsächliche Löschung muss der Empfänger umsetzen.

## Freigabe durch den Administrator

Die Voreinstellung ist gesperrt. Erst wenn die Datenschutzerklärung veröffentlicht ist, setzt der Betreiber
`NODVARD_DECK_TELEMETRY_PRIVACY_READY=true` und eine HTTPS-Basisadresse in `NODVARD_DECK_TELEMETRY_URL`.
Die Adresse ist standardmäßig leer. `https://telemetrie.example.invalid` ist ausschließlich ein Platzhalter.
Bei Compose müssen diese Variablen im `environment` des Containers stehen; ein Eintrag nur in der Compose-`.env`
reicht ohne Weitergabe an den Container nicht aus.

`NODVARD_DECK_TELEMETRY_INSTALLATION` wählt `docker`, `pi` oder `sonstiges`, standardmäßig `sonstiges`.
Unbekannte Werte werden als `sonstiges` gemeldet. Unbekannte Architekturen werden nicht erfunden: Ein Basisbericht
wird dann nicht gesendet. `NODVARD_TELEMETRIE=aus` oder `NODVARD_DECK_TELEMETRIE=aus` sperrt Opt-in und jeden
Versand, auch Löschwünsche. Eine bestehende Zustimmung allein übergeht keine dieser Sperren.

Empfänger, öffentliche Statistik und deren Datenschutztext sind eigene Bestandteile und werden hier nicht bereitgestellt.
