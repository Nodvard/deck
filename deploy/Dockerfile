# syntax=docker/dockerfile:1
#
# Ein Image, ein Container, ein Volume (/app/data), siehe docs/00-DECISIONS.md D-10.
# Build-Kontext ist das Repository-Root:
#
#   docker buildx build -f deploy/Dockerfile --platform linux/amd64,linux/arm64 -t nodvard-deck:latest .
#
# Multi-Arch ohne Sonderfaelle im Code: `python:3.12-slim` und `node:22-slim` liefern
# beide Ziel-Architekturen (linux/arm64 fuer den Pi, linux/amd64 fuer x86-Rechner, D-10), und
# keine Backend-Abhaengigkeit (siehe backend/pyproject.toml) braucht eine Kompilierung
# ohne vorgefertigtes Wheel fuer eine der beiden.

# ---------------------------------------------------------------------------
# Stufe 1: Frontend + Extension-Bundles bauen
# ---------------------------------------------------------------------------
# --platform=$BUILDPLATFORM: das Ergebnis ist reines JS/statische Dateien,
# architekturunabhaengig -- diese Stufe auf der BUILD-Maschine nativ laufen zu lassen
# (statt sie fuer arm64 zu emulieren) beschleunigt einen Cross-Build fuer den Pi
# erheblich. Nur die Python-Laufzeitstufe unten braucht die Ziel-Architektur wirklich.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend-build
WORKDIR /src

COPY frontend/package.json frontend/package-lock.json* frontend/
RUN cd frontend && npm ci

COPY frontend/ frontend/
# Extensions VOR dem Kern-Build: Tailwind erzeugt
# nur Klassen, die es in den gescannten Quellen findet -- die Extension-Seiten stehen
# in tailwind.config.js `content`, lagen hier aber erst NACH `npm run build` vor. Ihre
# eigenen Klassen (farbige Badges, Abstaende ...) fehlten deshalb im ausgelieferten CSS.
COPY extensions/ extensions/
# `tsc -b` prueft auch die Tests mit; frontend/src/lib/targetAddress.test.ts liest dieselben
# Beispiele wie das SDK (sdk/python/tests/vectors/same_target.json). Ohne sie scheitert der Build.
COPY sdk/python/tests/vectors/ sdk/python/tests/vectors/
RUN cd frontend && npm run build

# Extension-Frontend-Bundles sind laut .gitignore committete Fixtures (keine
# eigene CI) -- hier trotzdem frisch gebaut, weil das Docker-Image bewusst nicht von
# "hat jemand vor dem Commit an node build.mjs gedacht" abhaengen soll. Kein Fehler,
# wenn eine Extension kein frontend/ hat (nicht jede Extension hat eine eigene Seite).
RUN for build_script in extensions/*/frontend/build.mjs; do \
      [ -f "$build_script" ] && node "$build_script"; \
    done; true

# ---------------------------------------------------------------------------
# Stufe 2: Laufzeit-Image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Kein root-Prozess fuer den Anwendungscode. Feste UID/GID (nicht `useradd` ohne -u/-g)
# fuer reproduzierbare Ownership -- relevant fuer den Volume-Trick unten.
RUN groupadd --gid 1000 lattice && useradd --uid 1000 --gid lattice --no-create-home --shell /usr/sbin/nologin lattice

WORKDIR /app
# Bewusst NUR die neuen Namen (die alten LATTICE_* lesen Code und Compose weiter als
# Rueckfall). Alte Namen zusaetzlich im Image wuerden den Rueckweg aufs alte Image nicht
# absichern -- das bringt seine eigenen ENV-Werte mit -- und bei jedem Start die Warnung
# "alte Namen" ausloesen.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NODVARD_DECK_ENV=prod \
    NODVARD_DECK_LOG_JSON=true \
    NODVARD_DECK_DATA_DIR=/app/data

# Fuer die documents-Extension (nodvard_deck_ext_documents/ocr.py): `pytesseract` ist nur
# der Python-Wrapper, das eigentliche OCR-Programm ist dieses System-Paket -- ohne
# `tesseract-ocr` hier scheitert jeder Upload mit TesseractNotFoundError (Extension
# bleibt trotzdem nutzbar, siehe ocr.py/__init__.py::_run_ocr()s Fehler-Isolation,
# nur eben ohne erkannten Text). `-deu` zusaetzlich zu `-eng`, Homelab-Kontext ist
# deutschsprachig (siehe ocr.py::_OCR_LANGS_PRIMARY).
RUN apt-get update && apt-get install --no-install-recommends -y \
      tesseract-ocr tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

# nodvard-sdk zuerst installieren (backend haengt via pyproject.toml `dependencies =
# ["nodvard-sdk", ...]` davon ab, siehe dortige Datei) -- pip aufloest das zwar auch
# in einem Schritt richtig, getrennte Layer sind aber der bessere Build-Cache: sdk/
# aendert sich seltener als backend/.
# Reproduzierbar: alle Abhaengigkeiten auf die zuletzt getesteten Versionen festgelegt --
# ein neues Release eines Pakets bricht den Build nicht mehr unbemerkt (Beispiel:
# SQLAlchemy 2.1.0 kam ohne greenlet).
COPY deploy/constraints.txt constraints.txt

COPY sdk/python sdk/python
RUN pip install --no-cache-dir -c constraints.txt ./sdk/python

COPY backend backend
RUN pip install --no-cache-dir -c constraints.txt ./backend

# Extensions sind Verzeichnis-gescannt, kein separater `pip install`-Schritt
# (backend/src/nodvard_deck/ext/discovery.py::scan_directory() haengt sie per `sys.path`
# ein) -- die hier kopierte Version enthaelt die in Stufe 1 frisch gebauten Bundles.
COPY --from=frontend-build /src/extensions ./extensions
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

# Lizenzen der mitgelieferten Fremdkomponenten (deren Lizenzen verlangen, die Texte mitzuliefern).
# Die Datei ist eingecheckt und wird mit `python scripts/third_party_licenses.py` erzeugt;
# backend/tests/test_licenses.py schlaegt an, wenn sie nicht zu constraints.txt und
# package-lock.json passt. Bewusst NICHT im Build erzeugt: dort fehlen je nach Stufe
# entweder die Python- oder die npm-Pakete, und ein Build soll nicht an Papierkram scheitern.
# Daneben die eigene Lizenz des Projekts (PolyForm Noncommercial 1.0.0), damit das Image ihren Text mitbringt.
COPY THIRD_PARTY_LICENSES /app/THIRD_PARTY_LICENSES
COPY LICENSE /app/LICENSE

# Nur der Datenordner gehoert `lattice`. Code, Erweiterungen und Oberflaeche bleiben root-eigen und fuer
# andere nur lesbar: Wer als `lattice` Code ausfuehrt (Fehler in einer Erweiterung oder Abhaengigkeit), kann
# unter /app nichts ablegen -- und so nichts einschleusen, das spaeter als root startet (Healthcheck,
# `docker compose exec`). `/app` selbst ist ebenfalls root-eigen: sonst koennte `lattice` dort z. B. ein Paket
# `http/` anlegen, das ein als root gestartetes Python statt des echten laedt. Vorher werden die
# Erweiterungen und die Migrationen (laedt Alembic bei jedem Start) einmal uebersetzt, weil `lattice` zur
# Laufzeit keine `__pycache__`-Ordner mehr anlegen darf; sonst uebersetzt Python sie bei jedem Start neu.
RUN python -m compileall -q /app/extensions /app/backend/migrations || true
RUN mkdir -p /app/data \
    && chown -R root:root /app \
    && chmod -R go+rX,go-w /app \
    && chown lattice:lattice /app/data \
    && chmod 700 /app/data
# Docker initialisiert ein frisches BENANNTES Volume aus dem Inhalt (inkl. Ownership)
# des Image-Pfads, an dem es zum ersten Mal gemountet wird -- deshalb reicht dieses
# `chown` VOR `VOLUME` fuer den Normalfall. Ein Bind-Mount von einem Host-Verzeichnis
# bringt seine eigene Ownership mit; die setzt deploy/entrypoint.sh beim Start selbst
# (siehe dort).
VOLUME /app/data

# Ohne diesen Entrypoint startet uvicorn gegen ein unmigriertes Volume und stirbt sofort mit
# "no such table: roles" -- siehe entrypoint.sh fuer die volle Begruendung. Root-eigen;
# world-readable/-executable reicht, der `lattice`-Nutzer braucht keinen Schreibzugriff darauf.
#
# Warum `chmod 755` statt `chmod +x`: `chmod +x`
# (relativ, ohne u/g/o) ADDIERT nur das Execute-Bit auf die vorhandenen Rechte --
# welche Rechte die kopierte Datei tatsaechlich mitbringt, haengt vom Build-Kontext
# ab (z. B. per `git archive | tar -x` auf einen Pi uebertragen: andere Bit-Ausgangslage
# als beim direkten Docker-Desktop-Build auf Windows). Moegliches Ergebnis: `rwxrwx--x`
# -- fuer den `lattice`-Prozess (weder Owner noch Gruppe) bleibt nicht einmal Lesen
# erlaubt, `/bin/sh` kann das Skript nicht oeffnen. `chmod 755` (absolut) setzt
# die Rechte unabhaengig vom Ausgangszustand fest, statt sie relativ draufzusatteln.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

# Bewusst KEIN `USER lattice` mehr: Der Container startet als root, damit der Entrypoint dem
# Datenordner /app/data die richtigen Besitzrechte geben kann (eingebundene NAS-/Host-Ordner
# gehoeren oft root oder einem anderen Benutzer). Er wechselt mit `setpriv` zu `lattice`, BEVOR
# Migration und Anwendung starten -- beide laufen nie als root (Einzelheiten in entrypoint.sh).
# Laeuft der Container mit `user:` (Compose) oder unter Kubernetes schon als Nicht-root,
# bleibt alles wie frueher. Bauzeit-Pruefung: Fehlt setpriv im Basis-Image, scheitert schon der
# Build und nicht erst der Start beim Nutzer.
RUN setpriv --reuid=1000 --regid=1000 --clear-groups --no-new-privs true

# Herkunft des Images: `.github/workflows/release.yml` setzt `VERSION` (z. B. 0.6.0 oder 0.6.0-rc1) und
# `IMAGE` (ghcr.io/nodvard/deck, ohne Tag). Daran erkennt "Nach Updates suchen", ob das offizielle Image
# laeuft (dann passen die Anleitungen fuer Compose, Portainer usw.) und welche Version es genau ist. Ein
# eigener Build laesst beides leer, dann entsteht keine Datei.
# Bewusst eine DATEI (/app/image-info.json, sicher als JSON geschrieben von nodvard_deck.image_info) und
# KEINE ENV: Die ENV eines Images landet in den Einstellungen jedes Containers; wer den Container mit diesen
# Einstellungen und einem neuen Image neu anlegt, nimmt die alte Version mit. Die Datei kommt immer aus dem
# Image, das gerade laeuft. Die Build-Argumente sieht nur dieser RUN-Schritt (als Umgebung), nicht der Container.
# Bewusst erst hier unten: ein neuer Wert baut nur diese eine Schicht neu, nicht die Pakete darueber.
ARG VERSION=""
ARG IMAGE=""
RUN python -m nodvard_deck.image_info /app/image-info.json

EXPOSE 8080

# `--start-period=300s`: vor dem Start der Anwendung laeuft die Migration samt Kopie der Datenbank (`nodvard_deck.boot`);
# bei einer grossen Datenbank auf einem Pi dauert das Minuten. Waehrend der Karenzzeit zaehlen Fehlschlaege nicht,
# der Container gilt nicht als "unhealthy". Die Notseite antwortet mit 503, der Check schlaegt dann an.
# Der Check laeuft als root (das Image hat bewusst kein `USER`, siehe oben). `python -I` (isoliert) nimmt den
# aktuellen Ordner nicht in den Suchpfad auf und ignoriert PYTHON*-Umgebungsvariablen und das Benutzerverzeichnis
# fuer Pakete: so kann nichts, was jemand neben das Programm legt, mit root-Rechten geladen werden.
HEALTHCHECK --interval=30s --timeout=3s --start-period=300s --retries=3 \
    CMD python -I -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=2)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
# `--ws-max-size`: groesste WebSocket-Nachricht (uvicorn-Standard waeren 16 MiB, auch von Clients ohne Konto im
# 10-Sekunden-Anmeldefenster von /ws). 1 MiB reicht fuer Anmeldung, Terminal-Eingaben und Ereignisse bei Weitem.
# Der Entrypoint liest aus dem Startbefehl nur --host und --port.
CMD ["uvicorn", "nodvard_deck.main:app", "--host", "0.0.0.0", "--port", "8080", "--ws-max-size", "1048576"]
