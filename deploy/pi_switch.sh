#!/usr/bin/env bash
# Schaltschritt auf dem Zielrechner (Pi): neues Image in Betrieb nehmen, Rueckweg sichern,
# pruefen, notfalls zurueckschalten. Wird von scripts/deploy_pi.sh per ssh aufgerufen und
# liegt deshalb im deploy/-Ordner, der ohnehin mitgeschickt wird. Eigene Datei, damit sich
# der Ablauf mit einem nachgebauten `docker` testen laesst (backend/tests/test_deploy_switch.py).
#
#   ./pi_switch.sh switch <image-tag>   # pruefen, sichern, alten Container STOPPEN, compose up, verify
#   ./pi_switch.sh verify               # laeuft der neue Container, mit dem neuen Image und dem alten Volume?
#   ./pi_switch.sh cleanup              # erst NACH verify, Health und Datencheck: alten Container und alte Images entfernen
#   ./pi_switch.sh rollback             # zurueck auf den alten Stand, pruefen
#   ./pi_switch.sh precheck             # Stand VOR dem Umschalten: laeuft ein Dashboard, ist es eingerichtet?
#   ./pi_switch.sh bootstrap [versuche] # needed=false|needed=true|http=<code>|noanswer (GET /api/v1/auth/bootstrap)
#   ./pi_switch.sh loaded [sekunden]    # none|invalid|list=<kennungen>: welche Erweiterungen hat das laufende Dashboard geladen?
#   ./pi_switch.sh detached <unterbefehl> ...   # wie der Unterbefehl, aber von der ssh-Leitung abgekoppelt
#
# Exit 3 = es wurde nichts veraendert (Docker nicht erreichbar, Image fehlt, Compose-Datei
# nicht lesbar); der Aufrufer darf dann NICHT zurueckschalten.
# Exit 4 = der Container hat das richtige Volume, laeuft aber nicht (mehr) oder haengt in der
# Neustart-Schleife. War er vorher schon gesund, kann er die Datenbank bereits umgebaut haben.
#
# Die Namen sind ueberschreibbar (Tests), Standard ist der echte Betrieb:
#   DECK_IMAGE=nodvard-deck  CONTAINER=deploy-nodvard-deck-1
#   OLD_IMAGE=lattice        OLD_CONTAINER=deploy-lattice-1
#   PROJECT=deploy           VOLUME=deploy_lattice_data
#
# Warum es den alten Container gibt: Bis zur Umbenennung hiess der Compose-Dienst `lattice`
# (Container `deploy-lattice-1`, Image `lattice:latest`). Er belegt Port 8080 und muss vor dem
# neuen weg. Er wird aber nur GESTOPPT, nicht geloescht: Scheitert das Hochfahren, startet der
# Rollback genau diesen Container wieder (`docker start`), das geht auch, wenn Compose selbst
# das Problem ist. Geloescht wird er erst in `cleanup`, wenn der neue Stand gesund ist.
# Das Volume (`deploy_lattice_data`) bleibt immer unberuehrt; `verify` prueft, dass der neue
# Container wirklich dieses Volume unter /app/data eingebunden hat (sonst waere es leer).
# Bewusst KEIN `--remove-orphans`: das wirkt auf das ganze Projekt und loescht Container, die
# nicht zu diesem Dienst gehoeren.
set -euo pipefail
cd "$(dirname "$0")"

# Bricht die ssh-Verbindung mitten im Umschalten ab (WLAN, Zeitueberschreitung), bekommt die
# Shell ein SIGHUP. Das hier schuetzt nur die Shell selbst (und wird von den docker-Aufrufen
# geerbt), NICHT vor einem geschlossenen Ausgabe-Rohr (SIGPIPE, `printf` scheitert, docker
# stirbt). Dafuer gibt es `detached`: Ausgabe in Dateien, eigene Sitzung (setsid).
trap '' HUP
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

DECK_IMAGE="${DECK_IMAGE:-nodvard-deck}"
OLD_IMAGE="${OLD_IMAGE:-lattice}"
CONTAINER="${CONTAINER:-deploy-nodvard-deck-1}"
OLD_CONTAINER="${OLD_CONTAINER:-deploy-lattice-1}"
PROJECT="${PROJECT:-deploy}"
VOLUME="${VOLUME:-deploy_lattice_data}"

say() { printf '%s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
die_unchanged() { printf 'FAIL: %s\n' "$*" >&2; exit 3; }

# Projektname immer mitgeben: COMPOSE_PROJECT_NAME in der Umgebung oder in .env wuerde sonst
# ein anderes Projekt (und damit ein anderes, leeres Volume) waehlen.
compose() { docker compose -p "$PROJECT" "$@"; }

has_image() { docker image inspect "$1" >/dev/null 2>&1; }
has_container() { docker container inspect "$1" >/dev/null 2>&1; }
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
inspect_container() { docker container inspect -f "$2" "$1"; }

need_docker() {
  docker info >/dev/null 2>&1 || die_unchanged "Docker ist nicht erreichbar (laeuft der Dienst? Hat dieser Benutzer Docker-Rechte?). Nichts veraendert."
}

# Laeuft der Container gerade (und belegt dann z. B. Port 8080)?
container_running() {
  has_container "$1" && [ "$(inspect_container "$1" '{{.State.Status}}')" = "running" ]
}

# Rueckweg sichern: nodvard-deck:latest -> nodvard-deck:previous. Gibt es das noch nicht
# (erster Uebergang), wird das alte lattice:latest zu nodvard-deck:previous. Wurde dasselbe
# Image schon in Betrieb genommen (erneuter Aufruf nach einem Abbruch), bleibt der Rueckweg,
# wie er ist, damit er nicht mit dem fraglichen Stand ueberschrieben wird.
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
  elif has_image "$OLD_IMAGE:latest"; then
    docker tag "$OLD_IMAGE:latest" "$DECK_IMAGE:previous"
    say "Erster Uebergang: $OLD_IMAGE:latest -> $DECK_IMAGE:previous."
  else
    say "Frische Maschine: kein altes Image, keine Sicherung moeglich."
  fi
}

# Laeuft der Container wirklich (Status running, nicht im Neustart-Kreislauf) und haengt
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

# Traegt der neue Container das Image von nodvard-deck:latest, laeuft er, hat er das richtige
# Volume? Und laeuft der alte Container NICHT mehr? Ein "gesund" von /api/v1/health allein
# beweist das nicht (kann der alte Container sein).
verify() {
  local want have
  if container_running "$OLD_CONTAINER"; then
    die "Der alte Container $OLD_CONTAINER laeuft noch (Waise, belegt vermutlich Port 8080)."
  fi
  want="$(image_id "$DECK_IMAGE:latest")" || die "Image $DECK_IMAGE:latest fehlt."
  has_container "$CONTAINER" || die "Container $CONTAINER existiert nicht."
  have="$(inspect_container "$CONTAINER" '{{.Image}}')"
  [ "$have" = "$want" ] || die "Container $CONTAINER laeuft mit einem anderen Image als $DECK_IMAGE:latest."
  check_container "$CONTAINER"
  say "VERIFY-OK: $CONTAINER laeuft mit $DECK_IMAGE:latest ($want), Volume $VOLUME."
}

cmd_switch() {
  local new_tag="${1:?Nutzung: pi_switch.sh switch <image-tag>}" new_id
  # Alles, was schieflaufen kann, BEVOR etwas angefasst wird (Exit 3 = nichts veraendert).
  need_docker
  has_image "$new_tag" || die_unchanged "Image $new_tag ist auf diesem Rechner nicht vorhanden."
  compose config -q >/dev/null 2>&1 || die_unchanged "Die Compose-Datei ist nicht lesbar (docker compose config scheitert; zu alte Compose-Version?). Nichts veraendert."
  new_id="$(image_id "$new_tag")"
  # Ein gestoppter alter Container, obwohl nodvard-deck:latest schon vor diesem Deploy existierte:
  # der Uebergang war frueher schon gelungen, nur das Aufraeumen ist gescheitert. Er waere beim
  # naechsten Rollback ein uraltes Image -- weg damit.
  if has_image "$DECK_IMAGE:latest" && has_container "$OLD_CONTAINER" && ! container_running "$OLD_CONTAINER"; then
    say "Liegengebliebener alter Container $OLD_CONTAINER (Uebergang war schon gelungen) wird entfernt."
    docker rm -f "$OLD_CONTAINER" >/dev/null 2>&1 || true
  fi
  backup_previous "$new_id"
  docker tag "$new_tag" "$DECK_IMAGE:latest"
  # Alter Container: nur stoppen, nicht loeschen (Rueckweg, siehe Kopf).
  if container_running "$OLD_CONTAINER"; then
    say "Alter Container $OLD_CONTAINER wird gestoppt (bleibt fuer den Rueckweg erhalten)."
    docker stop -t 30 "$OLD_CONTAINER" >/dev/null 2>&1 || true
  fi
  compose up -d --no-build
  verify
}

# Erst aufrufen, wenn verify, Health und Datencheck bestanden sind: den alten Container und
# alte Images entfernen. Das Volume wird nie angefasst.
cmd_cleanup() {
  need_docker
  verify
  if has_container "$OLD_CONTAINER"; then
    say "Alter Container $OLD_CONTAINER wird entfernt."
    docker rm -f "$OLD_CONTAINER" >/dev/null 2>&1 || say "Hinweis: $OLD_CONTAINER liess sich nicht entfernen."
  fi
  local latest_id prev_id="" tag
  latest_id="$(image_id "$DECK_IMAGE:latest")"
  if has_image "$DECK_IMAGE:previous"; then prev_id="$(image_id "$DECK_IMAGE:previous")"; fi
  # Gelieferte Tags nodvard-deck:pi-<sha>, ausser den Staenden hinter latest/previous.
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
  # Alte lattice:*-Tags erst, wenn nodvard-deck:previous ein eigener Stand ist (nicht mehr
  # dasselbe Image wie lattice:latest). Vorher ist lattice:latest der einzige alte Stand.
  if [ -n "$prev_id" ] && { ! has_image "$OLD_IMAGE:latest" || [ "$(image_id "$OLD_IMAGE:latest")" != "$prev_id" ]; }; then
    for tag in $(docker images "$OLD_IMAGE" --format '{{.Tag}}' 2>/dev/null); do
      if docker rmi "$OLD_IMAGE:$tag" >/dev/null 2>&1; then
        say "Altes Image $OLD_IMAGE:$tag entfernt."
      else
        say "Hinweis: $OLD_IMAGE:$tag liess sich nicht entfernen."
      fi
    done
  else
    say "Alte lattice:*-Images bleiben (nodvard-deck:previous ist noch derselbe Stand wie lattice:latest)."
  fi
  say "CLEANUP-OK"
}

cmd_rollback() {
  need_docker
  if has_image "$DECK_IMAGE:previous"; then
    docker tag "$DECK_IMAGE:previous" "$DECK_IMAGE:latest"
    say "Rollback: $DECK_IMAGE:previous -> $DECK_IMAGE:latest."
  fi
  # Den alten Container nur nehmen, wenn er wirklich der Stand hinter nodvard-deck:previous ist.
  # Ein liegen gebliebener aus einem frueheren Deploy waere ein uraltes Image: dann per Compose.
  if has_container "$OLD_CONTAINER" && has_image "$DECK_IMAGE:previous" \
     && [ "$(inspect_container "$OLD_CONTAINER" '{{.Image}}')" = "$(image_id "$DECK_IMAGE:previous")" ]; then
    # Erster Uebergang, noch nicht aufgeraeumt: den neuen Container weg, den alten wieder starten.
    # Braucht weder Compose noch das neue Image.
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker start "$OLD_CONTAINER" >/dev/null
    check_container "$OLD_CONTAINER"
    say "VERIFY-OK: alter Container $OLD_CONTAINER laeuft wieder, Volume $VOLUME."
    return 0
  fi
  has_image "$DECK_IMAGE:previous" || die "Kein $DECK_IMAGE:previous und kein alter Container vorhanden, nichts zum Zurueckschalten."
  compose up -d --no-build
  verify
}

# Stand vor dem Umschalten. Gibt genau eine Zeile aus:
#   none            kein Dashboard-Container laeuft (frische Maschine oder aus)
#   needed=false    laeuft und ist eingerichtet
#   needed=true     laeuft, aber noch nicht eingerichtet
#   http=<code>     laeuft und antwortet, kennt den Endpunkt aber nicht (z. B. 404 beim alten Image)
#   noanswer        ein Container laeuft, antwortet aber auch nach mehreren Versuchen nicht
cmd_precheck() {
  need_docker
  local name running=""
  for name in "$CONTAINER" "$OLD_CONTAINER"; do
    if container_running "$name"; then running="$name"; break; fi
  done
  if [ -z "$running" ]; then say "none"; return 0; fi
  local answer
  answer="$(cmd_bootstrap 5)"
  case "$answer" in
    http=*)
      # Ohne eine Antwort vom Endpunkt zaehlt als "eingerichtet" nur, wer wirklich das alte Volume hat.
      if [ "$(inspect_container "$running" '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')" = "$VOLUME" ]; then
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
# ins Protokoll (backend/src/nodvard_deck/services/extensions.py, `_announce_loaded`; ein Test haelt den Text
# gleich), die letzte sagt, was gerade laeuft. scripts/deploy_pi.sh vergleicht die Zeile des alten mit der des neuen Containers: Eine Erweiterung,
# die vorher geladen war und nachher fehlt, gilt als Fehler -- auch wenn nichts sonst meldet, dass sie nicht
# laeuft (z. B. die Zeile der Erweiterung bleibt "eingeschaltet", der Dienst findet sie aber nicht mehr).
LOADED_MARKER='Erweiterungen geladen: '

# Liest ein Protokoll von stdin, nimmt die LETZTE Zeile mit dem Text (ein Neustart oder ein Ein-/Ausschalten schreibt
# eine neue) und gibt aus:
#   none            keine solche Zeile
#   invalid         die Zeile ist nicht lesbar (keine gueltige Liste hinter dem Text)
#   list=<a,b,c>    die Kennungen; `list=` ohne etwas dahinter: es war keine Erweiterung geladen ("(keine)")
# Vor der Zeile darf alles stehen (Zeitstempel, Ebene, Farbcodes). Dahinter muss genau die Liste folgen: Kennungen
# (a-z, 0-9, Bindestrich; Anfang a-z), durch Kommas getrennt, danach Ende der Zeile, Leerraum oder ein Anfuehrungs-/
# Klammerzeichen (falls einmal als JSON geschrieben). Alles andere ist `invalid`, nie eine verkuerzte Liste. Nichts davon
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

# `loaded [sekunden]`: Antwort fuer das gerade laufende Dashboard (neuer Name vor altem, wie bei `precheck`), ohne
# etwas zu veraendern. Laeuft keins, oder steht noch keine Zeile im Protokoll, wird bis zu `sekunden` gewartet
# (Standard 0 = ein Versuch; die Zeile steht vor dem ersten Health-Ok im Protokoll, das Warten faengt nur die
# Verzoegerung des Docker-Protokolls ab), danach `none`. Das ganze Protokoll wird gelesen (Docker begrenzt es auf
# 3 x 10 MB): der Start liegt oft weit zurueck, und jede Anfrage steht auch darin.
cmd_loaded() {
  need_docker
  local secs="${1:-0}" name="" candidate answer deadline
  case "$secs" in ''|*[!0-9]*) echo "Nutzung: $0 loaded [sekunden]" >&2; exit 2 ;; esac
  for candidate in "$CONTAINER" "$OLD_CONTAINER"; do
    if container_running "$candidate"; then name="$candidate"; break; fi
  done
  if [ -z "$name" ]; then say none; return 0; fi
  deadline=$((SECONDS + secs))
  while :; do
    answer="$(docker logs "$name" 2>&1 | loaded_from_log || true)"
    [ "$answer" = none ] || break
    [ "$SECONDS" -lt "$deadline" ] || break
    container_running "$name" || break
    sleep "${LOADED_POLL_SLEEP_S:-2}"
  done
  say "${answer:-none}"
}

# Wie der Unterbefehl, aber von der ssh-Leitung abgekoppelt: eigene Sitzung (setsid), Ein-/Ausgabe
# in Dateien. Reisst die Verbindung ab, laeuft der Schaltschritt auf dem Pi zu Ende (Ergebnis in
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
  cleanup) cmd_cleanup ;;
  rollback) cmd_rollback ;;
  *) echo "Nutzung: $0 switch <image-tag> | verify | cleanup | rollback | precheck | bootstrap | loaded" >&2; exit 2 ;;
esac
