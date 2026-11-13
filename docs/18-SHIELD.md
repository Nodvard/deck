# Nodvard Shield

## CVE-Abgleich und Prüfung von außen

In der Shield-Übersicht stehen je Server zwei Karten. **CVE-Abgleich** zeigt installierte Pakete mit bekannten
Sicherheitslücken: Paketname, installierte Version, CVE, Schwere und die Version mit der Behebung, sofern bekannt.
Die Karte zählt hohe und kritische Funde. **Updates ansehen** öffnet die Update-Zentrale für diesen Server.
Ein Fund installiert keine Updates. Eine fehlgeschlagene oder nicht unterstützte Prüfung gilt nicht als Entwarnung.

Das mitgelieferte Shield-Hilfsskript Version 24 liest die Paketliste mit `dpkg-query` oder `rpm`. Ohne aktuelles
Hilfsskript versucht Shield dieselbe Leseabfrage mit den vorhandenen Zugriffsrechten. Paketnamen und Versionen
gehen nur vom verwalteten Server an Deck. Deck lädt öffentliche Sicherheitsdaten, speichert sie für 24 Stunden
und gleicht lokal ab. Es übermittelt keine Paketlisten, Paketnamen oder installierten Versionen an die Anbieter.
Ein Konto bei einem externen Sicherheitsdienst ist nicht nötig. Berücksichtigt werden die Distribution und ihre
Version sowie die Paketversionen einschließlich Epoch und Distributionszusätzen.

Die Daten stammen aus dem [Debian Security Tracker](https://security-tracker.debian.org/tracker/data/json),
den [öffentlichen Ubuntu-Sicherheitsdaten](https://documentation.ubuntu.com/security/security-updates/osv/) und
den [Red-Hat-Sicherheitsdaten](https://access.redhat.com/security/data).
Für Fedora werden öffentliche Bodhi-Sicherheitsmeldungen verwendet, da kein offizieller OVAL-/CSAF-Feed
verfügbar ist. Sie erfassen veröffentlichte Sicherheitsupdates; noch nicht veröffentlichte Behebungen und
Meldungen ohne zuordenbare CVE sind damit nicht vollständig abgedeckt.
Debian wird nach Distributionscodename abgeglichen, Ubuntu über die offiziellen JSON-Daten im OSV-Archiv.
Ubuntu-Pro-/ESM-Daten werden nicht mit dem normalen Ubuntu-Paketbestand vermischt. Ein Stand, für den nur noch
Pro-Daten vorliegen, wird als nicht unterstützt angezeigt. RHEL 8 und 9 verwenden OVAL einschließlich noch
nicht behobener Lücken, RHEL ab Version 10 die veröffentlichten CSAF-Sicherheitsmeldungen. Noch nicht behobene
Lücken sind in diesen CSAF-Meldungen nicht vollständig enthalten. Nicht mit der Paketliste prüfbare OVAL-Regeln
und nicht eindeutig zuordenbare Produktvarianten erscheinen als Prüfproblem.
Drittanbieterpakete und nicht unterstützte Distributionsstände können außerhalb der Datenquellen liegen.

**Prüfung von außen** baut von Deck aus kurze TCP-Verbindungen zu gängigen Ports und den bekannten Diensten
des Servers auf. Der erste erfolgreiche Lauf bildet den Vergleichsstand. Später zusätzlich offene Ports
erscheinen als Warnung. Ein fehlgeschlagener Lauf ersetzt diesen Vergleichsstand nicht.
Geprüft wird aus dem Heimnetz, also von dem Rechner aus, auf dem Deck läuft, nicht aus dem Internet. Ob ein Port
aus dem Internet erreichbar ist, hängt zusätzlich von Router und Portfreigaben ab und wird nicht geprüft.
UDP und alle 65.535 TCP-Ports werden nicht erfasst.

Für bekannte HTTPS-Dienste prüft Shield Ablauf, Aussteller, passenden Hostnamen, Vertrauen und schwache
TLS-Protokolle. Zertifikate mit weniger als 14 Tagen Restlaufzeit, selbstsignierte Zertifikate und Abweichungen
beim Hostnamen werden erklärt. Selbstsigniert bedeutet, dass die Gegenstelle ihr Zertifikat selbst bestätigt;
das kann im eigenen Netz beabsichtigt sein, ersetzt aber keine unabhängige Bestätigung ihrer Identität.
Kann Deck eine Protokollprüfung nicht ausführen, steht das in der Karte. Ein unbekanntes Ergebnis gilt nicht als sicher.

**Jetzt prüfen** startet beide Leseprüfungen. Der erste Abruf der Sicherheitsdaten kann länger dauern.
Die Außenprüfung lässt sich je Server sowie unter **Shield-Einstellungen → Außenprüfung erlauben** ausschalten.
Dann finden auch bei manuellen Prüfungen keine Verbindungen dafür statt. Ein vorhandenes Ergebnis bleibt mit
seinem ursprünglichen Zeitpunkt sichtbar. Während eines Laufs sind doppelte Starts und Änderungen am Schalter gesperrt.

Der gemeinsame Zeitplan **CVE-Abgleich und Außenprüfung** läuft standardmäßig täglich um 06:30 Uhr in der
Zeitzone der Installation. Zeit und automatische Ausführung lassen sich in den Shield-Einstellungen ändern.
Es gelten dieselben ausgewählten Linux-Server mit SSH-Zugang wie im Virenschutz.
Beide Prüfungen lesen nur. Sie ändern keine Pakete, Firewall-Regeln oder Zertifikate auf den Servern.
Zum Anzeigen genügt `soc.read`, zum Starten und Umschalten je Server wird `soc.manage` benötigt.

## Nodvard KI: Anbieter einrichten

Unter **Einstellungen → Erweiterungen → Nodvard Shield** wählst du den Anbieter.
**Ollama (lokal)** bleibt die Voreinstellung. Für OpenRouter, vLLM oder llama.cpp wählst du
**OpenAI-kompatibel** (`openai_compat`). Trage die Basis-URL und den genauen Modellnamen ein.
Ein lokaler Server kann zum Beispiel unter `http://127.0.0.1:8080/v1` laufen. In einem Container
bezeichnet `127.0.0.1` den Deck-Container selbst. Nutze dort eine von Deck erreichbare Adresse.
Der Anbieter muss `POST /v1/chat/completions` ohne Streaming unterstützen. Ein bereits vorhandenes
`/v1` wird nicht doppelt angehängt; ein Anbieterpräfix vor `/v1` bleibt erhalten.

Den Token hinterlegst du im zugehörigen **Tresor-Feld**. Er gehört weder in die Basis-URL noch in
den Modellnamen. Lokale Anbieter ohne Anmeldung benötigen keinen Token. Haupt- und Ersatz-Server
eines Anbieters verwenden denselben Token. Trage einen Ersatz-Server deshalb nur ein, wenn er
denselben Zugang verwenden darf. Das Ersatzmodell wird genutzt, wenn das Hauptmodell nicht antwortet.
Ohne Ersatz-Server bleibt ein Ausfall des Hauptmodells als nicht erreichbare Verbindung erkennbar.

Speichere die Einstellungen und den Token, bevor du **Verbindung testen** drückst. Der Test nutzt
die gespeicherte Konfiguration, sendet eine kleine Anfrage und zeigt Antwortzeit sowie Modell an.
Falls `GET /v1/models` verfügbar ist, erscheint auch die Modellliste. Eine fehlende Modellliste
verhindert die Nutzung nicht; der Modellname lässt sich von Hand eintragen. Bei abgelehnten
Zugangsdaten prüfst du den Token im Tresor, bei Zeitüberschreitung Adresse, Port und Erreichbarkeit
aus Deck. Der Test benötigt das Recht, Einstellungen zu ändern.

Bei einer öffentlichen Basis-URL erscheint automatisch **Daten verlassen dein Netz**.
Das gilt auch für einen öffentlichen Ersatz-Server. Bei localhost, privaten IP-Netzen und
Tailscale-Namen entfällt dieser Hinweis. Er beschreibt die eingetragene Adresse, keine Prüfung
des Anbieters: Auch ein privater Server kann Anfragen weiterleiten. An den gewählten Anbieter
gehen die jeweilige Anfrage und die dafür verwendeten Server- oder Containerdaten. Prüfe vor
der Nutzung eines externen Anbieters, welche Daten du dorthin senden möchtest.

## Fail2ban je Server einstellen

Unter **Shield → Einbruchschutz** enthält jeder Server mit installiertem Fail2ban eine Karte
**Fail2ban-Einstellungen**. Beim Öffnen liest Shield den aktuellen Stand vom Server. Die Sperrdauer
(`bantime`) und Fundzeit (`findtime`) stehen in Sekunden, die Versuche (`maxretry`) als Anzahl.
Wer innerhalb der Fundzeit diese Zahl fehlgeschlagener Anmeldungen erreicht, wird für die Sperrdauer gesperrt.
Zeiten dürfen zwischen 1 und 31.536.000 Sekunden liegen, Versuche zwischen 1 und 1.000.
Ist der Dienst oder der SSH-Jail nicht erreichbar, zeigt die Karte gespeicherte eigene Einstellungen und
ansonsten Standardwerte mit einem Hinweis. Zum Aktivieren muss der Fail2ban-Dienst laufen.

Unter **Weitere Ausnahmen** lassen sich IPv4- und IPv6-Adressen sowie Netze in CIDR-Schreibweise eintragen,
jeweils eine Ausnahme je Zeile. Shield ergänzt automatisch die SSH-Quelladresse, die der Server für seine
Verbindung mit Deck meldet. Diese Adresse ist sichtbar und nicht entfernbar. Sie stammt aus der SSH-Verbindung,
unabhängig von der Adresse des Browsers. Ist sie unbekannt oder hat sie sich seit der Freigabe geändert,
übernimmt Shield nichts. Dann die Karte schließen und die Seite neu laden.

`sshd` ist verpflichtend. Weitere in der Jail-Konfiguration des Servers erkannte Jails lassen sich auswählen.
Die gewählten Jails erhalten dieselben Zeiten, Versuche und Ausnahmen. Abgewählte erkannte Jails werden in der
eigenen Datei deaktiviert. Überschreibt eine andere Konfigurationsdatei die Deck-Ausnahme, lehnt Shield die
Übernahme ab. Maßgeblich ist dabei die mit `fail2ban-client -d` gelesene wirksame Konfiguration aller aktiven Jails.

**Einstellungen übernehmen** erstellt eine Aktion mit Risiko „mittel“. Die Freigabe läuft wie bei anderen
Shield-Änderungen über das Aktions-Gate und wird im Audit protokolliert. Das Hilfsskript schreibt ausschließlich
`/etc/fail2ban/jail.d/nodvard.local`; `jail.conf` und `jail.local` bleiben unverändert, auch bei der Installation.
Ein bereits von Shield eingerichtetes systemd-Backend für `sshd` bleibt erhalten.
Vor dem Aktivieren führt es `fail2ban-client -t` aus. Nur nach erfolgreichem Test folgt `fail2ban-client reload`.
Bei einem fehlgeschlagenen Test oder Reload wird die vorherige Datei wiederhergestellt. Ein fehlgeschlagener
Reload kann den laufenden Dienst bereits teilweise verändert haben; nach Behebung des Fehlers erneut übernehmen.

Benötigt wird das mitgelieferte Shield-Hilfsskript ab Version 18. Bestehende Server nach dem Deck-Update erneut
einrichten, wenn Shield ein veraltetes Hilfsskript meldet. Die neuen Unterbefehle `fail2ban-config-read` und
`fail2ban-config-write` haben feste Argumente und prüfen die erlaubten Inhalte; ein frei formulierter Root-Befehl
wird dafür nicht verwendet.

Die bisherige Statusanzeige und die gesperrten IPs je Jail bleiben reine Leseabfragen. Zum Lesen der Karte genügt
das Leserecht für Shield. Zum Übernehmen werden zusätzlich die Verwaltungs- und Ausführungsrechte sowie die
passende Freigabe benötigt.
