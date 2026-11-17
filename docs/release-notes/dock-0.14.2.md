# Nodvard Dock 0.14.2

2026-11-17

## Behoben

- Fehler eines Proxmox-Sammelbackups werden anhand des Aufgabenprotokolls nur den Gästen (VMs und Containern) zugeordnet, bei denen sie wirklich passiert sind. Erfolgreiche Gäste bleiben erfolgreich. Ohne eindeutiges Ergebnis steht der Lauf als „Job mit Fehlern, Gast unklar“ da.
- Das Backup-Center zeigt unklare Sammeljob-Fehler verständlich an und fragt nicht bei jeder Aktualisierung erneut nach einem nicht lesbaren Aufgabenprotokoll.
- Wird dasselbe Modul doppelt gleichzeitig eingeschaltet, lädt Deck es nur einmal und schaltet es später sauber wieder aus.
- Ein Browser-Tab, der Live-Meldungen nicht mehr annimmt, hält die Meldungen an andere Tabs nicht mehr auf.
- Der ntfy-Verbindungstest prüft die Antwort des Servers und das Schreibrecht am Thema. Fremde Webserver, gesperrter Zugriff ohne Token und Token ohne Schreibrecht werden als Fehler gemeldet.
- Gelöschte Server werden in Shield nicht mehr in Listen, Bewertung und Wochenberichten mitgezählt. Server, die mit einem anderen Eintrag zusammengeführt werden, bleiben mit allen Funden im Bericht und werden nicht doppelt gezählt.
- Die Startseite zählt Container mit fehlgeschlagener Gesundheitsprüfung nicht mehr als laufenden Dienst und zeigt sie als Warnung an.

## Sicherheit

- Wer die Zwei-Faktor-Anmeldung einschaltet, meldet damit alle anderen Geräte ab, so wie beim Ändern des Passworts. Das Gerät, an dem du sie einschaltest, bleibt angemeldet; auf den anderen meldest du dich neu an, jetzt mit Code.
- Pi-hole und Nginx Proxy Manager nehmen keine Adresse mehr an, in der Benutzername oder Passwort stehen. Schon so gespeicherte Adressen funktionieren weiter. Übersicht, Kachel, App-Vorschlag und Fehlermeldungen blenden die Zugangsdaten darin aus oder zeigen die Adresse gar nicht an.
- Zu große oder zu tief verschachtelte Compose-Dateien werden mit einem Hinweis abgelehnt.
- Vor der Ersteinrichtung begrenzt Deck falsche Einrichtungscodes jetzt unabhängig von der IP-Adresse.
- Ein per Umgebungsvariable vorgegebener Einrichtungscode braucht mindestens 12 Buchstaben oder Ziffern und darf kein Platzhalter aus der Anleitung sein. Sonst erzeugt Deck einen zufälligen Code im Protokoll des Containers. Das betrifft nur Installationen, die noch nicht eingerichtet sind.
- Bei der Ersteinrichtung kann immer nur ein Inhaber-Konto angelegt werden.
- Lehnt Nextcloud die gespeicherten Zugangsdaten ab, fragt Deck bis zu 30 Minuten nicht neu an, jetzt auch nicht über „Aktualisieren“. So löst Deck in Nextcloud keine Kontosperre durch viele Fehlversuche aus. Ein neues App-Passwort, ein neues Token, geänderte Einstellungen oder „Verbindung testen“ beenden die Pause sofort.
