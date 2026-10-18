#!/bin/sh
# Hilfsskript des Moduls "Nodvard Shield" fuer Befehle, die root brauchen. Die Einrichtung legt es root-eigen unter
# /usr/local/lib/nodvard-deck/shield-root ab; per sudo darf das Dashboard nur dieses Skript statt aller Befehle. Jeder
# Unterbefehl prueft seine Argumente selbst, kein eval, kein fremder Text in "sh -c". Jede Aenderung braucht eine
# hoehere HELPER_VERSION und dieselbe Zahl unter root_helper in extension.toml.
#
# Unterbefehle, je wie der genannte Befehl des Moduls (in Klammern: ab Version):
#   version                                HELPER_VERSION
#   guard <dateien...> -- <programme...>   intrusion.guard_command (2); absolute Pfade ohne "..", * nur in Dateien
#   detect-templates                       watch_templates.DETECT_SCRIPT (2)
#   refresh-packages                       Teil @@refresh von updates.status_command (2)
#   quarantine <datei> <name>              antivirus.quarantine_command (3); ab 8 nur echte Funde (confirm_finding,
#                                          geprueft in $QDIR/.pruefen), Ursprung und Rechte in $QDIR/.herkunft/<name>
#   restore <name>                         an den Ort aus $QDIR/.herkunft, sonst wie antivirus.restore_command (8)
#   delete <name>                          antivirus.delete_command (3)
#   quarantine-state <name>                antivirus.quarantine_state_command (14); nur lesend
#   ban|unban <ip> <jail>                  intrusion.ban_command (3)
#   install <paket>                        antivirus.INSTALL_COMMANDS[<paket>] (3)
#   reboot                                 updates.REBOOT_COMMAND (4)
#   upgrade-start|-run|-poll <lauf-id> ... detached.launch_command, wrapper_command und poll_command mit
#                                          updates.upgrade_command (4); Pakete nur bei apt und "security"
#   audit-start|-run|-poll|-stop <lauf-id> dasselbe mit audit_job.lynis_command, poll_command, stop_command (5)
#   scan <marke> <limit> <warten> <max-mb> [<ausnahme>...] -- <pfad>...
#                                          antivirus.guard_command mit build_scan_command (6)
#   watch <marke> <limit> <minuten> <max-mb> 0|1 [<ordner>...] -- <pfad>...
#                                          antivirus.guard_command mit build_watch_command (6); 1: ueber clamd
#   scan-run, watch-run, odd-scan          Teile davon unter Sperre und Zeitlimit
#   realtime-on <max-mb> [<ausnahme>...] -- <ordner>...
#                                          realtime.enable_command (11); Pfade absolut, ohne Zeilenumbruch und ".."
#   realtime-off                           realtime.disable_command (11)
#   realtime-log <zeile>                   ob clamonacc laeuft, dazu sein Protokoll nach <zeile>: realtime.log_command (12)
#   realtime-confirm <datei>...            bis zu 50 Dateien ueber clamd pruefen: realtime.confirm_command (12)
# Meldungen als "@@msg=<kennung>" (nur ASCII); das Modul setzt denselben Satz ein (antivirus.HELPER_MESSAGES). Die nur
# lesenden Unterbefehle laufen wie die Befehle des Moduls ohne "set -e" weiter, wenn ein Programm fehlt.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL
# Leerzeichen, Tabulator, Zeilenumbruch: nichts aus der Umgebung des Aufrufers.
IFS=' 	
'
umask 022

HELPER_VERSION=14
QDIR=/var/lib/nexus-quarantine
JOBDIR=/var/lib/nexus-updates
SELF=/usr/local/lib/nodvard-deck/shield-root
SDMARK=/run/systemd/system
AUDITDIR=/var/lib/nodvard-shield-audit
LYNIS_REPORT=/var/log/lynis-report.dat
LYNIS_PIDS='/run/lynis.pid /var/run/lynis.pid'
LYNIS_LIMIT=10800
LOCKDIR=/run/nodvard-shield
TMPRUN=/run
ETCDIR=/etc
RT_WAIT=120
RT_SETTLE=10
RT_CONF=$ETCDIR/clamav/nodvard-onaccess.conf
RT_WATCH=$ETCDIR/clamav/nodvard-onaccess.watch
RT_EXCLUDE=$ETCDIR/clamav/nodvard-onaccess.exclude
RT_STATE=$ETCDIR/clamav/nodvard-onaccess.state
RT_DROPIN=$ETCDIR/systemd/system/clamav-clamonacc.service.d/nodvard.conf
RT_LOG=/var/log/clamav/nodvard-onaccess.log
RT_BATCH=2000
RT_NL='
'
RT_CR=$(printf '\r')

die() {
    printf '%s\n' "shield-root: $1" >&2
    exit 64
}

check_path() {
    case $1 in
        /*) ;;
        *) die "ungueltiger Pfad" ;;
    esac
    case $1 in
        *[!A-Za-z0-9_./*@+-]* | *..*) die "ungueltiger Pfad" ;;
    esac
}

guard() {
    files=
    bins=
    in_bins=0
    for a in "$@"; do
        if [ "$in_bins" -eq 0 ] && [ "$a" = -- ]; then
            in_bins=1
            continue
        fi
        check_path "$a"
        if [ "$in_bins" -eq 0 ]; then
            files="$files $a"
        else
            case $a in
                *'*'*) die "ungueltiger Pfad" ;;
            esac
            bins="$bins $a"
        fi
    done
    [ "$in_bins" -eq 1 ] || die "guard erwartet Dateien, dann --, dann Programme"
    set +eu
    echo @@uid
    id -u
    echo @@ssh
    L=$(journalctl -t sshd -t sshd-session -t sshd-auth --since '24 hours ago' --no-pager -q -o short-unix 2>/dev/null)
    if [ -z "$L" ]; then L=$(tail -q -n 20000 /var/log/auth.log /var/log/secure 2>/dev/null); fi
    printf '%s\n' "$L" | grep -E 'Failed password|Failed publickey|Invalid user|Accepted |authenticating user' | tail -n 8000
    echo @@f2b
    if command -v fail2ban-client >/dev/null 2>&1; then
        o=$(fail2ban-client ping 2>&1)
        rc=$?
        if [ "$rc" -eq 0 ]; then
            echo running
            for j in $(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' '); do
                echo "@@jail $j"
                fail2ban-client status "$j" 2>/dev/null
            done
        elif printf '%s' "$o" | grep -qiE 'permission denied|you must be root'; then
            echo noaccess
        else
            echo stopped
        fi
    else
        echo none
    fi
    echo @@sshd
    (sshd -T 2>/dev/null || /usr/sbin/sshd -T 2>/dev/null) | grep -E \
        '^(port|permitrootlogin|passwordauthentication|permitemptypasswords|pubkeyauthentication|maxauthtries|x11forwarding|kbdinteractiveauthentication) '
    echo @@range
    cat /proc/sys/net/ipv4/ip_local_port_range 2>/dev/null
    echo @@ports
    (ss -H -tulnp 2>/dev/null || ss -tulnp 2>/dev/null | tail -n +2) | grep -E '^(tcp|udp)[[:space:]]'
    echo @@files
    # Ohne Anfuehrungszeichen: Die Shell loest die Platzhalter auf (die Pfade sind oben geprueft).
    for f in $files; do
        [ -f "$f" ] || continue
        echo "$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1) $(stat -c '%a %U %Y %s' "$f" 2>/dev/null) - $f"
    done
    echo @@bins
    for f in $bins; do
        [ -f "$f" ] || continue
        v=-
        if command -v dpkg >/dev/null 2>&1; then
            r=$(readlink -f "$f")
            p=$(dpkg -S "$r" 2>/dev/null | head -n 1 | cut -d: -f1)
            [ -z "$p" ] && p=$(dpkg -S "$f" 2>/dev/null | head -n 1 | cut -d: -f1)
            if [ -n "$p" ]; then
                m=$(md5sum "$f" | cut -d' ' -f1)
                if grep -qE "^$m  (${r#/}|${f#/})$" /var/lib/dpkg/info/$p.md5sums /var/lib/dpkg/info/$p:*.md5sums 2>/dev/null; then
                    v=pkg-ok
                else
                    v=pkg-mismatch
                fi
            fi
        fi
        echo "$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1) $(stat -c '%a %U %Y %s' "$f" 2>/dev/null) $v $f"
    done
    echo @@end
}

detect_templates() {
    [ "$#" -eq 0 ] || die "detect-templates erwartet keine Argumente"
    set +eu
    echo @@samba
    grep -iE '^[[:space:]]*(\[[^]]*\]|(path|directory)[[:space:]]*=)' /etc/samba/smb.conf 2>/dev/null | head -n 300
    echo @@nfs
    grep -vE '^[[:space:]]*(#|$)' /etc/exports 2>/dev/null | head -n 50
    echo @@nextcloud
    for f in /var/www/nextcloud/config/config.php /var/www/html/nextcloud/config/config.php /var/www/html/config/config.php; do
        sed -n "s/^[[:space:]]*'datadirectory'[[:space:]]*=>[[:space:]]*'\([^']*\)'.*/\1/p" "$f" 2>/dev/null
    done | head -n 10
    for d in /var/lib/docker/volumes/*nextcloud*/_data /var/lib/docker/volumes/*nextcloud*/_data/data; do
        for a in "$d"/appdata_*; do
            [ -d "$a" ] && echo "$d"
            break
        done
    done 2>/dev/null | head -n 10
    echo @@end
}

refresh_packages() {
    [ "$#" -eq 0 ] || die "refresh-packages erwartet keine Argumente"
    set +eu
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq 2>&1 | grep -E '^(E|Err|W):' | head -n 20
    elif command -v dnf >/dev/null 2>&1; then
        dnf -q makecache 2>&1 | tail -n 5
    elif command -v apk >/dev/null 2>&1; then
        apk update -q 2>&1 | tail -n 5
    fi
}

# Namen mit Punkt am Anfang sind fuer die Ablage der Herkunft reserviert ($QDIR/.herkunft).
check_qname() {
    case $1 in
        "" | .* | *[!A-Za-z0-9._-]*) die "ungueltiger Name in der Quarantaene" ;;
    esac
}

# Prueft die Datei im aktuellen Ordner selbst: 0, wenn ClamAV etwas findet, 1 ohne Fund, 2, wenn nicht pruefbar. Zuerst
# ueber clamd (schnell, mit dessen Einstellungen), sonst und ohne Fund dort mit clamscan ohne Groessengrenze unter 4 GB:
# der Fund kann aus einem Scan mit hoeherer Grenze stammen als der von clamd.
confirm_finding() {
    if clamdscan --ping 1 >/dev/null 2>&1; then
        R=0
        clamdscan --fdpass --no-summary "$1" >/dev/null 2>&1 || R=$?
        [ "$R" -eq 1 ] && return 0
    fi
    R=0
    clamscan --no-summary --max-filesize=4000M --max-scansize=4000M "$1" >/dev/null 2>&1 || R=$?
    case $R in
        1) return 0 ;;
        0) return 1 ;;
        *) return 2 ;;
    esac
}

split_path() {
    case $1 in
        /*) ;;
        *) die "ungueltiger Pfad" ;;
    esac
    parent=${1%/*}
    name=${1##*/}
    case $name in
        "" | . | ..) die "ungueltiger Pfad" ;;
    esac
    [ -n "$parent" ] || parent=/
}

# $QDIR und die Unterordner in $2...: echte Ordner, root allein (700), wie LOCKDIR in guarded(). $1=anlegen: fehlende
# mit mkdir -m 700 anlegen (nie -p). Sonst Rueckgabe 1.
qdir_safe() {
    make=$1
    shift
    for d in "$QDIR" "$@"; do
        if [ "$make" = anlegen ] && [ ! -e "$d" ] && [ ! -L "$d" ]; then
            mkdir -m 700 "$d" 2>/dev/null || return 1
        fi
        [ -d "$d" ] && [ ! -L "$d" ] || return 1
        case "$(stat -c %u:%a "$d" 2>/dev/null)" in "$(id -u):700" | "$(id -u):2700") ;; *) return 1 ;; esac
    done
}

# $1 (Ordner nur fuer root) als $here zurueck nach $parent (Shell steht dort), Rechte $2 (leer: wie sie sind). Wie
# antivirus.restore_command in zwei Schritten (Kopieren ueber Dateisysteme ist nicht ueberall sicher): in einen
# geprueften Zwischenordner (mktemp -d), dann rename. 0 ok; 3 belegt, 5 kein Zwischenordner ($1 unberuehrt);
# 4 nicht verschoben, 6 inzwischen belegt (Datei in $1); 7 haengt im Zwischenordner ($stuck).
put_back() {
    stuck=
    if [ -e "$here" ] || [ -L "$here" ]; then return 3; fi
    T=$(mktemp -d ./.nodvard-wiederherstellen.XXXXXX 2>/dev/null) || return 5
    cd -P "$T" 2>/dev/null && [ "$(pwd -P)" = "$stage_base/${T#./}" ] &&
        case "$(stat -c %u:%a .)" in "$(id -u):700" | "$(id -u):2700") true ;; *) false ;; esac ||
        { cd "$parent" 2>/dev/null && rmdir "$T" 2>/dev/null || true; return 5; }
    [ -z "$2" ] || chmod "$2" "$1"
    mv -T -n "$1" "$here" 2>/dev/null || true
    if [ -e "$1" ] || [ -L "$1" ]; then
        cd .. && rmdir "$T" 2>/dev/null || true
        return 4
    fi
    mv -T -n "$here" "$up" 2>/dev/null || true
    if [ -e "$here" ] || [ -L "$here" ]; then
        mv -T -n "$here" "$1" 2>/dev/null || true
        if [ -f "$1" ]; then
            cd .. && rmdir "$T" 2>/dev/null || true
            return 6
        fi
        stuck=$(pwd -P)
        return 7
    fi
    cd .. && rmdir "$T" 2>/dev/null || true
}

quarantine() {
    [ "$#" -eq 2 ] || die "quarantine erwartet Datei und Name"
    check_qname "$2"
    split_path "$1"
    here="./$name"
    up="../$name"
    if [ "$parent" = / ]; then stage_base=; else stage_base=$parent; fi
    target="$QDIR/$2"
    check="$QDIR/.pruefen/$2"
    origin="$QDIR/.herkunft/$2"
    umask 077
    cd -P "$parent" 2>/dev/null || { echo "@@msg=gone"; exit 3; }
    [ "$(pwd -P)" = "$parent" ] || { echo "@@msg=path_link"; exit 5; }
    [ -L "$here" ] && { echo "@@msg=symlink"; exit 5; }
    [ -f "$here" ] || { echo "@@msg=gone"; exit 3; }
    [ "$(stat -c %h "$here")" = 1 ] || { echo "@@msg=hardlink"; exit 5; }
    qdir_safe anlegen "$QDIR/.herkunft" "$QDIR/.pruefen" || { echo "@@msg=quarantine_dir_unsafe"; exit 5; }
    if [ -e "$check" ] || [ -L "$check" ]; then echo "@@msg=quarantine_check_busy"; exit 5; fi
    # Erst am Ort pruefen: Eine saubere Datei bleibt, wo sie ist, auch wenn der Aufruf abbricht.
    F=0
    confirm_finding "$here" || F=$?
    [ "$F" -eq 1 ] && { echo "@@msg=quarantine_not_found"; exit 6; }
    [ "$F" -eq 0 ] || { echo "@@msg=quarantine_unchecked"; exit 7; }
    # Fund: im Pruefordner (nur root) noch einmal pruefen, denn am alten Ort liess sich die Datei waehrend der Pruefung
    # austauschen. Bricht der Aufruf dort ab, legt die Falle sie zurueck; sie steht schon vor dem mv und nimmt waehrend
    # des Zuruecklegens kein weiteres Signal an.
    I=$(stat -c %d:%i "$here" 2>/dev/null) || { echo "@@msg=gone"; exit 3; }
    trap 'trap "" TERM INT HUP; put_back "$check" "" >/dev/null 2>&1 || true; exit 143' TERM INT HUP
    mv -T -n "$here" "$check" 2>/dev/null || { trap - TERM INT HUP; echo "@@msg=gone"; exit 3; }
    # mv -n meldet ein Ueberspringen nicht ueberall als Fehler. Liegt dieselbe Datei noch am Ort, war ein zweiter Aufruf
    # mit demselben Namen schneller, und im Pruefordner liegt seine.
    if [ "$(stat -c %d:%i "$here" 2>/dev/null)" = "$I" ]; then
        trap - TERM INT HUP
        echo "@@msg=quarantine_check_busy"
        exit 5
    fi
    [ -e "$check" ] || [ -L "$check" ] || { echo "@@msg=gone"; exit 3; }
    if [ -L "$check" ]; then rm -f "$check"; echo "@@msg=symlink_swapped"; exit 5; fi
    [ -f "$check" ] || { echo "@@msg=quarantine_kept $check"; exit 4; }
    if [ "$(stat -c %h "$check")" != 1 ]; then rm -f "$check"; echo "@@msg=hardlink_swapped"; exit 5; fi
    M=$(stat -c %a "$check")
    F=0
    confirm_finding "$check" || F=$?
    trap - TERM INT HUP
    if [ "$F" -ne 0 ]; then
        R=0
        put_back "$check" "" || R=$?
        if [ "$R" -ne 0 ]; then
            [ "$R" -eq 7 ] || stuck=$check
            echo "@@msg=quarantine_kept $stuck"
            exit 4
        fi
        [ "$F" -eq 1 ] && { echo "@@msg=quarantine_not_found"; exit 6; }
        echo "@@msg=quarantine_unchecked"
        exit 7
    fi
    printf '%s\n%s\n' "$M" "$1" >"$origin"
    chmod 000 "$check"
    mv -T -f "$check" "$target" || { rm -f "$origin"; exit 1; }
    echo "@@mode=$M"
}

restore() {
    [ "$#" -eq 1 ] || die "restore erwartet einen Namen"
    check_qname "$1"
    source="$QDIR/$1"
    origin="$QDIR/.herkunft/$1"
    [ -f "$source" ] || { echo "@@msg=not_in_quarantine"; exit 3; }
    # Ort und Rechte nur aus der eigenen Ablage (nur root schreibt dort): erste Zeile die Rechte, danach der Pfad bis zum
    # letzten Zeilenumbruch (der Pfad selbst darf Zeilenumbrueche enthalten).
    [ -e "$QDIR/.herkunft" ] || [ -L "$QDIR/.herkunft" ] || { echo "@@msg=restore_no_origin"; exit 3; }
    qdir_safe pruefen "$QDIR/.herkunft" || { echo "@@msg=quarantine_dir_unsafe"; exit 5; }
    [ -f "$origin" ] && [ ! -L "$origin" ] || { echo "@@msg=restore_no_origin"; exit 3; }
    mode=$(sed -n 1p "$origin")
    case $mode in
        [0-7][0-7][0-7] | [0-7][0-7][0-7][0-7]) ;;
        *) echo "@@msg=restore_no_origin"; exit 3 ;;
    esac
    original=$(sed 1d "$origin"; echo x)
    original=${original%x}
    original=${original%?}
    split_path "$original"
    here="./$name"
    up="../$name"
    if [ "$parent" = / ]; then stage_base=; else stage_base=$parent; fi
    cd -P "$parent" 2>/dev/null || { echo "@@msg=restore_folder_missing"; exit 3; }
    [ "$(pwd -P)" = "$parent" ] || { echo "@@msg=restore_link"; exit 5; }
    # Klappt es nicht, bleibt die Datei in der Quarantaene (wieder 000).
    R=0
    put_back "$source" "$mode" || R=$?
    case $R in
        0) ;;
        3) echo "@@msg=restore_occupied"; exit 4 ;;
        5) echo "@@msg=restore_stage_failed"; exit 5 ;;
        4) chmod 000 "$source"; echo "@@msg=restore_not_moved"; exit 4 ;;
        6) chmod 000 "$source"; echo "@@msg=restore_target_taken"; exit 4 ;;
        *) echo "@@msg=restore_stuck $stuck"; exit 4 ;;
    esac
    rm -f "$origin"
    echo ok
}

# Nach einer Zeitueberschreitung beim Verschieben: liegt die Datei in der Quarantaene, noch im Pruefordner oder nicht da?
quarantine_state() {
    [ "$#" -eq 1 ] || die "quarantine-state erwartet einen Namen"
    check_qname "$1"
    set +e
    if [ -f "$QDIR/$1" ] && [ ! -L "$QDIR/$1" ]; then
        echo "@@state=quarantined"
        M=
        # Nur eine echte Datei: Ein Link oder eine FIFO in der Ablage wird nicht gelesen.
        if [ -f "$QDIR/.herkunft/$1" ] && [ ! -L "$QDIR/.herkunft/$1" ]; then
            M=$(sed -n 1p "$QDIR/.herkunft/$1" 2>/dev/null)
        fi
        case $M in
            [0-7][0-7][0-7] | [0-7][0-7][0-7][0-7]) echo "@@mode=$M" ;;
        esac
    elif [ -e "$QDIR/.pruefen/$1" ] || [ -L "$QDIR/.pruefen/$1" ]; then
        echo "@@state=checking"
    else
        echo "@@state=absent"
    fi
}

delete() {
    [ "$#" -eq 1 ] || die "delete erwartet einen Namen"
    check_qname "$1"
    set +e
    rm -f "$QDIR/$1" "$QDIR/.herkunft/$1"
    echo ok
}

fail2ban_set() {
    action=$1
    shift
    [ "$#" -eq 2 ] || die "ban/unban erwartet IP und Jail"
    case $1 in
        "" | *[!0-9A-Fa-f:.]*) die "ungueltige IP-Adresse" ;;
    esac
    [ "${#1}" -le 45 ] || die "ungueltige IP-Adresse"
    case $2 in
        "" | [!A-Za-z0-9]* | *[!A-Za-z0-9_.-]*) die "ungueltiger Jail-Name" ;;
    esac
    [ "${#2}" -le 64 ] || die "ungueltiger Jail-Name"
    fail2ban-client set "$2" "$action" "$1"
}

install_package() {
    [ "$#" -eq 1 ] || die "install erwartet ein Paket"
    case $1 in
        clamav | lynis | fail2ban | unattended | signatures) ;;
        *) die "unbekanntes Paket" ;;
    esac
    # Wie die Befehle in INSTALL_COMMANDS ohne "set -e": Nach einem gescheiterten freshclam wird das automatische
    # Signatur-Update trotzdem wieder eingeschaltet.
    set +e
    case $1 in
        clamav)
            if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q &&
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q clamav clamav-freshclam
            elif command -v dnf >/dev/null; then dnf install -y clamav clamav-update
            elif command -v apk >/dev/null; then apk add clamav clamav-libunrar
            else echo 'Kein bekannter Paketmanager'; exit 2; fi
            ;;
        lynis)
            if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q &&
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q lynis
            elif command -v dnf >/dev/null; then dnf install -y lynis
            elif command -v apk >/dev/null; then apk add lynis
            else echo 'Kein bekannter Paketmanager'; exit 2; fi
            ;;
        fail2ban)
            if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q &&
                DEBIAN_FRONTEND=noninteractive apt-get install -y -q fail2ban
            elif command -v dnf >/dev/null; then dnf install -y fail2ban
            elif command -v apk >/dev/null; then apk add fail2ban
            else echo 'Kein bekannter Paketmanager'; exit 2; fi
            if [ ! -f /etc/fail2ban/jail.local ]; then
                printf '[DEFAULT]\nbackend = systemd\n\n[sshd]\nenabled = true\n' > /etc/fail2ban/jail.local
            fi
            systemctl enable --now fail2ban 2>/dev/null
            systemctl restart fail2ban 2>/dev/null
            fail2ban-client ping
            ;;
        unattended)
            command -v apt-get >/dev/null || { echo "@@msg=only_apt"; exit 2; }
            DEBIAN_FRONTEND=noninteractive apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q unattended-upgrades &&
                printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades &&
                echo ok
            ;;
        signatures)
            systemctl stop clamav-freshclam 2>/dev/null
            freshclam --quiet
            R=$?
            systemctl enable --now clamav-freshclam 2>/dev/null
            exit $R
            ;;
    esac
}

reboot_host() {
    [ "$#" -eq 0 ] || die "reboot erwartet keine Argumente"
    set +e
    if command -v shutdown >/dev/null 2>&1; then shutdown -r +1 'Neustart durch Nodvard Deck nach Updates' 2>&1
    else (sleep 5; reboot) >/dev/null 2>&1 & echo 'Neustart in 5 Sekunden'; fi
}

# Art eines entkoppelten Laufs, wie detached.JobKind: setzt JD (Ordner), JP (Anfang der Lauf-ID), JU (Anfang der
# systemd-Unit) und JL (Beschreibung). Feste Werte, nie aus Argumenten.
job_kind() {
    case $1 in
        upgrade)
            JD=$JOBDIR
            JP=upd
            JU=lattice-upgrade-
            JL='Nodvard Deck Update'
            ;;
        audit)
            JD=$AUDITDIR
            JP=audit
            JU=nodvard-shield-audit-
            JL='Nodvard Shield Audit'
            ;;
    esac
}

# Lauf-ID der Art $1: <anfang>_<16 Hex-Zeichen>.
check_rid() {
    job_kind "$1"
    case $2 in
        "$JP"_????????????????) ;;
        *) die "ungueltige Lauf-ID" ;;
    esac
    case ${2#"$JP"_} in
        *[!0-9a-f]*) die "ungueltige Lauf-ID" ;;
    esac
}

# <lauf-id> <paketmanager> <art> dist|plain [<pakete>...]: was updates.upgrade_command annimmt. dist nur bei apt und
# "all", Pakete nur bei apt und "security" (mindestens eins, Namen wie updates._PKG_NAME_RE).
check_upgrade() {
    [ "$#" -ge 4 ] || die "erwartet Lauf-ID, Paketmanager, Art und dist/plain"
    check_rid upgrade "$1"
    case $2:$3 in
        apt:all | apt:security | apt:cleanup | dnf:all | dnf:security | dnf:cleanup | apk:all | apk:security | apk:cleanup) ;;
        *) die "nicht unterstuetzt" ;;
    esac
    case $4:$2:$3 in
        plain:*) ;;
        dist:apt:all) ;;
        *) die "dist nur bei apt und all" ;;
    esac
    if [ "$2:$3" = apt:security ]; then
        [ "$#" -ge 5 ] || die "keine Pakete"
    else
        [ "$#" -eq 4 ] || die "Pakete nur bei apt und security"
    fi
    shift 4
    for p in "$@"; do
        case $p in
            [A-Za-z0-9]*) ;;
            *) die "ungueltiger Paketname" ;;
        esac
        case $p in
            *[!A-Za-z0-9+._:~-]*) die "ungueltiger Paketname" ;;
        esac
    done
}

apt_q() {
    apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}

# Laeuft in einer Subshell (siehe run_job); der Befehl je Paketmanager und Art steht wie in updates.upgrade_command.
run_upgrade() {
    m=$1
    mode=$2
    dist=$3
    shift 3
    case $m:$mode in
        apt:all)
            export DEBIAN_FRONTEND=noninteractive
            if [ "$dist" = dist ]; then
                apt-get update -q && if command -v pveversion >/dev/null 2>&1; then apt_q dist-upgrade; else apt_q upgrade --with-new-pkgs; fi
            else
                apt-get update -q && apt_q upgrade --with-new-pkgs
            fi
            ;;
        apt:security)
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -q && apt_q install --only-upgrade "$@"
            ;;
        apt:cleanup)
            export DEBIAN_FRONTEND=noninteractive
            apt-get -y -q autoremove --purge && apt-get clean
            ;;
        dnf:all) dnf -y upgrade ;;
        dnf:security) dnf -y upgrade --security ;;
        dnf:cleanup) dnf -y autoremove && dnf clean packages ;;
        apk:all | apk:security) apk update -q && apk upgrade ;;
        apk:cleanup) apk cache clean 2>/dev/null; true ;;
    esac
    R=$?
    echo '@@upgrade-done'
    [ -f /var/run/reboot-required ] && echo '@@reboot-required'
    exit $R
}

# Der entkoppelte Lauf der Art $1 mit Lauf-ID $2: pid merken, $3 mit den uebrigen Argumenten in einer Subshell mit
# Ausgabe ins Protokoll, Rueckgabecode atomar ablegen (wie detached.wrapper_command). Die Befehlszeile traegt die
# Lauf-ID, daran erkennen die Abfragen den Prozess.
run_job() {
    job_kind "$1"
    f=$JD/$2
    shift 2
    set +e
    echo $$ >"$f.pid.tmp" && mv -f "$f.pid.tmp" "$f.pid"
    ( "$@" ) </dev/null >"$f.log" 2>&1
    echo $? >"$f.rc.tmp"
    mv -f "$f.rc.tmp" "$f.rc"
}

# Startet "$SELF <unterbefehl> <lauf-id> ..." (ab $3) entkoppelt vom SSH-Kanal (systemd-run, sonst setsid, sonst nohup)
# und wartet bis zu 5 s auf die pid-Datei, wie detached.launch_command. $1: Art, $2: Lauf-ID (beide geprueft).
# Ausgabe: @@launch, @@method=..., dann @@pid=<n> oder @@nopid; @@exists: der Lauf wurde schon gestartet.
start_job() {
    job_kind "$1"
    rid=$2
    d=$JD
    shift 2
    set +e
    echo @@launch
    mkdir -p -m 700 "$d" || { echo @@nopid; exit 1; }
    find "$d" -maxdepth 1 -type f -name "${JP}_*" -mtime +30 -exec rm -f {} + 2>/dev/null
    if [ -e "$d/$rid.pid" ]; then echo @@exists; echo "@@pid=$(cat "$d/$rid.pid")"; exit 0; fi
    if [ -d "$SDMARK" ] && command -v systemd-run >/dev/null 2>&1; then
        if systemd-run --unit="$JU$rid" --collect --quiet --description="$JL $rid" \
            /bin/sh "$SELF" "$@" </dev/null 2>&1; then
            echo @@method=systemd
        else
            sleep 1
            [ -s "$d/$rid.pid" ] || {
                nohup setsid /bin/sh "$SELF" "$@" </dev/null >/dev/null 2>&1 &
                echo @@method=setsid-fallback
            }
        fi
    elif command -v setsid >/dev/null 2>&1; then
        nohup setsid /bin/sh "$SELF" "$@" </dev/null >/dev/null 2>&1 &
        echo @@method=setsid
    else
        nohup /bin/sh "$SELF" "$@" </dev/null >/dev/null 2>&1 &
        echo @@method=nohup
    fi
    i=0
    while [ ! -s "$d/$rid.pid" ] && [ $i -lt 50 ]; do
        sleep 0.1 2>/dev/null || sleep 1
        i=$((i + 1))
    done
    if [ -s "$d/$rid.pid" ]; then echo "@@pid=$(cat "$d/$rid.pid")"; else echo @@nopid; fi
}

# Setzt p auf die Nummer aus der pid-Datei des Laufs $2 (Art $1) und ist wahr, wenn dieser Prozess noch laeuft und die
# Lauf-ID in seiner Befehlszeile traegt (wie detached.running_test).
job_alive() {
    job_kind "$1"
    p=$(cat "$JD/$2.pid" 2>/dev/null)
    [ -n "$p" ] && [ -r "/proc/$p/cmdline" ] && tr '\000' ' ' <"/proc/$p/cmdline" | grep -q "$2"
}

# Erst "laeuft der Prozess noch?", dann "gibt es die rc-Datei?" (sie entsteht vor dem Ende des Prozesses).
upgrade_poll() {
    [ "$#" -eq 1 ] || die "upgrade-poll erwartet eine Lauf-ID"
    check_rid upgrade "$1"
    rid=$1
    d=$JOBDIR
    set +e
    if job_alive upgrade "$rid"; then
        echo @@running
        echo @@tail
        tail -n 1 "$d/$rid.log" 2>/dev/null
    elif [ -f "$d/$rid.rc" ]; then
        echo "@@rc=$(cat "$d/$rid.rc")"
        echo @@summary
        grep -E 'upgraded, .* newly installed|aktualisiert, .* neu installiert|^E:' "$d/$rid.log" 2>/dev/null | tail -n 5
        # Von apt zurueckgehaltene Pakete in eine Zeile "@@kept-back: a b c" (wie detached._KEPT_AWK).
        awk '/(have been kept back|zur.*ckgehalten).*:[ \t]*$/ { if (f) print ""; f = 1; printf "@@kept-back:"; next } f && /^[ \t]+[^ \t]/ { $1 = $1; printf " %s", $0; next } f { f = 0; print "" } END { if (f) print "" }' \
            "$d/$rid.log" 2>/dev/null | head -n 2
        echo @@tail
        tail -c 20000 "$d/$rid.log" 2>/dev/null
    elif [ -n "$p" ]; then
        echo @@lost
        echo @@summary
        dpkg --audit 2>/dev/null | head -n 5
        echo @@tail
        tail -n 20 "$d/$rid.log" 2>/dev/null
    else
        echo @@unknown
        echo @@tail
    fi
}

# Das Lynis-Audit im entkoppelten Lauf (Subshell, siehe run_job), wie audit_job.lynis_command. Marker: @@nolynis,
# @@busy, @@timeout, @@killed oder @@lynis-rc=<n> mit @@report bzw. @@stale.
lynis_audit() {
    rid=$1
    # Wie im Befehl des Moduls: /usr/local/sbin, /usr/sbin und /sbin anhaengen.
    PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
    command -v lynis >/dev/null 2>&1 || { echo @@nolynis; exit 0; }
    # Lynis loescht beim Start ein ./lynis.pid im aktuellen Ordner: nur im eigenen Ordner laufen.
    cd "$AUDITDIR" || exit 1
    L=
    command -v flock >/dev/null 2>&1 && L=1
    (
        if [ -n "$L" ]; then flock -n 9; [ $? -ne 1 ] || { echo @@busy; exit 0; }; fi
        for f in $LYNIS_PIDS; do
            p=$(cat "$f" 2>/dev/null)
            case "$p" in '' | *[!0-9]*) continue ;; esac
            if [ -r "/proc/$p/cmdline" ] && tr '\000' ' ' <"/proc/$p/cmdline" | grep -q 'lynis audit'; then
                echo @@busy
                exit 0
            fi
        done
        : >"$AUDITDIR/$rid.start" || exit 1
        N=
        command -v nice >/dev/null 2>&1 && N="nice -n 19"
        if command -v ionice >/dev/null 2>&1 && ionice -c3 true >/dev/null 2>&1; then N="$N ionice -c3"; fi
        T=
        case "$(timeout --version 2>/dev/null)" in
            *coreutils*) timeout -k 1 5 true >/dev/null 2>&1 && T="timeout -k 60 $LYNIS_LIMIT" ;;
        esac
        echo "@@prio=${N:-normal}"
        S=$(date +%s 2>/dev/null)
        $T $N lynis audit system --quick --quiet --no-colors </dev/null
        R=$?
        if [ -n "$T" ] && [ "$R" -eq 124 ]; then echo @@timeout; exit 0; fi
        if [ "$R" -eq 137 ]; then
            E=$(date +%s 2>/dev/null)
            if [ -n "$T" ] && { [ -z "$S" ] || [ -z "$E" ] || [ $((E - S)) -ge "$LYNIS_LIMIT" ]; }; then
                echo @@timeout
            else
                echo @@killed
            fi
            exit 0
        fi
        echo "@@lynis-rc=$R"
        if [ -f "$LYNIS_REPORT" ] && [ ! -L "$LYNIS_REPORT" ] && [ "$LYNIS_REPORT" -nt "$AUDITDIR/$rid.start" ]; then
            echo @@report
            grep -E '^(warning\[\]|suggestion\[\]|hardening_index|finish)=' "$LYNIS_REPORT"
        else
            echo @@stale
        fi
    ) 9>>"$AUDITDIR/lynis.lock"
}

# Wie audit_job.poll_command.
audit_poll() {
    [ "$#" -eq 1 ] || die "audit-poll erwartet eine Lauf-ID"
    check_rid audit "$1"
    rid=$1
    d=$AUDITDIR
    set +e
    if job_alive audit "$rid"; then
        echo @@running
        echo @@tail
    elif [ -f "$d/$rid.rc" ]; then
        echo "@@rc=$(cat "$d/$rid.rc")"
        echo @@tail
        tail -c 262144 "$d/$rid.log" 2>/dev/null
    elif [ -n "$p" ]; then
        echo @@lost
        echo @@tail
        tail -n 20 "$d/$rid.log" 2>/dev/null
    else
        echo @@unknown
        echo @@tail
    fi
}

# Wie audit_job.stop_command: Der Lauf fuehrt eine eigene Session, pkill -s trifft alles darin. Erst TERM, damit Lynis
# seine PID-Datei wegraeumt, nach 10 Sekunden KILL.
audit_stop() {
    [ "$#" -eq 1 ] || die "audit-stop erwartet eine Lauf-ID"
    check_rid audit "$1"
    rid=$1
    set +e
    if job_alive audit "$rid"; then
        systemctl stop --no-block "nodvard-shield-audit-$rid.service" >/dev/null 2>&1
        pkill -TERM -s "$p" 2>/dev/null
        kill -TERM "-$p" 2>/dev/null
        kill -TERM "$p" 2>/dev/null
        i=0
        while [ $i -lt 10 ] && kill -0 "$p" 2>/dev/null; do
            sleep 1
            i=$((i + 1))
        done
        pkill -KILL -s "$p" 2>/dev/null
        kill -KILL "-$p" 2>/dev/null
        kill -KILL "$p" 2>/dev/null
        sleep 1
        if job_alive audit "$rid" || pgrep -s "$p" >/dev/null 2>&1; then echo @@stuck; else echo @@stopped; fi
    else
        echo @@notrunning
    fi
}

# Marke eines Scans: @@scan-rc-<16 Hex-Zeichen>= (wie antivirus.new_rc_mark).
check_mark() {
    case $1 in
        @@scan-rc-????????????????=) ;;
        *) die "ungueltige Marke" ;;
    esac
    m=${1#@@scan-rc-}
    case ${m%=} in
        *[!0-9a-f]*) die "ungueltige Marke" ;;
    esac
}

# Ganze Zahl von 1 bis 999999 ohne fuehrende Null.
check_num() {
    case $1 in
        '' | *[!0-9]* | 0*) die "ungueltige Zahl" ;;
    esac
    [ "${#1}" -le 6 ] || die "ungueltige Zahl"
}

# Argumente vor "--" beginnen mit $1 (Ausnahmen), danach mindestens ein absoluter Pfad.
check_lists() {
    first=$1
    shift
    seen=0
    n=0
    for a in "$@"; do
        if [ "$seen" -eq 0 ]; then
            if [ "$a" = -- ]; then
                seen=1
                continue
            fi
            case $a in
                "$first"?*) ;;
                *) die "ungueltige Ausnahme" ;;
            esac
        else
            case $a in
                /*) ;;
                *) die "ungueltiger Pfad" ;;
            esac
            n=$((n + 1))
        fi
    done
    [ "$seen" -eq 1 ] && [ "$n" -ge 1 ] || die "erwartet Ausnahmen, dann --, dann Pfade"
}

# Sperre fuer scan-run und watch-run: - (keine), n (sofort aufgeben) oder w<sekunden> (so lange warten).
check_take() {
    case $1 in
        - | n) ;;
        w*) check_num "${1#w}" ;;
        *) die "ungueltige Sperre" ;;
    esac
}

# Wie antivirus.guard_command: Sperre (flock auf LOCKDIR/scan.lock) und Zeitlimit. $1 Marke, $2 Limit in s, $3 Wartezeit
# auf die Sperre (s oder n), $4 innerer Unterbefehl samt Argumenten. Dazu @@scan-busy-<hex> bzw. @@scan-timeout-<hex>.
guarded() {
    mark=$1
    limit_s=$2
    wait=$3
    inner=$4
    shift 4
    hex=${mark#@@scan-rc-}
    hex=${hex%=}
    if [ "$limit_s" -gt 90 ]; then
        limit=$((limit_s - 45))
    else
        limit=$((limit_s / 2))
        [ "$limit" -ge 1 ] || limit=1
    fi
    if [ "$wait" = n ]; then
        take=n
    else
        w=$wait
        [ "$w" -le $((limit / 2)) ] || w=$((limit / 2))
        [ "$w" -ge 1 ] || w=1
        take=w$w
    fi
    set +eu
    D=$LOCKDIR
    N=scan.lock
    F=
    case "$(flock --version 2>/dev/null)" in
        *util-linux*)
            mkdir -m 700 "$D" 2>/dev/null
            if [ -d "$D" ] && [ ! -L "$D" ] &&
                case "$(stat -c %u:%a "$D" 2>/dev/null)" in "$(id -u):700" | "$(id -u):2700") true ;; *) false ;; esac &&
                (: >>"$D/$N") 2>/dev/null; then
                F="$D/$N"
            fi
            ;;
    esac
    [ -n "$F" ] || take=-
    T=
    case "$(timeout --version 2>/dev/null)" in
        *coreutils*) timeout -k 1 5 true 2>/dev/null && T="timeout -k 15 $limit" ;;
    esac
    $T /bin/sh "$SELF" "$inner" "$take" "$mark" "$@" 9>>"${F:-/dev/null}"
    R=$?
    if [ -n "$T" ] && { [ "$R" -eq 124 ] || [ "$R" -eq 137 ]; }; then echo "@@scan-timeout-$hex"; fi
}

# Die Sperre auf Kanal 9 nehmen (siehe guarded); ist sie belegt, @@scan-busy-<hex> und Ende.
take_lock() {
    hex=${2#@@scan-rc-}
    hex=${hex%=}
    case $1 in
        n)
            flock -n 9
            [ $? -ne 1 ] || { echo "@@scan-busy-$hex"; exit 0; }
            ;;
        w*)
            flock -w "${1#w}" 9
            [ $? -ne 1 ] || { echo "@@scan-busy-$hex"; exit 0; }
            ;;
    esac
}

# Wie antivirus._resolve_roots_command: je Pfad nach "--" eine Zeile <$1><aufgeloester Pfad>, leer fuer Pfade mit
# Zeilenumbruch oder Wagenruecklauf und fuer nicht aufloesbare. Setzt NL und CR.
resolve_roots() {
    label=$1
    shift
    NL=$(printf '\nx')
    NL=${NL%x}
    CR=$(printf '\r')
    seen=0
    for P in "$@"; do
        if [ "$seen" -eq 0 ]; then
            [ "$P" = -- ] && seen=1
            continue
        fi
        D=$(cd -P -- "$P" 2>/dev/null && pwd -P || readlink -f -- "$P" 2>/dev/null)
        case "$D" in *"$NL"* | *"$CR"*) D= ;; esac
        printf '%s\n' "$label$D"
    done
}

# Der Scan selbst (startet scan): <sperre> <marke> <max-mb> [<ausnahme>...] -- <pfad>..., wie
# antivirus.build_scan_command.
scan_run() {
    take=$1
    mark=$2
    mb=$3
    shift 3
    set +eu
    take_lock "$take" "$mark"
    resolve_roots "@@scan-roots-${mark#@@scan-rc-}" "$@"
    # Neue Argumente hinten anhaengen, am Ende die alten n vorne wegschieben.
    n=$#
    np=0
    only_root=1
    seen=0
    for a in "$@"; do
        if [ "$seen" -eq 0 ]; then
            [ "$a" = -- ] && seen=1
            continue
        fi
        np=$((np + 1))
        [ "$a" = / ] || only_root=0
    done
    set -- "$@" clamscan -r -i --stdout
    if [ "$np" -eq 1 ] && [ "$only_root" -eq 1 ]; then set -- "$@" --cross-fs=no; fi
    set -- "$@" "--max-filesize=${mb}M" "--max-scansize=$((mb * 4))M"
    i=0
    seen=0
    for a in "$@"; do
        i=$((i + 1))
        [ "$i" -le "$n" ] || break
        if [ "$a" = -- ]; then
            seen=1
        elif [ "$seen" -eq 0 ]; then
            set -- "$@" "--exclude-dir=$a"
        fi
    done
    i=0
    seen=0
    for a in "$@"; do
        i=$((i + 1))
        [ "$i" -le "$n" ] || break
        if [ "$a" = -- ]; then
            seen=1
        elif [ "$seen" -eq 1 ]; then
            set -- "$@" "$a"
        fi
    done
    shift "$n"
    "$@" 2>&1
    echo "$mark$?"
}

# Der Waechter-Lauf selbst (startet watch): <sperre> <marke> <minuten> <max-mb> 0|1 [<ordner>...] -- <pfad>..., wie
# antivirus.build_watch_command. Die Dateiliste und ihre Hilfsdateien liegen unter TMPRUN (nur root, nie ueberwacht).
watch_run() {
    take=$1
    mark=$2
    M=$3
    MB=$4
    clamd=$5
    shift 5
    set +eu
    take_lock "$take" "$mark"
    resolve_roots "@@scan-roots-${mark#@@scan-rc-}" "$@"
    trap 'rm -f "$L" "$X" "$Y" "$K" 2>/dev/null; exit 143' TERM
    L=$(mktemp -p "$TMPRUN" 2>/dev/null || mktemp)
    X=$(mktemp -p "$TMPRUN" 2>/dev/null || mktemp)
    Y=$(mktemp -p "$TMPRUN" 2>/dev/null || mktemp)
    K=$(mktemp -p "$TMPRUN" 2>/dev/null || mktemp)
    LONG=$(printf '%01023d' 0 | tr 0 '?')
    LONG="$LONG*"
    # Argumente fuer find hinten anhaengen, am Ende die alten n vorne wegschieben.
    n=$#
    set -- "$@" -H
    i=0
    seen=0
    for a in "$@"; do
        i=$((i + 1))
        [ "$i" -le "$n" ] || break
        if [ "$a" = -- ]; then
            seen=1
        elif [ "$seen" -eq 1 ]; then
            set -- "$@" "$a"
        fi
    done
    set -- "$@" -xdev
    i=0
    have=0
    for a in "$@"; do
        i=$((i + 1))
        [ "$i" -le "$n" ] && [ "$a" != -- ] || break
        if [ "$have" -eq 0 ]; then
            set -- "$@" '(' -path "$a" -o -path "$a/*"
            have=1
        else
            set -- "$@" -o -path "$a" -o -path "$a/*"
        fi
    done
    [ "$have" -eq 0 ] || set -- "$@" ')' -prune -o
    set -- "$@" -type f '(' -mmin "-$M" -o -cmin "-$M" ')' -size "-${MB}M" -not -path "$QDIR/*" \
        -not -path '/dev/shm/qb-*' -not -path "$L" -not -path "$X" -not -path "$Y" -not -path "$K" \
        '(' '(' -path "*$NL*" -o -path "*$CR*" -o -path "$LONG" ')' -exec /bin/sh "$SELF" odd-scan "$X" "$Y" '{}' + \
        -o -print ')'
    shift "$n"
    LC_ALL=C find "$@" 2>/dev/null |
        L="$L" K="$K" awk 'NR <= 2000 { print > ENVIRON["L"] } END { print (NR > 2000 ? NR - 2000 : 0) > ENVIRON["K"] }'
    cat "$X"
    if [ -s "$L" ]; then
        C=
        if [ "$clamd" = 1 ]; then
            if clamdscan --ping 1 >/dev/null 2>&1; then
                O=$(clamdscan --fdpass --no-summary -i --file-list="$L" 2>&1)
                R=$?
                if [ "$R" -eq 0 ] || { [ "$R" -le 2 ] &&
                    ! printf '%s\n' "$O" | grep -qvE '^(ERROR|WARNING): Can.t access file | FOUND$'; }; then
                    C=1
                    [ -z "$O" ] || printf '%s\n' "$O"
                    echo "Scanned files: $(wc -l <"$L" | tr -d ' ')"
                fi
            fi
        fi
        if [ -z "$C" ]; then
            clamscan -i --stdout --file-list="$L" 2>&1
            R=$?
        fi
    else
        echo 'Scanned files: 0'
        R=0
    fi
    # Rueckgabecodes der Sonderlaeufe (odd-scan) in den Gesamtcode: 127 vor 1 vor allem anderen.
    T=0
    while read -r r n; do
        T=$((T + ${n:-0}))
        case "$r" in
            0) ;;
            1) [ "$R" -eq 127 ] || R=1 ;;
            *) [ "$R" -eq 0 ] || [ "$R" -eq 2 ] && R=$r ;;
        esac
    done <"$Y"
    [ "$T" -eq 0 ] || echo "Unusual file names: $T"
    S=$(tr -cd 0-9 <"$K")
    [ "${S:-0}" -eq 0 ] || echo "Skipped files: $S"
    rm -f "$L" "$X" "$Y" "$K"
    echo "$mark$R"
}

# Fuer watch-run: Dateien mit ungewoehnlichem Namen als Argumente pruefen (antivirus._ODD_NAME_SCAN). Ausgabe an $1,
# "Rueckgabecode Anzahl" an $2 (eigene mktemp-Dateien unter TMPRUN), absolute Pfade.
odd_scan() {
    [ "$#" -ge 3 ] || die "odd-scan erwartet zwei Dateien und Pfade"
    for f in "$1" "$2"; do
        case $f in
            "$TMPRUN"/tmp.??????????) ;;
            *) die "ungueltige Datei" ;;
        esac
        [ -f "$f" ] && [ ! -L "$f" ] && [ -O "$f" ] || die "ungueltige Datei"
    done
    o=$1
    r=$2
    shift 2
    for f in "$@"; do
        case $f in
            /*) ;;
            *) die "ungueltiger Pfad" ;;
        esac
    done
    set +eu
    n=$#
    clamscan -i --stdout --no-summary "$@" >>"$o" 2>&1
    echo "$? $n" >>"$r"
}

# Ordner fuer den Echtzeit-Waechter, wie realtime.clean_paths.
check_rt_dir() {
    case $1 in
        /*) ;;
        *) die "ungueltiger Ordner" ;;
    esac
    case $1 in
        *"$RT_NL"* | *"$RT_CR"*) die "ungueltiger Ordner" ;;
    esac
    case /$1/ in
        */../*) die "ungueltiger Ordner" ;;
    esac
    [ "${#1}" -le 4096 ] || die "ungueltiger Ordner"
}

# Wie realtime.enable_command: $1 groesste Datei in MB, dann Ausnahmen, -- und Ordner. clamd.conf wird nur gelesen.
realtime_on() {
    max=$1
    shift
    seen=0
    n=0
    for a in "$@"; do
        if [ "$seen" -eq 0 ] && [ "$a" = -- ]; then
            seen=1
            continue
        fi
        check_rt_dir "$a"
        [ "$seen" -eq 0 ] || n=$((n + 1))
    done
    [ "$seen" -eq 1 ] && [ "$n" -ge 1 ] || die "erwartet Ausnahmen, dann --, dann Ordner"
    if ! command -v clamd >/dev/null 2>&1 || ! command -v clamonacc >/dev/null 2>&1; then
        echo @@msg=realtime_no_package
        exit 3
    fi
    if [ ! -d "$SDMARK" ]; then
        echo @@msg=realtime_no_systemd
        exit 3
    fi
    onacc=$(command -v clamonacc)
    sock=$(awk '$1 == "LocalSocket" { print $2; exit }' "$ETCDIR/clamav/clamd.conf" 2>/dev/null) || true
    [ -n "$sock" ] || sock=/run/clamav/clamd.ctl
    mkdir -p "${RT_CONF%/*}" "${RT_DROPIN%/*}"
    # Nur beim ersten Einschalten: sonst hielte der Rueckweg den Stand nach dem ersten Mal fuer den alten.
    if [ ! -e "$RT_STATE" ]; then
        {
            if systemctl is-active --quiet clamav-daemon; then echo clamd_active=yes; else echo clamd_active=no; fi
            if systemctl is-enabled --quiet clamav-daemon; then echo clamd_enabled=yes; else echo clamd_enabled=no; fi
        } > "$RT_STATE"
    fi
    printf '%s\n' "LocalSocket $sock" 'OnAccessExcludeUname clamav' "OnAccessMaxFileSize ${max}M" \
        'OnAccessPrevention no' 'OnAccessExtraScanning yes' 'LogFileMaxSize 0' > "$RT_CONF"
    : > "$RT_WATCH"
    ne=0
    seen=0
    for a in "$@"; do
        if [ "$seen" -eq 0 ] && [ "$a" = -- ]; then
            seen=1
            continue
        fi
        if [ "$seen" -eq 1 ]; then
            printf '%s\n' "$a" >> "$RT_WATCH"
        else
            [ "$ne" -gt 0 ] || : > "$RT_EXCLUDE"
            printf '%s\n' "$a" >> "$RT_EXCLUDE"
            ne=$((ne + 1))
        fi
    done
    excl=
    if [ "$ne" -gt 0 ]; then
        excl=" --exclude-list=$RT_EXCLUDE"
    else
        rm -f "$RT_EXCLUDE"
    fi
    printf '%s\n' '[Service]' 'ExecStart=' \
        "ExecStart=$onacc -F --fdpass --config-file=$RT_CONF --watch-list=$RT_WATCH$excl --log=$RT_LOG" > "$RT_DROPIN"
    systemctl daemon-reload
    systemctl enable --now clamav-daemon
    systemctl enable clamav-clamonacc
    systemctl restart clamav-clamonacc || realtime_fail
    w=0
    while [ "$w" -lt "$RT_WAIT" ] && ! systemctl is-active --quiet clamav-clamonacc; do
        sleep 2
        w=$((w + 2))
    done
    systemctl is-active --quiet clamav-clamonacc || realtime_fail
    # active heisst nur gestartet: clamonacc stirbt oft Sekunden spaeter. Laeuft nach RT_SETTLE noch dieselbe PID?
    p=$(systemctl show -p MainPID --value clamav-clamonacc) || p=0
    sleep "$RT_SETTLE"
    [ "${p:-0}" != 0 ] && [ "$(systemctl show -p MainPID --value clamav-clamonacc)" = "$p" ] || realtime_fail
    echo @@realtime-on
}

# Nach dem Journal wie realtime_off zurueck: kein halb eingerichteter Dienst, der sich immer wieder neu startet.
realtime_fail() {
    echo @@msg=realtime_not_started
    journalctl -u clamav-clamonacc -n 20 --no-pager 2>&1 || true
    rt_undo
    exit 4
}

# Wie realtime.disable_command; clamd wie vor dem ersten Einschalten (ohne Merkdatei bleibt er).
rt_undo() {
    set +e
    systemctl disable --now clamav-clamonacc
    rm -f "$RT_DROPIN" "$RT_CONF" "$RT_WATCH" "$RT_EXCLUDE"
    rmdir "${RT_DROPIN%/*}" 2>/dev/null
    systemctl daemon-reload
    if grep -qx clamd_active=no "$RT_STATE" 2>/dev/null; then systemctl stop clamav-daemon; fi
    if grep -qx clamd_enabled=no "$RT_STATE" 2>/dev/null; then systemctl disable clamav-daemon; fi
    rm -f "$RT_STATE"
}

realtime_off() {
    rt_undo
    echo @@realtime-off
}

# Zeile im Protokoll: 0 oder eine ganze Zahl ohne fuehrende Null, hoechstens 12 Stellen.
check_line() {
    case $1 in
        0) ;;
        '' | *[!0-9]* | 0*) die "ungueltige Zeile" ;;
    esac
    [ "${#1}" -le 12 ] || die "ungueltige Zeile"
}

# Wie realtime.log_command: laeuft clamonacc, dann die Zeilen des Protokolls nach Zeile $1 (hoechstens RT_BATCH, nur
# ganze Zeilen). Hat das Protokoll weniger Zeilen als gemerkt, ist es neu: dann von vorn.
realtime_log() {
    set +e
    if systemctl is-active --quiet clamav-clamonacc; then echo @@active=yes; else echo @@active=no; fi
    n=0
    [ ! -r "$RT_LOG" ] || n=$(wc -l < "$RT_LOG")
    n=$((n + 0))
    l=$1
    [ "$l" -le "$n" ] || l=0
    e=$((l + RT_BATCH))
    [ "$e" -le "$n" ] || e=$n
    echo "@@lines=$l-$e"
    if [ "$e" -gt "$l" ]; then sed -n "$((l + 1)),${e}p" "$RT_LOG"; fi
    echo @@end
}

# Wie realtime.confirm_command: genau diese Dateien noch einmal ueber clamd pruefen, dazu dessen Rueckgabewert.
realtime_confirm() {
    [ "$#" -ge 1 ] && [ "$#" -le 50 ] || die "realtime-confirm erwartet 1 bis 50 Dateien"
    for a in "$@"; do
        check_rt_dir "$a"
    done
    set +e
    clamdscan --fdpass --no-summary "$@"
    echo "@@rc=$?"
}

[ "$#" -ge 1 ] || die "Unterbefehl fehlt"
command=$1
shift

case $command in
    version)
        [ "$#" -eq 0 ] || die "version erwartet keine Argumente"
        printf '%s\n' "$HELPER_VERSION"
        ;;
    guard)
        guard "$@"
        ;;
    detect-templates)
        detect_templates "$@"
        ;;
    refresh-packages)
        refresh_packages "$@"
        ;;
    quarantine)
        quarantine "$@"
        ;;
    restore)
        restore "$@"
        ;;
    delete)
        delete "$@"
        ;;
    quarantine-state)
        quarantine_state "$@"
        ;;
    ban)
        fail2ban_set banip "$@"
        ;;
    unban)
        fail2ban_set unbanip "$@"
        ;;
    install)
        install_package "$@"
        ;;
    reboot)
        reboot_host "$@"
        ;;
    upgrade-start)
        check_upgrade "$@"
        start_job upgrade "$1" upgrade-run "$@"
        ;;
    upgrade-run)
        check_upgrade "$@"
        rid=$1
        shift
        run_job upgrade "$rid" run_upgrade "$@"
        ;;
    upgrade-poll)
        upgrade_poll "$@"
        ;;
    audit-start)
        [ "$#" -eq 1 ] || die "audit-start erwartet eine Lauf-ID"
        check_rid audit "$1"
        start_job audit "$1" audit-run "$1"
        ;;
    audit-run)
        [ "$#" -eq 1 ] || die "audit-run erwartet eine Lauf-ID"
        check_rid audit "$1"
        run_job audit "$1" lynis_audit "$1"
        ;;
    audit-poll)
        audit_poll "$@"
        ;;
    audit-stop)
        audit_stop "$@"
        ;;
    scan)
        [ "$#" -ge 6 ] || die "scan erwartet Marke, Limit, Wartezeit, Groesse, Ausnahmen, -- und Pfade"
        check_mark "$1"
        check_num "$2"
        check_num "$3"
        check_num "$4"
        a1=$1
        a2=$2
        a3=$3
        a4=$4
        shift 4
        check_lists ^ "$@"
        guarded "$a1" "$a2" "$a3" scan-run "$a4" "$@"
        ;;
    watch)
        [ "$#" -ge 7 ] || die "watch erwartet Marke, Limit, Minuten, Groesse, 0/1, Ordner, -- und Pfade"
        check_mark "$1"
        check_num "$2"
        check_num "$3"
        check_num "$4"
        case $5 in
            0 | 1) ;;
            *) die "ungueltige Angabe zu clamd" ;;
        esac
        a1=$1
        a2=$2
        a3=$3
        a4=$4
        a5=$5
        shift 5
        check_lists / "$@"
        guarded "$a1" "$a2" n watch-run "$a3" "$a4" "$a5" "$@"
        ;;
    scan-run)
        [ "$#" -ge 5 ] || die "scan-run erwartet Sperre, Marke, Groesse, Ausnahmen, -- und Pfade"
        check_take "$1"
        check_mark "$2"
        check_num "$3"
        a1=$1
        a2=$2
        a3=$3
        shift 3
        check_lists ^ "$@"
        scan_run "$a1" "$a2" "$a3" "$@"
        ;;
    watch-run)
        [ "$#" -ge 7 ] || die "watch-run erwartet Sperre, Marke, Minuten, Groesse, 0/1, Ordner, -- und Pfade"
        check_take "$1"
        check_mark "$2"
        check_num "$3"
        check_num "$4"
        case $5 in
            0 | 1) ;;
            *) die "ungueltige Angabe zu clamd" ;;
        esac
        a1=$1
        a2=$2
        a3=$3
        a4=$4
        a5=$5
        shift 5
        check_lists / "$@"
        watch_run "$a1" "$a2" "$a3" "$a4" "$a5" "$@"
        ;;
    odd-scan)
        odd_scan "$@"
        ;;
    realtime-on)
        [ "$#" -ge 3 ] || die "realtime-on erwartet Groesse, Ausnahmen, -- und Ordner"
        check_num "$1"
        [ "$1" -le 1000 ] || die "ungueltige Zahl"
        realtime_on "$@"
        ;;
    realtime-off)
        [ "$#" -eq 0 ] || die "realtime-off erwartet keine Argumente"
        realtime_off
        ;;
    realtime-log)
        [ "$#" -eq 1 ] || die "realtime-log erwartet eine Zeile"
        check_line "$1"
        realtime_log "$1"
        ;;
    realtime-confirm)
        realtime_confirm "$@"
        ;;
    *)
        die "unbekannter Unterbefehl"
        ;;
esac
