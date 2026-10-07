# Image des Update-Helfers (ghcr.io/nodvard/deck-updater). Build-Kontext ist das Repository-Root:
#
#   docker build -f deploy/updater/Dockerfile -t nodvard-deck-updater:test .
#   docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges nodvard-deck-updater:test --selftest
#
# Der Helfer bekommt den Docker-Socket und darf damit so viel wie root auf dem Rechner. Darum ist das Image bewusst
# klein: nur Python aus dem Basis-Image, dazu das Paket nodvard_deck_updater (nur Standardbibliothek, keine
# pip-Pakete) und die Lizenz. Kein Shell-Skript, kein apt-get, kein pip.

# Basis per Digest festgelegt: ein verschobenes Tag kann keinen fremden Code in ein Image mit Zugriff auf Docker
# bringen. Der Digest ist der der Manifest-LISTE (linux/amd64 und linux/arm64). Erneuern ist ein Einzeiler:
#   docker buildx imagetools inspect python:3.12-slim   (Zeile "Digest:" oben, nicht die einer einzelnen Plattform)
# Python-Version wie das Dashboard-Image (deploy/Dockerfile); der Pfad unten nennt sie mit.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

# Gelten fuer Aufrufe ohne `-I` (z. B. `docker exec ... python`). Der Helfer selbst startet mit `-I`, das diese
# Variablen nicht liest; er schreibt seine Zeilen ohnehin sofort (flush), und Bytecode liegt schon im Image (unten).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Nur diese zwei Quellen, nichts sonst aus dem Repository (keine Tests, keine Aufnahmen, kein Werkzeug).
# site-packages, weil `python -I` weder den aktuellen Ordner noch PYTHONPATH in den Suchpfad nimmt.
COPY deploy/updater/nodvard_deck_updater /usr/local/lib/python3.12/site-packages/nodvard_deck_updater
COPY LICENSE /usr/share/doc/nodvard-deck-updater/LICENSE

# Rechte unabhaengig vom Build-Rechner festlegen (root-eigen, fuer andere nur lesbar) und den Bytecode einmal hier
# erzeugen: Der Container laeuft nur lesbar (read_only), Python koennte ihn zur Laufzeit nicht ablegen und uebersetzte
# sonst bei jedem Start neu. Ein Syntaxfehler im Paket laesst schon den Build scheitern.
RUN chmod -R u=rwX,go=rX /usr/local/lib/python3.12/site-packages/nodvard_deck_updater /usr/share/doc/nodvard-deck-updater \
    && python -I -m compileall -q /usr/local/lib/python3.12/site-packages/nodvard_deck_updater

# Version kommt aus .github/workflows/release.yml (VERSION, z. B. 0.7.0); ein eigener Build laesst sie leer.
ARG VERSION=""
LABEL org.opencontainers.image.title="Nodvard Deck Update-Helfer" \
      org.opencontainers.image.description="Aktualisiert Nodvard Deck auf Wunsch aus dem Dashboard (Compose-Installationen)." \
      org.opencontainers.image.source="https://github.com/nodvard/deck" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${VERSION}"

# Bewusst KEIN `USER`: Der Helfer laeuft als root (uid 0), aber ohne Capabilities (Compose: cap_drop ALL,
# no-new-privileges). Er prueft, dass /channel und /state root gehoeren und fuer andere nicht beschreibbar sind
# (channel.py, state.py: erwartete uid 0); ein neues Volume legt Docker genau so an (root, 0755). Den Docker-Socket
# (root, Gruppe docker, 0660) oeffnet er als Besitzer, ohne die je nach Rechner andere Gruppen-ID kennen zu muessen;
# bei Rootless Docker ist root im Container der Benutzer, dem der Socket gehoert.
#
# Bewusst KEIN `VOLUME`: /channel und /state kommen nur aus der Compose-Datei. Gaebe es die Pfade im Image, bekaemen
# neue Volumes beim ersten Einhaengen deren Besitzer und Rechte mit.
# Bewusst KEIN `HEALTHCHECK`: Faellt der Helfer aus, zeigt das Dashboard am veralteten Lebenszeichen "antwortet nicht".
# Kein Port: Der Helfer hoert auf nichts, er liest nur den Kanal.
ENTRYPOINT ["python", "-I", "-m", "nodvard_deck_updater"]
