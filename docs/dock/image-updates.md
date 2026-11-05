# Image-Updates

[English](../en/dock/image-updates.md) · [Dock-Dokumentation](README.md) · [Fehlerbehebung](troubleshooting.md)

## Prüfen und Einspielen sind getrennt

Auf **Docker** startet **Image-Updates prüfen** einen lesenden Vergleich: Hat die Registry unter demselben Image-Tag
einen anderen Digest als der laufende Container? Es wird dabei kein Image heruntergeladen und kein Container neu gestartet.
Die Prüfung sucht keine höhere Versionsnummer. Für einen Wechsel von etwa `app:1` zu `app:2` musst du die Compose-Datei
ändern, bei einem Git-Stack in dessen Repository.

Die automatische Prüfung ist unter **Einstellungen → Erweiterungen → Docker → Erweitert** mit
`image_updates_enabled` einschaltbar und standardmäßig aus. Sie installiert keine Updates.
Der Docker-Host fragt die Registry ab; für private Images den Registry-Zugang hinterlegen und die Anmeldung am Host
über die Freigabe ausführen. Ein nicht prüfbares Image bedeutet nicht, dass es aktuell ist.

## Einen Dienst aktualisieren

1. Einen Container mit verfügbarem Update auswählen und den Update-Plan öffnen.
2. Dienst, Compose-Projekt, betroffene Container und Warnungen prüfen. Dock nennt einen Grund, wenn es nicht geht.
3. Das Einspielen bestätigen und freigeben. Dock sichert das alte Image unter einem lokalen Tag, lädt das neue Image
   und erstellt den Compose-Dienst neu. Der Dienst ist dabei kurz nicht erreichbar.
4. Das Ergebnis unter **Aktionen**, den Containerzustand und die Anwendungslogs prüfen.

Unterstützt werden geeignete Compose-Dienste mit erreichbaren Compose-Dateien auf dem Host.
Einzelne `docker run`-Container müssen zuerst in einen Stack umgewandelt werden; die Oberfläche bietet dafür eine eigene Aktion.
Digest-fixierte Images, lokale Builds und unklare oder veränderte Compose-Zuordnungen können nicht wie ein normales
Registry-Image unter demselben Tag aktualisiert werden. Dock zeigt den jeweiligen Grund.

## Stack-Updates und Sicherungen

Unter **Stacks** kannst du den Update-Plan für den ganzen Stack öffnen. Die Vorschau nennt die aktualisierbaren Dienste.

Gestoppte Dienste werden hier nicht aktualisiert, weil sie dadurch starten würden.
Um sie mit dem aktuellen Image zu starten, wähle beim Stack zuerst **Images holen** und danach **Anwenden**.
**Anwenden** allein kann bei unverändertem Tag das alte lokale Image verwenden.

Optional **Volumes vorher sichern** wählen. Dock prüft den Platz und stoppt die betroffenen Dienste während der Sicherung.
Danach wendet Compose den Stack an. Die Archive liegen auf dem Docker-Host, nicht im Dock-Datenspeicher.

Die Stack-Sicherungen stehen unter **Stacks → Sicherungen** zum Wiederherstellen oder Löschen bereit.
Bind-Mounts und Daten außerhalb der gesicherten Volumes brauchen eine eigene Sicherung.
Ein Datei- oder Image-Rückweg stellt keine Datenbankinhalte wieder her.

## Rückweg und Grenzen

Nach einem Stack-Update bietet **Zurück zur vorigen Version** den Rückweg über die gesicherten Images.
Bei einem einzelnen Image-Update versucht Dock standardmäßig automatisch zurückzugehen, wenn der neue Container nicht
sauber startet. `image_update_auto_rollback` schaltet diesen Versuch ab. Bei erkannten Datenbanken und anderen
Datenspeichern gibt es keinen automatischen Rückweg, weil eine neue Version das Datenformat verändert haben kann.
Die Erkennung ersetzt deine Prüfung der Anwendung nicht.

Gesicherte Images auf dem Host für den Rückweg aufbewahren. Auch ein als laufend oder gesund erkannter Container
kann fachlich fehlerhaft sein; nach einem Update die Anwendung selbst prüfen.
Dock selbst wird über [Compose aktualisiert](../../deploy/nodvard-dock/README.md#dock-aktualisieren).

Codebelege: [Digest-Prüfung](../../extensions/docker/src/nodvard_deck_ext_docker/image_updates.py),
[geeignete Compose-Dienste](../../extensions/docker/src/nodvard_deck_ext_docker/image_apply.py),
[Einspielen und automatische Rückkehr](../../extensions/docker/src/nodvard_deck_ext_docker/applier.py),
[Stack-Updates](../../extensions/docker/src/nodvard_deck_ext_docker/stack_update.py),
[Volume-Wiederherstellung](../../extensions/docker/src/nodvard_deck_ext_docker/stack_restore.py).
