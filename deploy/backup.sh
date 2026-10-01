#!/usr/bin/env bash
# Sichert das komplette /app/data-Volume (SQLite, Master-Key, jwt_secret.key,
# Extension-Daten, Script-Git-Repo) in eine einzelne tar.gz-Datei.
# Siehe docs/00-DECISIONS.md D-10.
#
# Stoppt den Dienst kurz fuer eine garantiert konsistente Kopie -- fuer die in D-02
# beschriebene Last (eine Handvoll Nutzer, ein paar Dutzend Hosts) ist das einfacher
# und sicherer als ein Live-Kopiertrick gegen eine offene SQLite-WAL-Datei. Ein Weg
# ohne Ausfallzeit steht in deploy/README.md ("Backup ohne Stopp").
#
# Einige Dinge sind hier bewusst so:
# - `--entrypoint tar`: ohne das laeuft der Hilfscontainer durch /entrypoint.sh und
#   damit durch `python -m nodvard_deck.migrate` -- ein "Vorher-Backup" nach einem
#   `docker compose build` waere dann schon auf das NEUE Schema migriert und taugt
#   nicht mehr fuer ein Zurueck auf die alte Version.
# - `--user 1000:1000`: Das Image startet als root (der Entrypoint gibt die Rechte selbst ab). Dieser
#   Hilfscontainer umgeht den Entrypoint und laeuft deshalb ausdruecklich als `lattice`, damit die
#   Sicherungsdatei nicht root gehoert.
# - `trap ... EXIT`: scheitert das tar (volle SD-Karte, fehlende Rechte), bleibt der
#   Dienst trotzdem nicht gestoppt zurueck.
#
# Dienstname in Compose: `nodvard-deck` (frueher `lattice`); das Volume heisst weiter
# `deploy_lattice_data`. Aeltere Sicherungen (`lattice-backup-*.tar.gz`) sind einfache
# tar.gz des Datenordners und lassen sich mit restore.sh unveraendert einspielen.
#
# Nutzung (aus deploy/):  ./backup.sh [zielverzeichnis]
set -euo pipefail
cd "$(dirname "$0")"

OUT_DIR="${1:-.}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="nodvard-deck-backup-${TIMESTAMP}.tar.gz"

# Lief vor dem Umschalten (oder nach einem Rollback) noch der alte Container `deploy-lattice-1`
# (Dienst `lattice` vor der Umbenennung), hat er dasselbe Volume offen: ihn ebenfalls stoppen und
# am Ende wieder starten, sonst wuerde er mitten im Betrieb gesichert bzw. geleert.
OLD_CONTAINER="deploy-lattice-1"
OLD_WAS_RUNNING=0
if [ "$(docker container inspect -f '{{.State.Status}}' "$OLD_CONTAINER" 2>/dev/null || true)" = "running" ]; then
  OLD_WAS_RUNNING=1
  echo "Stoppe den alten Container $OLD_CONTAINER (hat dasselbe Volume offen) ..."
  docker stop -t 30 "$OLD_CONTAINER" >/dev/null
fi
restart_services() {
  if [ "$OLD_WAS_RUNNING" = 1 ]; then
    echo "Starte den alten Container $OLD_CONTAINER wieder ..."
    docker start "$OLD_CONTAINER" >/dev/null || echo "WARNUNG: $OLD_CONTAINER liess sich nicht starten." >&2
  else
    echo "Starte den nodvard-deck-Dienst wieder ..."
    docker compose -p deploy start nodvard-deck
  fi
}
trap restart_services EXIT

echo "Stoppe den nodvard-deck-Dienst (fuer eine konsistente Kopie) ..."
docker compose -p deploy stop nodvard-deck

echo "Sichere /app/data nach ${OUT_DIR}/${OUT_FILE} ..."
docker compose -p deploy run --rm --no-deps --user 1000:1000   --entrypoint tar   -v "${OUT_DIR}:/backup"   nodvard-deck   czf "/backup/${OUT_FILE}" -C /app/data .

echo "Fertig: ${OUT_DIR}/${OUT_FILE}"
