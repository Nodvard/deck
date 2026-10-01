#!/bin/sh
# Warum ein Entrypoint: Das Image kopiert Alembic (backend/alembic.ini, backend/migrations/)
# zwar mit, uvicorn allein fuehrt die Migration aber nie aus -- ein frisches /app/data-Volume
# hat keine Tabellen, der allererste Zugriff (`ensure_builtin_roles()` in main.py's lifespan)
# scheitert mit "no such table: roles", der Prozess stirbt. Im Entwicklungsbetrieb faellt das
# nicht auf, wenn `alembic upgrade head` dort von Hand VOR `uvicorn` laeuft.
# Ein Container muss das selbst koennen -- deshalb dieser Entrypoint statt eines
# direkten `CMD uvicorn ...`.
set -eu

# --- Besitzrechte am Datenordner, dann vom Benutzer root zu `lattice` wechseln ----------------------
#
# Das Image startet als root (kein `USER` im Dockerfile), damit dieses Skript den Datenordner
# /app/data in Ordnung bringen kann: Ein eingebundener NAS-/Host-Ordner gehoert oft root oder
# einem anderen Benutzer, dann koennte `lattice` (UID 1000) dort nichts schreiben ("unable to open
# database file"). Danach startet dieses Skript sich SELBST als `lattice` neu (setpriv, Teil von
# util-linux, im Basis-Image vorhanden) -- Migration und Anwendung laufen also nie als root.
#
# Sicherheit:
#  - chown NUR auf /app/data (fest eingetragen, nicht aus einer Umgebungsvariable), und nur auf
#    das, was dort nicht schon `lattice` gehoert. `find` folgt keinen Links und bleibt mit -xdev auf
#    dem Dateisystem des Ordners; `chown -h` aendert bei einem Link den Link selbst, nie sein Ziel.
#    Ein Link aus dem Datenordner heraus kann root also nicht auf andere Dateien lenken.
#  - Der Wechsel geschieht VOR `python -m nodvard_deck.boot` und vor dem `exec` der Anwendung.
#    setpriv setzt Benutzer und Gruppe, leert die Zusatzgruppen und verbietet neue Rechte
#    (no-new-privs, z. B. durch setuid-Programme).
#  - Klappt der Wechsel nicht (z. B. wegen `cap_drop: ALL`), bricht das Skript ab, statt als root
#    weiterzulaufen. Dasselbe, wenn es nach dem Wechsel noch root waere.
#  - Laeuft der Container schon als Nicht-root (`user:` in Compose, Kubernetes), wird nichts
#    geaendert: kein chown, kein Wechsel, Verhalten wie bisher.
if [ "$(id -u)" = "0" ]; then
  # Nummern des Image-Benutzers; der Rueckfall 1000 ist die feste UID/GID aus dem Dockerfile.
  uid="$(id -u lattice 2>/dev/null || echo 1000)"
  gid="$(id -g lattice 2>/dev/null || echo 1000)"
  case "$uid$gid" in
    *[!0-9]*) echo "FEHLER: ungueltige Benutzernummer fuer lattice ($uid:$gid)." >&2; exit 1 ;;
  esac
  if [ "$uid" = "0" ] || [ "$gid" = "0" ]; then
    echo "FEHLER: Der Benutzer lattice darf nicht root sein ($uid:$gid)." >&2
    exit 1
  fi
  if [ "${NODVARD_DECK_PRIV_DROPPED:-}" = "1" ]; then
    echo "FEHLER: Der Wechsel vom Benutzer root zu lattice hat nicht gewirkt; ich laufe nicht als root weiter." >&2
    exit 1
  fi

  if [ -d /app/data ]; then
    find /app/data -xdev \( ! -user "$uid" -o ! -group "$gid" \) -exec chown -h "$uid:$gid" {} + || {
      echo "Warnung: Die Besitzrechte an /app/data liessen sich nicht setzen (NAS mit eingeschraenkten Rechten?)." >&2
      echo "         Gib dem Benutzer mit der Nummer $uid Schreibrechte auf den Ordner, oder starte den Container mit user: \"$uid:$gid\"." >&2
    }
  fi

  # Probelauf: Wechsel moeglich? Sonst klar abbrechen, nie als root weitermachen.
  if ! setpriv --reuid="$uid" --regid="$gid" --clear-groups --no-new-privs true; then
    echo "FEHLER: Der Wechsel zum Benutzer lattice ist nicht moeglich (fehlen dem Container Rechte, z. B. durch cap_drop?)." >&2
    echo "        Starte den Container stattdessen direkt als dieser Benutzer: in der Compose-Datei user: \"$uid:$gid\" eintragen" >&2
    echo "        (und dem Ordner /app/data selbst diesem Benutzer zuweisen)." >&2
    exit 1
  fi
  export NODVARD_DECK_PRIV_DROPPED=1
  exec setpriv --reuid="$uid" --regid="$gid" --clear-groups --no-new-privs "$0" "$@"
fi

cd /app

# --- Start: erst `nodvard_deck.boot`, scheitert das, die Notseite -- nie eine Neustart-Schleife --------------------------
#
# `python -m nodvard_deck.boot` spielt zuerst eine vorgemerkte Wiederherstellung ein (Einstellungen -> System ->
# Wiederherstellen, der Assistent oder `python -m nodvard_deck.admin restore-backup`), legt dann vor jeder Migration
# eine Kopie der Datenbank an und fuehrt die Migration aus ("alembic upgrade heads": Kern UND jede Erweiterung mit
# eigenem `migrations/versions`-Ordner, siehe nodvard_deck/migrate.py). Beides laeuft schon als Benutzer `lattice` (siehe oben).
#
# Frueher brach `set -e` hier den Start ab; mit einer Neustart-Regel (`restart: unless-stopped`) startete Docker den
# Container dann endlos neu -- und jedes Mal lief die gescheiterte Migration wieder an. Jetzt:
#   * boot gelingt (0)           -> die Anwendung startet (`exec "$@"`).
#   * boot meldet 75             -> der Datenordner gehoert einem anderen Prozess (die laufende Anwendung, z. B. bei einem
#                                   `compose run` neben dem laufenden Container). Es wurde nichts veraendert: einfach
#                                   mit 75 beenden, KEINE Notseite.
#   * boot scheitert (sonst)     -> `python -m nodvard_deck.rescue`: die Notseite (nur Standardbibliothek) antwortet auf dem
#                                   Port der Anwendung, `/api/v1/health` mit 503. Sie ersetzt die Anwendung, bis jemand
#                                   mit dem Notfallcode "neu versuchen" waehlt (Ende mit 75: der Container endet und
#                                   startet per Restart-Regel neu).
#   * Notseite faellt aus        -> der Container wartet (`exec sleep`), er beendet sich NICHT: eine Schleife aus
#                                   Absturz und Neustart waere schlimmer als ein stehender Container, den man am
#                                   Protokoll erkennt.
# Die Notseite laeuft als Kind (nicht per `exec`), damit dieses Skript bei Nichtstarten noch auf `sleep` ausweichen kann;
# SIGTERM (`docker stop`) gibt es an sie weiter.
boot_rc=0
python -m nodvard_deck.boot || boot_rc=$?
if [ "$boot_rc" -ne 0 ]; then
  if [ "$boot_rc" -eq 75 ]; then
    echo "Der Datenordner wird gerade von einem anderen Prozess benutzt (laeuft Nodvard Deck schon mit diesem Datenordner?). Es wurde nichts veraendert." >&2
    exit 75
  fi
  echo "FEHLER: Der Start ist gescheitert (Code $boot_rc). Statt der Anwendung laeuft jetzt die Notseite; der Notfallcode steht weiter unten im Protokoll." >&2

  # Port und Adresse der Anwendung aus dem Startbefehl (`uvicorn ... --host H --port P`, sonst wie uvicorn `UVICORN_HOST`);
  # nur Zahlen bzw. Adresszeichen -- nie wird etwas aus den Argumenten ausgefuehrt. Fehlt die Adresse oder ist sie nicht
  # erkennbar (Hostname), lauscht die Notseite nur lokal (127.0.0.1, wie uvicorn ohne --host): lieber zu eng als ins
  # ganze Netz, wenn die Anwendung absichtlich nur lokal lief. Das Image startet mit `--host 0.0.0.0`.
  rescue_host="${UVICORN_HOST:-127.0.0.1}"
  rescue_port=8080
  prev=""
  for arg in "$@"; do
    case "$prev" in
      --port) rescue_port="$arg" ;;
      --host) rescue_host="$arg" ;;
    esac
    case "$arg" in
      --port=*) rescue_port="${arg#--port=}" ;;
      --host=*) rescue_host="${arg#--host=}" ;;
    esac
    prev="$arg"
  done
  case "$rescue_port" in ''|*[!0-9]*) rescue_port=8080 ;; esac
  case "$rescue_host" in ''|*[!0-9A-Fa-f:.]*) rescue_host=127.0.0.1 ;; esac

  NODVARD_DECK_BOOT_EXIT="$boot_rc"
  export NODVARD_DECK_BOOT_EXIT
  python -m nodvard_deck.rescue --host "$rescue_host" --port "$rescue_port" &
  rescue_pid=$!
  trap 'kill -TERM "$rescue_pid" 2>/dev/null || true' TERM INT HUP
  rescue_rc=0
  while :; do
    # `wait` kehrt auch bei einem Signal zurueck: dann nachsehen, ob die Notseite wirklich beendet ist.
    wait "$rescue_pid" && rescue_rc=0 || rescue_rc=$?
    kill -0 "$rescue_pid" 2>/dev/null || break
  done
  trap - TERM INT HUP
  case "$rescue_rc" in
    75) exit 75 ;;                    # "neu versuchen": Container endet, die Restart-Regel startet ihn neu
    0|129|130|143) exit 0 ;;          # ordentlich beendet (docker stop)
  esac
  echo "Notseite nicht startbar (Code $rescue_rc) -- der Container bleibt stehen statt in eine Neustart-Schleife zu laufen." >&2
  echo "Die Ursache steht im Protokoll weiter oben. Zum Neu-Versuch den Container von Hand neu starten." >&2
  exec sleep 2147483647
fi

exec "$@"
