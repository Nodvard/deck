# Fehlersuche

Nodvard Deck steht unter der Lizenz PolyForm Noncommercial: Quelltext einsehbar, kostenlos für den privaten Gebrauch. Nachfolgend sind 20 häufige Probleme bei Erst-Einrichtung und laufendem Betrieb mit Symptom, Ursache und Lösung zusammengefasst.

## 1. Einrichtungscode nicht gefunden
* **Symptom:** Beim ersten Aufruf des Einrichtungsassistenten unter `/setup` wird ein Einrichtungscode verlangt, der auf der Weboberfläche nirgends angezeigt wird.
* **Ursache:** Nodvard Deck erzeugt diesen Code beim allerersten Start zufällig und schreibt ihn ausschließlich in das Protokoll des Containers.
* **Lösung:** Lies den Code im Terminal mit `docker compose logs nodvard-deck` oder in der Log-Ansicht deiner Container-Oberfläche aus dem dort sichtbaren Rahmen ab.

## 2. Port 8080 belegt
* **Symptom:** Der Start des Containers bricht mit dem Fehler `port is already allocated` oder `address already in use` ab.
* **Ursache:** Ein anderer Dienst auf dem Hostsystem belegt bereits Port 8080, wie es beispielsweise bei der Weboberfläche eines QNAP-NAS der Fall ist.
* **Lösung:** Lege eine Datei `.env` mit dem Eintrag `NODVARD_DECK_PORT=8081` an oder passe die Portweiterleitung in deiner Compose-Datei auf `8081:8080` an.

## 3. SSH-Schlüssel abgelehnt
* **Symptom:** Die Verbindungsprüfung auf der Server-Seite scheitert bei der Authentifizierung mit der Meldung, dass der Server den Schlüssel nicht akzeptiert hat.
* **Ursache:** Der öffentliche Schlüssel fehlt in der Datei `~/.ssh/authorized_keys` des Zielkontos, oder ein kleinerer SSH-Server wie Dropbear benötigt angepasste Optionen statt der Standardparameter.
* **Lösung:** Führe den generierten Einrichtungsbefehl aus den Server-Einstellungen einmal direkt auf dem Zielsystem aus, damit Benutzer und Schlüsseldatei passend eingerichtet werden.

## 4. Fehlende Root-Rechte / sudo-Regel fehlt
* **Symptom:** Administrationsaufgaben wie Quarantäne, Dienst-Neustarts oder Systemupdates brechen mit dem Hinweis auf fehlende Root-Rechte und eine Passwortabfrage von sudo ab.
* **Ursache:** Nodvard Deck meldet sich auf dem Zielsystem nicht als `root` an und für den verwendeten SSH-Benutzer existiert noch keine passwortlose sudo-Regel für die Hilfsskripte.
* **Lösung:** Führe den Einrichtungsbefehl unter Server & Zugänge mit gesetztem Haken für Root-Rechte ohne Passwort erneut aus, um die passenden sudoers-Dateien mit Prüfsummen zu hinterlegen.

## 5. Docker-Socket / Gruppe docker fehlt
* **Symptom:** Das Docker-Modul kann Container auf einem Zielhost nicht auslesen oder bedienen, obwohl die Markierung `docker` am Server hinterlegt ist.
* **Ursache:** Der konfigurierte Benutzer gehört auf dem Zielsystem nicht zur Gruppe `docker`, oder bei Betrieb in einem Proxmox-LXC-Container fehlen die erforderlichen Features für Nesting und keyctl.
* **Lösung:** Wiederhole den Einrichtungsbefehl mit aktiviertem Haken für die Gruppe docker oder aktiviere in den Proxmox-Optionen des LXC-Containers die Merkmale `nesting=1` und `keyctl=1`.

## 6. Helfer veraltet nach Update
* **Symptom:** Unter Einstellungen → System → Updates steht der Update-Helfer auf `nicht bereit` oder die Server-Prüfung markiert Hilfsskripte gelb als veraltet.
* **Ursache:** Eine neu eingespielte Version von Nodvard Deck erwartet aktualisierte Hilfsskripte mit neuen Prüfsummen oder einen erneuerten Stand des Update-Helfer-Dienstes.
* **Lösung:** Aktualisiere den Container des Update-Helfers in deiner Compose-Datei und führe den Einrichtungsbefehl auf den gemeldeten Zielservern noch einmal aus.

## 7. Proxmox-Token abgelehnt oder Rechte fehlen
* **Symptom:** Die Proxmox-Erweiterung meldet `HTTP 401: Proxmox hat den Zugang abgelehnt` oder `HTTP 403: Dem Token fehlt ein Recht für diese Abfrage`.
* **Ursache:** Token-ID oder Geheimnis wurden fehlerhaft eingetragen, oder der zugehörigen Rolle fehlen Leserechte wie `VM.Audit` für die Abfrage von Gästen.
* **Lösung:** Trage das Token-Geheimnis in den Moduleinstellungen neu ein und stelle sicher, dass die Rolle in Proxmox alle notwendigen Berechtigungen auf dem Cluster-Pfad besitzt.

## 8. Zeitzone verstellt
* **Symptom:** Geplante Vorgänge wie das Morgen-Briefing, Bereinigungen oder Wartungsfenster werden zu unerwarteten Uhrzeiten ausgeführt.
* **Ursache:** Beim Einrichten wurde die Zeitzonenauswahl übersprungen oder eine vom Standort abweichende Region gespeichert.
* **Lösung:** Öffne die Seite Einstellungen → System, wähle dort deine tatsächliche Region aus der Zeitzonenliste und speichere die Anpassung ab.

## 9. Proxy davor / Warnbalken und Umleitungen
* **Symptom:** Das Dashboard zeigt dauerhaft einen Warnbalken zur unverschlüsselten Verbindung an oder externe Abfragen scheitern mit dem Hinweis auf eine Weiterleitung.
* **Ursache:** Ein vorgeschalteter Reverse-Proxy reicht HTTPS-Header nicht durch oder leitet HTTP-Anfragen weiter, denen Nodvard Deck aus Sicherheitsgründen nicht folgt.
* **Lösung:** Trage die IP-Adresse deines Proxys unter Einstellungen → System → Vertrauenswürdiger Proxy ein und verwende in allen Moduleinstellungen direkt die finale HTTPS-Adresse.

## 10. Handy-App / Push-Nachrichten (ntfy)
* **Symptom:** Auf dem Smartphone treffen trotz eingetragener ntfy-Konfiguration keine Benachrichtigungen über den Systemzustand oder Lageberichte ein.
* **Ursache:** Der in der ntfy-App abonnierte Kanalname stimmt nicht mit den Einstellungen überein, oder die Mindest-Stufe filtert einfache Info-Nachrichten heraus.
* **Lösung:** Scanne den QR-Code aus den Moduleinstellungen direkt mit der Kamera deines Handys und überprüfe den Empfang mit dem Knopf zur Testnachricht.

## 11. Server-Schlüssel unbestätigt oder geändert
* **Symptom:** Die SSH-Verbindung zu einem Server stoppt mit dem Hinweis, dass der Server-Schlüssel noch nicht bestätigt wurde oder sich geändert hat.
* **Ursache:** Die Sicherheitsfunktion zur Schlüsselprüfung fordert eine Bestätigung des Fingerabdrucks ein, oder der Zielrechner wurde neu installiert und liefert neue Host-Keys.
* **Lösung:** Prüfe den neuen Fingerabdruck unabhängig (zum Beispiel direkt an der Konsole des Servers). Vergiss danach unter „Server-Schlüssel“ den alten Schlüssel des Servers, klicke dann auf Verbindung prüfen und bestätige den neuen Schlüssel. Solange der alte Schlüssel gespeichert ist, lässt sich der neue nicht bestätigen.

## 12. Ausgesperrt / Passwort oder 2FA verloren
* **Symptom:** Der Zugang zum Inhaber-Konto ist blockiert, weil das Passwort vergessen wurde und keine funktionierenden Authenticator-Codes vorliegen.
* **Ursache:** Die Anmeldedaten sowie die zehnstelligen Notfall-Wiederherstellungscodes sind verloren gegangen.
* **Lösung:** Im Ordner mit der Compose-Datei auf dem Docker-Host: `docker compose exec nodvard-deck python -m nodvard_deck.admin list-users` zeigt die Benutzernamen; dann `docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password <benutzername>` oder `docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa <benutzername>`. Die Befehle laufen im Container, nicht auf dem Host.

## 13. Notseite aktiv / Dashboard startet nicht
* **Symptom:** Der Webbrowser zeigt nach einem Update statt der regulären Oberfläche eine Notseite mit Fehlerstatus 503 an.
* **Ursache:** Ein Datenbankumbau ist fehlgeschlagen, auf dem Datenträger war kein Platz für die automatische Sicherheitskopie, oder die Daten stammen aus einer neueren Version.
* **Lösung:** Lies den Notfallcode aus dem Containerprotokoll ab und melde dich damit auf der Notseite an. Bei Platzmangel: Platz schaffen und „Neu versuchen“. Bei einer fehlgeschlagenen Migration: die vorherige Image-Version starten (Tag in der Compose-Datei zurücksetzen, `docker compose up -d`). Den Knopf „Stand vor dem Update wiederherstellen“ gibt es nur, wenn die Daten von einer neueren Version stammen und eine Rückweg-Kopie vorliegt.

## 14. Dateirechte auf NAS / `/app/data` fehlerhaft
* **Symptom:** Der Container verharrt im Fehlerzustand und meldet im Log, dass sich die Besitzrechte an `/app/data` nicht setzen ließen.
* **Ursache:** Ein eingebundener Ordner auf einer NAS-Freigabe oder einem Netzlaufwerk verweigert dem Container-Benutzer mit der UID 1000 die erforderlichen Schreibrechte.
* **Lösung:** Nutze vorzugsweise das Standard-Volume `nodvard-deck-data` oder passe die Zugriffsrechte des Host-Verzeichnisses für den Benutzer 1000 an.

## 15. Server nicht erreichbar / Verbindungsabbruch
* **Symptom:** Terminal, Dateiansicht und Überwachung melden eine Zeitüberschreitung, einen Verbindungsabbruch oder dass kein Weg zum Server existiert.
* **Ursache:** Das Zielgerät ist ausgeschaltet, eine Firewall blockiert den konfigurierten SSH-Port, oder die eingetragene Netzwerkadresse ist falsch geschrieben.
* **Lösung:** Kontrolliere die IP-Adresse unter Server & Zugänge und stelle sicher, dass der Zielhost erreichbar ist sowie Verbindungen auf Port 22 annimmt.

## 16. Proxmox-Zertifikat nicht akzeptiert
* **Symptom:** Der Verbindungsaufbau zu Proxmox bricht mit dem Hinweis ab, dass das Zertifikat nicht akzeptiert wird oder der Server ein anderes Zertifikat zeigt.
* **Ursache:** Proxmox nutzt standardmäßig ein selbstsigniertes SSL-Zertifikat, dessen Fingerabdruck in Nodvard Deck noch nicht explizit bestätigt wurde.
* **Lösung:** Öffne in der Proxmox-Erweiterung den Reiter für Verbindungen, wähle den Menüpunkt Zertifikat und bestätige dort den angezeigten Fingerabdruck.

## 17. Zu viele Fehlversuche / Rate-Limit greift
* **Symptom:** Die Anmeldung oder Code-Eingabe wird blockiert mit dem Hinweis, dass zu viele Versuche stattfanden oder viele Anmeldungen gleichzeitig laufen.
* **Ursache:** Ein interner Schutzmechanismus sperrt Konten und IP-Adressen nach wiederholten Fehleingaben temporär gegen Brute-Force-Angriffe.
* **Lösung:** Warte die angezeigte Wartezeit ab, bevor du die Eingabe wiederholst. Nach vielen falschen Codes innerhalb von 24 Stunden greift zusätzlich eine Tagessperre, dann hilft auch ein richtiger App-Code erst am nächsten Tag. Mit einem Wiederherstellungscode kommst du trotzdem hinein.

## 18. Sicherung einspielen scheitert oder Container bleibt aus
* **Symptom:** Nach dem Einspielen einer Sicherungsdatei bleibt der Container gestoppt oder das Wiederherstellen bricht mit einer Fehlermeldung ab.
* **Ursache:** Dem Container fehlt eine automatische Neustart-Richtlinie in Docker, oder die Sicherung wurde mit einer neueren Programmversion erstellt und ist inkompatibel.
* **Lösung:** Starte den Container auf dem Host manuell über deine Compose-Umgebung und stelle sicher, dass das aktuelle Image mindestens den Stand der Sicherungsdatei aufweist.

## 19. Updates durch Phasing oder apt-hold zurückgehalten
* **Symptom:** In der Update-Übersicht von Nodvard Shield stehen Pakete dauerhaft als verzögert oder zurückgehalten und lassen sich nicht einspielen.
* **Ursache:** Drei verschiedene Fälle: Ubuntu verteilt manche Pakete schrittweise per Phasing; apt hält Pakete zurück, weil die Aktualisierung andere Pakete entfernen oder neu installieren müsste; oder ein Paket ist mit `apt-mark hold` ausdrücklich gesperrt.
* **Lösung:** Phasing: abwarten. Zurückgehaltene Abhängigkeiten: auf dem Server `sudo apt full-upgrade` ausführen und die angezeigten Änderungen prüfen. Ausdrückliche Sperre: `apt-mark showhold` zeigt sie; ist sie unbeabsichtigt, hebt `sudo apt-mark unhold <Paketname>` sie auf. Ein `full-upgrade` löst eine solche Sperre nicht.

## 20. Veraltete Handy-App bei Kontoeinstellungen
* **Symptom:** Beim Versuch, das Passwort zu ändern oder den zweiten Faktor in einer mobilen App zu verwalten, schlägt der Vorgang fehl.
* **Ursache:** Ältere App-Fassungen unterstützen die zwingend erforderliche zusätzliche Code-Abfrage bei sicherheitsrelevanten Kontoänderungen noch nicht.
* **Lösung:** Führe ein Update deiner Smartphone-App durch oder öffne die Einstellungen zur Passwortänderung vorübergehend im Desktop-Browser.
