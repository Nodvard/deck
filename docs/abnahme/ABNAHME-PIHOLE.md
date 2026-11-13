# Abnahme-Checkliste: Erweiterung Pi-hole (v6)

Diese Checkliste dient als Arbeitsliste für den Test des Moduls Pi-hole am echten Gerät (Hardware oder Container mit Pi-hole ab Version 6). Jeder Prüfpunkt beschreibt Schritt, erwartetes Verhalten und den Fehlerfall.

### 1. Ausgangszustand ohne Einrichtung und Konfigurationshinweis
* **Schritt:** Rufe das Modul im Dashboard auf, ohne zuvor Einstellungen für Pi-hole hinterlegt zu haben.
* **Erwartetes Verhalten:** Das Backend liefert den Zustand `not_configured`. Die Oberfläche zeigt eine Informationskarte mit dem neutralen Abzeichen `nicht eingerichtet`, dem deutschen Hinweistext und für berechtigte Konten die Schaltfläche `Jetzt einrichten` mit Verweis auf `/settings/extensions/pihole`. Die Übersichtskachel meldet ebenfalls `nicht eingerichtet` und blendet sich in der Übersicht automatisch aus.
* **Fehlerfall:** Die Seite stürzt ab, zeigt eine leere Ansicht oder meldet Verbindungsfehler statt des Hinweises auf die fehlende Einrichtung.

### 2. Adressbereinigung, Tresor-Passwort und Sitzungsverwaltung
* **Schritt:** Trage in den Einstellungen eine Weboberflächen-Adresse mit Pfadbestandteilen ein (zum Beispiel `http://pi.hole/admin` oder `http://192.168.1.2/api/docs`, Beispiel, dein Wert weicht ab) und hinterlege dein Kennwort.
* **Erwartetes Verhalten:** Pfade wie `/admin` oder `/api/docs` werden automatisch abgeschnitten. Das Kennwort oder App-Passwort wird ausschließlich im Tresor unter `network-pihole-password` abgelegt und erscheint zu keinem Zeitpunkt im Klartext im Protokoll. Die Authentifizierung erfolgt über `POST /api/auth`. Die erhaltene Sitzungskennung (`sid`) wird für nachfolgende Anfragen im Header `X-FTL-SID` geführt. Beim Stoppen der Erweiterung wird die Sitzung über `DELETE /api/auth` freigegeben, um die begrenzten API-Plätze von Pi-hole nicht zu blockieren.
* **Fehlerfall:** Pfade werden nicht bereinigt und erzeugen fehlerhafte Doppelpfade, das Passwort taucht im Klartext im Protokoll auf oder die Sitzung wird beim Beenden nicht freigegeben.

### 3. Statistische Kennzahlen und Plausibilitätsprüfung
* **Schritt:** Rufe nach erfolgreicher Verbindung die Modulseite auf und prüfe die vier Hauptstatistiken.
* **Erwartetes Verhalten:**
  1. `Anfragen (24 Std.)`: Gesamtzahl der DNS-Anfragen formatiert mit Tausenderpunkt (zum Beispiel `12.450 Anfragen`, Beispiel, dein Wert weicht ab). Der Zusatzhinweis nennt die Anzahl aktiver Geräte.
  2. `Blockiert`: Anzahl der abgewehrten Anfragen (zum Beispiel `1.820`, Beispiel, dein Wert weicht ab).
  3. `Anteil blockiert`: Prozentualer Anteil mit einer Nachkommastelle und Komma formatiert (zum Beispiel `14,6 %`, Beispiel, dein Wert weicht ab).
  4. `Domains auf der Sperrliste`: Anzahl geblockter Domains aus der Gravity-Datenbank (zum Beispiel `120.000`, Beispiel, dein Wert weicht ab). Der Text nennt den relativen Aktualisierungszeitpunkt.
* **Fehlerfall:** Zahlen werden ohne Tausenderpunkte dargestellt, Prozentwerte verwenden einen englischen Punkt statt eines Kommas oder Werte fehlen ganz und bleiben bei null stehen.

### 4. Blockierungszustand und Pausieren über das Aktions-Gate
* **Schritt:** Löse auf der Modulseite eine Pause über die Schaltflächen `5 Min.`, `15 Min.` oder `60 Min.` aus.
* **Erwartetes Verhalten:** Es stehen ausschließlich die drei festen Werte 5, 15 und 60 Minuten zur Auswahl. Vor dem Absenden öffnet sich ein Bestätigungsdialog. Nach deiner Bestätigung sendet Deck die Aktion zum Pausieren. Die Oberfläche schaltet um auf das gelbe Warnabzeichen `pausiert (noch X Min.)`.
* **Fehlerfall:** Andere Zeitwerte werden angeboten, die Aktion wird ohne Bestätigungsdialog ausgeführt oder die Statusanzeige wechselt nach der Bestätigung nicht in den Pausenzustand.

### 5. Manuelles Fortsetzen der Blockierung
* **Schritt:** Klicke bei pausierter oder deaktivierter Blockierung auf `Blockierung fortsetzen`.
* **Erwartetes Verhalten:** Deck sendet die Aktion zum Fortsetzen der Blockierung an Pi-hole. Das Backend aktiviert die Blockierung wieder. Die Oberfläche wechselt unmittelbar auf das grüne Erfolgsabzeichen `Blockierung aktiv` mit dem Hinweistext `Blockierung pausieren:`.
* **Fehlerfall:** Die Anzeige bleibt im Pausenzustand oder auf gelb stehen, oder das Reaktivieren schlägt ohne verständliche Fehlermeldung fehl.

### 6. Liste der aktivsten Geräte (Top Clients)
* **Schritt:** Kontrolliere die Karte `Aktivste Geräte` auf der Modulseite.
* **Erwartetes Verhalten:** Es werden bis zu 5 Geräte angezeigt. Zu jedem Eintrag erscheinen Name, IP-Adresse und die formatierte Anzahl der Anfragen (Beispiel, deine Gerätewerte weichen ab). Falls Name und IP-Adresse identisch sind, wird der Wert nicht doppelt ausgegeben.
* **Fehlerfall:** Es werden mehr als 5 Geräte aufgeführt, Name und IP-Adresse stehen redundant doppelt nebeneinander oder Geräte ohne Hostnamen erzeugen Darstellungsfehler.

### 7. Aktualisierung der Sperrlisten (Gravity) mit Frist
* **Schritt:** Klicke in der Karte `Sperrlisten` auf `Listen aktualisieren`.
* **Erwartetes Verhalten:** Vor der Ausführung erscheint eine Sicherheitsrückfrage. Für jeden Verbindungs- und Leseschritt gelten 600 Sekunden (10 Minuten); das ist keine Gesamtfrist, liefert Pi-hole laufend weitere Daten, kann der Abruf länger dauern. Die Konsolenausgabe von Pi-hole wird von ANSI-Farbcodes und Wagenrückläufen bereinigt und gekürzt auf maximal 2.000 Zeichen im Aktionsergebnis ausgegeben.
* **Fehlerfall:** Die Aktion bricht vorzeitig wegen einer zu kurzen Frist ab, ANSI-Steuerzeichen verunreinigen die Ausgabe oder der Text überschreitet 2.000 Zeichen und sprengt das Layout.

### 8. Domain erlauben oder sperren mit Validierung
* **Schritt:** Trage eine gültige Domain (etwa `test.example.com`, Beispiel, dein Wert weicht ab) sowie ungültige Werte (zum Beispiel mit vorangestelltem `http://` oder mit Leerzeichen) ein.
* **Erwartetes Verhalten:** Ungültige Domains werden sofort abgewiesen mit dem Hinweistext `Bitte eine Domain wie werbung.example.com eintragen (ohne http und ohne Pfad).` Gültige Domains werden mit dem festen Kommentar `Über Nodvard Deck eingetragen` hinzugefügt. Existiert der Eintrag bereits, meldet das System keinen Fehler, sondern die Erfolgsmeldung `„...“ stand schon auf der Liste`.
* **Fehlerfall:** Ungültige Domains werden ungeprüft an Pi-hole gesendet, rohe API-Fehlermeldungen erscheinen im Dialog oder bereits vorhandene Domains lösen einen Fehlerabbruch aus.

### 9. Fehlerfall: Abgelehntes Kennwort und 30-Sekunden-Schutzpause
* **Schritt:** Hinterlege in den Einstellungen absichtlich ein falsches Kennwort für Pi-hole.
* **Erwartetes Verhalten:** Pi-hole quittiert die Anmeldung mit HTTP 401. Das Backend übersetzt dies in `Pi-hole hat das Passwort abgelehnt. Bei Zwei-Faktor-Anmeldung bitte ein App-Passwort verwenden.` Um ein Aussperren durch Pi-hole (HTTP 429) zu verhindern, blockiert der Client Folgeanfragen für exakt 30 Sekunden. Die Seite zeigt die Fehlerkarte mit dem roten Abzeichen `Anmeldung fehlgeschlagen` und bietet eine Verknüpfung zu den Einstellungen.
* **Fehlerfall:** Deck sendet in schneller Folge weitere Anfragen und riskiert eine Kontosperre, oder es erscheint lediglich ein technischer Fehlercode ohne Erklärung.

### 10. Fehlerfälle: Gerät nicht erreichbar, Zeitüberschreitung, Umleitungen und Pi-hole v5
* **Schritt:** Teste das Verhalten bei ausgeschaltetem Gerät, falschem Port, Umleitungen oder einer veralteten Pi-hole Version 5.
* **Erwartetes Verhalten:**
  1. Gerät ausgeschaltet oder falscher Port: Verbindungsabbruch führt zur festen Meldung `Pi-hole ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin). Läuft Pi-hole, und stimmen Adresse und Port?`
  2. Zeitüberschreitung: Nach Ablauf der Frist von 10 Sekunden erscheint `Pi-hole antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port.`
  3. HTTP-Umleitungen: Weiterleitungen (Statuscodes 3xx) werden blockiert mit dem Text `Pi-hole leitet auf eine andere Adresse um...`
  4. Veraltetes Pi-hole v5: Wird ein Pi-hole v5 erkannt, meldet das Backend `Pi-hole v5 wird nicht unterstützt, bitte auf v6 aktualisieren.` mit dem gelben Abzeichen `veraltet`.
* **Fehlerfall:** Die Oberfläche reagiert nicht mehr, Anfragen hängen über Minuten fest, Umleitungen werden ungeprüft befolgt oder veraltete Versionen führen zu unklaren Fehlermeldungen.
