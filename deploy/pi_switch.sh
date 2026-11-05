#!/usr/bin/env bash
# Schaltschritt auf dem Zielrechner (Pi): neues Image in Betrieb nehmen, Rückweg sichern,
# prüfen, notfalls zurückschalten. Wird von scripts/deploy_pi.sh per ssh aufgerufen und
# liegt deshalb im deploy/-Ordner, der ohnehin mitgeschickt wird. Eigene Datei, damit sich
# der Ablauf mit einem nachgebauten `docker` testen lässt (backend/tests/test_deploy_switch.py).
#
#   ./pi_switch.sh switch <image-tag>   # prüfen, sichern, compose up, verify
#   ./pi_switch.sh verify               # läuft der neue Container, mit dem neuen Image und dem alten Volume?
#   ./pi_switch.sh cleanup              # erst NACH verify, Health und Datencheck: alte Images entfernen
#   ./pi_switch.sh rollback             # zurück auf den alten Stand, prüfen
#   ./pi_switch.sh precheck             # Stand VOR dem Umschalten: läuft ein Dashboard, ist es eingerichtet?
#   ./pi_switch.sh bootstrap [versuche] # needed=false|needed=true|http=<code>|noanswer (GET /api/v1/auth/bootstrap)
#   ./pi_switch.sh loaded [sekunden]    # none|invalid|list=<kennungen>: welche Erweiterungen hat das laufende Dashboard geladen?
#   ./pi_switch.sh detached <unterbefehl> ...   # wie der Unterbefehl, aber von der ssh-Leitung abgekoppelt
#   ./pi_switch.sh oldname              # nur: liegt hier noch ein Stand von vor 0.6? (Exit 3 = ja)
#
# Exit 3 = es wurde nichts verändert (Docker nicht erreichbar, Image fehlt, Compose-Datei
# nicht lesbar, Stand von vor 0.6); der Aufrufer darf dann NICHT zurückschalten.
# Exit 4 = der Container hat das richtige Volume, läuft aber nicht (mehr) oder hängt in der
# Neustart-Schleife. War er vorher schon gesund, kann er die Datenbank bereits umgebaut haben.
#
# Die Namen sind überschreibbar (Tests), Standard ist der echte Betrieb:
#   DECK_IMAGE=nodvard-deck  CONTAINER=deploy-nodvard-deck-1
#   PROJECT=deploy           VOLUME=deploy_lattice_data
#
# Das Volume (`deploy_lattice_data`) bleibt immer unberührt; `verify` prüft, dass der neue
# Container wirklich dieses Volume unter /app/data eingebunden hat (sonst wäre es leer).
# Bewusst KEIN `--remove-orphans`: das wirkt auf das ganze Projekt und löscht Container, die
# nicht zu diesem Dienst gehören.
set -euo pipefail
cd "$(dirname "$0")"

# Bricht die ssh-Verbindung mitten im Umschalten ab (WLAN, Zeitüberschreitung), bekommt die
# Shell ein SIGHUP. Das hier schützt nur die Shell selbst (und wird von den docker-Aufrufen
# geerbt), NICHT vor einem geschlossenen Ausgabe-Rohr (SIGPIPE, `printf` scheitert, docker
# stirbt). Dafür gibt es `detached`: Ausgabe in Dateien, eigene Sitzung (setsid).
trap '' HUP
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

DECK_IMAGE="${DECK_IMAGE:-nodvard-deck}"
CONTAINER="${CONTAINER:-deploy-nodvard-deck-1}"
PROJECT="${PROJECT:-deploy}"
VOLUME="${VOLUME:-deploy_lattice_data}"
# So hieß der Container vor Version 0.6. Den Umstieg von dort liefert nur noch Version 0.11 aus.
PRE_RENAME_CONTAINER="deploy-lattice-1"
# Compose liest seine Variablen aus der .env neben der Compose-Datei; dort gelten die alten Namen LATTICE_* nur noch
# in dieser Version.
DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-.env}"

say() { printf '%s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
die_unchanged() { printf 'FAIL: %s\n' "$*" >&2; exit 3; }

# Projektname immer mitgeben: COMPOSE_PROJECT_NAME in der Umgebung oder in .env würde sonst
# ein anderes Projekt (und damit ein anderes, leeres Volume) wählen.
compose() { docker compose -p "$PROJECT" "$@"; }

has_image() { docker image inspect "$1" >/dev/null 2>&1; }
has_container() { docker container inspect "$1" >/dev/null 2>&1; }
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
inspect_container() { docker container inspect -f "$2" "$1"; }

need_docker() {
  docker info >/dev/null 2>&1 || die_unchanged "Docker ist nicht erreichbar (laeuft der Dienst? Hat dieser Benutzer Docker-Rechte?). Nichts veraendert."
}

# Läuft der Container gerade (und belegt dann z. B. Port 8080)?
container_running() {
  has_container "$1" && [ "$(inspect_container "$1" '{{.State.Status}}')" = "running" ]
}

# Rückweg sichern: nodvard-deck:latest -> nodvard-deck:previous. Wurde dasselbe Image schon in
# Betrieb genommen (erneuter Aufruf nach einem Abbruch), bleibt der Rückweg, wie er ist, damit er
# nicht mit dem fraglichen Stand überschrieben wird.
backup_previous() {
  local new_id="$1" cur_id
  if has_image "$DECK_IMAGE:latest"; then
    cur_id="$(image_id "$DECK_IMAGE:latest")"
    if [ "$cur_id" = "$new_id" ] && has_image "$DECK_IMAGE:previous"; then
      say "Sicherung: $DECK_IMAGE:latest ist schon das neue Image, $DECK_IMAGE:previous bleibt."
    else
      docker tag "$DECK_IMAGE:latest" "$DECK_IMAGE:previous"
      say "Sicherung: $DECK_IMAGE:latest -> $DECK_IMAGE:previous."
    fi
  else
    say "Frische Maschine: kein altes Image, keine Sicherung moeglich."
  fi
}

# Läuft der Container wirklich (Status running, nicht im Neustart-Kreislauf) und hängt
# /app/data am Volume mit den alten Daten?
check_container() {
  local name="$1" status restarting vol
  status="$(inspect_container "$name" '{{.State.Status}}')"
  restarting="$(inspect_container "$name" '{{.State.Restarting}}')"
  vol="$(inspect_container "$name" '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')"
  [ "$vol" = "$VOLUME" ] || die "Container $name hat unter /app/data das Volume '${vol:-<keins>}' statt $VOLUME -- die Daten waeren weg (leeres Volume)."
  if [ "$status" != "running" ] || [ "$restarting" != "false" ]; then
    printf 'FAIL: %s\n' "Container $name laeuft nicht (Status: $status, Neustart-Schleife: $restarting)." >&2
    exit 4
  fi
}

# Trägt der neue Container das Image von nodvard-deck:latest, läuft er, hat er das richtige
# Volume? Ein "gesund" von /api/v1/health allein beweist das nicht.
verify() {
  local want have
  want="$(image_id "$DECK_IMAGE:latest")" || die "Image $DECK_IMAGE:latest fehlt."
  has_container "$CONTAINER" || die "Container $CONTAINER existiert nicht."
  have="$(inspect_container "$CONTAINER" '{{.Image}}')"
  [ "$have" = "$want" ] || die "Container $CONTAINER laeuft mit einem anderen Image als $DECK_IMAGE:latest."
  check_container "$CONTAINER"
  say "VERIFY-OK: $CONTAINER laeuft mit $DECK_IMAGE:latest ($want), Volume $VOLUME."
}

# Gibt es den Container von vor 0.6 noch, bricht das vor jeder Änderung ab (Exit 3): Er hätte
# dasselbe Volume offen, und sein Image wäre kein Rückweg, den `rollback` kennt.
refuse_pre_rename() {
  has_container "$PRE_RENAME_CONTAINER" || return 0
  if container_running "$PRE_RENAME_CONTAINER" || ! has_image "$DECK_IMAGE:latest"; then
    die_unchanged "Hier liegt noch Nodvard Deck von vor Version 0.6 (Container $PRE_RENAME_CONTAINER). Den Umstieg liefert nur Version 0.11 aus: git checkout v0.11.1, scripts/deploy_pi.sh, danach diese Version. Nichts veraendert."
  fi
  die_unchanged "Der alte Container $PRE_RENAME_CONTAINER liegt noch gestoppt auf dem Rechner. Entfernen mit 'docker rm $PRE_RENAME_CONTAINER', dann erneut ausliefern. Nichts veraendert."
}

# Alte Namen in der .env auf die neuen umbenennen, die Datei vorher als Kopie behalten. Hat der neue Name schon einen
# Wert, fällt die alte Zeile weg; ein leerer neuer weicht dem alten Wert, wie bei `${NEU:-${ALT:-}}` in Compose.
rename_old_env() {
  [ -f "$DEPLOY_ENV_FILE" ] || return 0
  if grep -q '^LATTICE_[A-Za-z0-9_]*=' "$DEPLOY_ENV_FILE"; then
    local copy="$DEPLOY_ENV_FILE.vor-umbenennung" next="$DEPLOY_ENV_FILE.neu"
    cp -p "$DEPLOY_ENV_FILE" "$copy" || die_unchanged "Kopie von $DEPLOY_ENV_FILE ging nicht. Nichts veraendert."
    # umask 077: Die Zwischendatei enthält dieselben Werte wie die .env und soll nicht kurz für alle lesbar sein.
    if ! (umask 077 && awk -v q="'" '
          function key(line) { sub(/=.*/, "", line); return line }
          function filled(line,  v) {
            v = substr(line, index(line, "=") + 1); sub(/^[ \t]+/, "", v); sub(/[ \t\r]+$/, "", v)
            return v != "" && v != "\"\"" && v != q q
          }
          NR == FNR {
            if ($0 ~ /^NODVARD_DECK_[A-Za-z0-9_]*=/ && filled($0)) set[key($0)] = 1
            if ($0 ~ /^LATTICE_[A-Za-z0-9_]*=/) old["NODVARD_DECK_" substr(key($0), 9)] = 1
            next
          }
          /^NODVARD_DECK_[A-Za-z0-9_]*=/ && !filled($0) && (key($0) in old) && !(key($0) in set) { next }
          /^LATTICE_[A-Za-z0-9_]*=/ { line = "NODVARD_DECK_" substr($0, 9); if (!(key(line) in set)) print line; next }
          { print }' "$copy" "$copy" > "$next") || ! cat "$next" > "$DEPLOY_ENV_FILE"; then
      rm -f "$next"
      die_unchanged "$DEPLOY_ENV_FILE liess sich nicht umbenennen (vorher: $copy). Nichts veraendert."
    fi
    rm -f "$next"
    say "$DEPLOY_ENV_FILE: alte Namen LATTICE_* auf NODVARD_DECK_* umbenannt (vorher: $copy)."
  fi
  if grep -q '^[^#]*LATTICE_' "$DEPLOY_ENV_FILE"; then
    say "WARNUNG: In $DEPLOY_ENV_FILE stehen noch Zeilen mit LATTICE_, die nicht automatisch umbenannt werden konnten. Bitte von Hand auf NODVARD_DECK_ umstellen, ab Version 1.0 gelten sie nicht mehr."
  fi
}

cmd_switch() {
  local new_tag="${1:?Nutzung: pi_switch.sh switch <image-tag>}" new_id
  # Alles, was schieflaufen kann, BEVOR etwas angefasst wird (Exit 3 = nichts verändert).
  need_docker
  refuse_pre_rename
  has_image "$new_tag" || die_unchanged "Image $new_tag ist auf diesem Rechner nicht vorhanden."
  compose config -q >/dev/null 2>&1 || die_unchanged "Die Compose-Datei ist nicht lesbar (docker compose config scheitert; zu alte Compose-Version?). Nichts veraendert."
  rename_old_env
  new_id="$(image_id "$new_tag")"
  backup_previous "$new_id"
  docker tag "$new_tag" "$DECK_IMAGE:latest"
  compose up -d --no-build
  verify
}

# Erst aufrufen, wenn verify, Health und Datencheck bestanden sind: alte Images entfernen. Das
# Volume wird nie angefasst.
cmd_cleanup() {
  need_docker
  verify
  local latest_id prev_id="" tag
  latest_id="$(image_id "$DECK_IMAGE:latest")"
  if has_image "$DECK_IMAGE:previous"; then prev_id="$(image_id "$DECK_IMAGE:previous")"; fi
  # Gelieferte Tags nodvard-deck:pi-<sha>, außer den Ständen hinter latest/previous.
  for tag in $(docker images "$DECK_IMAGE" --format '{{.Tag}}' 2>/dev/null); do
    case "$tag" in
      pi-*)
        if [ "$(image_id "$DECK_IMAGE:$tag")" != "$latest_id" ] && [ "$(image_id "$DECK_IMAGE:$tag")" != "$prev_id" ]; then
          if docker rmi "$DECK_IMAGE:$tag" >/dev/null 2>&1; then
            say "Altes Image $DECK_IMAGE:$tag entfernt."
          else
            say "Hinweis: $DECK_IMAGE:$tag liess sich nicht entfernen."
          fi
        fi
        ;;
    esac
  done
  say "CLEANUP-OK"
}

cmd_rollback() {
  need_docker
  if has_image "$DECK_IMAGE:previous"; then
    docker tag "$DECK_IMAGE:previous" "$DECK_IMAGE:latest"
    say "Rollback: $DECK_IMAGE:previous -> $DECK_IMAGE:latest."
  fi
  has_image "$DECK_IMAGE:previous" || die "Kein $DECK_IMAGE:previous vorhanden, nichts zum Zurueckschalten."
  compose up -d --no-build
  verify
}

# Stand vor dem Umschalten. Gibt genau eine Zeile aus:
#   none            kein Dashboard-Container läuft (frische Maschine oder aus)
#   needed=false    läuft und ist eingerichtet
#   needed=true     läuft, aber noch nicht eingerichtet
#   http=<code>     läuft und antwortet, kennt den Endpunkt aber nicht (z. B. 404 beim alten Image)
#   noanswer        ein Container läuft, antwortet aber auch nach mehreren Versuchen nicht
cmd_precheck() {
  need_docker
  refuse_pre_rename
  if ! container_running "$CONTAINER"; then say "none"; return 0; fi
  local answer
  answer="$(cmd_bootstrap 5)"
  case "$answer" in
    http=*)
      # Ohne eine Antwort vom Endpunkt zählt als "eingerichtet" nur, wer wirklich das alte Volume hat.
      if [ "$(inspect_container "$CONTAINER" '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')" = "$VOLUME" ]; then
        say "$answer"
      else
        say "noanswer"
      fi
      ;;
    *) say "$answer" ;;
  esac
}

# GET /api/v1/auth/bootstrap, bis zu $1 Versuche (Standard 1) mit Pause BOOTSTRAP_RETRY_SLEEP_S.
cmd_bootstrap() {
  local tries="${1:-1}" i out code body
  for i in $(seq 1 "$tries"); do
    out="$(curl -s -m 3 -w '\n%{http_code}' http://127.0.0.1:8080/api/v1/auth/bootstrap 2>/dev/null || true)"
    code="${out##*$'\n'}"
    body="${out%$'\n'*}"
    if [ -n "$code" ] && [ "$code" != "000" ]; then
      if [ "$code" = "200" ] && printf '%s' "$body" | grep -Eq '"needed": ?false'; then say "needed=false"; return 0; fi
      if [ "$code" = "200" ] && printf '%s' "$body" | grep -Eq '"needed": ?true'; then say "needed=true"; return 0; fi
      say "http=$code"; return 0
    fi
    [ "$i" -lt "$tries" ] && sleep "${BOOTSTRAP_RETRY_SLEEP_S:-3}"
  done
  say "noanswer"
}

# Welche Erweiterungen hat das laufende Dashboard geladen? Jeder Start und jedes Ein- oder Ausschalten danach schreibt
# eine Zeile
#   Erweiterungen geladen: <Speicher-Kennungen, sortiert, durch Komma getrennt>      oder      ... (keine)
# ins Protokoll (backend/src/nodvard_deck/services/extensions.py, `_announce_loaded`; ein Test hält den Text
# gleich), die letzte sagt, was gerade läuft. scripts/deploy_pi.sh vergleicht die Zeile des alten mit der des neuen Containers: Eine Erweiterung,
# die vorher geladen war und nachher fehlt, gilt als Fehler -- auch wenn nichts sonst meldet, dass sie nicht
# läuft (z. B. die Zeile der Erweiterung bleibt "eingeschaltet", der Dienst findet sie aber nicht mehr).
LOADED_MARKER='Erweiterungen geladen: '

# Liest ein Protokoll von stdin, nimmt die LETZTE Zeile mit dem Text (ein Neustart oder ein Ein-/Ausschalten schreibt
# eine neue) und gibt aus:
#   none            keine solche Zeile
#   invalid         die Zeile ist nicht lesbar (keine gültige Liste hinter dem Text)
#   list=<a,b,c>    die Kennungen; `list=` ohne etwas dahinter: es war keine Erweiterung geladen ("(keine)")
# Vor der Zeile darf alles stehen (Zeitstempel, Ebene, Farbcodes). Dahinter muss genau die Liste folgen: Kennungen
# (a-z, 0-9, Bindestrich; Anfang a-z), durch Kommas getrennt, danach Ende der Zeile, Leerraum oder ein Anführungs-/
# Klammerzeichen (falls einmal als JSON geschrieben). Alles andere ist `invalid`, nie eine verkürzte Liste. Nichts davon
# wird je als Befehl oder Muster ausgewertet, es wird nur verglichen.
loaded_from_log() {
  local LC_ALL=C line rest esc re_none re_list
  line="$(grep -a -F -- "$LOADED_MARKER" | tail -n 1)" || true
  if [ -z "$line" ]; then say none; return 0; fi
  esc="$(printf '\033')"
  line="$(printf '%s' "$line" | sed -e "s/${esc}\\[[0-9;]*[A-Za-z]//g")" || true
  rest="${line##*"$LOADED_MARKER"}"
  re_none='^\(keine\)([]}"[:space:]]|$)'
  re_list='^([a-z][a-z0-9-]*(,[a-z][a-z0-9-]*)*)([]}"[:space:]]|$)'
  if [[ "$rest" =~ $re_none ]]; then
    say "list="
  elif [[ "$rest" =~ $re_list ]]; then
    say "list=${BASH_REMATCH[1]}"
  else
    say invalid
  fi
}

# `loaded [sekunden]`: Antwort für das gerade laufende Dashboard, ohne etwas zu verändern. Läuft keins, oder steht
# noch keine Zeile im Protokoll, wird bis zu `sekunden` gewartet (Standard 0 = ein Versuch; die Zeile steht vor dem
# ersten Health-Ok im Protokoll, das Warten fängt nur die Verzögerung des Docker-Protokolls ab), danach `none`. Das
# ganze Protokoll wird gelesen (Docker begrenzt es auf 3 x 10 MB): Der Start liegt oft weit zurück, und jede Anfrage
# steht auch darin.
cmd_loaded() {
  need_docker
  local secs="${1:-0}" answer deadline
  case "$secs" in ''|*[!0-9]*) echo "Nutzung: $0 loaded [sekunden]" >&2; exit 2 ;; esac
  if ! container_running "$CONTAINER"; then say none; return 0; fi
  deadline=$((SECONDS + secs))
  while :; do
    answer="$(docker logs "$CONTAINER" 2>&1 | loaded_from_log || true)"
    [ "$answer" = none ] || break
    [ "$SECONDS" -lt "$deadline" ] || break
    container_running "$CONTAINER" || break
    sleep "${LOADED_POLL_SLEEP_S:-2}"
  done
  say "${answer:-none}"
}

# Wie der Unterbefehl, aber von der ssh-Leitung abgekoppelt: eigene Sitzung (setsid), Ein-/Ausgabe
# in Dateien. Reisst die Verbindung ab, läuft der Schaltschritt auf dem Pi zu Ende (Ergebnis in
# pi_switch.out/.err/.log). Bleibt die Verbindung, wird die Ausgabe am Ende ausgegeben.
cmd_detached() {
  local rc=0 dir="${PI_SWITCH_LOG_DIR:-.}"
  mkdir -p "$dir"
  : > "$dir/pi_switch.out"; : > "$dir/pi_switch.err"
  if command -v setsid >/dev/null 2>&1; then
    setsid -w bash "$SELF" "$@" >"$dir/pi_switch.out" 2>"$dir/pi_switch.err" </dev/null || rc=$?
  else
    bash "$SELF" "$@" >"$dir/pi_switch.out" 2>"$dir/pi_switch.err" </dev/null || rc=$?
  fi
  cat "$dir/pi_switch.out"
  cat "$dir/pi_switch.err" >&2
  return "$rc"
}

case "${1:-}" in
  detached) shift; cmd_detached "$@" ;;
  precheck) cmd_precheck ;;
  bootstrap) shift; cmd_bootstrap "$@" ;;
  loaded) shift; cmd_loaded "$@" ;;
  switch) shift; cmd_switch "$@" ;;
  verify) need_docker; verify ;;
  oldname) need_docker; refuse_pre_rename ;;
  cleanup) cmd_cleanup ;;
  rollback) cmd_rollback ;;
  *) echo "Nutzung: $0 switch <image-tag> | verify | cleanup | rollback | precheck | bootstrap | loaded" >&2; exit 2 ;;
esac
