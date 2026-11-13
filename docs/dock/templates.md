# Vorlagen für neue Stacks

[English](../en/dock/templates.md) · [Dock-Dokumentation](README.md) · [Stacks aus Git](stacks-from-git.md)

## Vorhandener Katalog

Der mitgelieferte Katalog enthält genau diese vier Vorlagen. Die Ports gelten für die unveränderte Vorlage auf dem Docker-Host.

| Vorlage | ID | Host-Ports | Nach dem Start |
|---|---|---|---|
| Uptime Kuma | `uptime-kuma` | `3001` | Port 3001 öffnen, Datenbank wählen und eigenes Konto anlegen |
| Vaultwarden | `vaultwarden` | `8081` | Konto anlegen, danach `SIGNUPS_ALLOWED` auf `false` setzen; Browser-Tresor und Apps brauchen HTTPS |
| Nginx Proxy Manager | `nginx-proxy-manager` | `80`, `443`, `81` | Ports 80 und 443 freihalten, Verwaltung auf Port 81 öffnen und Konto anlegen |
| Gitea | `gitea` | `3000`, `2222` | Einrichtung auf Port 3000 abschließen und dort SSH-Port 2222 eintragen |

Die Images haben im Katalog feste Versions-Tags. Die genaue Compose-Datei und die aktuellen Tags stehen im
[Vorlagen-Code](../../extensions/docker/src/nodvard_deck_ext_docker/templates.py), der über `/ext/docker/stack-templates`
an die Oberfläche geliefert wird. Ein festes Tag garantiert keine automatische Suche nach höheren Versionsnummern.
Die Hinweise zum ersten Konto sofort beachten, damit niemand im erreichbaren Netz die Einrichtung übernimmt.

## Eine Vorlage verwenden

1. **Docker → Stacks → Neuer Stack** öffnen und unter **Vorlage** eine Anwendung wählen.
2. Name, Ordner, Compose-Datei, Ports und Einrichtungshinweis prüfen. Ein selbst gewählter Name bleibt bei einem
   Vorlagenwechsel erhalten; vor dem Ersetzen bearbeiteten Texts fragt Dock nach.
3. Bei Portkonflikten den Host-Port in der Compose-Datei ändern. Für neue Stacks gilt zunächst `/opt/stacks`;
   der Ordner lässt sich je Host ändern, auch über **Im Heimatordner**.
4. Erkannte Variablen unter **Werte für diesen Stack** ausfüllen. Pflichtfelder müssen vor dem Prüfen und Anlegen gefüllt sein.
5. **Prüfen** validiert mit Docker Compose auf dem Host. **Anlegen** schreibt `compose.yaml` und die eingegebenen Werte in
   die `.env` im neuen Stack-Ordner und registriert den Stack;
   **Anlegen und starten** startet ihn zusätzlich. Beide Änderungen laufen über die Freigabe.
6. Das Ergebnis unter **Aktionen** prüfen und die Ersteinrichtung der Anwendung abschließen.

Dock überschreibt keinen bestehenden Stack-Ordner. Du kannst auch ein Beispiel einfügen, eine eigene Compose-Datei
hochladen oder Text einfügen. Die Vorlagen installieren Anwendungen; sie schalten keine Server-Module frei.

## Werte für diesen Stack

Für Variablen ohne Vorgabe wie `${DB_PASSWORD}` oder `$APP_USER` zeigt **Neuer Stack** eigene Eingabefelder.
`${DB_PASSWORD:?}` und `${DB_PASSWORD?}` markieren Pflichtfelder, auch mit einem Hinweis hinter dem Fragezeichen.
Solange ein Pflichtfeld leer ist, sind **Prüfen**, **Anlegen** und **Anlegen und starten** gesperrt.
Felder ohne Pflichtmarkierung dürfen leer bleiben. Variablen mit einer Vorgabe wie `${TZ:-UTC}` brauchen kein Feld;
`$$NAME` wertet erst der Container aus.

Namen mit `PASSWORD`, `SECRET`, `TOKEN` oder `KEY` werden unabhängig von Groß- und Kleinschreibung maskiert.
Die Compose-Datei behält ihre Variablen. Die eingegebenen Werte schreibt Dock nach der Freigabe in eine `.env` nur
im neuen Stack-Ordner, mit Leserecht ausschließlich für den SSH-Zugang. Sie werden weder in eine Vorlage übernommen
noch in den Compose-Text eingesetzt. Beim Vorlagenwechsel werden die Eingaben geleert; entfernst du eine Variable
aus der Compose-Datei, wird auch ihr eingegebener Wert verworfen.

## Eigene Vorlagen

Neben dem Katalog kannst du eigene Vorlagen speichern und wiederverwenden. Die Vorlagen stehen allen zur Verfügung, die Stacks auf den Docker-Hosts anlegen dürfen.

### Speichern

Eine eigene Vorlage lässt sich an zwei Stellen anlegen:

* **Aus „Neuer Stack“:** Unter **Docker → Stacks → Neuer Stack** trägst du eine Compose-Datei ein oder lädst eine hoch. Die Schaltfläche **Als Vorlage speichern** öffnet den Dialog für Name und optionale Beschreibung.
* **Aus der Stack-Datei:** Unter **Docker → Stacks** öffnest du bei einem bestehenden Stack den Bereich **Datei**. Neben der Compose-Datei bietet **Als Vorlage speichern** an, diesen Stand direkt als Vorlage zu übernehmen.

Gespeichert wird ausschließlich die Compose-Datei (`compose.yaml`). Zugehörige `.env`-Dateien und Eingaben aus
**Werte für diesen Stack** werden nie in der Vorlage abgelegt. Benötigt ein Stack Zugangsdaten oder individuelle
Werte, trägst du Variablen wie `${DB_PASSWORD:?}` in die Compose-Datei ein. Wer die Vorlage für einen neuen Stack
auswählt, gibt die eigenen Werte in den oben beschriebenen Feldern ein.

### Ablehnung fester Passwörter

Vor dem Speichern prüft die Vorlagenverwaltung die Compose-Datei auf feste Geheimnisse, soweit sie sich an bekannten Namen und Mustern erkennen lassen. Das senkt das Risiko, dass Passwörter oder private Schlüssel in geteilten Vorlagen landen, ersetzt aber nicht den eigenen Blick vor dem Teilen:

* **Schlüssel und Variablen:** Einträge mit bekannten Geheimnis-Namen (wie `password`, `secret`, `token` oder `credentials`) dürfen keinen festen Klartextwert enthalten; andere Namen prüft Dock nicht. Variablen wie `${PASS}` oder `$PASS`, Verweise (`file://`, `env://`) und Schalterwerte (`yes`, `true`, `1`) bei Schaltern wie `MYSQL_RANDOM_ROOT_PASSWORD` sind erlaubt.
* **Adressen und Befehle:** URLs dürfen keine Passwörter enthalten (`user:pass@host`). Befehle und Parameter dürfen keine Passwörter fest übergeben (beispielsweise `mysql -pGeheim` oder `--requirepass Geheim`).
* **Erkennung von Tokens und Schlüsseln:** Private Schlüssel (`BEGIN ... PRIVATE KEY`), API-Schlüssel sowie Tokens von GitHub, GitLab, AWS, Slack, Tailscale oder JSON Web Tokens (JWT) werden im gesamten Text abgewiesen, auch in Kommentaren.

Enthält die Datei ein erkanntes Geheimnis, bricht das Speichern ab. Die Fehlermeldung nennt für bis zu fünf Fundstellen die jeweilige Zeilennummer und den Grund (etwa welcher Dienst oder Schlüssel betroffen ist), ohne das Geheimnis selbst im Klartext anzuzeigen.

### Ersetzen und Löschen

* **Ersetzen:** Wählst du unter **Neuer Stack** eine eigene Vorlage aus und bearbeitest die Compose-Datei im Textfeld, kannst du über **Als Vorlage speichern** wählen, ob du die Vorlage unter ihrem bisherigen Namen aktualisieren (**Vorlage „…“ ersetzen**) oder als neue Vorlage speichern möchtest.
* **Löschen:** Eine gewählte eigene Vorlage entfernst du unter **Neuer Stack** über die Schaltfläche **Vorlage löschen** nach Bestätigung. Stacks, die bereits aus dieser Vorlage angelegt wurden, bleiben auf den Hosts unverändert erhalten.

Die Prüfung und Speicherung steuert [own_templates.py](../../extensions/docker/src/nodvard_deck_ext_docker/own_templates.py) (maximal 100 eigene Vorlagen und höchstens 64 KB je Compose-Datei).

## Edition und Pflege

Dock und Deck nutzen denselben Vorlagenkatalog und dieselbe Verwaltung eigener Vorlagen des Docker-Moduls. Im
[Katalog](../../extensions/docker/src/nodvard_deck_ext_docker/templates.py), in
[Eigene Vorlagen](../../extensions/docker/src/nodvard_deck_ext_docker/own_templates.py) und in
[Neuer Stack](../../extensions/docker/src/nodvard_deck_ext_docker/new_stack.py) gibt es keine Editionsabfrage.
Die [Editionsunterschiede](README.md#edition-und-umfang) betreffen den Dock-Build und die Kernwerkzeuge.
Eine angelegte Vorlage bleibt eine lokale Compose-Datei und wird durch spätere Katalogänderungen nicht automatisch ersetzt.
Für eigene Änderungen und Updates gelten [Image-Updates](image-updates.md) und [Stacks aus Git](stacks-from-git.md).
