#!/bin/sh
# Modul Docker, "Nur bedienen": feste Unterbefehle statt Gruppe docker. Aenderung: HELPER_VERSION und extension.toml anheben.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL
IFS='
'
umask 022
HOME=/root
export HOME
# Nur der Docker-Dienst dieses Rechners, nichts aus der Umgebung des Aufrufers. DOCKER_HOST schlaegt jeden Kontext aus
# /root/.docker; DOCKER_CONFIG zeigt auf einen Ordner, den es nicht gibt, damit auch Plugins und uebrige Eintraege von
# dort nicht gelten. Er laege im Ordner der Hilfsskripte, den nur root aendern kann.
unset DOCKER_CONTEXT DOCKER_CERT_PATH DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_API_VERSION
unset DOCKER_CLI_PLUGIN_EXTRA_DIRS DOCKER_DEFAULT_PLATFORM
DOCKER_HOST=unix:///var/run/docker.sock
DOCKER_CONFIG=/usr/local/lib/nodvard-deck/docker-root-config
export DOCKER_HOST DOCKER_CONFIG

HELPER_VERSION=2
MAX_ITEMS=200
MAX_TAIL=5000
PS_FORMAT='{{.ID}}|{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}|{{.Label "com.docker.compose.project.working_dir"}}'
STATS_FORMAT='{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'
PORTS_FORMAT='{{.Id}}|{{json .HostConfig.PortBindings}}'
DETAILS_FORMAT='[{"Name":{{json .Name}},"Image":{{json .Image}},"Created":{{json .Created}},"RestartCount":{{json .RestartCount}},"State":{{with .State}}{"Running":{{json .Running}},"StartedAt":{{json .StartedAt}},"FinishedAt":{{json .FinishedAt}},"ExitCode":{{json .ExitCode}},"OOMKilled":{{json .OOMKilled}},"Health":{{if .Health}}{"Status":{{json .Health.Status}}}{{else}}null{{end}}}{{end}},"HostConfig":{{with .HostConfig}}{"RestartPolicy":{"Name":{{json .RestartPolicy.Name}}},"Privileged":{{json .Privileged}},"NetworkMode":{{json .NetworkMode}},"Memory":{{json .Memory}}}{{end}},"NetworkSettings":{{with .NetworkSettings}}{"Ports":{{json .Ports}},"Networks":{ {{- $f := 1}}{{range $n, $v := .Networks}}{{if not $f}},{{end}}{{$f = 0}}{{json $n}}:{"IPAddress":{{json $v.IPAddress}}}{{end -}} }}{{end}},"Mounts":[{{range $i, $m := .Mounts}}{{if $i}},{{end}}{"Type":{{json .Type}},"Name":{{json .Name}},"Source":{{json .Source}},"Destination":{{json .Destination}},"RW":{{json .RW}}}{{end}}],"Config":{"Image":{{json .Config.Image}},"Env":[{{range $i, $e := .Config.Env}}{{if $i}},{{end}}{{json (index (split . "=") 0)}}{{end}}],"Labels":{{with .Config.Labels}}{"com.docker.compose.project":{{json (index . "com.docker.compose.project")}},"com.docker.compose.service":{{json (index . "com.docker.compose.service")}},"com.docker.compose.project.working_dir":{{json (index . "com.docker.compose.project.working_dir")}}}{{else}}{}{{end}}}}]'
STATE_FORMAT='{"name":{{json .Name}},"image_id":{{json .Image}},"status":{{json .State.Status}},"exit_code":{{json .State.ExitCode}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"restarts":{{json .RestartCount}},"started":{{json .State.StartedAt}}}'

die() {
	echo "docker-root: $1" >&2
	exit 64
}

# Wie CONTAINER_NAME_RE und DOCKER_ID_RE im Modul.
check_name() {
	case $1 in
	'' | [!A-Za-z0-9]* | *[!A-Za-z0-9_.-]*) die "ungueltiger Name" ;;
	esac
	[ "${#1}" -le 255 ] || die "Name zu lang"
}

check_id() {
	case $1 in
	'' | *[!0-9a-f]*) die "ungueltige ID" ;;
	esac
	[ "${#1}" -ge 12 ] && [ "${#1}" -le 64 ] || die "ungueltige ID"
}

# names <hoechstens> <name>...
names() {
	max=$1
	shift
	[ "$#" -ge 1 ] && [ "$#" -le "$max" ] || die "falsche Zahl von Containern"
	for item in "$@"; do check_name "$item"; done
}

[ "$#" -ge 1 ] || die "Unterbefehl fehlt"
command=$1
shift
case $command in
version | ps | stats) [ "$#" -eq 0 ] || die "zu viele Argumente" ;;
esac
case $command in
version) echo "$HELPER_VERSION" ;;
ps) exec docker ps -a --format "$PS_FORMAT" ;;
stats) exec docker stats --no-stream --format "$STATS_FORMAT" ;;
ports)
	[ "$#" -ge 1 ] && [ "$#" -le "$MAX_ITEMS" ] || die "falsche Zahl von Containern"
	for item in "$@"; do check_id "$item"; done
	exec docker inspect --type container --format "$PORTS_FORMAT" -- "$@"
	;;
inspect)
	names 1 "$@"
	exec docker inspect --type container --format "$DETAILS_FORMAT" -- "$1"
	;;
id)
	names 1 "$@"
	exec docker inspect --type container -f '{{.Id}}' -- "$1"
	;;
id-state)
	names 1 "$@"
	exec docker inspect --type container -f '{{.Id}}|{{.State.Status}}' -- "$1"
	;;
state)
	names "$MAX_ITEMS" "$@"
	exec docker container inspect --format "$STATE_FORMAT" -- "$@"
	;;
logs)
	[ "$#" -eq 4 ] || die "logs <zeilen> <seit|-> 0|1 <name>"
	case $1 in
	'' | *[!0-9]*) die "ungueltige Zeilenzahl" ;;
	esac
	[ "${#1}" -le 4 ] && [ "$1" -le "$MAX_TAIL" ] || die "ungueltige Zeilenzahl"
	case $2 in
	- | [1-9]m | [1-9]h | [1-9]*[0-9]m | [1-9]*[0-9]h)
		n=${2%[mh]}
		case $n in -) ;; *[!0-9]*) die "ungueltiger Zeitraum" ;; esac
		[ "${#n}" -le 4 ] || die "ungueltiger Zeitraum"
		;;
	*) die "ungueltiger Zeitraum" ;;
	esac
	case $3 in
	0 | 1) ;;
	*) die "folgen ist 0 oder 1" ;;
	esac
	names 1 "$4"
	name=$4
	since=$2
	follow=$3
	set -- logs --tail "$1" --timestamps
	[ "$since" = "-" ] || set -- "$@" --since "$since"
	[ "$follow" = "0" ] || set -- "$@" --follow
	exec docker "$@" -- "$name"
	;;
start | stop | restart)
	names 1 "$@"
	exec docker "$command" -- "$1"
	;;
*) die "unbekannter Unterbefehl" ;;
esac
