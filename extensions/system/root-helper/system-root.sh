#!/bin/sh
# Hilfsskript des Moduls "Server-Zustand" fuer Befehle, die root brauchen.
#
# Die Einrichtung legt es root-eigen unter /usr/local/lib/nodvard-deck/system-root ab und erlaubt dem
# Benutzer des Dashboards per sudo nur dieses Skript statt aller Befehle. Jeder Unterbefehl prueft seine
# Argumente selbst, es gibt kein eval und keinen fremden Text in "sh -c". Jede Aenderung an dieser Datei
# braucht eine hoehere HELPER_VERSION und dieselbe Zahl unter root_helper in extension.toml.
#
# Unterbefehle:
#   version                  gibt HELPER_VERSION aus
#   service-restart <dienst> startet einen systemd-Dienst neu (ab Version 2). Name und Sperrliste wie in
#                            actions.py (UNIT_RE, DENY_*): Was das Dashboard nicht neu startet, startet auch dieses
#                            Skript nicht. Ob Docker auf dem Dashboard-Rechner laeuft, weiss nur das Dashboard; das
#                            prueft es vorher selbst. Ausgabe wie restart_command: "@@rc=<n>", dann der Zustand.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL
# Leerzeichen, Tabulator, Zeilenumbruch: nichts aus der Umgebung des Aufrufers.
IFS=' 	
'
umask 022

HELPER_VERSION=2

die() {
    printf '%s\n' "system-root: $1" >&2
    exit 64
}

service_restart() {
    [ "$#" -eq 1 ] || die "service-restart erwartet genau einen Dienstnamen"
    unit=$1
    case $unit in
        -* | *[!A-Za-z0-9@._:-]*) die "ungueltiger Dienstname" ;;
        *.service | *.socket | *.timer) ;;
        *) die "ungueltiger Dienstname" ;;
    esac
    base=${unit%.*}
    if [ -z "$base" ] || [ "${#base}" -gt 128 ]; then
        die "ungueltiger Dienstname"
    fi
    name=$(printf '%s' "${base%%@*}" | tr 'A-Z' 'a-z')
    case $name in
        ssh | sshd | ssh-* | sshd-* | dbus | dbus-broker | dbus-* | systemd-* | networking | networkmanager | dhcpcd | \
        wpa_supplicant | nodvard | nodvard-deck* | lattice*)
            printf '%s\n' "system-root: dieser Dienst wird nicht neu gestartet" >&2
            exit 77
            ;;
    esac
    rc=0
    systemctl restart -- "$unit" || rc=$?
    printf '%s\n' "@@rc=$rc"
    systemctl is-active -- "$unit"
}

[ "$#" -ge 1 ] || die "Unterbefehl fehlt"
command=$1
shift

case $command in
    version)
        [ "$#" -eq 0 ] || die "version erwartet keine Argumente"
        printf '%s\n' "$HELPER_VERSION"
        ;;
    service-restart)
        service_restart "$@"
        ;;
    *)
        die "unbekannter Unterbefehl"
        ;;
esac
