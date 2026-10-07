#!/usr/bin/env bash
# Nodvard Deck auf einen Raspberry Pi (oder einen anderen Linux-Server) ausliefern, OHNE dort
# zu bauen.
#
# Warum: Der Pi hat 3,7 GB RAM. Ein Docker-Build dort treibt die Last auf 18, laesst
# den Container ueber 90 s lang starten und hat schon zu falschen "DEPLOY-FAIL"-
# Meldungen gefuehrt. Das Image wird deshalb hier auf dem PC gebaut (Docker Desktop
# mit QEMU fuer linux/arm64; die Frontend-Stufe laeuft nativ, siehe deploy/Dockerfile
# `FROM --platform=$BUILDPLATFORM`) und fertig auf den Pi kopiert.
#
# Aufruf aus dem Repository-Root (Git Bash):
#   scripts/deploy_pi.sh                 # HEAD bauen, ausliefern, pruefen
#   scripts/deploy_pi.sh --no-build      # zuletzt gebautes Image erneut ausliefern
#   PI_HOST=admin@192.168.1.50 scripts/deploy_pi.sh
#
# Zielrechner und Optionen: als Umgebungsvariablen ODER (bequemer) einmalig in der
# lokalen, git-ignorierten Datei deploy/.env.deploy (Vorlage: deploy/.env.deploy.example):
#   PI_HOST=benutzer@adresse      # SSH-Ziel, Pflicht (ohne bricht das Skript ab)
#   DEPLOY_ROOT=lattice-deploy-test
#   NODVARD_DECK_DNS_1=... NODVARD_DECK_DNS_2=...   # optional: Resolver fuer den Container,
#                                         # wird als deploy/.env auf dem Ziel abgelegt.
#                                         # Die alten Namen LATTICE_DNS_1/2 gelten weiter;
#                                         # sind beide gesetzt, gewinnt der neue.
#
# Voraussetzung: Die komplette Test-Suite ist gruen (siehe deploy/README.md). Das
# Skript prueft das NICHT selbst, es dauert ~6 Minuten und gehoert vor den Aufruf.
#
# Ablauf: bauen -> docker save | gzip | ssh 'gunzip | docker load' -> auf dem Pi
# `deploy/pi_switch.sh switch` (vorab: Docker erreichbar? Compose-Datei lesbar? Dann altes Image
# als nodvard-deck:previous sichern, beim ersten Uebergang aus lattice:latest; den alten
# Container deploy-lattice-1 nur STOPPEN; compose up --no-build; pruefen, dass der neue Container
# mit dem neuen Image und dem alten Volume laeuft) -> bis zu 4 Minuten auf /api/v1/health
# warten -> Image/Volume-Pruefung wiederholen -> Datencheck (/api/v1/auth/bootstrap muss
# needed=false bleiben, wenn es vorher so war) -> Erweiterungen vergleichen (jeder Start und jedes Ein- oder Ausschalten
# schreibt die Zeile "Erweiterungen geladen: <Kennungen>" ins Protokoll, die letzte zaehlt; fehlt nachher eine
# Erweiterung, die vorher geladen war, ist das ein Fehler wie ein gescheiterter Datencheck, auch wenn sonst nichts
# meldet, dass sie nicht laeuft; kennt der alte Stand die Zeile noch nicht, gibt es nur einen Hinweis) -> Logs auf
# Tracebacks pruefen. Scheitert der Start des neuen Images
# (Migration), antwortet statt der Anwendung die Notseite mit 503 `{"status":"rescue"}`: nie "ok", also Rollback
# (und ohne die volle Wartezeit). Schlaegt etwas
# fehl, wird automatisch zurueckgeschaltet (`pi_switch.sh rollback`) und geprueft, dass der
# alte Stand wieder antwortet, sonst ROLLBACK-FAIL. Ausnahme: Hat der neue Container die Datenbank schon
# umgebaut und war gestartet (oder laesst sich das nicht feststellen), gibt es KEINEN automatischen
# Rollback (das alte Image wuerde die Daten ablehnen), nur DEPLOY-FAIL mit Hinweisen. Erst wenn alles stimmt, raeumt
# `pi_switch.sh cleanup` den alten Container und alte Images weg. Rueckfallweg (tar + Build auf
# dem Pi): deploy/README.md.
set -euo pipefail

# Lokale Einstellungen (git-ignoriert) laden; bereits gesetzte Umgebungsvariablen
# haben Vorrang vor der Datei.
_ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/deploy/.env.deploy"
if [ -f "$_ENV_FILE" ]; then
  while IFS='=' read -r _key _val || [ -n "$_key" ]; do
    _val="${_val%$'\r'}"
    case "$_key" in ''|\#*) continue ;; esac
    _val="${_val%\"}"; _val="${_val#\"}"
    [ -z "${!_key+x}" ] && export "$_key=$_val"
  done < "$_ENV_FILE"
fi

if [ -z "${PI_HOST:-}" ]; then
  echo "FEHLER: PI_HOST ist nicht gesetzt - wohin soll deployt werden?" >&2
  echo "  Einmalig anlegen: deploy/.env.deploy mit der Zeile PI_HOST=benutzer@adresse" >&2
  echo "  (Vorlage: deploy/.env.deploy.example) oder: PI_HOST=benutzer@adresse scripts/deploy_pi.sh" >&2
  exit 1
fi
DEPLOY_ROOT="${DEPLOY_ROOT:-lattice-deploy-test}"   # relativ zum Home des Pi-Nutzers
COMPOSE_DIR="$DEPLOY_ROOT/deploy"
CONTAINER="${CONTAINER:-deploy-nodvard-deck-1}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-240}"
ROLLBACK_HEALTH_TIMEOUT_S="${ROLLBACK_HEALTH_TIMEOUT_S:-120}"
LOADED_WAIT_S="${LOADED_WAIT_S:-30}"   # so lange hoechstens auf die Zeile "Erweiterungen geladen" des neuen Stands warten
BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    -h|--help) sed -n '2,/^set -euo/{/^set -euo/!p;}' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short HEAD)"
LOCAL_TAG="nodvard-deck:pi-$SHA"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$PI_HOST")

say() { printf '\n== %s ==\n' "$*"; }
fail() { echo "DEPLOY-FAIL: $*" >&2; exit 1; }

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Hinweis: Arbeitskopie hat nicht committete Aenderungen; gebaut wird der Arbeitsbaum, nicht $SHA." >&2
fi

say "Ziel erreichbar?"
"${SSH[@]}" 'hostname; cat /proc/loadavg' || fail "Ziel nicht erreichbar ($PI_HOST). PI_HOST gesetzt? (siehe Kopf dieser Datei)"

if [ "$BUILD" = 1 ]; then
  say "Image fuer linux/arm64 auf dem PC bauen ($LOCAL_TAG)"
  docker buildx build --platform linux/arm64 -f deploy/Dockerfile -t "$LOCAL_TAG" --load . \
    || fail "Build fehlgeschlagen."
else
  docker image inspect "$LOCAL_TAG" >/dev/null 2>&1 || fail "Kein lokales Image $LOCAL_TAG; ohne --no-build aufrufen."
fi
ARCH="$(docker image inspect "$LOCAL_TAG" --format '{{.Architecture}}')"
[ "$ARCH" = "arm64" ] || fail "Image ist $ARCH statt arm64."

say "deploy/-Ordner (compose, entrypoint) auf den Pi bringen"
git archive HEAD deploy | "${SSH[@]}" "mkdir -p ~/$DEPLOY_ROOT && tar -x -C ~/$DEPLOY_ROOT" \
  || fail "deploy/ konnte nicht uebertragen werden."

# Neuer Name, sonst alter Name (Umgebung und deploy/.env.deploy sind oben schon gemischt).
DNS_1="${NODVARD_DECK_DNS_1:-${LATTICE_DNS_1:-}}"
DNS_2="${NODVARD_DECK_DNS_2:-${LATTICE_DNS_2:-}}"
if [ -n "$DNS_1" ] || [ -n "$DNS_2" ]; then
  say "DNS-Einstellung fuer den Container auf dem Ziel ablegen (deploy/.env)"
  # BEIDE Namen schreiben: Compose liest den neuen, ein Rueckweg aufs alte Image oder eine
  # alte Compose-Datei den alten.
  printf 'NODVARD_DECK_DNS_1=%s\nNODVARD_DECK_DNS_2=%s\nLATTICE_DNS_1=%s\nLATTICE_DNS_2=%s\n' \
    "${DNS_1:-1.1.1.1}" "${DNS_2:-1.0.0.1}" "${DNS_1:-1.1.1.1}" "${DNS_2:-1.0.0.1}" \
    | "${SSH[@]}" "cat > ~/$COMPOSE_DIR/.env" || fail "deploy/.env konnte nicht geschrieben werden."
fi

say "Image uebertragen (docker save | gzip | ssh | docker load)"
docker save "$LOCAL_TAG" | gzip -1 | "${SSH[@]}" 'gunzip | docker load' || fail "Image-Transfer fehlgeschlagen."

# --- Hilfen fuer den Schaltschritt -----------------------------------------------------------
# Aufruf eines Unterbefehls von deploy/pi_switch.sh auf dem Pi (Schritte dort, getestet).
pi_switch() { "${SSH[@]}" "cd ~/$COMPOSE_DIR && CONTAINER='$CONTAINER' bash pi_switch.sh $*"; }
# Umschalten und Zurueckschalten laufen auf dem Pi von der ssh-Leitung abgekoppelt (setsid, Ausgabe
# in Dateien, Ergebnis danach hier ausgegeben): reisst die Verbindung ab, laeuft der Schritt dort zu
# Ende, statt halb getan zu sterben. Exit 255 = Verbindung abgerissen, Zustand auf dem Pi unklar.
pi_switch_detached() { pi_switch detached "$@"; }

# Antwortet /api/v1/health mit "ok"? Wartet bis zu $1 Sekunden. Setzt HEALTHY=0/1 und RESCUE_SEEN=0/1.
#
# Die Notseite (`nodvard_deck.rescue`, laeuft statt der Anwendung, wenn der Start scheitert) antwortet mit 503
# `{"status":"rescue"}` -- nie mit "ok", das Muster unten trifft sie also nie, und der Rueckweg greift. Sie startet
# aber auch nie von selbst wieder: Meldet sie sich zweimal hintereinander, wird nicht die ganze Wartezeit abgewartet.
wait_health() {
  local limit="$1" i max BODY rescue_polls=0
  max=$((limit / 5)); [ "$max" -ge 1 ] || max=1
  HEALTHY=0
  RESCUE_SEEN=0
  for i in $(seq 1 "$max"); do
    BODY="$("${SSH[@]}" 'curl -s -m 3 localhost:8080/api/v1/health' 2>/dev/null || true)"
    if printf '%s' "$BODY" | grep -q '"status":"ok"'; then
      HEALTHY=1
      echo "healthy nach $((i * 5)) s: $BODY"
      return 0
    fi
    if printf '%s' "$BODY" | grep -q '"status":"rescue"'; then
      RESCUE_SEEN=1
      rescue_polls=$((rescue_polls + 1))
      if [ "$rescue_polls" -ge 2 ]; then
        echo "Notseite erkannt (nach $((i * 5)) s): Nodvard Deck ist nicht normal gestartet und startet auch nicht von selbst neu: $BODY"
        return 1
      fi
    else
      rescue_polls=0
    fi
    [ "$i" -lt "$max" ] && sleep 5
  done
  return 1
}

# Zurueck auf den alten Stand, und PRUEFEN, dass er wirklich wieder antwortet. Endet immer
# mit fail: entweder "Rollback ausgefuehrt" oder ROLLBACK-FAIL.
rollback_and_fail() {
  echo "$1 -- Rollback auf den alten Stand." >&2
  "${SSH[@]}" "docker logs --since 5m $CONTAINER 2>&1 | tail -n 60" >&2 || true
  local rc=0
  pi_switch_detached rollback || rc=$?
  if [ "$rc" = 255 ]; then
    fail "Verbindung zum Pi waehrend des Rollbacks abgerissen, Zustand unklar. Bitte auf dem Pi pruefen: docker ps -a; cat ~/$COMPOSE_DIR/pi_switch.out; bash pi_switch.sh verify"
  fi
  if [ "$rc" = 0 ] && wait_health "$ROLLBACK_HEALTH_TIMEOUT_S"; then
    fail "Rollback ausgefuehrt, der alte Stand laeuft wieder und antwortet."
  fi
  echo "ROLLBACK-FAIL: Der alte Stand laeuft NICHT oder antwortet nicht. Auf dem Pi nachsehen: docker ps -a; bash pi_switch.sh rollback" >&2
  if [ "${RESCUE_SEEN:-0}" = 1 ]; then
    echo "Hinweis: Der alte Stand meldet den Notfallmodus (Notseite). Vermutlich hat das neue Image die Datenbank schon umgebaut, und sie ist fuer die alte Version zu neu." >&2
    echo "         Der Notfallcode steht im Protokoll (docker logs $CONTAINER). Auf der Notseite (Port 8080) laesst sich der Stand von vor dem Update wiederherstellen" >&2
    echo "         (Aenderungen seit dem Update gehen dabei verloren); das klappt ab einer Version, die diese Notseite schon kennt." >&2
  fi
  fail "ROLLBACK-FAIL: Dashboard vermutlich ausser Betrieb."
}

# Stand vor dem Umschalten (auf dem Pi gemessen, bis zu 5 Versuche): Hat der laufende Dienst schon
# Nutzer? Dann muss der neue dieselben Daten sehen. Auf einer frischen Maschine (kein Container
# laeuft) ist "Einrichtung noetig" dagegen richtig. Laeuft ein Dashboard, antwortet aber nicht,
# ist der Stand unklar: dann lieber abbrechen, solange noch nichts veraendert ist.
say "Stand vor dem Umschalten pruefen"
PRE="$(pi_switch precheck)" || fail "Stand vor dem Umschalten nicht pruefbar (ssh/pi_switch.sh precheck gescheitert); es wurde nichts veraendert."
echo "Vorher: ${PRE:-<leer>}"
PRE_NEEDED=""
case "$PRE" in
  needed=false) PRE_NEEDED=false ;;
  http=*) PRE_NEEDED=false; echo "Hinweis: der laufende Dienst kennt /auth/bootstrap nicht ($PRE, altes Image); er laeuft mit dem Volume, die Daten gelten als vorhanden." ;;
  noanswer) fail "Nichts veraendert: Auf dem Pi laeuft ein Dashboard-Container, antwortet aber nicht. Erst klaeren (docker ps, docker logs), damit der Datencheck moeglich ist." ;;
esac

# Antwort von `pi_switch.sh loaded`: nur `none`, `invalid` oder `list=<Kennungen>` mit gueltigen Kennungen (a-z, 0-9,
# Bindestrich, Anfang a-z, durch Komma getrennt; leer = keine). Alles andere gilt als `invalid`, damit nichts
# Unerwartetes aus der Verbindung je weiterverarbeitet oder ausgegeben wird.
clean_loaded_answer() {
  local re='^list=([a-z][a-z0-9-]*(,[a-z][a-z0-9-]*)*)?$'
  case "$1" in
    none|invalid) printf '%s' "$1" ;;
    *) if [[ "$1" =~ $re ]]; then printf '%s' "$1"; else printf 'invalid'; fi ;;
  esac
}

# Kennungen, die in $1 stehen, aber nicht in $2 (beide durch Komma getrennt, nur a-z, 0-9, Bindestrich), durch Komma
# getrennt auf stdout. Wird nur verglichen, nie als Befehl oder Muster ausgewertet; was nicht dem Muster entspricht, zaehlt
# als fehlend (lieber ein Fehlalarm als eine uebersehene Erweiterung).
missing_ids() {
  local before="$1" now=",$2," id out="" ids
  local re='^[a-z0-9,-]*$'
  if [[ ! "$before" =~ $re ]] || [[ ! "$2" =~ $re ]]; then printf '%s' "$before"; return 0; fi
  IFS=, read -r -a ids <<<"$before"
  for id in "${ids[@]}"; do
    case "$now" in *",$id,"*) ;; *) out="${out:+$out,}$id" ;; esac
  done
  printf '%s' "$out"
}

# Migrationsstand nach dem Umschalten: Hat das neue Image die Datenbank umgebaut und ist gestartet (started_ok),
# lehnt das alte Image die Daten ab, und ein automatischer Rollback endete auf der Notseite. Ausgabe von boot_state:
# erste Zeile = Erstellzeit des Containers (UTC), danach .boot/state.json (vom Dienst mit indent=1 und sortierten
# Schluesseln geschrieben: "at" ist die erste Zeile in "last_migration", Schluessel der obersten Ebene haben genau
# ein Leerzeichen Einrueckung). `docker cp` liest das Volume auch, wenn der Container nicht (mehr) laeuft.
# Exit 255 = Verbindung abgerissen.
boot_state() {
  "${SSH[@]}" "docker container inspect -f '{{.Created}}' $CONTAINER 2>/dev/null; docker cp $CONTAINER:/app/data/.boot/state.json - 2>/dev/null | tar -xOf - 2>/dev/null; true" 2>/dev/null
}
mig_at() { sed -n '/"last_migration"/,/}/{/"at"/{s/.*"at": *"\([^"]*\)".*/\1/p;q;}}'; }

# Welche Erweiterungen hat der laufende Dienst geladen (letzte Zeile "Erweiterungen geladen: ..." in seinem Protokoll)?
# Nach dem Umschalten muss jede davon wieder geladen sein. Kennt der alte Stand die Zeile noch nicht (erster Lauf mit
# dieser Pruefung) oder ist sie aus dem Protokoll herausgerollt (Docker behaelt nur 3 x 10 MB), gibt es nichts zu
# vergleichen: dann nur ein Hinweis, kein Fehler. Auf einer frischen Maschine (nichts laeuft) entfaellt es ganz.
PRE_LOADED=""   # leer = kein Vergleich
if [ "$PRE" != none ]; then
  PRE_LOADED_RC=0
  PRE_LOADED_ANSWER="$(pi_switch loaded)" || PRE_LOADED_RC=$?
  if [ "$PRE_LOADED_RC" != 0 ]; then
    fail "Die geladenen Erweiterungen des laufenden Dienstes liessen sich nicht lesen (ssh/pi_switch.sh loaded gescheitert, Code $PRE_LOADED_RC); es wurde nichts veraendert."
  fi
  PRE_LOADED_ANSWER="$(clean_loaded_answer "$PRE_LOADED_ANSWER")"
  case "$PRE_LOADED_ANSWER" in
    list=?*) PRE_LOADED="${PRE_LOADED_ANSWER#list=}"; echo "Vorher geladene Erweiterungen: $PRE_LOADED" ;;
    list=)   echo "Vorher war keine Erweiterung geladen: nichts zu vergleichen." ;;
    invalid) echo "Hinweis: Die Zeile \"Erweiterungen geladen\" im Protokoll des laufenden Dienstes ist nicht lesbar. Ob nachher alle Erweiterungen wieder laufen, wird nicht verglichen." ;;
    *)       echo "Hinweis: Das Protokoll des laufenden Dienstes hat keine Zeile \"Erweiterungen geladen\" (aelterer Stand, oder sie ist herausgerollt). Ob nachher alle Erweiterungen wieder laufen, wird nicht verglichen." ;;
  esac
fi

say "Auf dem Pi umschalten"
# Exit 3 von pi_switch.sh = nichts veraendert (Docker nicht erreichbar, Image fehlt,
# Compose-Datei nicht lesbar): kein Rollback. Sonst ist der Dienst ggf. schon unten.
SWITCH_RC=0
pi_switch_detached switch "'$LOCAL_TAG'" || SWITCH_RC=$?
if [ "$SWITCH_RC" = 3 ]; then
  fail "Umschalten nicht begonnen, es wurde nichts veraendert (Meldung oben)."
elif [ "$SWITCH_RC" = 255 ]; then
  fail "Verbindung zum Pi waehrend des Umschaltens abgerissen. Der Schritt laeuft dort moeglicherweise zu Ende. KEIN automatischer Rollback. Bitte auf dem Pi pruefen: docker ps -a; cat ~/$COMPOSE_DIR/pi_switch.out; bash pi_switch.sh verify"
elif [ "$SWITCH_RC" != 0 ]; then
  rollback_and_fail "Umschalten ist fehlgeschlagen"
fi

say "Auf Health warten (bis $HEALTH_TIMEOUT_S s)"
HEALTH_WAIT_STARTED=$SECONDS
wait_health "$HEALTH_TIMEOUT_S" || true
if [ "${RESCUE_SEEN:-0}" = 1 ] && [ "$HEALTHY" != 1 ]; then
  echo "Die Notseite von Nodvard Deck antwortet statt der Anwendung: Der Start des neuen Images ist gescheitert (z. B. die Migration)." >&2
  echo "Ein Grund steht im Protokoll auf dem Pi: docker logs $CONTAINER." >&2
fi

# "gesund" allein reicht nicht (koennte ein alter Container sein, oder ein leeres Volume):
# Der Container muss das neue Image tragen, laufen und das alte Volume haben ...
# CRASHED=1 (Exit 4): richtiges Volume, aber nach dem Health-Ok nicht mehr laufend (Absturz, Neustart-Schleife).
IMAGE_OK=0
CRASHED=0
if [ "$HEALTHY" = 1 ]; then
  VERIFY_RC=0
  pi_switch verify || VERIFY_RC=$?
  if [ "$VERIFY_RC" = 0 ]; then
    IMAGE_OK=1
  elif [ "$VERIFY_RC" = 4 ]; then
    CRASHED=1
  elif [ "$VERIFY_RC" = 255 ]; then
    fail "Verbindung zum Pi bei der Image/Volume-Pruefung abgerissen, der Zustand ist unklar. KEIN automatischer Rollback. Bitte auf dem Pi pruefen: bash pi_switch.sh verify; docker ps -a"
  fi
fi

# Hat dieser Container die Datenbank umgebaut (Migration nicht aelter als der Container) und ist gestartet?
# BOOT_UNKNOWN=1: nicht feststellbar (Verbindung abgerissen oder state.json nicht lesbar).
MIGRATED=0
MIG_AFTER=""
BOOT_UNKNOWN=0
if [ "$HEALTHY" = 1 ]; then
  BOOT_RC=0
  BOOT_AFTER="$(boot_state)" || BOOT_RC=$?
  if [ "$BOOT_RC" = 255 ]; then
    sleep 2
    BOOT_RC=0
    BOOT_AFTER="$(boot_state)" || BOOT_RC=$?
  fi
  CREATED="$(head -n 1 <<<"$BOOT_AFTER")"
  MIG_AFTER="$(mig_at <<<"$BOOT_AFTER")"
  TOP_STARTED=$'\n "started_ok": '
  if [ "$BOOT_RC" = 255 ] || [ -z "$CREATED" ] || [[ "$BOOT_AFTER" != *"$TOP_STARTED"* ]]; then
    BOOT_UNKNOWN=1
  elif [ -n "$MIG_AFTER" ] && [[ ! "${MIG_AFTER:0:19}" < "${CREATED:0:19}" ]] && [[ "$BOOT_AFTER" == *"${TOP_STARTED}true"* ]]; then
    MIGRATED=1
  fi
fi

# ... und die Daten muessen da sein: war der Dienst vorher eingerichtet, darf er es jetzt
# nicht als "Einrichtung noetig" melden.
DATA_OK=1
if [ "$HEALTHY" = 1 ] && [ "$PRE_NEEDED" = false ]; then
  BOOTSTRAP_RC=0
  POST="$(pi_switch bootstrap 3 2>/dev/null)" || BOOTSTRAP_RC=$?
  if [ "$BOOTSTRAP_RC" = 255 ]; then
    fail "Verbindung zum Pi beim Datencheck abgerissen, der Zustand ist unklar. KEIN automatischer Rollback. Bitte auf dem Pi pruefen: bash pi_switch.sh bootstrap; docker logs --since 5m $CONTAINER"
  fi
  if [ "$POST" != "needed=false" ]; then
    DATA_OK=0
    echo "Datencheck: /api/v1/auth/bootstrap meldet ${POST:-<keine Antwort>}, vorher war der Dienst eingerichtet -- die Daten fehlen?" >&2
  else
    echo "Datencheck ok: needed=false wie vorher."
  fi
fi

# Erweiterungen: Jede, die vorher geladen war, muss auch im neuen Container geladen sein (neue dazu sind in Ordnung).
# Die Zeile steht schon vor dem ersten Health-Ok im Protokoll; gewartet wird nur auf die Verzoegerung des Docker-Protokolls:
# hoechstens LOADED_WAIT_S, nie ueber den Rest der Health-Wartezeit hinaus (mindestens 5 s). Fehlt die Zeile ganz, obwohl der alte
# Stand eine hatte, gilt das wie eine fehlende Erweiterung: Der Vergleich ist dann nicht moeglich, und genau so sieht ein
# Stand aus, der seine Erweiterungen nicht mehr meldet.
LOADED_OK=1
LOADED_MISSING=""
if [ "$HEALTHY" = 1 ] && [ "$IMAGE_OK" = 1 ] && [ "$DATA_OK" = 1 ] && [ -n "$PRE_LOADED" ]; then
  LOADED_LEFT=$((HEALTH_TIMEOUT_S - (SECONDS - HEALTH_WAIT_STARTED)))
  [ "$LOADED_LEFT" -ge 5 ] || LOADED_LEFT=5
  LOADED_SECS="$LOADED_WAIT_S"
  [ "$LOADED_SECS" -le "$LOADED_LEFT" ] || LOADED_SECS="$LOADED_LEFT"
  POST_LOADED_RC=0
  POST_LOADED="$(pi_switch loaded "$LOADED_SECS")" || POST_LOADED_RC=$?
  if [ "$POST_LOADED_RC" != 0 ]; then
    fail "Die geladenen Erweiterungen des neuen Stands liessen sich nicht lesen (Verbindung abgerissen? Code $POST_LOADED_RC), der Zustand ist unklar. KEIN automatischer Rollback. Bitte auf dem Pi pruefen: bash pi_switch.sh loaded; docker logs $CONTAINER 2>&1 | grep 'Erweiterungen geladen'; bash pi_switch.sh verify"
  fi
  POST_LOADED="$(clean_loaded_answer "$POST_LOADED")"
  case "$POST_LOADED" in
    list=*)
      LOADED_MISSING="$(missing_ids "$PRE_LOADED" "${POST_LOADED#list=}")"
      if [ -n "$LOADED_MISSING" ]; then
        LOADED_OK=0
        echo "Erweiterungen: Vorher geladen, jetzt nicht mehr: $LOADED_MISSING (vorher: $PRE_LOADED; jetzt: ${POST_LOADED#list=})." >&2
        echo "  Soll eine davon mit dem neuen Stand bewusst wegfallen: sie erst in den Einstellungen ausschalten, dann das Deploy wiederholen." >&2
      else
        echo "Erweiterungen ok: alles, was vorher geladen war, ist wieder geladen ($PRE_LOADED)."
      fi
      ;;
    *)
      LOADED_OK=0
      LOADED_MISSING="$PRE_LOADED"
      echo "Erweiterungen: Der neue Stand meldet keine lesbare Zeile \"Erweiterungen geladen\" (Antwort: ${POST_LOADED:-<keine>}), der alte meldete: $PRE_LOADED. Ein Vergleich ist nicht moeglich." >&2
      ;;
  esac
fi

TRACEBACKS=0
if [ "$HEALTHY" = 1 ] && [ "$IMAGE_OK" = 1 ] && [ "$DATA_OK" = 1 ]; then
  TRACEBACKS="$("${SSH[@]}" "docker logs --since 5m $CONTAINER 2>&1 | grep -c Traceback || true")" || TRACEBACKS=unbekannt
  case "$TRACEBACKS" in
    ''|*[!0-9]*)
      fail "Die Log-Pruefung auf dem Pi ist gescheitert (Verbindung abgerissen?), der Zustand ist unklar. KEIN automatischer Rollback. Bitte auf dem Pi pruefen: docker logs --since 5m $CONTAINER | grep -c Traceback; bash pi_switch.sh verify" ;;
  esac
fi

if [ "$HEALTHY" != 1 ] || [ "$IMAGE_OK" != 1 ] || [ "$DATA_OK" != 1 ] || [ "$LOADED_OK" != 1 ] || [ "${TRACEBACKS:-0}" -gt 0 ]; then
  # Nur wenn der neue Container mit richtigem Image und Volume lief (IMAGE_OK) oder danach abgestuerzt ist (CRASHED):
  # bei falschem Image oder Volume sind die alten Daten unberuehrt, der Rollback ist sicher.
  if [ "$HEALTHY" = 1 ] && { [ "$IMAGE_OK" = 1 ] || [ "$CRASHED" = 1 ]; }; then
    PROBLEM="Datencheck=$DATA_OK, Tracebacks=${TRACEBACKS:-0}, Erweiterungen=$LOADED_OK"
    [ "$LOADED_OK" = 1 ] || PROBLEM="$PROBLEM (fehlen: $LOADED_MISSING)"
    [ "$CRASHED" = 1 ] && PROBLEM="laeuft nach dem Health-Ok nicht mehr: abgestuerzt oder Neustart-Schleife"
    BACK="Zurueck nur bewusst: bash pi_switch.sh rollback, danach auf der Notseite (Port 8080) den Stand vor dem Update wiederherstellen; der Notfallcode steht im Protokoll (docker logs $CONTAINER). Aenderungen seit dem Update gehen dabei verloren. Das klappt nur, wenn die alte Version (nodvard-deck:previous) die Notseite schon kennt; sonst die neue Version laufen lassen und den Fehler dort beheben."
    if [ "$BOOT_UNKNOWN" = 1 ]; then
      fail "Neuer Stand meldet Probleme ($PROBLEM), ob er die Datenbank schon umgebaut hat, liess sich aber nicht feststellen (.boot/state.json nicht lesbar oder Verbindung abgerissen). Zustand unklar, KEIN automatischer Rollback. Bitte auf dem Pi pruefen: docker ps -a; docker cp $CONTAINER:/app/data/.boot/state.json - | tar -xOf -; docker logs --since 10m $CONTAINER. $BACK"
    fi
    if [ "$MIGRATED" = 1 ]; then
      fail "Neuer Stand meldet Probleme ($PROBLEM), hat die Datenbank aber schon umgebaut und war gestartet (Migration $MIG_AFTER). KEIN automatischer Rollback: das alte Image wuerde die Daten ablehnen und nur die Notseite zeigen. Bitte pruefen: docker ps -a; docker logs --since 10m $CONTAINER. $BACK"
    fi
  fi
  rollback_and_fail "Neuer Stand ist nicht in Ordnung (healthy=$HEALTHY, Image/Volume-Pruefung=$IMAGE_OK, Datencheck=$DATA_OK, Tracebacks=$TRACEBACKS, Erweiterungen=$LOADED_OK${LOADED_MISSING:+, fehlen: $LOADED_MISSING})"
fi

say "Aufraeumen (alter Container, alte Images)"
# Erst jetzt, wo alles geprueft ist. Ein Fehler hier laesst den Deploy nicht scheitern.
pi_switch cleanup || echo "Hinweis: Aufraeumen auf dem Pi war nicht vollstaendig (pi_switch.sh cleanup)." >&2
"${SSH[@]}" "docker image prune -f >/dev/null; docker images nodvard-deck --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}'; cat /proc/loadavg; free -m | sed -n 2p"

echo
echo "DEPLOY-OK: $SHA laeuft auf $PI_HOST (Rollback: nodvard-deck:previous)."
