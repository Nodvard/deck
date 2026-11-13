# Häufige Fragen zu Nodvard Dock

[English](../en/dock/FAQ.md) · [Dock-Dokumentation](README.md) · [Dock-Schnellstart](../../deploy/nodvard-dock/README.md#loslegen)

Self-hosted, Quelltext einsehbar (PolyForm Noncommercial), kostenlos für private Nutzung.
Die folgenden 12 FAQ-Einträge behandeln die Docker-Funktionen in Nodvard Dock und Deck. Alle Funktionen und Grenzwerte sind direkt mit den Quelldateien belegt.

## 1. Docker-Netze: Neues Netz anlegen und interne Netze

**Frage:** Wie legst du ein neues Docker-Netz an und was bewirkt die Option „Nur intern“?  
**Antwort:** Du erstellst ein Netz über die Schaltfläche „Netz anlegen“ und vergibst einen Namen mit bis zu 63 Zeichen ([NetworkTools.tsx:14, 56-62](../../extensions/docker/frontend/src/NetworkTools.tsx)). Aktivierst du die Option „Nur intern“, erreichen sich verbundene Container gegenseitig, haben über dieses Netz aber keinen Zugriff auf das Internet oder dein Heimnetz ([NetworkTools.tsx:68-70](../../extensions/docker/frontend/src/NetworkTools.tsx)). Reservierte Namen wie bridge, host, none oder default darfst du für eigene Netze nicht verwenden ([NetworkTools.tsx:15, 65](../../extensions/docker/frontend/src/NetworkTools.tsx)).

## 2. Docker-Netze: Container aus Compose-Stacks manuell verbinden

**Frage:** Was passiert, wenn du einen Container aus einem Compose-Stack manuell mit einem Netz verbindest?  
**Antwort:** Du kannst Container im Menü des jeweiligen Netzes direkt verbinden oder trennen ([NetworkTools.tsx:107-124](../../extensions/docker/frontend/src/NetworkTools.tsx)). Gehört der Container zu einem Compose-Stack, bleibt diese manuelle Verbindung jedoch nur bis zur nächsten Stack-Änderung oder zum nächsten Update bestehen ([NetworkTools.tsx:80-85](../../extensions/docker/frontend/src/NetworkTools.tsx)). Sobald Compose den Container neu erstellt, greifen wieder ausschließlich die in der Compose-Datei eingetragenen Netze ([NetworkTools.tsx:82-85](../../extensions/docker/frontend/src/NetworkTools.tsx)).

## 3. Docker-Netze: Trennen des letzten Netzes und Eigenschutz

**Frage:** Warum warnt Dock vor dem Trennen des letzten Netzes und lässt sich Dock selbst trennen?  
**Antwort:** Trennst du einen Container von seinem einzigen Netz, verliert er jegliche Verbindung, hat keinen Internetzugang mehr und veröffentlichte Ports sind nicht mehr erreichbar ([NetworkTools.tsx:111-113](../../extensions/docker/frontend/src/NetworkTools.tsx)). Aus diesem Grund zeigt Dock vor dem Trennen eine deutliche Sicherheitsabfrage ([NetworkTools.tsx:114-116](../../extensions/docker/frontend/src/NetworkTools.tsx)). Der eigene Container von Nodvard bleibt zum Schutz der Verbindung dauerhaft verknüpft und lässt sich in der Oberfläche nicht vom Netz trennen ([NetworkTools.tsx:105, 141-148](../../extensions/docker/frontend/src/NetworkTools.tsx)).

## 4. Image holen: Auswirkung auf laufende Container

**Frage:** Was bewirkt die Funktion „Image holen“ und was passiert mit deinen laufenden Containern?  
**Antwort:** Über „Image holen“ lädt Docker ein bestimmtes Image von der Registry auf deinen Server herunter ([ImagePull.tsx:1, 41](../../extensions/docker/frontend/src/ImagePull.tsx)). Deine laufenden Container verändern sich dadurch nicht und laufen unterbrechungsfrei weiter ([ImagePull.tsx:77-80](../../extensions/docker/frontend/src/ImagePull.tsx)). Du kannst die Seite während des Herunterladens verlassen, während die Aktion im Hintergrund über die Freigabe läuft ([ImagePull.tsx:41, 77-80](../../extensions/docker/frontend/src/ImagePull.tsx)).

## 5. Image holen: Versions-Tags gegenüber latest

**Frage:** Warum solltest du beim Holen eines Images ein festes Tag statt „latest“ angeben?  
**Antwort:** Wenn du bei der Eingabe kein Tag und keinen Digest nennst, holt Docker automatisch das Tag latest mit dem aktuellen Stand der Registry ([ImagePull.tsx:21-25](../../extensions/docker/frontend/src/ImagePull.tsx)). Für Stacks empfiehlt Dock eine feste Versionsangabe wie nginx:1.27, damit deine Containerumgebung stabil und nachvollziehbar bleibt ([ImagePull.tsx:71-75](../../extensions/docker/frontend/src/ImagePull.tsx)). Feste Versionen schützen dich davor, dass spätere Container-Starts unbemerkt neue Stände einziehen ([ImagePull.tsx:71-75](../../extensions/docker/frontend/src/ImagePull.tsx)).

## 6. Image holen: Gestoppte Dienste in Stacks aktualisieren

**Frage:** Wie startest du gestoppte Dienste eines Stacks mit einem neuen Image?  
**Antwort:** Ein normaler Stack-Update-Plan überspringt gestoppte Dienste, weil sie durch das Einspielen sonst automatisch anlaufen würden ([image-updates.md:34-35](image-updates.md#stack-updates-und-sicherungen)). Wenn du sie mit dem frischen Image hochfahren möchtest, wählst du beim Stack zuerst „Images holen“ und danach „Anwenden“ ([image-updates.md:35-36](image-updates.md#stack-updates-und-sicherungen)). Würdest du nur „Anwenden“ aufrufen, könnte Compose bei unverändertem Tag weiterhin das alte lokale Image nutzen ([image-updates.md:36](image-updates.md#stack-updates-und-sicherungen)).

## 7. Eigene Vorlagen: Was gespeichert wird und Ausschluss von Passwörtern

**Frage:** Was speichert Dock in einer eigenen Vorlage und warum werden Passwörter abgewiesen?  
**Antwort:** Eine eigene Vorlage speichert ausschließlich den Text der Compose-Datei für künftige Stacks, niemals aber eine zugehörige .env-Datei ([OwnTemplates.tsx:1, 15-17](../../extensions/docker/frontend/src/OwnTemplates.tsx)). Erkannte feste Passwörter, Tokens und geheime Schlüssel weist Dock beim Speichern ab, damit keine sensiblen Daten geteilt werden ([OwnTemplates.tsx:16-17](../../extensions/docker/frontend/src/OwnTemplates.tsx)). Gespeicherte Vorlagen stehen anschließend allen Benutzern zur Verfügung, die Stacks auf deinen Servern anlegen dürfen ([OwnTemplates.tsx:17](../../extensions/docker/frontend/src/OwnTemplates.tsx)).

## 8. Eigene Vorlagen: Pflichtvariablen mit Fragezeichen deklarieren

**Frage:** Wie definierst du Pflichtvariablen in deinen eigenen Compose-Vorlagen?  
**Antwort:** Du kannst in der Compose-Datei Pflichtvariablen mit `${NAME:?}` oder `${NAME?}` deklarieren. Unter **Neuer Stack → Werte für diesen Stack** füllst du die erkannten Felder aus; erst danach kannst du prüfen und anlegen. Dock schreibt die Werte nach der Freigabe in die `.env` des neuen Stacks und übernimmt sie nie in eine Vorlage. Namen mit `PASSWORD`, `SECRET`, `TOKEN` oder `KEY` werden maskiert. Auch Variablen ohne Vorgabe wie `${NAME}` bekommen Felder, dürfen aber leer bleiben. `$$NAME` wertet erst der Container aus. Beispiele und Einzelheiten stehen unter [Werte für diesen Stack](templates.md#werte-für-diesen-stack).

## 9. Eigene Vorlagen: Nachträgliche Katalogänderungen

**Frage:** Verändern spätere Vorlagen-Updates deine bereits angelegten Stacks?  
**Antwort:** Nein, ein aus einer Vorlage erstellter Stack bleibt als unabhängige lokale Compose-Datei auf deinem Server liegen ([templates.md:41-42](templates.md#edition-und-pflege)). Nachträgliche Anpassungen im Katalog oder an deiner Vorlage verändern vorhandene Stacks zu keinem Zeitpunkt ([templates.md:41-42](templates.md#edition-und-pflege)). Auch überschreibt Dock beim Auswählen einer Vorlage niemals ein bestehendes Stack-Verzeichnis auf dem Zielhost ([templates.md:32](templates.md#eine-vorlage-verwenden)).

## 10. Stacks aus Git: Protokolle und Git auf dem Host

**Frage:** Welche Verbindungsprotokolle unterstützt Dock für Stacks aus Git und muss Git auf dem Host laufen?  
**Antwort:** Dock bindet Repositories ausschließlich über die HTTPS-Klonadresse ein, Git über SSH wird nicht unterstützt ([stacks-from-git.md:25-26](stacks-from-git.md#quelle-eintragen)). Auf deinem Docker-Host muss kein Git installiert sein, da Dock den gesamten Abruf selbstständig über das Netzwerk erledigt ([stacks-from-git.md:35-36](stacks-from-git.md#quelle-eintragen)). Für private Repositories hinterlegst du ein persönliches Zugriffs-Token geschützt im Tresor unter den erweiterten Docker-Einstellungen ([stacks-from-git.md:30-33](stacks-from-git.md#quelle-eintragen)).

## 11. Stacks aus Git: Schutz lokaler .env-Dateien

**Frage:** Werden deine lokalen Umgebungsdateien wie die .env überschrieben, wenn du aus Git einspielst?  
**Antwort:** Nein, vorhandene .env-Dateien und dem Stack bekannte Umgebungsdateien auf dem Host werden bei einem Git-Abgleich niemals ersetzt ([stacks-from-git.md:41-42](stacks-from-git.md#prüfen-und-einspielen)). Dock gleicht ausschließlich die Compose-Datei sowie zusätzliche Dateien innerhalb des konfigurierten Ordners ab ([stacks-from-git.md:28-29, 41-42](stacks-from-git.md#quelle-eintragen)). Deine geheimen Schlüssel und hostspezifischen Variablen bleiben dadurch lokal auf dem Docker-Host geschützt ([stacks-from-git.md:21-22, 41-42](stacks-from-git.md#quelle-eintragen)).

## 12. Stacks aus Git: Limits und Dateibeschränkungen

**Frage:** Welche Grenzen und Dateibeschränkungen gelten beim Einbinden von Stacks aus Git?  
**Antwort:** Der abgeglichene Ordner darf höchstens 300 Dateien sowie 10 MiB Dateiinhalte umfassen, und die Compose-Datei darf maximal 256 KiB groß sein ([stacks-from-git.md:64](stacks-from-git.md#neue-commits-und-grenzen)). Submodule, Symlinks und Git-LFS-Dateien werden nicht unterstützt und verhindern das Einspielen ([stacks-from-git.md:61-62](stacks-from-git.md#neue-commits-und-grenzen)). Außerdem muss der Stack aus genau einer Compose-Datei direkt im gewählten Stack-Ordner bestehen ([stacks-from-git.md:60](stacks-from-git.md#neue-commits-und-grenzen)).
