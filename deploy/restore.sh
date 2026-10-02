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
# Die Sicherungsdatei (tar.gz) wird NICHT in den Container eingebunden, sondern ueber die Standardeingabe
# hineingereicht (`tar xzf -`, `< Datei`, dazu `-T`: kein Pseudo-Terminal). Gelesen wird sie also von DIESER
# Shell, mit den Rechten des Aufrufers: Es klappt auch, wenn die Datei root gehoert (z. B. von
# `sudo ./backup.sh`, Rechte 0600) oder in einem Ordner liegt, den der Container-Benutzer 1000 nicht
# lesen darf. Vorher wird die Datei hier einmal mit `tar tzf` geprueft: Eine beschaedigte Datei soll nicht
# erst NACH dem Leeren des Volumes auffallen.
#
# Das Einspielen geschieht in zwei Schritten, damit ein Abbruch den bisherigen Stand nicht zerstoert:
# 1. Die Datei wird in `/app/data/.restore-neu` ENTPACKT; die bisherigen Daten bleiben unberuehrt. Scheitert das
#    (Datentraeger voll, Lesefehler, Ctrl-C), laeuft der Dienst danach auf dem alten Stand weiter.
# 2. Erst wenn alles entpackt und nicht leer ist, werden die alten Daten nach `.restore-alt` beiseitegeschoben, die
#    neuen eingesetzt und `.restore-alt` geloescht (nur Umbenennungen innerhalb des Volumes). Dieser Schritt ist
#    kurz und wird weder von SIGHUP (SSH-Abbruch) noch von Ctrl-C unterbrochen.
# Die Marke `/app/data/.restore-austausch` haelt fest, dass Schritt 2 laeuft (und ob gerade die alten Daten beiseite-
# oder die neuen eingesetzt werden). Sie entsteht vor der ersten Umbenennung und verschwindet erst, wenn alles fertig
# ist. Bricht Schritt 2 trotzdem ab, bleibt der Dienst GESTOPPT (nie auf halben Daten starten), und ein erneuter Lauf
# mit derselben Datei macht den Austausch an der richtigen Stelle fertig: auch er startet den Dienst nicht, solange die
# Marke liegt (auch nicht, wenn sein Entpacken scheitert). backup.sh lehnt eine Sicherung ab, solange die Marke liegt.
# Ein `.restore-alt` aus einem frueheren Lauf wird nie geloescht, sondern unter `.restore-alt-<Zeit>` beiseitegelegt.
# Die Arbeitsordner (`.restore-*`) kommen weder in eine Sicherung noch beim Einspielen ins Volume, auch aus aelteren
# Sicherungen nicht, in denen sie schon stecken.
# Platz: waehrend des Austauschs liegen alte und neue Daten kurz nebeneinander (doppelter Platzbedarf).
#
# Verbindungsabbruch: SIGHUP und SIGPIPE werden von Anfang an ignoriert, und nach der Rueckfrage scheitert keine
# Meldung mehr an einem weggefallenen Terminal (jede Ausgabe ist abgesichert, die Ausgabe der Hilfscontainer geht erst
# in eine Datei und wird dann gezeigt). Die Meldungen ab der Rueckfrage stehen zusaetzlich in `restore.log` (neben
# diesem Skript; anderer Ordner: RESTORE_LOG_DIR): dort steht nach einem Abbruch der SSH-Verbindung, wie es ausging.
#
# Sperre: backup.sh und restore.sh (beide Zweige, auch `.ndbak`) halten dieselbe Sperre (`flock` auf `.backup-restore.lock`
# neben dem Skript) bis zum Ende. Ein zweiter Lauf, der gleichzeitig startet (etwa nach einem SSH-Abbruch, waehrend der erste
# im Hintergrund weiterlaeuft, oder eine Cron-Sicherung waehrend des Einspielens), wird abgewiesen, ohne etwas zu veraendern.
# Der Dateideskriptor 9 bleibt Hilfsprogrammen verschlossen (`9>&-`): ein haengender Hilfsprozess haelt die Sperre sonst ueber
# das Ende des Skripts hinaus. Fehlt `flock` (Paket util-linux), gibt es eine Warnung, und das Skript laeuft ohne Sperre weiter.
# Die Sperrdatei und restore.log gehoeren nach `sudo` dem Aufrufer (SUDO_UID), damit spaetere Laeufe ohne sudo nicht still
# auf beides verzichten muessen.
#
# Exit-Codes (3 bis 5: es wurde nichts veraendert, nichts angehalten und nichts gestartet):
#   0 fertig   1 Fehler (je nach Meldung)   3 ein unterbrochenes Einspielen liegt noch (Marke, nur fuer `.ndbak`: erst
#   dieses mit derselben tar.gz abschliessen)   4 es laeuft schon eine Sicherung oder ein Einspielen (Sperre)
#   5 die Marke ist leer oder beschaedigt (Meldung nennt den Weg von Hand)
# Der Austausch im Hilfscontainer hat eigene, innere Codes (4 = nichts veraendert); sie sind nicht die des Skripts.
#
# Nutzung (aus deploy/):  ./restore.sh /pfad/zu/nodvard-deck-backup-20260101-120000.tar.gz   (auch lattice-backup-*.tar.gz)
#
# Eine verschluesselte Sicherung aus der Oberflaeche (`*.ndbak`) wird NICHT sofort eingespielt: das Skript
# laesst sie von `python -m nodvard_deck.admin restore-backup` pruefen und VORMERKEN (Passwort bzw. Wiederherstellungs-
# schluessel wird abgefragt, es erscheint eine Zusammenfassung) und startet den Dienst auf Wunsch neu. Eingespielt wird
# dann beim Start (`nodvard_deck.boot`), mit denselben Pruefungen und demselben Rueckweg wie in der Oberflaeche.
# Hier braucht der Container die Datei als Pfad (die Standardeingabe gehoert dem Passwort-Dialog, und der
# braucht ein Terminal). Das Skript legt dafuer eine KOPIE in einem eigenen Ordner mit zufaelligem Namen
# an (Ordner 0700, Datei 0600) und loescht sie am Ende wieder. Damit der Container-Benutzer 1000 sie trotzdem
# lesen kann: Als root gehoert die Kopie 1000 (der Ordner bleibt root-eigen und wird erst danach auf 0711
# geoeffnet, so kann niemand dazwischen etwas austauschen); als 1000 passt es von selbst; als jeder andere
# bekommt 1000 per `setfacl` genau auf diesen Ordner und diese Datei Zugriff -- fehlt `setfacl`, bricht das
# Skript ab und verlangt sudo. Fuer andere lokale Benutzer ist die Kopie nie lesbar. So scheitert es weder
# an einer root-eigenen Datei noch an einem Ordner, den 1000 nicht betreten darf:
#   ./restore.sh /pfad/zu/nodvard-deck-sicherung-20261001-023000.ndbak
set -euo pipefail
trap '' HUP PIPE
cd "$(dirname "$0")"

# Meldungen: scheitern nie (Terminal nach einem SSH-Abbruch weg) und stehen zusaetzlich in restore.log, sobald es offen ist.
LOG_FILE=""
log_line() {
  if [ -n "$LOG_FILE" ]; then printf '%s\n' "$*" >> "$LOG_FILE" 2>/dev/null || true; fi
  return 0
}
say() {
  printf '%s\n' "$*" 2>/dev/null || true
  log_line "$*"
}
warn() {
  printf '%s\n' "$*" >&2 2>/dev/null || true
  log_line "$*"
}

# Nach `sudo` gehoeren Sperrdatei und Protokoll dem Aufrufer (SUDO_UID), nicht root.
give_to_sudo_user() {
  [ "$(id -u)" = 0 ] || return 0
  [ -n "${SUDO_UID:-}" ] && [ "$SUDO_UID" != 0 ] || return 0
  chown -h "${SUDO_UID}:${SUDO_GID:-$SUDO_UID}" "$@" 2>/dev/null || true
  return 0
}

# Gemeinsame Sperre mit backup.sh (siehe Kopfkommentar). Offen bleibt Deskriptor 9 bis zum Ende des Skripts.
LOCK_FILE="$PWD/.backup-restore.lock"
acquire_lock() {
  local rc=0
  if ! command -v flock >/dev/null 2>&1; then
    warn "WARNUNG: 'flock' fehlt (Paket util-linux). Es laeuft ohne Sperre weiter: Achte selbst darauf, dass nicht gleichzeitig eine Sicherung oder ein Einspielen laeuft."
    return 0
  fi
  # Nie einem Link folgen: als root wuerde sonst eine Datei an dessen Ziel angelegt und auf 0644 gesetzt (oder an einer
  # Pipe gewartet). Angelegt wird exklusiv (noclobber), ein vorhandener Name wird nie ueberschrieben.
  if [ -L "$LOCK_FILE" ] || { [ -e "$LOCK_FILE" ] && [ ! -f "$LOCK_FILE" ]; }; then
    warn "FEHLER: ${LOCK_FILE} ist keine gewoehnliche Datei (ein Link oder Aehnliches) und wird nicht benutzt. Bitte von Hand entfernen: rm ${LOCK_FILE} -- danach legt das Skript sie selbst neu an. Es wurde nichts veraendert."
    exit 1
  fi
  if [ ! -e "$LOCK_FILE" ] && ( set -C; : > "$LOCK_FILE" ) 2>/dev/null; then
    chmod 0644 "$LOCK_FILE" 2>/dev/null || true
    give_to_sudo_user "$LOCK_FILE"
  fi
  # Nur lesend oeffnen: flock geht auch so, und es klappt, wenn die Datei einem anderen Benutzer (z. B. root) gehoert.
  if ! { exec 9< "$LOCK_FILE"; } 2>/dev/null; then
    warn "WARNUNG: Die Sperrdatei ${LOCK_FILE} laesst sich nicht oeffnen. Es laeuft ohne Sperre weiter: Achte selbst darauf, dass nicht gleichzeitig eine Sicherung oder ein Einspielen laeuft."
    return 0
  fi
  flock -n -E 200 9 || rc=$?
  case "$rc" in
    0) ;;
    200)
      warn "Es laeuft schon eine Sicherung oder ein Einspielen (siehe restore.log). Bitte warten, bis es fertig ist. Es wurde nichts veraendert."
      exit 4
      ;;
    *) warn "WARNUNG: Die Sperre liess sich nicht anfordern (flock meldete einen Fehler). Es laeuft ohne Sperre weiter." ;;
  esac
}

# Ob ein frueheres Einspielen mitten im Austausch stehen geblieben ist (Marke): Exit 0 = nein, 3 = ja (alt|neu),
# 5 = Marke leer, unlesbar oder mit unbekanntem Inhalt. Es wird nichts veraendert.
# shellcheck disable=SC2016 # laeuft im Container
CHECK_SCRIPT='M=/app/data/.restore-austausch
if [ -e "$M" ] || [ -L "$M" ]; then
  p=$(cat "$M" 2>/dev/null) || exit 5
  case "$p" in alt|neu) exit 3 ;; *) exit 5 ;; esac
fi'
MARK=/app/data/.restore-austausch
warn_broken_mark() {
  warn "FEHLER: Die Marke ${MARK} eines unterbrochenen Einspielens ist leer, unlesbar oder hat einen unbekannten Inhalt."
  warn "Sie haelt fest, wie weit der Austausch war; ohne sie weiss niemand, welche Daten sicher sind. Auch eine Wiederholung mit derselben Datei hilft hier nicht."
  warn "Es wurde nichts veraendert, nichts angehalten und nichts gestartet, und es wird auch nichts automatisch geloescht."
  warn "Von Hand: Sieh im Datenvolume nach (deploy_lattice_data, im Container /app/data):  docker compose -p deploy run --rm --no-deps --entrypoint ls nodvard-deck -la /app/data"
  warn "  .restore-alt  enthaelt den alten Stand (ganz oder zum Teil); .restore-neu die frisch entpackten neuen Daten; was sonst dort liegt, ist ein Gemisch aus beiden."
  warn "  Hast du entschieden, was gelten soll, und es dorthin gelegt, entfernst du die Marke:"
  warn "  docker compose -p deploy run --rm --no-deps --entrypoint rm nodvard-deck -f ${MARK}"
  warn "  Danach geht ./restore.sh <Datei> wieder; ein uebrig gebliebenes .restore-alt wird dabei nie geloescht, sondern beiseitegelegt."
}

BACKUP_FILE="${1:?Nutzung: restore.sh <backup-datei.tar.gz>}"
[ -f "$BACKUP_FILE" ] || { echo "Nicht gefunden: ${BACKUP_FILE}" >&2; exit 1; }
[ -r "$BACKUP_FILE" ] || { echo "Nicht lesbar fuer den aktuellen Benutzer: ${BACKUP_FILE} (gehoert sie root? Dann das Skript mit sudo starten.)" >&2; exit 1; }
BACKUP_NAME="$(basename "$BACKUP_FILE")"

case "$BACKUP_NAME" in
  *.ndbak)
    acquire_lock
    # Steht ein tar.gz-Einspielen mitten im Austausch, ist der Dienst absichtlich gestoppt: ein Neustart (und damit auch das
    # Vormerken) wuerde ihn auf halbem Stand starten. Dann wird nichts vorgemerkt.
    ndbak_check_rc=0
    docker compose -p deploy run -T --rm --no-deps --user 1000:1000 --entrypoint sh nodvard-deck -c "$CHECK_SCRIPT" 9>&- < /dev/null || ndbak_check_rc=$?
    case "$ndbak_check_rc" in
      0) ;;
      3)
        warn "FEHLER: Ein frueheres Einspielen einer tar.gz-Sicherung ist mitten im Austausch der Daten stehen geblieben (Marke ${MARK}). Der Datenbestand ist unvollstaendig und Nodvard Deck absichtlich gestoppt: Das Vormerken und der Neustart wuerden es auf halbem Stand starten."
        warn "Bitte zuerst das unterbrochene Einspielen mit derselben tar.gz-Datei abschliessen:  ./restore.sh <die tar.gz-Datei>   Danach kannst du diese .ndbak-Datei einspielen. Es wurde nichts vorgemerkt, nichts angehalten und nichts gestartet."
        exit 3
        ;;
      5) warn_broken_mark; exit 5 ;;
      *) warn "FEHLER: Der Datenordner liess sich nicht pruefen (Docker meldete einen Fehler, siehe oben). Es wurde nichts vorgemerkt und nichts veraendert."; exit 1 ;;
    esac
    echo "Verschluesselte Sicherung: sie wird geprueft und zum Einspielen vorgemerkt (Passwort wird gleich abgefragt)."
    # Kopie fuer den Container (siehe Kopfkommentar): eigener Ordner (mktemp: 0700), nach dem Lauf wieder weg.
    STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nodvard-restore.XXXXXX")"
    trap 'rm -rf "$STAGE_DIR"' EXIT
    CALLER_UID="$(id -u)"
    cp "$BACKUP_FILE" "${STAGE_DIR}/${BACKUP_NAME}"
    chmod 0600 "${STAGE_DIR}/${BACKUP_NAME}"
    if [ "$CALLER_UID" = 0 ]; then
      # Die Datei gehoert 1000, der Ordner bleibt root-eigen und wird erst jetzt (Datei steht schon) fuer 1000 begehbar.
      chown 1000:1000 "${STAGE_DIR}/${BACKUP_NAME}"
      chmod 0711 "$STAGE_DIR"
    elif [ "$CALLER_UID" != 1000 ]; then
      # Weder root noch 1000: dem Container-Benutzer nur auf diesen Ordner und diese Datei Zugriff geben.
      if ! { command -v setfacl >/dev/null 2>&1 \
          && setfacl -m u:1000:x "$STAGE_DIR" 2>/dev/null \
          && setfacl -m u:1000:r "${STAGE_DIR}/${BACKUP_NAME}" 2>/dev/null; }; then
        echo "FEHLER: Die verschluesselte Sicherung laesst sich dem Container (Benutzer 1000) so nicht sicher bereitstellen (setfacl fehlt oder geht hier nicht). Bitte das Skript mit sudo starten: sudo ./restore.sh ${BACKUP_FILE}" >&2
        exit 1
      fi
    fi
    docker compose -p deploy run --rm --no-deps --user 1000:1000 \
      --entrypoint python -v "${STAGE_DIR}:/backup:ro" nodvard-deck \
      -m nodvard_deck.admin restore-backup "/backup/${BACKUP_NAME}" 9>&-
    read -r -p "Nodvard Deck jetzt neu starten, damit die Sicherung eingespielt wird? [y/N] " restart
    case "$restart" in
      y|Y) docker compose -p deploy restart nodvard-deck 9>&-; echo "Neu gestartet. Der Fortschritt steht im Protokoll: docker compose -p deploy logs -f nodvard-deck" ;;
      *) echo "Vorgemerkt (gilt eine Stunde). Zum Einspielen den Dienst neu starten: docker compose -p deploy restart nodvard-deck" ;;
    esac
    exit 0
    ;;
esac

# Vor dem Leeren des Volumes: ist das ueberhaupt ein tar.gz? (Sonst waere das Volume danach leer UND nichts eingespielt.)
if command -v tar >/dev/null 2>&1 && ! tar tzf "$BACKUP_FILE" >/dev/null 2>&1; then
  echo "FEHLER: ${BACKUP_FILE} ist kein lesbares tar.gz (beschaedigt oder abgeschnitten). Es wurde nichts veraendert." >&2
  exit 1
fi

echo "WARNUNG: das ersetzt den kompletten aktuellen Datenbestand (/app/data,"
echo "inkl. Master-Key und Datenbank) im lattice_data-Volume unwiderruflich."
read -r -p "Fortfahren? [y/N] " confirm
case "$confirm" in
  y|Y) ;;
  *) echo "Abgebrochen."; exit 1 ;;
esac

# Ab hier darf keine Meldung das Skript beenden (weggefallenes Terminal nach einem SSH-Abbruch: jedes Schreiben
# scheitert dann). Alles steht zusaetzlich im Protokoll, wenn es sich anlegen laesst.
# fd 3/4 merken sich die Ausgabe des Aufrufers: Endet das Skript durch ein Signal (Ctrl-C), waehrend die Ausgabe eines
# Hilfscontainers umgeleitet ist, laeuft der EXIT-Trap sonst noch mit dieser Umleitung, und seine Meldungen gingen verloren.
exec 3>&1 4>&2
LOG_DIR="${RESTORE_LOG_DIR:-.}"
if mkdir -p -- "$LOG_DIR" 2>/dev/null && : >> "$LOG_DIR/restore.log" 2>/dev/null; then
  LOG_FILE="$(cd "$LOG_DIR" && pwd)/restore.log"
  give_to_sudo_user "$LOG_FILE"
  log_line "=== $(date '+%Y-%m-%d %H:%M:%S') restore.sh ${BACKUP_FILE} ==="
  say "Protokoll: ${LOG_FILE}"
fi

# Die Ausgabe der Hilfscontainer landet erst in dieser Datei und wird danach gezeigt: Docker schreibt so nie selbst auf
# ein Terminal, das es vielleicht nicht mehr gibt. Laesst sie sich nicht anlegen, geht die Ausgabe direkt durch.
STEP_OUT="$(mktemp "${TMPDIR:-/tmp}/nodvard-restore-ausgabe.XXXXXX" 2>/dev/null)" || STEP_OUT=""
SERVICE_TOUCHED=0
on_exit() {
  exec 1>&3 2>&4
  if [ "$SERVICE_TOUCHED" = 1 ]; then restart_services || true; fi
  if [ -n "$STEP_OUT" ]; then rm -f -- "$STEP_OUT" 2>/dev/null || true; fi
  return 0
}
trap on_exit EXIT

# run_logged <befehl>...: Ausgabe ueber STEP_OUT, Exit-Code des Befehls. Die Standardeingabe reicht er durch.
run_logged() {
  local rc=0 line
  if [ -z "$STEP_OUT" ]; then
    "$@" || rc=$?
    return "$rc"
  fi
  "$@" > "$STEP_OUT" 2>&1 || rc=$?
  while IFS= read -r line || [ -n "$line" ]; do warn "$line"; done < "$STEP_OUT"
  return "$rc"
}

# Docker bekommt die gemerkten Ausgaben 3/4 nicht mit.
# Auch die Sperre (Deskriptor 9) bleibt Docker verschlossen.
dk() {
  docker "$@" 3>&- 4>&- 9>&-
}
helper() {
  dk compose -p deploy run -T --rm --no-deps --user 1000:1000 --entrypoint sh nodvard-deck -c "$@"
}

# Vorab (der Dienst laeuft noch, es wird nichts veraendert): liegt die Marke eines unterbrochenen Austauschs? (CHECK_SCRIPT, oben)
# Schritt 1: entpacken, die bisherigen Daten bleiben dabei unberuehrt. Arbeitsordner aus der Sicherung (`.restore-*`, auch
# ohne `./` im Namen) werden nie mit eingespielt. Bleibt ein halb entpackter Ordner zurueck (Abbruch von aussen), raeumt
# der naechste Durchlauf ihn weg; Sicherungen lassen ihn aus.
# shellcheck disable=SC2016 # laeuft im Container, dort wird `$(...)` ausgewertet, nicht hier
UNPACK_SCRIPT='
D=/app/data/.restore-neu
cd /app/data || exit 1
# Stand ein abgebrochener Austausch beim Einsetzen der neuen Daten (Marke `neu`), liegen die alten vollstaendig in
# .restore-alt, und oben liegt nur ein Teil der neuen: weg damit, sonst braucht das Entpacken noch mehr Platz.
if [ -e .restore-austausch ] && [ "$(cat .restore-austausch)" = neu ]; then
  find . -mindepth 1 -maxdepth 1 ! -name .restore-neu ! -name ".restore-alt*" ! -name ".restore-austausch*" -exec rm -rf {} + || exit 1
fi
rm -rf "$D" && mkdir "$D" || exit 1
if ! tar xzf - -C /app/data/.restore-neu --exclude=./.restore-neu "--exclude=./.restore-alt*" "--exclude=./.restore-austausch*"; then
  rm -rf "$D"
  exit 1
fi
if ! rm -rf "$D/.restore-neu" "$D"/.restore-alt* "$D"/.restore-austausch*; then
  rm -rf "$D"
  exit 1
fi
if [ -z "$(ls -A "$D")" ]; then
  echo "Die Sicherung enthaelt keine Dateien." >&2
  rm -rf "$D"
  exit 1
fi
'
# Schritt 2: nur Umbenennungen innerhalb des Volumes; `find ... {} +` meldet einen Fehler von `mv` weiter.
# Die Marke sagt, wo ein abgebrochener Lauf stand: `alt` = die alten Daten werden beiseitegeschoben (oben liegt nur Altes,
# der Rest steckt schon in .restore-alt), `neu` = die alten liegen vollstaendig in .restore-alt, oben liegt nur, was schon
# von den neuen Daten eingesetzt war (das kommt gleich frisch aus .restore-neu). Geschrieben wird sie per Umbenennen,
# also immer vollstaendig, und vorher mit `sync` auf den Datentraeger gebracht: sonst kann sie nach einem Stromausfall
# leer sein, und kein Lauf koennte mehr sicher weitermachen. Genauso werden die eingesetzten neuen Daten (ein `sync` ganz
# ohne Angabe) auf den Datentraeger gebracht, BEVOR die Marke und der alte Stand geloescht werden: sonst kann ein Stromausfall
# kurz nach "Fertig." eine leere Datenbank hinterlassen, und der alte Stand waere dann schon weg. Exit 4: es wurde nichts
# veraendert (die alten Daten sind vollstaendig oben).
# shellcheck disable=SC2016 # laeuft im Container
SWAP_SCRIPT='
cd /app/data || exit 4
M=.restore-austausch
nothing_changed() { rm -rf .restore-neu "$M.neu"; exit 4; }
mark() { printf "%s\n" "$1" > "$M.neu" && sync "$M.neu" && mv -f "$M.neu" "$M"; }
if [ -e "$M" ]; then
  phase=$(cat "$M") || exit 5
  echo "Ein frueherer Austausch wurde unterbrochen; er wird jetzt fertig gemacht." >&2
else
  if [ -e .restore-alt ]; then
    keep=".restore-alt-$(date +%Y%m%d-%H%M%S)"
    mv -T .restore-alt "$keep" || nothing_changed
    echo "Hinweis: Unter /app/data/.restore-alt lag noch ein alter Stand von einem frueheren Einspielen. Er wurde nicht geloescht, sondern liegt jetzt unter /app/data/$keep. Brauchst du ihn nicht mehr: docker compose -p deploy run --rm --no-deps --entrypoint rm nodvard-deck -rf /app/data/$keep" >&2
  fi
  mark alt || nothing_changed
  phase=alt
fi
case "$phase" in
  alt|neu) ;;
  *) echo "Unbekannter Inhalt in /app/data/$M: $phase" >&2; exit 5 ;;
esac
set -e
if [ "$phase" = alt ]; then
  mkdir -p .restore-alt
  find . -mindepth 1 -maxdepth 1 ! -name .restore-neu ! -name ".restore-alt*" ! -name "$M" ! -name "$M.neu" -exec mv -t .restore-alt/ {} +
  mark neu
fi
find . -mindepth 1 -maxdepth 1 ! -name .restore-neu ! -name ".restore-alt*" ! -name "$M" ! -name "$M.neu" -exec rm -rf {} +
find .restore-neu -mindepth 1 -maxdepth 1 -exec mv -t . {} +
rmdir .restore-neu
sync
rm -f "$M"
rm -rf .restore-alt || echo "Hinweis: Der bisherige Stand unter /app/data/.restore-alt liess sich nicht ganz loeschen. Er stoert nicht (Sicherungen lassen ihn aus) und wird beim naechsten Einspielen beiseitegelegt." >&2
'

# Erst pruefen, dann anhalten: Liegt die Marke, ist der Datenbestand schon halb ausgetauscht.
acquire_lock
check_rc=0
run_logged helper "$CHECK_SCRIPT" < /dev/null || check_rc=$?
RESUME=0
case "$check_rc" in
  0) ;;
  3)
    RESUME=1
    warn "Ein frueheres Einspielen ist mitten im Austausch der Daten stehen geblieben (Marke ${MARK}). Dieser Lauf macht es fertig."
    warn "Bis das geklappt hat, bleibt Nodvard Deck gestoppt."
    ;;
  5)
    warn_broken_mark
    exit 5
    ;;
  *)
    warn "FEHLER: Der Datenordner liess sich nicht pruefen (Docker meldete einen Fehler, siehe oben). Es wurde nichts veraendert."
    exit 1
    ;;
esac

# 1 = der Datenbestand ist (moeglicherweise) halb ausgetauscht: der Dienst darf NICHT starten.
KEEP_STOPPED="$RESUME"
restart_services() {
  if [ "$KEEP_STOPPED" = 1 ]; then
    warn "WARNUNG: Nodvard Deck wird NICHT gestartet: Der Austausch der Daten ist nicht fertig, der Datenbestand ist unvollstaendig."
    warn "Der bisherige Stand ist nicht geloescht: Er liegt ganz oder zum Teil unter /app/data/.restore-alt (Volume deploy_lattice_data)."
    warn "So geht es weiter: das Einspielen mit derselben Datei wiederholen: ./restore.sh ${BACKUP_FILE}"
    warn "Es macht den Austausch fertig und startet den Dienst danach selbst. Bis dahin den Dienst nicht von Hand starten; backup.sh lehnt so lange ab."
    if [ -n "$LOG_FILE" ]; then warn "Protokoll: ${LOG_FILE}"; fi
    return 0
  fi
  if [ "$OLD_WAS_RUNNING" = 1 ]; then
    say "Starte den alten Container $OLD_CONTAINER wieder ..."
    run_logged dk start "$OLD_CONTAINER" || warn "WARNUNG: $OLD_CONTAINER liess sich nicht starten."
  else
    say "Starte den nodvard-deck-Dienst wieder ..."
    run_logged dk compose -p deploy start nodvard-deck || warn "WARNUNG: Der nodvard-deck-Dienst liess sich nicht starten: docker compose -p deploy start nodvard-deck"
  fi
}

# Lief vor dem Umschalten (oder nach einem Rollback) noch der alte Container `deploy-lattice-1`
# (Dienst `lattice` vor der Umbenennung), hat er dasselbe Volume offen: ihn ebenfalls stoppen und
# am Ende wieder starten, sonst wuerde er mitten im Betrieb gesichert bzw. geleert.
OLD_CONTAINER="deploy-lattice-1"
OLD_WAS_RUNNING=0
SERVICE_TOUCHED=1
if [ "$(dk container inspect -f '{{.State.Status}}' "$OLD_CONTAINER" 2>/dev/null || true)" = "running" ]; then
  OLD_WAS_RUNNING=1
  say "Stoppe den alten Container $OLD_CONTAINER (hat dasselbe Volume offen) ..."
  run_logged dk stop -t 30 "$OLD_CONTAINER"
fi

say "Stoppe den nodvard-deck-Dienst ..."
run_logged dk compose -p deploy stop nodvard-deck

say "Entpacke ${BACKUP_NAME} neben die bisherigen Daten ..."
if ! run_logged helper "$UNPACK_SCRIPT" < "$BACKUP_FILE"; then
  if [ "$RESUME" = 1 ]; then
    warn "FEHLER: Das Entpacken ist fehlgeschlagen (siehe oben; ist der Platz knapp? Es braucht Platz fuer eine zweite Kopie der Daten)."
  else
    warn "FEHLER: Das Einspielen ist fehlgeschlagen (siehe oben; ist der Platz knapp? Es braucht kurz Platz fuer eine zweite Kopie der Daten). Die bisherigen Daten sind unveraendert, der Dienst wird wieder gestartet."
  fi
  exit 1
fi

# Ab hier ist der alte Stand angegriffen: Der Dienst startet erst wieder, wenn der Austausch ganz durch ist.
KEEP_STOPPED=1
trap '' HUP INT TERM
say "Tausche die Daten aus (bitte nicht unterbrechen) ..."
swap_rc=0
run_logged helper "$SWAP_SCRIPT" < /dev/null || swap_rc=$?
if [ "$swap_rc" != 0 ]; then
  if [ "$swap_rc" = 4 ] && [ "$RESUME" = 0 ]; then
    KEEP_STOPPED=0
    warn "FEHLER: Der Austausch liess sich nicht beginnen (siehe oben). Die bisherigen Daten sind unveraendert, der Dienst wird wieder gestartet."
  else
    warn "FEHLER: Das Austauschen der Daten ist mittendrin fehlgeschlagen (siehe oben). Der Datenbestand ist jetzt unvollstaendig."
  fi
  exit 1
fi
KEEP_STOPPED=0
trap - INT TERM

say "Fertig."
