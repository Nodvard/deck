#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL
IFS=' 	
'
umask 022

HELPER_VERSION=18
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

check_qname() {
	case $1 in
		"" | .* | *[!A-Za-z0-9._-]*) die "ungueltiger Name in der Quarantaene" ;;
	esac
}

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

mode4() {
	case $1 in
		[0-7]) echo "000$1" ;;
		[0-7][0-7]) echo "00$1" ;;
		[0-7][0-7][0-7]) echo "0$1" ;;
		[0-7][0-7][0-7][0-7]) echo "$1" ;;
		*) return 1 ;;
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
	F=0
	confirm_finding "$here" || F=$?
	[ "$F" -eq 1 ] && { echo "@@msg=quarantine_not_found"; exit 6; }
	[ "$F" -eq 0 ] || { echo "@@msg=quarantine_unchecked"; exit 7; }
	I=$(stat -c %d:%i "$here" 2>/dev/null) || { echo "@@msg=gone"; exit 3; }
	trap 'trap "" TERM INT HUP; put_back "$check" "" >/dev/null 2>&1 || true; exit 143' TERM INT HUP
	mv -T -n "$here" "$check" 2>/dev/null || { trap - TERM INT HUP; echo "@@msg=gone"; exit 3; }
	if [ "$(stat -c %d:%i "$here" 2>/dev/null)" = "$I" ]; then
		trap - TERM INT HUP
		echo "@@msg=quarantine_check_busy"
		exit 5
	fi
	[ -e "$check" ] || [ -L "$check" ] || { echo "@@msg=gone"; exit 3; }
	if [ -L "$check" ]; then rm -f "$check"; echo "@@msg=symlink_swapped"; exit 5; fi
	[ -f "$check" ] || { echo "@@msg=quarantine_kept $check"; exit 4; }
	if [ "$(stat -c %h "$check")" != 1 ]; then rm -f "$check"; echo "@@msg=hardlink_swapped"; exit 5; fi
	M=$(mode4 "$(stat -c %a "$check")")
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
	[ -e "$QDIR/.herkunft" ] || [ -L "$QDIR/.herkunft" ] || { echo "@@msg=restore_no_origin"; exit 3; }
	qdir_safe pruefen "$QDIR/.herkunft" || { echo "@@msg=quarantine_dir_unsafe"; exit 5; }
	[ -f "$origin" ] && [ ! -L "$origin" ] || { echo "@@msg=restore_no_origin"; exit 3; }
	mode=$(mode4 "$(sed -n 1p "$origin")") || { echo "@@msg=restore_no_origin"; exit 3; }
	original=$(sed 1d "$origin"; echo x)
	original=${original%x}
	original=${original%?}
	split_path "$original"
	here="./$name"
	up="../$name"
	if [ "$parent" = / ]; then stage_base=; else stage_base=$parent; fi
	cd -P "$parent" 2>/dev/null || { echo "@@msg=restore_folder_missing"; exit 3; }
	[ "$(pwd -P)" = "$parent" ] || { echo "@@msg=restore_link"; exit 5; }
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
	printf '@@restored_to=%s\n' "$original"
	echo ok
}

quarantine_state() {
	[ "$#" -eq 1 ] || die "quarantine-state erwartet einen Namen"
	check_qname "$1"
	set +e
	if [ -f "$QDIR/$1" ] && [ ! -L "$QDIR/$1" ]; then
		echo "@@state=quarantined"
		M=
		if [ -f "$QDIR/.herkunft/$1" ] && [ ! -L "$QDIR/.herkunft/$1" ]; then
			M=$(sed -n 1p "$QDIR/.herkunft/$1" 2>/dev/null)
		fi
		if M=$(mode4 "$M"); then echo "@@mode=$M"; fi
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

pkg_install() {
	if command -v rpm-ostree >/dev/null 2>&1 || [ -f /run/ostree-booted ]; then
		echo '@@msg=rpm_ostree'
		exit 2
	fi
	if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -q &&
		DEBIAN_FRONTEND=noninteractive apt-get install -y -q $1
	elif command -v dnf >/dev/null; then dnf install -y $2
	elif command -v apk >/dev/null; then apk add $3
	else echo 'Kein bekannter Paketmanager'; exit 2; fi
}

install_package() {
	[ "$#" -eq 1 ] || die "install erwartet ein Paket"
	case $1 in
		clamav | lynis | fail2ban | unattended | signatures) ;;
		*) die "unbekanntes Paket" ;;
	esac
	set +e
	case $1 in
		clamav)
			pkg_install 'clamav clamav-freshclam' 'clamav clamav-update' 'clamav clamav-libunrar'
			;;
		lynis)
			pkg_install lynis lynis lynis
			;;
		fail2ban)
			pkg_install fail2ban fail2ban fail2ban
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

one_rid() {
	[ "$#" -eq 3 ] || die "$2 erwartet eine Lauf-ID"
	check_rid "$1" "$3"
}

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

job_alive() {
	job_kind "$1"
	p=$(cat "$JD/$2.pid" 2>/dev/null)
	[ -n "$p" ] && [ -r "/proc/$p/cmdline" ] && tr '\000' ' ' <"/proc/$p/cmdline" | grep -q "$2"
}

job_poll() {
	kind=$1
	shift
	one_rid "$kind" "$kind-poll" "$@"
	rid=$1
	log=$JD/$rid.log
	set +e
	if job_alive "$kind" "$rid"; then
		echo @@running
		echo @@tail
		[ "$kind" = audit ] || tail -n 1 "$log" 2>/dev/null
	elif [ -f "$JD/$rid.rc" ]; then
		echo "@@rc=$(cat "$JD/$rid.rc")"
		if [ "$kind" = upgrade ]; then
			echo @@summary
			grep -E 'upgraded, .* newly installed|aktualisiert, .* neu installiert|^E:' "$log" 2>/dev/null | tail -n 5
			awk '/(have been kept back|zur.*ckgehalten).*:[ \t]*$/ { if (f) print ""; f = 1; printf "@@kept-back:"; next } f && /^[ \t]+[^ \t]/ { $1 = $1; printf " %s", $0; next } f { f = 0; print "" } END { if (f) print "" }' \
				"$log" 2>/dev/null | head -n 2
			echo @@tail
			tail -c 20000 "$log" 2>/dev/null
		else
			echo @@tail
			tail -c 262144 "$log" 2>/dev/null
		fi
	elif [ -n "$p" ]; then
		echo @@lost
		if [ "$kind" = upgrade ]; then
			echo @@summary
			dpkg --audit 2>/dev/null | head -n 5
		fi
		echo @@tail
		tail -n 20 "$log" 2>/dev/null
	else
		echo @@unknown
		echo @@tail
	fi
}

lynis_audit() {
	rid=$1
	PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
	command -v lynis >/dev/null 2>&1 || { echo @@nolynis; exit 0; }
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

audit_stop() {
	one_rid audit audit-stop "$@"
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

check_num() {
	case $1 in
		'' | *[!0-9]* | 0*) die "ungueltige Zahl" ;;
	esac
	[ "${#1}" -le 6 ] || die "ungueltige Zahl"
}

check_lists() {
	first=$1
	shift "$(($2 + 2))"
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

check_take() {
	case $1 in
		- | n) ;;
		w*) check_num "${1#w}" ;;
		*) die "ungueltige Sperre" ;;
	esac
}

check_clamd() {
	case $1 in
		0 | 1) ;;
		*) die "ungueltige Angabe zu clamd" ;;
	esac
}

guarded() {
	inner=$1
	mark=$2
	limit_s=$3
	wait=$4
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

scan_run() {
	take=$1
	mark=$2
	mb=$3
	shift 3
	set +eu
	take_lock "$take" "$mark"
	resolve_roots "@@scan-roots-${mark#@@scan-rc-}" "$@"
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
	p=$(systemctl show -p MainPID --value clamav-clamonacc) || p=0
	sleep "$RT_SETTLE"
	[ "${p:-0}" != 0 ] && [ "$(systemctl show -p MainPID --value clamav-clamonacc)" = "$p" ] || realtime_fail
	echo @@realtime-on
}

realtime_fail() {
	echo @@msg=realtime_not_started
	journalctl -u clamav-clamonacc -n 20 --no-pager 2>&1 || true
	rt_undo
	exit 4
}

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

check_line() {
	case $1 in
		0) ;;
		'' | *[!0-9]* | 0*) die "ungueltige Zeile" ;;
	esac
	[ "${#1}" -le 12 ] || die "ungueltige Zeile"
}

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
	guard) guard "$@" ;;
	detect-templates) detect_templates "$@" ;;
	refresh-packages) refresh_packages "$@" ;;
	quarantine) quarantine "$@" ;;
	restore) restore "$@" ;;
	delete) delete "$@" ;;
	quarantine-state) quarantine_state "$@" ;;
	ban) fail2ban_set banip "$@" ;;
	unban) fail2ban_set unbanip "$@" ;;
	install) install_package "$@" ;;
	reboot) reboot_host "$@" ;;
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
	upgrade-poll | audit-poll) job_poll "${command%-poll}" "$@" ;;
	audit-start)
		one_rid audit audit-start "$@"
		start_job audit "$1" audit-run "$1"
		;;
	audit-run)
		one_rid audit audit-run "$@"
		run_job audit "$1" lynis_audit "$1"
		;;
	audit-stop) audit_stop "$@" ;;
	scan)
		[ "$#" -ge 6 ] || die "scan erwartet Marke, Limit, Wartezeit, Groesse, Ausnahmen, -- und Pfade"
		check_mark "$1"
		check_num "$2"
		check_num "$3"
		check_num "$4"
		check_lists ^ 4 "$@"
		guarded scan-run "$@"
		;;
	watch)
		[ "$#" -ge 7 ] || die "watch erwartet Marke, Limit, Minuten, Groesse, 0/1, Ordner, -- und Pfade"
		check_mark "$1"
		check_num "$2"
		check_num "$3"
		check_num "$4"
		check_clamd "$5"
		check_lists / 5 "$@"
		mark=$1
		limit_s=$2
		shift 2
		guarded watch-run "$mark" "$limit_s" n "$@"
		;;
	scan-run)
		[ "$#" -ge 5 ] || die "scan-run erwartet Sperre, Marke, Groesse, Ausnahmen, -- und Pfade"
		check_take "$1"
		check_mark "$2"
		check_num "$3"
		check_lists ^ 3 "$@"
		scan_run "$@"
		;;
	watch-run)
		[ "$#" -ge 7 ] || die "watch-run erwartet Sperre, Marke, Minuten, Groesse, 0/1, Ordner, -- und Pfade"
		check_take "$1"
		check_mark "$2"
		check_num "$3"
		check_num "$4"
		check_clamd "$5"
		check_lists / 5 "$@"
		watch_run "$@"
		;;
	odd-scan) odd_scan "$@" ;;
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
	realtime-confirm) realtime_confirm "$@" ;;
	*) die "unbekannter Unterbefehl" ;;
esac
