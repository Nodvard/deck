# SSH härten mit Shield

Im Reiter **Einbruchschutz** bietet jede Serverkarte **SSH härten** an. Der Deck-Zugang muss einen SSH-Schlüssel verwenden. Bei einem Passwortzugang ist die Aktion gesperrt; **Schlüssel hinterlegen** öffnet **Server & Zugänge**. Dort den Schlüssel als Standardzugang hinterlegen.

Fehlt der öffentliche Deck-Schlüssel in `authorized_keys` des Zugangsnutzers, trägt **Deck-Schlüssel eintragen** ihn als eigene Aktion ein. Vorhandene Schlüssel bleiben erhalten. Beide Schritte und **Härtung zurücknehmen** laufen mit hohem Risiko durch das Aktions-Gate und erscheinen im Audit-Protokoll.

Die Härtung benötigt das aktuelle Shield-Hilfsskript ab Version 26. Fehlt es, den Server über seinen Einrichtungsbefehl erneut einrichten. Die einzige geänderte SSH-Konfigurationsdatei ist `/etc/ssh/sshd_config.d/nodvard-haertung.conf`:

```text
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
MaxAuthTries 3
```

Wenn Deck sich als `root` anmeldet, steht dort `PermitRootLogin prohibit-password`. Root bleibt dann mit Schlüssel erreichbar. Die Serverkarte und die Freigabe warnen ausdrücklich davor.

Meldet sich Deck als anderer Benutzer an, sind für root aber Schlüssel in `authorized_keys` eingetragen, steht dort ebenfalls `PermitRootLogin prohibit-password`. Wer sich als root mit Schlüssel anmeldet, wird so nicht ausgesperrt; gesperrt wird nur die Passwortanmeldung. Karte und Freigabe nennen diesen Fall. Wer sich bisher mit Passwort anmeldet, braucht danach einen Schlüssel. Wegen `MaxAuthTries 3` sollte ein SSH-Client mit vielen Schlüsseln im Agenten den passenden fest angeben (`IdentitiesOnly yes`).

Das Hilfsskript prüft die Konfiguration mit `sshd -t` vor dem Schreiben und vor dem Neuladen. Es prüft außerdem mit `sshd -T`, ob die vier Werte wirksam sind. Fehlt die Einbindung des Konfigurationsordners oder hat eine andere Einstellung Vorrang, bricht die Härtung ab. Bestehende Konfigurationen werden nicht umgeschrieben.

Nach dem Neuladen öffnet Deck eine neue Verbindung mit demselben Schlüssel und prüft den gespeicherten Server-Schlüssel. Passwort und interaktive Anmeldung sind dabei ausgeschlossen. Scheitert die Gegenprobe, entfernt Deck die eigene Datei über die noch offene Verbindung und lädt SSH erneut. Die Aktion erscheint als Fehler. Ohne Bestätigung versucht der Server nach 60 Sekunden selbst die Rücknahme, auch wenn Deck inzwischen nicht mehr erreichbar ist. Kann Deck den Rückweg nicht bestätigen, steht das ausdrücklich im Ergebnis.

**Härtung zurücknehmen** entfernt die Datei, prüft die verbleibende Konfiguration und lädt SSH neu. Danach gelten wieder die übrigen Einstellungen. Ein separat eingetragener Schlüssel bleibt erhalten. Die Karte zeigt den geprüften Zustand; bei einem Lesefehler steht dort **Zustand unbekannt**. **SSH-Zustand prüfen** liest ihn erneut.

Die Statusabfrage liegt unter `GET /api/v1/ext/shield/defender/hosts/{host_id}/ssh-hardening` und braucht `soc.read`. `POST` auf denselben Pfad braucht `soc.manage` und `hosts.execute`, erwartet `{"mode":"on"}`, `{"mode":"off"}` oder `{"mode":"add-key"}` und liefert den Aktionsvorschlag. Die Freigabe benötigt `actions.approve:high`.
