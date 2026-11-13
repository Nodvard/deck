# Abnahme-Checkliste: Erweiterung Synology DSM

Diese Checkliste dient als Arbeitsliste für den Test des Moduls Synology DSM an echten Synology NAS-Systemen (DiskStation oder RackStation unter DSM 7.x). Das Modul arbeitet rein lesend und überwacht Hardware, Speicherpools, Volumes, Festplatten, Hyper Backup und System-Updates. Jeder Prüfpunkt beschreibt Schritt, erwartetes Verhalten und den Fehlerfall.

### 1. Ersteinrichtung, Mehrgerätesteuerung und Reiterleiste
* **Schritt:** Richte in den Einstellungen zuerst ein einzelnes NAS und anschließend mindestens ein weiteres NAS ein (maximal 10 Geräte zulässig).
* **Erwartetes Verhalten:**
  1. Einzelgerät: Die Seite zeigt direkt den Systemüberblick ohne Reiterleiste.
  2. Mehrere Geräte: Oberhalb der Karten erscheint eine horizontale Reiterleiste mit Tastatursteuerung über die Pfeiltasten sowie Pos1 und Ende. Jeder Reiter zeigt den Kurznamen des Geräts und dessen aktuellen Zustand als farbiges Abzeichen (`in Ordnung`, `Hinweis`, `Problem` oder `nicht erreichbar`).
  3. URL-Synchronisation: Die Auswahl aktualisiert die Browser-Adresse mit Parametern wie `?device=...&nr=...`.
* **Fehlerfall:** Es lassen sich mehr als 10 Geräte speichern, die Reiterleiste reagiert nicht auf Tastaturnavigation oder die URL wird beim Wechseln des Reiters nicht synchronisiert.

### 2. API-Entdeckung, Authentifizierung und Protokoll-Schutz für Sitzungs-IDs
* **Schritt:** Beobachte die erste Verbindung zur Synology Web-API und kontrolliere die Protokollierung.
* **Erwartetes Verhalten:**
  1. Discovery: Deck fragt über `SYNO.API.Info` die Pfade und Versionen der DSM-Module ab.
  2. Anmeldung: Die Anmeldung erfolgt per POST über `SYNO.API.Auth` mit dem festen Sitzungsnamen `NodvardDeck`. Die erhaltene Sitzungskennung (`_sid`) wird bei Abfragen als URL-Parameter übergeben.
  3. Protokoll-Filter: Alle Vorkommen von `_sid=...` werden im Protokoll auf INFO-Ebene vollständig herausgefiltert. Die Seriennummer des Geräts wird aus Datenschutzgründen grundsätzlich nicht eingelesen.
  4. Abmeldung: Beim Stoppen der Erweiterung wird die Sitzung über `SYNO.API.Auth` ordnungsgemäß abgemeldet.
* **Fehlerfall:** Die Sitzungskennung taucht im Klartext im Protokoll auf, die Seriennummer wird ausgelesen oder verwaiste Sitzungen bleiben nach Beendigung auf der Synology geöffnet.

### 3. Systeminformationen, Hardware-Auslastung und DSM-Updates
* **Schritt:** Prüfe die Systemkarte und die vier Messwert-Kacheln für CPU, Arbeitsspeicher, Temperatur und Laufzeit.
* **Erwartetes Verhalten:**
  1. Systemkarte: Modellbezeichnung (zum Beispiel `DS220+` oder `DS920+`, Beispiel, dein Modell weicht ab), DSM-Version (zum Beispiel `DSM 7.2.1-69057`, Beispiel, deine Version weicht ab) und Speicherausbau.
  2. Kachel CPU: Auslastung in Prozent (0 bis 100 %). Bei mehr als 90 % färbt sich der Wert gelb.
  3. Kachel Arbeitsspeicher: Belegung in Prozent mit Angabe des Gesamtausbaus (zum Beispiel `41 % von 2,0 GB`, Beispiel, deine Werte weichen ab).
  4. Kachel Temperatur: Systemtemperatur in °C (zum Beispiel 42 °C, Beispiel, dein Wert weicht ab). Meldet DSM Überhitzung (`temperature_warn`), färbt sich der Wert rot mit dem Hinweis `DSM meldet: zu warm`.
  5. Kachel Laufzeit: Zeit seit Systemstart formatiert in Tagen und Stunden (zum Beispiel `3 Tage 4 Std.`, Beispiel, dein Wert weicht ab).
  6. Update: Anzeige von `DSM ist aktuell.` oder in gelber Schrift `Update verfügbar: DSM ...` (Beispiel, deine Version weicht ab).
* **Fehlerfall:** Die CPU-Auslastung über 90 % bleibt unmarkiert, Messwerte fehlen ganz oder Überhitzungsmeldungen werden ignoriert.

### 4. Speicherpools (Storage Pools) und RAID-Konfiguration
* **Schritt:** Kontrolliere die Tabelle `Speicherpools` auf der Modulseite.
* **Erwartetes Verhalten:**
  1. Spalten: Name, RAID-Typ, Zustand, Anzahl Festplatten, enthaltene Volumes und Gesamtgröße.
  2. RAID-Typen: Saubere Anzeige von Bezeichnungen wie `SHR`, `SHR-2`, `RAID 1`, `RAID 5`, `Basic` oder `JBOD`.
  3. Zustände: Farblich differenzierte Status-Abzeichen für `Normal` (grün), `Eingeschränkt` (gelb), `Wird repariert` (gelb) oder `Ausgefallen` (rot).
  4. Größen: Formatierung in 1024er-Schritten (zum Beispiel `3,6 TB` oder `14,5 TB`, Beispiel, deine Werte weichen ab).
* **Fehlerfall:** RAID-Typen führen zu Darstellungsfehlern, fehlerhafte Speicherpools werden als gesund ausgewiesen oder Kapazitätsangaben stimmen rechnerisch nicht.

### 5. Volumes, Dateisysteme und Belegungswarnung
* **Schritt:** Kontrolliere die Tabelle `Volumes` auf der Modulseite.
* **Erwartetes Verhalten:**
  1. Anzeige: Name (zum Beispiel `Volume 1`), Dateisystem (`btrfs` oder `ext4`), Status-Abzeichen und Belegung.
  2. Belegungstext: Angabe von Belegung und Gesamtkapazität (zum Beispiel `412 GB von 3,6 TB (11 %)`, Beispiel, deine Werte weichen ab).
  3. Füllstandswarnung: Überschreitet die Belegung 90 %, färbt sich der Füllstandsbalken bernsteingelb und der Text wechselt auf Warnfarbe.
* **Fehlerfall:** Dateisysteme werden falsch deklariert, die Warnung bei über 90 % Belegung bleibt aus oder Füllstandsbalken zeigen falsche Prozentwerte.

### 6. Festplatten, SMART-Werte und konfigurierbare Temperaturprüfung
* **Schritt:** Kontrolliere die Tabelle `Festplatten`.
* **Erwartetes Verhalten:**
  1. Datenträgerdaten: Laufwerksbezeichnung (zum Beispiel `Laufwerk 1`), Herstellermodell (zum Beispiel `WD40EFRX`, Beispiel, dein Modell weicht ab) und Status; der Datenträgertyp wird nicht angezeigt.
  2. SMART: Auswertung des SMART-Zustands mit Abzeichen `In Ordnung` (grün) oder `Achtung` (gelb). Warnhinweise bei fehlerhaften Sektoren oder abgelaufener Restlebensdauer erscheinen direkt unter dem Laufwerksnamen.
  3. Temperaturgrenzen: Standardmäßig gelten feste Warngrenzen von 55 °C für HDD, 65 °C für SSD und 70 °C für NVMe (sofern in den Einstellungen nicht anders festgelegt). Bei Überschreiten der Grenze wird die Temperatur bernsteingelb markiert mit dem Tooltip `über der Grenze`.
* **Fehlerfall:** SMART-Warnungen werden übersehen, Laufwerkstypen vertauscht oder Temperaturüberschreitungen nicht farblich signalisiert.

### 7. Hyper Backup Aufgaben und Überfälligkeitserkennung
* **Schritt:** Prüfe bei eingerichteten Datensicherungsaufgaben die Tabelle `Hyper Backup`.
* **Erwartetes Verhalten:**
  1. Abfrage und Frist: Aufgabenliste mit Einzellaufprüfung unter einer isolierten Gesamtfrist von 25 Sekunden. Bei Timeout bleibt die restliche Systemanzeige intakt.
  2. Statusanzeige: Aufgabenname, Ergebnisabzeichen (`Erfolgreich` in grün, `Teilweise gesichert` in gelb, `Fehlgeschlagen`, `Ziel nicht gefunden` oder `Prüfsumme falsch` in rot).
  3. Zeiten: Anzeige von letztem und nächstem Lauf in lokaler Synology-Zeit.
  4. Überfälligkeit: Ist ein geplanter Lauf um mehr als 6 Stunden überfällig, markiert das Modul die Aufgabe mit dem Text `Der geplante Lauf vom ... hat nicht stattgefunden.`
* **Fehlerfall:** Langsame Backup-Abfragen blockieren das gesamte Modul, überfällige Sicherungen werden ohne Warnung als wartend angezeigt oder Zeitzonen werden falsch umgerechnet.

### 8. Aufmerksamkeit-Karte und automatischer Wächter (WatchJob)
* **Schritt:** Simuliere einen Fehlerzustand (beispielsweise ein fehlgeschlagenes Test-Backup oder eine herabgesetzte Temperaturgrenze).
* **Erwartetes Verhalten:**
  1. Zentrale Karte: Alle kritischen Fehler und Warnungen werden ganz oben in der Karte `Braucht Aufmerksamkeit` zusammengefasst.
  2. Wächter: Ein periodischer Hintergrundauftrag prüft alle 5 Minuten.
  3. Benachrichtigung: Bei neuen Problemen versendet der Wächter Systemmeldungen über das Deck-Meldungscenter.
  4. Temperatur-Hysterese: Laufwerks-Temperaturwarnungen werden erst nach 3 aufeinanderfolgenden Messungen ausgelöst und erst dann als behoben markiert, wenn die Temperatur um mindestens 5 °C unter die Warngrenze sinkt. Die Systemtemperatur hat keine Hysterese: Sie gilt als behoben, sobald DSM die Warnung selbst zurücknimmt.
  5. Entwarnung: Sobald ein Fehler behoben ist, meldet das System `Wieder in Ordnung.` und schließt die offene Meldung ab.
* **Fehlerfall:** Probleme erscheinen nicht in der Sammelkarte, kurzzeitige Temperaturschwankungen lösen ständige Fehlalarme aus oder behobene Fehler bleiben dauerhaft als ungelöst gemeldet.

### 9. Fehlerfall: Abgelehntes Passwort, DSM-Kontosperre und 30-Minuten-Pause
* **Schritt:** Hinterlege absichtlich falsche Anmeldedaten im Tresor.
* **Erwartetes Verhalten:**
  1. Fehlercode: DSM quittiert die Anmeldung mit Code 400 (falsche Daten), 403/404/406 (Zwei-Faktor aktiv) oder 407 (IP-Adresse gesperrt).
  2. Schutzpause: Da Synology DSM Adressen nach mehreren Fehlversuchen automatisch sperrt (Auto-Block), verhängt das Modul eine strikte Pause von exakt 1.800 Sekunden (30 Minuten). Die Schaltfläche `Erneut versuchen` wird ausgeblendet.
  3. Pausenaufhebung: Die Pause wird sofort beendet, wenn in den Einstellungen `Verbindung testen` ausgeführt oder ein neues Passwort im Tresor hinterlegt wird.
  4. Fehlende Rechte (Code 105): Verwendet das Konto keine Administratorrechte, erscheint der Hinweis: `Diesem Konto fehlen Rechte. Für [Speicher/Auslastung/...] braucht das Konto Administratorrechte (Mitglied der Gruppe „administrators“)`. Andere lesbare Abschnitte bleiben intakt.
* **Fehlerfall:** Wiederholte Anmeldeversuche sperren die Deck-Adresse auf der Synology aus, die Pause lässt sich nach Passworteingabe nicht abkürzen oder fehlende Rechte bringen die gesamte Seite zum Stillstand.

### 10. Fehlerfälle: Gerät aus, Zeitüberschreitung, Port-Verwechslung und TLS-Zertifikate
* **Schritt:** Teste das Verhalten bei ausgeschaltetem NAS, falschem Port oder geänderter Verschlüsselung.
* **Erwartetes Verhalten:**
  1. Gerät ausgeschaltet oder falscher Port: Die Verbindung wird abgelehnt; die Meldung lautet `Synology DSM lehnt die Verbindung ab. Läuft die Synology, und stimmen Adresse und Port (meist 5000 oder 5001)?`
  2. Zeitüberschreitung: Drei Grenzen gelten getrennt: 10 Sekunden je Verbindungs- oder Leseschritt, 30 Sekunden je HTTP-Anfrage, 50 Sekunden für die gesamte Serverabfrage. Nach Ablauf erscheint `Synology DSM antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob die Synology läuft.`
  3. Port-Verwechslung: Wird HTTPS über den HTTP-Port 5000 aufgerufen (oder umgekehrt), meldet der Transport: `Synology DSM: Die Verschlüsselung passt nicht zum Port. DSM nutzt Port 5001 für https und 5000 für http; trage in den Einstellungen die passende Adresse ein.`
  4. Umleitungen: HTTP-Weiterleitungen (Statuscodes 3xx) werden blockiert und erzeugen die Meldung `Synology DSM leitet auf eine andere Adresse um...`
  5. Selbstsigniertes Zertifikat: Standardmäßig verlangt DSM vertrauenswürdige Zertifikate. Bei eigenen Zertifikaten ist in den Einstellungen entweder der SHA-256-Fingerabdruck einzutragen oder `Selbstsigniertes Zertifikat erlauben` zu aktivieren. Weicht ein gemerkter Fingerabdruck ab, blockiert die Sicherheitsprüfung die Verbindung ohne Datenversand.
* **Fehlerfall:** Allgemeine Netzwerkfehler ohne Port-Hinweis erschweren die Ursachensuche, Zertifikatsänderungen werden unbemerkt ignoriert oder Timeouts blockieren das Backend unbegrenzt.
