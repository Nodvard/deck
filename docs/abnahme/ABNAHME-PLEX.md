# Abnahme-Checkliste: Erweiterung Plex

Diese Checkliste dient als Arbeitsliste für den Test des Moduls Plex an einem echten Plex Media Server im lokalen Netzwerk. Das Modul arbeitet rein lesend und kommuniziert ausschließlich direkt mit dem Server im Heimnetz, niemals mit Cloud-Diensten wie plex.tv. Jeder Prüfpunkt beschreibt Schritt, erwartetes Verhalten und den Fehlerfall.

### 1. Basiszustand, Server-Verwaltung und Nur-Lese-Garantie
* **Schritt:** Rufe die Plex-Seite im Dashboard auf, ohne zuvor eine Verbindung eingerichtet zu haben.
* **Erwartetes Verhalten:** Die Oberfläche zeigt die Leeransicht `Noch keine Verbindung` mit dem Hinweis `Trage Plex unter Einstellungen → Erweiterungen → Plex ein.` Die Kachel meldet `nicht eingerichtet`. Das Modul besitzt ausschließlich lesende Zugriffsrechte und bietet keinerlei Schreibfunktionen. Es werden mehrere Instanzen nebeneinander unterstützt.
* **Fehlerfall:** Die Seite zeigt Fehlermeldungen über Verbindungsabbrüche statt der neutralen Leeransicht, oder es werden Aktionen zur Servermodifikation angeboten.

### 2. Authentifizierung über X-Plex-Token und Tresor-Schutz
* **Schritt:** Trage den Plex-Token im Einstellungsformular ein und speichere die Verbindung.
* **Erwartetes Verhalten:** Der Token wird im Tresor unter `plex-token:{name}` abgelegt und an die Serveradresse gebunden. Vor der Verwendung prüft der Dienst, dass der Token frei von Leerzeichen, Gleichheitszeichen oder vorangestellten Texten wie `X-Plex-Token=` ist. Der Token wird ausschließlich über den HTTP-Header `X-Plex-Token` mitgeführt und taucht niemals in URLs oder Protokollen auf.
* **Fehlerfall:** Der Token wird im Klartext im Protokoll oder in der URL übermittelt, oder versehentlich mitkopierte Leerzeichen führen zu dauerhaften Verbindungsabbrüchen ohne Bereinigung.

### 3. Server-Identifikation und Version
* **Schritt:** Kontrolliere nach erfolgreicher Verbindung die Kopfzeile der Serverkarte.
* **Erwartetes Verhalten:** Die Karte zeigt den offiziellen Servernamen und die Version des Plex Media Servers an (zum Beispiel `1.41.3.9314`, Beispiel, deine Version weicht ab). Das Status-Abzeichen in der Karte meldet `nichts läuft` (neutral), `1 läuft` beziehungsweise `X laufen` (grün) oder `unvollständig` (gelb).
* **Fehlerfall:** Name oder Version fehlen, Sonderzeichen im Namen stören die Darstellung oder das Status-Abzeichen bleibt dauerhaft leer.

### 4. Laufende Wiedergaben (Sessions) und Benutzerdetails
* **Schritt:** Starte auf einem Abspielgerät einen Film oder eine Serienfolge, klicke in Deck auf „Aktualisieren“ (die Seite lädt nur beim Öffnen und auf Klick neu) und prüfe den Abschnitt `Läuft gerade`. Dasselbe beim Wechsel der Wiedergabeart und nach einem erzeugten Fehlerzustand.
* **Erwartetes Verhalten:** Der Stream wird in der Liste angezeigt. Bei Serienfolgen werden Staffel- und Folgencodenummer formatiert (zum Beispiel `Serie S02E05, Folgentitel`, Beispiel, dein Titel weicht ab). Es erscheinen der Name des Plex-Benutzers sowie das Abspielgerät oder der Browser. Die Liste zeigt maximal 50 gleichzeitige Wiedergaben; bei Überschreitung erscheint ein Hinweistext auf die Gesamtanzahl.
* **Fehlerfall:** Laufende Streams werden gar nicht oder erst nach Minuten angezeigt, Namen von Benutzern oder Geräten fehlen, oder mehr als 50 Streams führen zu Anzeigefehlern.

### 5. Abspielmethode, Transkodierungsanzeige und Fortschrittsbalken
* **Schritt:** Vergleiche die Anzeige bei einer direkten Wiedergabe (Direct Play) mit einem Stream, der eine Transkodierung erzwingt.
* **Erwartetes Verhalten:** Die Wiedergabeart wird unterschieden in `direkt` (Direct Play), `Direktstream` (Direct Stream) oder `transkodiert`. Transkodierung erzeugt ein gelbes Warnabzeichen `transkodiert`, direkte Wiedergabe ein grünes Abzeichen. Bei Pause erscheint zusätzlich ein neutrales Abzeichen `pausiert`. Ein visueller Fortschrittsbalken zeigt den Stand in Prozent von 0 bis 100 %.
* **Fehlerfall:** Transkodierte Streams werden fälschlicherweise als Direktwiedergabe eingestuft, Fortschrittsbalken springen unkontrolliert oder pausierte Wiedergaben werden nicht erkannt.

### 6. Bibliotheksübersicht und schonende Zählung
* **Schritt:** Kontrolliere den Abschnitt `Bibliotheken` auf der Modulseite.
* **Erwartetes Verhalten:** Es werden maximal 30 Bibliotheken aufgelistet. Die Abfrage erfolgt ressourcenschonend mit maximal 4 gleichzeitigen Anfragen und fordert nur die Gesamtzahlen an, ohne einzelne Medienobjekte abzurufen. Die Zahlen sind mit Tausenderpunkt formatiert (zum Beispiel `1.296 Filme` oder `450 Serien`, Beispiel, deine Werte weichen ab). Läuft auf dem Server gerade ein Medienscan, meldet der Hinweis `wird aktualisiert`.
* **Fehlerfall:** Der Server wird durch massenhafte Objektanfragen überlastet, mehr als 30 Bibliotheken werden unbegrenzt geladen oder Zahlen erscheinen unformatiert.

### 7. Zuletzt hinzugefügte Medien (Recently Added)
* **Schritt:** Kontrolliere den Bereich `Zuletzt hinzugefügt`.
* **Erwartetes Verhalten:** Es werden serverseitig begrenzt die neuesten 10 Einträge angezeigt. Die neuesten Einträge stehen oben. Das Hinzufügedatum wird gemäß der konfigurierten Zeitzone von Deck im deutschen Format formatiert ausgegeben.
* **Fehlerfall:** Es werden mehr als 10 Einträge geladen, die Sortierung ist verkehrt herum oder Zeitangaben erscheinen in englischer Notation oder als roher UTC-Zeitstempel.

### 8. Teilweise Ausfälle und Fehlertoleranz (Partial Failures)
* **Schritt:** Simuliere einen Teilausfall, indem einzelne Bibliotheken nicht erreichbar sind oder der Endpunkt für kürzlich hinzugefügte Medien fehlschlägt.
* **Erwartetes Verhalten:** Die Abschnitte werden isoliert voneinander abgefragt. Scheitert beispielsweise das Lesen der Bibliotheken, bleiben laufende Streams und die Serverinformationen uneingeschränkt sichtbar. Die aufgetretenen Teilfehler erscheinen in gelber Schrift unter `problems`, und das Abzeichen meldet `unvollständig`.
* **Fehlerfall:** Der Ausfall einer einzelnen Datenabfrage bringt die gesamte Seite zum Absturz oder führt zu einer leeren Fehlerseite.

### 9. Fehlerfall: Abgelehnter Token und 5-Minuten-Schutzpause
* **Schritt:** Hinterlege absichtlich einen ungültigen Token oder ziehe den Token in den Einstellungen von Plex zurück.
* **Erwartetes Verhalten:** Plex antwortet mit HTTP 401 oder 403. Das Backend erzeugt die Meldung `Plex „...“: Der Token wurde abgelehnt (HTTP 401). Trage in den Einstellungen einen gültigen Token ein...` Dieser Fehlerzustand wird für exakt 300 Sekunden (5 Minuten) zwischengespeichert. Der Knopf `Erneut versuchen` wird ausgeblendet und ein Klick auf `Aktualisieren` umgeht die Pause nicht. Die Pause endet sofort, sobald in den Einstellungen `Verbindung testen` ausgeführt oder ein neues Geheimnis im Tresor gespeichert wird.
* **Fehlerfall:** Deck bombardiert Plex kontinuierlich mit ungültigen Token-Anfragen, oder die 5-Minuten-Schutzpause lässt sich nach Eingabe eines gültigen Tokens nicht vorzeitig beenden.

### 10. Fehlerfälle: Server aus, Zeitüberschreitung, TLS und Antwortgrößen-Schutz
* **Schritt:** Teste das Verhalten bei gestopptem Plex Media Server, falschem Port, geändertem Zertifikat oder übergroßen Antworten.
* **Erwartetes Verhalten:**
  1. Server aus oder Port geschlossen: Die Meldung lautet `Plex „...“ lehnt die Verbindung ab. Läuft Plex, und stimmen Adresse und Port (meist 32400)?`
  2. Zeitüberschreitung: Drei Grenzen gelten getrennt: 10 Sekunden je Verbindungs- oder Leseschritt, 30 Sekunden je HTTP-Anfrage, 50 Sekunden für die gesamte Serverabfrage. Nach Ablauf erscheint `Plex „...“ antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob Plex läuft.`
  3. TLS-Zertifikatsänderung: Bei abweichendem SHA-256-Fingerabdruck blockiert der Client den Aufruf sofort mit dem Text `Plex „...“ zeigt ein anderes Zertifikat als das gemerkte (Fingerabdruck). Es wurde nichts gesendet, auch nicht der Token.`
  4. Antwortgrößen-Schutz: Antworten über 1 MB (Metadaten) beziehungsweise über 4 MB (Listen) werden verworfen mit dem Hinweis auf eine ungewöhnlich große Antwort.
* **Fehlerfall:** Verbindungsabbrüche lassen das Backend endlos warten, Zertifikatsänderungen werden unbemerkt ignoriert oder übergroße Antworten überlasten den Speicher.
