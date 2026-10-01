#!/usr/bin/env bash
# Spielt eine mit backup.sh erzeugte tar.gz zurueck -- ERSETZT den kompletten
# aktuellen Inhalt von /app/data.
#
# `--entrypoint sh` statt des Image-Entrypoints: der wuerde vor dem Einspielen
# `python -m nodvard_deck.migrate` gegen genau die (moeglicherweise kaputte) Datenbank
# fahren, die hier ersetzt werden soll, und mit `set -e` den Dienst gestoppt
# zuruecklassen. Der `trap` startet den Dienst in jedem Fall
# wieder; die Migration laeuft dann beim normalen Start ueber /entrypoint.sh.
#
# `--user 1000:1000`: Der Hilfscontainer umgeht den Entrypoint (der sonst von root zu `lattice` wechselt)
# und laeuft deshalb ausdruecklich als `lattice`; eingespielte Dateien gehoeren so nicht root.
#
# Nutzung (aus deploy/):  ./restore.sh /pfad/zu/nodvard-deck-backup-20260101-120000.tar.gz   (auch lattice-backup-*.tar.gz)
#
# Eine verschluesselte Sicherung aus der Oberflaeche (`*.ndbak`) wird NICHT sofort eingespielt: das Skript
# laesst sie von `python -m nodvard_deck.admin restore-backup` pruefen und VORMERKEN (Passwort bzw. Wiederherstellungs-
# schluessel wird abgefragt, es erscheint eine Zusammenfassung) und startet den Dienst auf Wunsch neu. Eingespielt wird
# dann beim Start (`nodvard_deck.boot`), mit denselben Pruefungen und demselben Rueckweg wie in der Oberflaeche:
#   ./restore.sh /pfad/zu/nodvard-deck-sicherung-20261001-023000.ndbak
set -euo pipefail
cd "$(dirname "$0")"

BACKUP_FILE="${1:?Nutzung: restore.sh <backup-datei.tar.gz>}"
[ -f "$BACKUP_FILE" ] || { echo "Nicht gefunden: ${BACKUP_FILE}" >&2; exit 1; }
BACKUP_DIR="$(cd "$(dirname "$BACKUP_FILE")" && pwd)"
BACKUP_NAME="$(basename "$BACKUP_FILE")"

case "$BACKUP_NAME" in
  *.ndbak)
    echo "Verschluesselte Sicherung: sie wird geprueft und zum Einspielen vorgemerkt (Passwort wird gleich abgefragt)."
    docker compose -p deploy run --rm --no-deps --user 1000:1000 \
      --entrypoint python -v "${BACKUP_DIR}:/backup:ro" nodvard-deck \
      -m nodvard_deck.admin restore-backup "/backup/${BACKUP_NAME}"
    read -r -p "Nodvard Deck jetzt neu starten, damit die Sicherung eingespielt wird? [y/N] " restart
    case "$restart" in
      y|Y) docker compose -p deploy restart nodvard-deck; echo "Neu gestartet. Der Fortschritt steht im Protokoll: docker compose -p deploy logs -f nodvard-deck" ;;
      *) echo "Vorgemerkt (gilt eine Stunde). Zum Einspielen den Dienst neu starten: docker compose -p deploy restart nodvard-deck" ;;
    esac
    exit 0
    ;;
esac

echo "WARNUNG: das ersetzt den kompletten aktuellen Datenbestand (/app/data,"
echo "inkl. Master-Key und Datenbank) im lattice_data-Volume unwiderruflich."
read -r -p "Fortfahren? [y/N] " confirm
case "$confirm" in
  y|Y) ;;
  *) echo "Abgebrochen."; exit 1 ;;
esac

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

echo "Stoppe den nodvard-deck-Dienst ..."
docker compose -p deploy stop nodvard-deck

echo "Leere das Volume und spiele ${BACKUP_NAME} ein ..."
docker compose -p deploy run --rm --no-deps --user 1000:1000   --entrypoint sh   -v "${BACKUP_DIR}:/backup:ro"   nodvard-deck   -c "find /app/data -mindepth 1 -delete && tar xzf /backup/${BACKUP_NAME} -C /app/data"

echo "Fertig."
