# syntax=docker/dockerfile:1
#
# Ein Image, ein Container, ein Volume (/app/data), siehe docs/00-DECISIONS.md D-10.
# Build-Kontext ist das Repository-Root:
#
#   docker buildx build -f deploy/Dockerfile --platform linux/amd64,linux/arm64 -t nodvard-deck:latest .
#
# Nur einige Module (z. B. für ein Einzelprodukt), durch Kommas getrennt; ohne Angabe kommen alle mit:
#
#   docker buildx build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker -t nodvard-docker:test .
#
# Multi-Arch ohne Sonderfälle im Code: `python:3.12-slim` und `node:22-slim` liefern
# beide Ziel-Architekturen (linux/arm64 für den Pi, linux/amd64 für x86-Rechner, D-10), und
# keine Backend-Abhängigkeit (siehe backend/pyproject.toml) braucht eine Kompilierung
# ohne vorgefertigtes Wheel für eine der beiden.

# ---------------------------------------------------------------------------
# Stufe 1: Frontend + Extension-Bundles bauen
# ---------------------------------------------------------------------------
# --platform=$BUILDPLATFORM: das Ergebnis ist reines JS/statische Dateien,
# architekturunabhängig -- diese Stufe auf der BUILD-Maschine nativ laufen zu lassen
# (statt sie für arm64 zu emulieren) beschleunigt einen Cross-Build für den Pi
# erheblich. Nur die Python-Laufzeitstufe unten braucht die Ziel-Architektur wirklich.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend-build
WORKDIR /src

COPY frontend/package.json frontend/package-lock.json* frontend/
RUN cd frontend && npm ci

COPY frontend/ frontend/
# Extensions VOR dem Kern-Build: Tailwind erzeugt
# nur Klassen, die es in den gescannten Quellen findet -- die Extension-Seiten stehen
# in tailwind.config.js `content`, lagen hier aber erst NACH `npm run build` vor. Ihre
# eigenen Klassen (farbige Badges, Abstände ...) fehlten deshalb im ausgelieferten CSS.
COPY extensions/ extensions/
ARG NODVARD_DECK_EXTENSIONS=""
# --chmod: Die Rechte im Build-Kontext schwanken (Windows, Kopien), der Standard-Build darf daran nicht scheitern.
COPY --chmod=755 deploy/select_extensions.sh /usr/local/bin/select_extensions.sh
RUN select_extensions.sh "$NODVARD_DECK_EXTENSIONS" extensions
# `tsc -b` prüft auch die Tests mit; frontend/src/lib/targetAddress.test.ts liest dieselben
# Beispiele wie das SDK (sdk/python/tests/vectors/same_target.json). Ohne sie scheitert der Build.
COPY sdk/python/tests/vectors/ sdk/python/tests/vectors/
# Ebenso liest frontend/src/routes/settings/updaterTexts.test.ts die Protokoll- und Status-Vektoren des Update-Helfers.
COPY deploy/updater/tests/vectors/ deploy/updater/tests/vectors/
RUN cd frontend && npm run build

# Extension-Frontend-Bundles sind laut .gitignore committete Fixtures (keine
# eigene CI) -- hier trotzdem frisch gebaut, weil das Docker-Image bewusst nicht von
# "hat jemand vor dem Commit an node build.mjs gedacht" abhängen soll. Kein Fehler,
# wenn eine Extension kein frontend/ hat (nicht jede Extension hat eine eigene Seite).
RUN for build_script in extensions/*/frontend/build.mjs; do \
      [ -f "$build_script" ] && node "$build_script"; \
    done; true

# ---------------------------------------------------------------------------
# Stufe 2: Laufzeit-Image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Kein root-Prozess für den Anwendungscode. Feste UID/GID (nicht `useradd` ohne -u/-g)
# für reproduzierbare Ownership -- relevant für den Volume-Trick unten.
RUN groupadd --gid 1000 lattice && useradd --uid 1000 --gid lattice --no-create-home --shell /usr/sbin/nologin lattice

WORKDIR /app
# Bewusst NUR die neuen Namen (die alten LATTICE_* lesen Code und Compose weiter als
# Rückfall). Alte Namen zusätzlich im Image würden den Rückweg aufs alte Image nicht
# absichern -- das bringt seine eigenen ENV-Werte mit -- und bei jedem Start die Warnung
# "alte Namen" auslösen.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NODVARD_DECK_ENV=prod \
    NODVARD_DECK_LOG_JSON=true \
    NODVARD_DECK_DATA_DIR=/app/data

# Für die documents-Extension (nodvard_deck_ext_documents/ocr.py): `pytesseract` ist nur
# der Python-Wrapper, das eigentliche OCR-Programm ist dieses System-Paket -- ohne
# `tesseract-ocr` hier scheitert jeder Upload mit TesseractNotFoundError (Extension
# bleibt trotzdem nutzbar, siehe ocr.py/__init__.py::_run_ocr()s Fehler-Isolation,
# nur eben ohne erkannten Text). `-deu` zusätzlich zu `-eng`, Homelab-Kontext ist
# deutschsprachig (siehe ocr.py::_OCR_LANGS_PRIMARY).
# Nur, wenn das Modul documents mitkommt (ohne Angabe kommen alle Module mit). Vor den
# pip-Schritten, damit eine Änderung am Backend apt nicht jedes Mal neu laufen lässt.
ARG NODVARD_DECK_EXTENSIONS=""
# Ein neuer Wert holt die Pakete frisch: Sonst bliebe diese Schicht samt ihrem Stand der Debian-Sicherheitsupdates im
# Build-Cache, bis sich das Basis-Image ändert. Der Release-Job setzt die Nummer seines Laufs.
ARG APT_REFRESH=""
RUN case ",${NODVARD_DECK_EXTENSIONS:-documents}," in \
      *,documents,*) apt-get update && apt-get install --no-install-recommends -y \
          tesseract-ocr tesseract-ocr-deu \
        && rm -rf /var/lib/apt/lists/* ;; \
    esac

# nodvard-sdk zuerst installieren (backend hängt via pyproject.toml `dependencies =
# ["nodvard-sdk", ...]` davon ab, siehe dortige Datei) -- pip auflöst das zwar auch
# in einem Schritt richtig, getrennte Layer sind aber der bessere Build-Cache: sdk/
# ändert sich seltener als backend/.
# Reproduzierbar: alle Abhängigkeiten auf die zuletzt getesteten Versionen festgelegt --
# ein neues Release eines Pakets bricht den Build nicht mehr unbemerkt (Beispiel:
# SQLAlchemy 2.1.0 kam ohne greenlet).
COPY deploy/constraints.txt constraints.txt

COPY sdk/python sdk/python
RUN pip install --no-cache-dir -c constraints.txt ./sdk/python

COPY backend backend
RUN pip install --no-cache-dir -c constraints.txt ./backend

# Extensions sind Verzeichnis-gescannt, kein separater `pip install`-Schritt
# (backend/src/nodvard_deck/ext/discovery.py::scan_directory() hängt sie per `sys.path`
# ein) -- die hier kopierte Version enthält die in Stufe 1 frisch gebauten Bundles.
COPY --from=frontend-build /src/extensions ./extensions
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

# Lizenzen der mitgelieferten Fremdkomponenten (deren Lizenzen verlangen, die Texte mitzuliefern).
# Die Datei ist eingecheckt und wird mit `python scripts/third_party_licenses.py` erzeugt;
# backend/tests/test_licenses.py schlägt an, wenn sie nicht zu constraints.txt und
# package-lock.json passt. Bewusst NICHT im Build erzeugt: dort fehlen je nach Stufe
# entweder die Python- oder die npm-Pakete, und ein Build soll nicht an Papierkram scheitern.
# Daneben die eigene Lizenz des Projekts (PolyForm Noncommercial 1.0.0), damit das Image ihren Text mitbringt.
COPY THIRD_PARTY_LICENSES /app/THIRD_PARTY_LICENSES
COPY LICENSE /app/LICENSE

# Nur der Datenordner gehört `lattice`. Code, Erweiterungen und Oberfläche bleiben root-eigen und für
# andere nur lesbar: Wer als `lattice` Code ausführt (Fehler in einer Erweiterung oder Abhängigkeit), kann
# unter /app nichts ablegen -- und so nichts einschleusen, das später als root startet (Healthcheck,
# `docker compose exec`). `/app` selbst ist ebenfalls root-eigen: sonst könnte `lattice` dort z. B. ein Paket
# `http/` anlegen, das ein als root gestartetes Python statt des echten lädt. Vorher werden die
# Erweiterungen und die Migrationen (lädt Alembic bei jedem Start) einmal übersetzt, weil `lattice` zur
# Laufzeit keine `__pycache__`-Ordner mehr anlegen darf; sonst übersetzt Python sie bei jedem Start neu.
RUN python -m compileall -q /app/extensions /app/backend/migrations || true
RUN mkdir -p /app/data \
    && chown -R root:root /app \
    && chmod -R go+rX,go-w /app \
    && chown lattice:lattice /app/data \
    && chmod 700 /app/data
# Docker initialisiert ein frisches BENANNTES Volume aus dem Inhalt (inkl. Ownership)
# des Image-Pfads, an dem es zum ersten Mal gemountet wird -- deshalb reicht dieses
# `chown` VOR `VOLUME` für den Normalfall. Ein Bind-Mount von einem Host-Verzeichnis
# bringt seine eigene Ownership mit; die setzt deploy/entrypoint.sh beim Start selbst
# (siehe dort).
VOLUME /app/data

# Ohne diesen Entrypoint startet uvicorn gegen ein unmigriertes Volume und stirbt sofort mit
# "no such table: roles" -- siehe entrypoint.sh für die volle Begründung. Root-eigen;
# world-readable/-executable reicht, der `lattice`-Nutzer braucht keinen Schreibzugriff darauf.
#
# Warum `chmod 755` statt `chmod +x`: `chmod +x`
# (relativ, ohne u/g/o) ADDIERT nur das Execute-Bit auf die vorhandenen Rechte --
# welche Rechte die kopierte Datei tatsächlich mitbringt, hängt vom Build-Kontext
# ab (z. B. per `git archive | tar -x` auf einen Pi übertragen: andere Bit-Ausgangslage
# als beim direkten Docker-Desktop-Build auf Windows). Mögliches Ergebnis: `rwxrwx--x`
# -- für den `lattice`-Prozess (weder Owner noch Gruppe) bleibt nicht einmal Lesen
# erlaubt, `/bin/sh` kann das Skript nicht öffnen. `chmod 755` (absolut) setzt
# die Rechte unabhängig vom Ausgangszustand fest, statt sie relativ draufzusatteln.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

# Bewusst KEIN `USER lattice` mehr: Der Container startet als root, damit der Entrypoint dem
# Datenordner /app/data die richtigen Besitzrechte geben kann (eingebundene NAS-/Host-Ordner
# gehören oft root oder einem anderen Benutzer). Er wechselt mit `setpriv` zu `lattice`, BEVOR
# Migration und Anwendung starten -- beide laufen nie als root (Einzelheiten in entrypoint.sh).
# Läuft der Container mit `user:` (Compose) oder unter Kubernetes schon als Nicht-root,
# bleibt alles wie früher. Bauzeit-Prüfung: Fehlt setpriv im Basis-Image, scheitert schon der
# Build und nicht erst der Start beim Nutzer.
RUN setpriv --reuid=1000 --regid=1000 --clear-groups --no-new-privs true

# Herkunft des Images: `.github/workflows/release.yml` setzt `VERSION` (z. B. 0.6.0 oder 0.6.0-rc1) und
# `IMAGE` (ghcr.io/nodvard/deck, ohne Tag). Daran erkennt "Nach Updates suchen", ob das offizielle Image
# läuft (dann passen die Anleitungen für Compose, Portainer usw.) und welche Version es genau ist. Ein
# eigener Build lässt beides leer, dann entsteht keine Datei. `EDITION=dock` macht daraus Nodvard Dock (Name,
# Dienstname, eigenes Image für die Update-Suche; nodvard_deck/edition.py), passend zu NODVARD_DECK_EXTENSIONS=docker.
# Bewusst eine DATEI (/app/image-info.json, sicher als JSON geschrieben von nodvard_deck.image_info) und
# KEINE ENV: Die ENV eines Images landet in den Einstellungen jedes Containers; wer den Container mit diesen
# Einstellungen und einem neuen Image neu anlegt, nimmt die alte Version mit. Die Datei kommt immer aus dem
# Image, das gerade läuft. Die Build-Argumente sieht nur dieser RUN-Schritt (als Umgebung), nicht der Container.
# Bewusst erst hier unten: ein neuer Wert baut nur diese eine Schicht neu, nicht die Pakete darüber.
ARG VERSION=""
ARG IMAGE=""
ARG EDITION=""
RUN python -m nodvard_deck.image_info /app/image-info.json

EXPOSE 8080

# `--start-period=300s`: vor dem Start der Anwendung läuft die Migration samt Kopie der Datenbank (`nodvard_deck.boot`);
# bei einer großen Datenbank auf einem Pi dauert das Minuten. Während der Karenzzeit zählen Fehlschläge nicht,
# der Container gilt nicht als "unhealthy". Die Notseite antwortet mit 503, der Check schlägt dann an.
# Der Check läuft als root (das Image hat bewusst kein `USER`, siehe oben). `python -I` (isoliert) nimmt den
# aktuellen Ordner nicht in den Suchpfad auf und ignoriert PYTHON*-Umgebungsvariablen und das Benutzerverzeichnis
# für Pakete: so kann nichts, was jemand neben das Programm legt, mit root-Rechten geladen werden.
HEALTHCHECK --interval=30s --timeout=3s --start-period=300s --retries=3 \
    CMD python -I -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=2)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
# `--ws-max-size`: größte WebSocket-Nachricht (uvicorn-Standard wären 16 MiB, auch von Clients ohne Konto im
# 10-Sekunden-Anmeldefenster von /ws). 1 MiB reicht für Anmeldung, Terminal-Eingaben und Ereignisse bei Weitem.
# `--no-proxy-headers`: uvicorn glaubt sonst vorab still jeder Anfrage von 127.0.0.1 ihr `X-Forwarded-For`. Wem
# geglaubt wird, entscheidet allein die Einstellung "Vertrauenswürdiger Proxy" (api/proxy_headers.py).
# Der Entrypoint liest aus dem Startbefehl nur --host und --port.
CMD ["uvicorn", "nodvard_deck.main:app", "--host", "0.0.0.0", "--port", "8080", "--ws-max-size", "1048576", "--no-proxy-headers"]
