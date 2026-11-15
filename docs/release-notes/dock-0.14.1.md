# Nodvard Dock 0.14.1

2026-11-15

## Behoben

- Fehlt auf einem Server Docker Compose, zeigen die Stacks (auch beim Neustart und bei der Übernahme) jetzt den klaren Satz „Docker Compose fehlt auf diesem Server“ statt einer Fehlermeldung wie „unknown flag“.
- Der Abgleich bekannter Sicherheitslücken bei Debian- und Proxmox-Servern bricht nicht mehr ab, wenn Debian einzelne Einträge noch nicht bewertet hat.
- Gemeldet werden Lücken, für die Debian eine Korrektur kennt, die neuer ist als die installierte Version: aktualisieren oder, bei Altpaketen ohne Quelle, entfernen. Einträge ohne Korrektur, noch nicht bewertete und Pakete, die nicht aus Debian stammen, werden gezählt und unter dem Ergebnis angezeigt.
- Server-Zustand und Live-Ansicht bleiben erreichbar, wenn auf dem Server ein Netzlaufwerk nicht antwortet: Netzlaufwerke werden nicht mehr abgefragt und stehen nur noch als Zeile "Belegung nicht gemessen" unter der Plattentabelle, und ein hängender Plattenbefehl wird nach fünf Sekunden abgebrochen.
- Kann die Plattenbelegung eines Servers nicht gelesen werden, steht das jetzt als Warnung im Server-Zustand und in der Kachel auf der Startseite, statt dass dort alles in Ordnung gemeldet wird.
- Speichergrößen in Deck, Docker, Proxmox, Gameserver, Backups und Shield zeigen ganze Werte ohne überflüssige Nachkommastelle an, zum Beispiel „608 GB“ statt „608.0 GB“.
- Nodvard Shield: SSH-Härtung und Fail2ban-Einstellungen gehen jetzt auch auf Servern, an denen Deck als root angemeldet ist. Den Zustand zeigt Deck dort an, ohne das Hilfsskript auf dem Server abzulegen. Das nötige Hilfsskript kommt erst mit der ersten freigegebenen Änderung auf den Server, ein Einrichtungsbefehl ist dafür nicht mehr nötig.
- Im Shield-Wochenbericht zeigt die Kachel „Bald ablaufende Zertifikate“ jetzt die Zertifikate aus der letzten Außenprüfung, die in 30 Tagen ablaufen, statt immer leer zu bleiben.
- Beim Hochladen eines Logos werden übermäßig komplexe Bilddateien abgewiesen.
- Liefert ein Server eine unlesbare Speicher- oder Temperaturzeile, laufen die Warnungen für alle anderen Server weiter.
- Startseite und Apps: Die Kachel eines Containers, der läuft, aber nicht gesund ist, zeigt jetzt einen gelben statt eines grünen Punkts.
- Docker: Die Speicher-Tafel zeigt auf Docker 29 wieder die verwaisten Images, also genau die, die „Verwaiste Images entfernen“ löscht. Vorher fehlten sie in der Tabelle.
- Docker: Der Reiter Images nennt ein Image nur noch dann „ohne Namen“, wenn Docker es selbst als verwaist führt. Images, die ohne Tag geholt wurden, zeigen jetzt ihren Namen; vorher standen sie als „ohne Namen“ da, obwohl das Aufräumen sie nicht entfernt.
- Gameserver: Steht die Markierung auf einem Linux-Server, sagt die Seite jetzt klar, dass das Valheim-Profil einen Windows-Server mit PowerShell braucht, statt nur „Die Anfrage ist fehlgeschlagen“ mit einer Fehlerkennung zu zeigen. Auch die Beschreibung des Moduls nennt diese Grenze.
- Gameserver: Ein eigener Befehl zum Lesen des Join-Codes läuft jetzt auch dann, wenn das Spiel-Profil auf dem Server nicht passt. Bisher blieb der Join-Code in diesem Fall immer leer.
- Push-Nachrichten: „Verbindung testen“ prüft bei ntfy jetzt auch das Thema. Kennt ntfy das Thema nicht oder nimmt es den Namen nicht an, oder lehnt der Server das Zugriffstoken ab, meldet der Test das. Darf ein Token nur schreiben, bleibt der Test grün und weist darauf hin, dass erst eine Testnachricht zeigt, ob Senden klappt.
- Proxmox: Ein Datenträger, dessen Zustand sich nicht auslesen lässt (etwa eine USB-Platte), gilt nicht mehr als defekt. Er wird als „unbekannt“ angezeigt und löst keine Meldung aus.
- Docker: Ein Container, dessen Gesundheitsprüfung scheitert, erscheint in der Liste und auf der Kachel der Startseite nicht mehr grün, sondern gelb.
- Server-Zustand: Auf Servern mit deutscher Systemsprache werden ausstehende Updates wieder gezählt und angezeigt, vorher stand dort fälschlich 0.
- Uptime Kuma: Zeigt die eingetragene Adresse nicht auf die Messwerte von Uptime Kuma, zum Beispiel wegen eines falschen Unterpfads, meldet Nodvard Deck jetzt einen Fehler. Bisher stand dort „ok“ mit 0 Monitoren.
- Vaultwarden: Die Kachel und die Seite melden nicht mehr „Registrierung offen“, wenn die Registrierung in Vaultwarden gesperrt ist. Lässt sich das von außen nicht sicher erkennen, steht dort jetzt ein neutraler Hinweis zum Nachsehen.

## Sicherheit

- Die SSH-Härtung zeigt beim Prüfen des Zustands keine rohe Ausgabe des Servers mehr an, sondern einen festen Hinweis. Die Einzelheiten stehen im Container-Protokoll.
- Namen von Variablen für Stacks dürfen höchstens 255 Zeichen lang sein.
- Wer neben Nodvard Deck unter demselben Rechnernamen noch andere Dienste ohne HTTPS betreibt, kann die HTTPS-Pflicht für den Browser jetzt mit NODVARD_DECK_HSTS=false abschalten, das gilt auch für die Notseite; ohne die Angabe bleibt alles wie bisher.
- Anmeldung und Abmeldung prüfen Eingaben strenger und zählen Fehlversuche zuverlässig mit.
