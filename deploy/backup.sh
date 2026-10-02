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
#   Hilfscontainer umgeht den Entrypoint und laeuft deshalb ausdruecklich als `lattice`.
# - tar schreibt nach STDOUT (`czf -`), die Datei legt DIESE Shell an (`> Datei`). Frueher wurde der
#   Zielordner in den Container eingebunden und der Container (als 1000) schrieb hinein: wer das Skript
#   wie dokumentiert mit `sudo` startete, hatte einen Zielordner, der root gehoert -- der Container
#   durfte dort nicht schreiben. So schreibt immer der Aufrufer selbst, mit seinen Rechten.
#   Dafuer ist `-T` (kein Pseudo-Terminal) Pflicht: ein TTY wuerde die Binaerdaten verfaelschen.
#   Auf stdout darf nichts anderes als die tar-Daten stehen; Compose meldet auf stderr, und die
#   fertige Datei wird trotzdem noch einmal als tar.gz geprueft.
# - Die Datei entsteht zuerst unter einem Zwischennamen `<name>.partial.<Zufall>` (angelegt mit `mktemp`:
#   exklusiv, immer mit Rechten 0600, der Name ist nicht vorhersehbar -- sie enthaelt den Master-Key) und
#   wird erst nach allen Pruefungen (Exit-Code, Mindestgroesse, tar-Test) umbenannt (`mv -fT`: ein dort
#   vorab abgelegter Link wird ersetzt, nie verfolgt). Bei jedem Fehler wird die halbe Datei geloescht;
#   scheitert schon das Loeschen, bleibt der Dienst trotzdem nicht gestoppt (nur ein Hinweis). Ob in den
#   Zielordner geschrieben werden kann, zeigt sich schon VOR dem Stoppen des Dienstes.
# - `trap ... EXIT`: scheitert das tar (volle SD-Karte, fehlende Rechte), bleibt der
#   Dienst trotzdem nicht gestoppt zurueck.
# - Ist ein Einspielen mit restore.sh mitten im Austausch stehen geblieben (Marke `/app/data/.restore-austausch`), ist
#   der Datenbestand unvollstaendig und der Dienst absichtlich gestoppt: dann bricht das Skript VOR jeder Aenderung ab,
#   sichert nichts und startet auch nichts. Die Arbeitsordner von restore.sh (`.restore-neu`, `.restore-alt*`) und die
#   Marke kommen nie mit in die Sicherung.
#
# - Sperre: backup.sh und restore.sh halten dieselbe Sperre (`flock` auf `.backup-restore.lock` neben dem Skript) bis zum
#   Ende; laeuft schon eine Sicherung oder ein Einspielen (z. B. Cron-Sicherung waehrend eines Einspielens), wird dieser Lauf
#   abgewiesen, ohne etwas zu veraendern. Deskriptor 9 bleibt Docker verschlossen (`9>&-`): ein haengender Hilfsprozess haelt
#   die Sperre sonst ueber das Skript hinaus. Fehlt `flock` (Paket util-linux), gibt es eine Warnung, und es laeuft ohne Sperre.
#
# Exit-Codes (3 bis 5: es wurde nichts gesichert, nichts angehalten und nichts gestartet): 0 fertig, 1 Fehler (je nach Meldung),
# 3 ein unterbrochenes Einspielen liegt noch (Marke; erst mit restore.sh abschliessen), 4 es laeuft schon eine Sicherung oder
# ein Einspielen (Sperre), 5 die Marke ist leer oder beschaedigt (die Meldung nennt den Weg von Hand).
#
# Mit `sudo` gestartet gehoert die fertige Datei (und ein dabei neu angelegter Zielordner) dem Benutzer,
# der sudo aufgerufen hat (SUDO_UID), nicht root: so kommt er ohne weiteres sudo an die Sicherung. Als root
# ohne sudo (z. B. aus der root-Crontab) bekommt sie der Besitzer des Zielordners, wenn der nicht root ist:
# er kann dort ohnehin Dateien loeschen und ersetzen, und kann sie z. B. per scp mit seinem Konto abholen.
#
# Dienstname in Compose: `nodvard-deck` (frueher `lattice`); das Volume heisst weiter
# `deploy_lattice_data`. Aeltere Sicherungen (`lattice-backup-*.tar.gz`) sind einfache
# tar.gz des Datenordners und lassen sich mit restore.sh unveraendert einspielen.
#
# Nutzung (aus deploy/):  ./backup.sh [zielverzeichnis]      (auch:  sudo ./backup.sh ~/nodvard-deck-backups)
set -euo pipefail
cd "$(dirname "$0")"

# Gemeinsame Sperre mit restore.sh (siehe Kopfkommentar). Offen bleibt Deskriptor 9 bis zum Ende des Skripts.
LOCK_FILE="$PWD/.backup-restore.lock"
acquire_lock() {
  local rc=0
  if ! command -v flock >/dev/null 2>&1; then
    echo "WARNUNG: 'flock' fehlt (Paket util-linux). Es laeuft ohne Sperre weiter: Achte selbst darauf, dass nicht gleichzeitig eine Sicherung oder ein Einspielen laeuft." >&2
    return 0
  fi
  # Nie einem Link folgen: als root wuerde sonst eine Datei an dessen Ziel angelegt und auf 0644 gesetzt (oder an einer
  # Pipe gewartet). Angelegt wird exklusiv (noclobber), ein vorhandener Name wird nie ueberschrieben.
  if [ -L "$LOCK_FILE" ] || { [ -e "$LOCK_FILE" ] && [ ! -f "$LOCK_FILE" ]; }; then
    echo "FEHLER: ${LOCK_FILE} ist keine gewoehnliche Datei (ein Link oder Aehnliches) und wird nicht benutzt. Bitte von Hand entfernen: rm ${LOCK_FILE} -- danach legt das Skript sie selbst neu an. Es wurde nichts veraendert." >&2
    exit 1
  fi
  if [ ! -e "$LOCK_FILE" ] && ( set -C; : > "$LOCK_FILE" ) 2>/dev/null; then
    chmod 0644 "$LOCK_FILE" 2>/dev/null || true
    # Nach `sudo` gehoert die Datei dem Aufrufer (SUDO_UID), nicht root.
    if [ "$(id -u)" = 0 ] && [ -n "${SUDO_UID:-}" ] && [ "$SUDO_UID" != 0 ]; then
      chown -h "${SUDO_UID}:${SUDO_GID:-$SUDO_UID}" "$LOCK_FILE" 2>/dev/null || true
    fi
  fi
  # Nur lesend oeffnen: flock geht auch so, und es klappt, wenn die Datei einem anderen Benutzer (z. B. root) gehoert.
  if ! { exec 9< "$LOCK_FILE"; } 2>/dev/null; then
    echo "WARNUNG: Die Sperrdatei ${LOCK_FILE} laesst sich nicht oeffnen. Es laeuft ohne Sperre weiter: Achte selbst darauf, dass nicht gleichzeitig eine Sicherung oder ein Einspielen laeuft." >&2
    return 0
  fi
  flock -n -E 200 9 || rc=$?
  case "$rc" in
    0) ;;
    200)
      echo "Es laeuft schon eine Sicherung oder ein Einspielen (siehe restore.log). Bitte warten, bis es fertig ist. Es wurde nichts gesichert und nichts veraendert." >&2
      exit 4
      ;;
    *) echo "WARNUNG: Die Sperre liess sich nicht anfordern (flock meldete einen Fehler). Es laeuft ohne Sperre weiter." >&2 ;;
  esac
}
acquire_lock

# Docker bekommt die Sperre (Deskriptor 9) nicht mit.
dk() {
  docker "$@" 9>&-
}

OUT_DIR="${1:-.}"
DIR_CREATED=0
[ -d "$OUT_DIR" ] || DIR_CREATED=1
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="nodvard-deck-backup-${TIMESTAMP}.tar.gz"
TMP_FILE=""
# Eine echte Sicherung (Datenbank, Schluessel) ist deutlich groesser; ein leerer Ordner ergibt nur ~100 Bytes.
MIN_BYTES=1024

# fail [<exit-code>] <meldung>: ohne Code Exit 1.
fail() {
  local code=1
  case "${1:-}" in [0-9]) code="$1"; shift ;; esac
  echo "FEHLER: $*" >&2
  exit "$code"
}

# uid:gid, dem die fertige Datei (bzw. ein neu angelegter Zielordner) gehoeren soll; leer = nichts aendern.
# Nur als root gibt es etwas zu tun, sonst gehoert alles ohnehin dem Aufrufer.
# - per sudo gestartet: der sudo-Aufrufer (SUDO_UID), sonst gehoert die Sicherung root und er kommt nicht heran;
# - als root ohne sudo (etwa aus der root-Crontab): der Besitzer des Zielordners, wenn der nicht root ist.
target_owner() {
  local owner
  [ "$(id -u)" = 0 ] || return 0
  if [ -n "${SUDO_UID:-}" ] && [ "$SUDO_UID" != 0 ]; then
    printf '%s:%s\n' "$SUDO_UID" "${SUDO_GID:-$SUDO_UID}"
    return 0
  fi
  owner="$(stat -c '%u:%g' -- "$OUT_DIR" 2>/dev/null)" || return 0
  case "$owner" in
    0:*) ;;
    *) printf '%s\n' "$owner" ;;
  esac
}

# give_to <uid:gid> <pfad>... : `-h`, damit ein untergeschobener Link selbst und nicht sein Ziel umgehaengt wird.
give_to() {
  local owner="$1"
  shift
  [ -n "$owner" ] || return 0
  chown -h "$owner" "$@" 2>/dev/null \
    || echo "Hinweis: Besitzer von $* liess sich nicht auf ${owner} setzen (gehoert root)." >&2
  return 0
}

# Darf nie scheitern: laeuft im EXIT-Trap, und ein Fehler dort wuerde den Neustart des Dienstes verhindern
# (z. B. bei einem nur noch lesbaren oder veralteten Zielordner).
cleanup_partial() {
  [ -n "$TMP_FILE" ] || return 0
  rm -f -- "$TMP_FILE" 2>/dev/null \
    || echo "Hinweis: ${TMP_FILE} liess sich nicht loeschen (unvollstaendige Sicherung, bitte von Hand entfernen)." >&2
  return 0
}
trap cleanup_partial EXIT

# Fehlt das Image, wuerde `compose run` es erst bauen (auf dem Pi sehr lange, bei gestopptem Dienst) und das
# Bauprotokoll mitten in die Sicherungsdatei schreiben. Der Name steht so in docker-compose.yml.
# Antwortet Docker gar nicht (kein Zugriff, Dienst gestoppt), ist das etwas anderes als ein fehlendes Image: dann steht
# Dockers eigene Meldung in der Fehlermeldung.
if ! dk image inspect nodvard-deck:latest >/dev/null 2>&1; then
  if ! docker_err="$(dk info 2>&1 >/dev/null)"; then
    docker_err="$(printf '%s' "$docker_err" | tr '\n' ' ')"
    # Als root (etwa schon mit sudo gestartet) fehlt kein Recht: dann laeuft der Docker-Dienst nicht.
    if [ "$(id -u)" = 0 ]; then
      fail "Docker antwortet nicht (${docker_err:-keine Meldung}). Laeuft der Docker-Dienst? Es wurde nichts angehalten."
    fi
    fail "Docker antwortet nicht (${docker_err:-keine Meldung}). Laeuft Docker, und darfst du es benutzen? Starte das Skript mit sudo oder nimm deinen Benutzer in die Gruppe docker auf. Es wurde nichts angehalten."
  fi
  fail "Das Image nodvard-deck:latest gibt es hier nicht (noch nicht gebaut oder ausgeliefert). Erst starten bzw. ausliefern, dann sichern. Es wurde nichts angehalten."
fi

# Vorab pruefen, ob hier ueberhaupt geschrieben werden kann -- VOR dem Stoppen des Dienstes.
# Die leere Zwischendatei entsteht sofort, exklusiv und mit 0600.
if ! TMP_FILE="$(mktemp "${OUT_DIR}/${OUT_FILE}.partial.XXXXXX" 2>/dev/null)"; then
  TMP_FILE=""
  fail "In ${OUT_DIR} laesst sich nicht schreiben (Rechte, volles oder schreibgeschuetztes Laufwerk?). Es wurde nichts angehalten."
fi

# Steht ein Einspielen mitten im Austausch (siehe restore.sh)? Vor jeder Aenderung pruefen: Dann den Dienst weder
# anhalten noch (am Ende) starten -- er ist absichtlich gestoppt, der Datenbestand ist unvollstaendig.
check_rc=0
# shellcheck disable=SC2016 # laeuft im Container
CHECK_SCRIPT='M=/app/data/.restore-austausch
if [ -e "$M" ] || [ -L "$M" ]; then
  p=$(cat "$M" 2>/dev/null) || exit 5
  case "$p" in alt|neu) exit 3 ;; *) exit 5 ;; esac
fi'
dk compose -p deploy run -T --rm --no-deps --user 1000:1000 --entrypoint sh \
  nodvard-deck -c "$CHECK_SCRIPT" < /dev/null || check_rc=$?
case "$check_rc" in
  0) ;;
  3) fail 3 "Ein Einspielen mit restore.sh ist mitten im Austausch der Daten stehen geblieben (Marke /app/data/.restore-austausch). Der Datenbestand ist unvollstaendig, eine Sicherung jetzt waere unbrauchbar. Bitte zuerst das Einspielen fertig machen: ./restore.sh <dieselbe Sicherungsdatei> -- es macht den Austausch fertig und startet den Dienst danach selbst. Es wurde nichts gesichert, nichts angehalten und nichts gestartet." ;;
  5) fail 5 "Die Marke /app/data/.restore-austausch eines unterbrochenen Einspielens ist leer, unlesbar oder hat einen unbekannten Inhalt. Sie haelt fest, wie weit der Austausch war; ohne sie weiss niemand, welche Daten sicher sind, und eine Sicherung jetzt waere vermutlich unbrauchbar. Es wurde nichts gesichert, nichts angehalten und nichts gestartet, und es wird auch nichts automatisch geloescht. Von Hand: Sieh im Datenvolume nach (deploy_lattice_data, im Container /app/data): docker compose -p deploy run --rm --no-deps --entrypoint ls nodvard-deck -la /app/data -- .restore-alt enthaelt den alten Stand (ganz oder zum Teil), .restore-neu die frisch entpackten neuen Daten, was sonst dort liegt, ist ein Gemisch aus beiden. Hast du entschieden, was gelten soll, entfernst du die Marke: docker compose -p deploy run --rm --no-deps --entrypoint rm nodvard-deck -f /app/data/.restore-austausch -- danach geht die Sicherung wieder (und ./restore.sh <Datei>)." ;;
  *) fail "Der Datenordner liess sich nicht pruefen (Docker meldete einen Fehler, siehe oben). Es wurde nichts angehalten." ;;
esac

# Lief vor dem Umschalten (oder nach einem Rollback) noch der alte Container `deploy-lattice-1`
# (Dienst `lattice` vor der Umbenennung), hat er dasselbe Volume offen: ihn ebenfalls stoppen und
# am Ende wieder starten, sonst wuerde er mitten im Betrieb gesichert bzw. geleert.
OLD_CONTAINER="deploy-lattice-1"
OLD_WAS_RUNNING=0
if [ "$(dk container inspect -f '{{.State.Status}}' "$OLD_CONTAINER" 2>/dev/null || true)" = "running" ]; then
  OLD_WAS_RUNNING=1
  echo "Stoppe den alten Container $OLD_CONTAINER (hat dasselbe Volume offen) ..."
  dk stop -t 30 "$OLD_CONTAINER" >/dev/null
fi
restart_services() {
  if [ "$OLD_WAS_RUNNING" = 1 ]; then
    echo "Starte den alten Container $OLD_CONTAINER wieder ..." || true
    dk start "$OLD_CONTAINER" >/dev/null || echo "WARNUNG: $OLD_CONTAINER liess sich nicht starten." >&2
  else
    # Die Meldung darf den Start nicht verhindern (z. B. wenn das Terminal schon weg ist).
    echo "Starte den nodvard-deck-Dienst wieder ..." || true
    dk compose -p deploy start nodvard-deck
  fi
}
# Erst die halbe Datei loeschen, dann den Dienst wieder starten.
trap 'cleanup_partial; restart_services' EXIT

echo "Stoppe den nodvard-deck-Dienst (fuer eine konsistente Kopie) ..."
dk compose -p deploy stop nodvard-deck

echo "Sichere /app/data nach ${OUT_DIR}/${OUT_FILE} ..."
# stdin aus /dev/null: tar liest nichts, und ein offenes stdin soll Compose nicht festhalten.
if ! dk compose -p deploy run -T --rm --no-deps --user 1000:1000 --entrypoint tar \
    nodvard-deck czf - --exclude=./.restore-neu '--exclude=./.restore-alt*' '--exclude=./.restore-austausch*' \
    -C /app/data . > "$TMP_FILE" < /dev/null; then
  fail "Das Sichern ist fehlgeschlagen (Docker oder tar meldeten einen Fehler, siehe oben). Die unvollstaendige Datei wurde geloescht."
fi

size=$(( $(wc -c < "$TMP_FILE") ))
if [ "$size" -lt "$MIN_BYTES" ]; then
  fail "Die Sicherung ist mit ${size} Bytes zu klein, vermutlich ist das Datenvolume leer oder tar hat nichts geliefert. Die Datei wurde geloescht."
fi
# Ist es wirklich ein tar.gz? (faengt auch Fremdausgabe auf stdout oder eine abgeschnittene Datei ab)
if command -v tar >/dev/null 2>&1 && ! tar tzf "$TMP_FILE" >/dev/null 2>&1; then
  fail "Die erzeugte Datei ist kein lesbares tar.gz (beschaedigt oder abgeschnitten). Sie wurde geloescht."
fi

# Rechte 0600 hat die Zwischendatei schon von mktemp. Erst den Besitzer setzen, dann umbenennen: `mv -fT` ersetzt
# einen unter dem Endnamen abgelegten Link, statt ihm zu folgen (oder die Datei in einen gleichnamigen Ordner zu legen).
give_to "$(target_owner)" "$TMP_FILE"
# Erst auf den Datentraeger, dann unter dem Endnamen: sonst kann ein Stromausfall kurz danach eine leere Datei mit dem
# fertigen Namen hinterlassen. Aeltere `sync`-Versionen kennen keine Dateiangabe, dann gilt das ganze System; ein Fehler
# dabei haelt die Sicherung nicht auf.
sync -- "$TMP_FILE" 2>/dev/null || sync 2>/dev/null || true
mv -fT -- "$TMP_FILE" "${OUT_DIR}/${OUT_FILE}"
if [ "$DIR_CREATED" = 1 ]; then give_to "$(target_owner)" "$OUT_DIR"; fi

echo "Fertig: ${OUT_DIR}/${OUT_FILE}"
