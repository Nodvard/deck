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
4. **Prüfen** validiert mit Docker Compose auf dem Host. **Anlegen** schreibt `compose.yaml` und registriert den Stack;
   **Anlegen und starten** startet ihn zusätzlich. Beide Änderungen laufen über die Freigabe.
5. Das Ergebnis unter **Aktionen** prüfen und die Ersteinrichtung der Anwendung abschließen.

Dock überschreibt keinen bestehenden Stack-Ordner. Du kannst auch ein Beispiel einfügen, eine eigene Compose-Datei
hochladen oder Text einfügen. Die Vorlagen installieren Anwendungen; sie schalten keine Server-Module frei.

## Edition und Pflege

Dock und Deck nutzen denselben Vorlagenkatalog des Docker-Moduls. Im
[Katalog](../../extensions/docker/src/nodvard_deck_ext_docker/templates.py) und in
[Neuer Stack](../../extensions/docker/src/nodvard_deck_ext_docker/new_stack.py) gibt es keine Editionsabfrage.
Die [Editionsunterschiede](README.md#edition-und-umfang) betreffen den Dock-Build und die Kernwerkzeuge.
Eine angelegte Vorlage bleibt eine lokale Compose-Datei und wird durch spätere Katalogänderungen nicht automatisch ersetzt.
Für eigene Änderungen und Updates gelten [Image-Updates](image-updates.md) und [Stacks aus Git](stacks-from-git.md).
