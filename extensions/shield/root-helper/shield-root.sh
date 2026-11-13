#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LC_ALL=C
export LC_ALL
IFS=' 	
'
umask 022

HELPER_VERSION=26
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
	{
		sshd_error=$({ (sshd -T || /usr/sbin/sshd -T) | grep -iE \
			'^(port|permitrootlogin|passwordauthentication|permitemptypasswords|pubkeyauthentication|maxauthtries|x11forwarding|kbdinteractiveauthentication) '; } 2>&1 1>&3)
		echo @@sshderror
		printf '%s\n' "$sshd_error" | sed 's/^/!/'
	} 3>&1
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

package_inventory() {
	[ "$#" -eq 0 ] || die "package-inventory erwartet keine Argumente"
	echo @@os
	cat "$ETCDIR/os-release"
	echo @@packages
	if command -v dpkg-query >/dev/null 2>&1; then
		dpkg-query -W -f='${db:Status-Abbrev}\t${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}\n'
	elif command -v rpm >/dev/null 2>&1; then
		rpm -qa --qf 'ii \t%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{SOURCERPM}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\t%{RSAHEADER:pgpsig}\n'
	else
		echo @@unsupported
	fi
	echo @@end
}

refresh_packages() {
	[ "$#" -eq 0 ] || die "refresh-packages erwartet keine Argumente"
	set +eu
	refresh_rc=0
	refresh_out=
	if command -v apt-get >/dev/null 2>&1; then
		refresh_out=$(apt-get update -qq 2>&1) || refresh_rc=$?
		refresh_out=$(printf '%s\n' "$refresh_out" | awk -v failed="$refresh_rc" '/^(E|Err|W):/ || (failed && !/^(Hit|Get|Ign):/)')
	elif command -v dnf >/dev/null 2>&1; then
		refresh_out=$(dnf -q makecache 2>&1) || refresh_rc=$?
	elif command -v apk >/dev/null 2>&1; then
		refresh_out=$(apk update -q 2>&1) || refresh_rc=$?
	fi
	printf '%s\n' "$refresh_out" | sed -E 's#(https?://)[^/[:space:]]*@#\1#g' | tail -n 20 | cut -c 1-500
	return "$refresh_rc"
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
	rm -f "$QDIR/$1" "$QDIR/.herkunft/$1" && echo ok
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

fail2ban_jails() {
	printf 'sshd\n'
	for f in "$ETCDIR"/fail2ban/jail.conf "$ETCDIR"/fail2ban/jail.local "$ETCDIR"/fail2ban/jail.d/*.conf "$ETCDIR"/fail2ban/jail.d/*.local; do
		[ -f "$f" ] || continue
		sed -n 's/^[[:space:]]*\[\([A-Za-z0-9][A-Za-z0-9_.-]*\)\][[:space:]]*$/\1/p' "$f"
	done | grep -viE '^(DEFAULT|INCLUDES|logtarget|loglevel|syslogsocket|allowipv6|dbfile|dbpurgeage|dbmaxmatches|thread)$' | sort -u
}

fail2ban_config_read() {
	[ "$#" -eq 0 ] || die "fail2ban-config-read erwartet keine Argumente"
	runtime=1
	for key in bantime findtime maxretry ignoreip; do
		printf '@@%s\n' "$key"
		if value=$(fail2ban-client get sshd "$key" 2>/dev/null); then
			printf '%s\n' "$value"
		else
			runtime=0
			case $key in
				bantime | findtime) echo 600 ;;
				maxretry) echo 5 ;;
				ignoreip) echo '127.0.0.1/8 ::1' ;;
			esac
		fi
	done
	echo @@available
	fail2ban_jails
	echo @@status
	fail2ban-client status 2>/dev/null || runtime=0
	echo @@runtime
	echo "$runtime"
	echo @@managed
	if [ -f "$ETCDIR/fail2ban/jail.d/nodvard.local" ]; then cat "$ETCDIR/fail2ban/jail.d/nodvard.local"; fi
}

lockdir_safe() {
	mkdir -m 700 "$LOCKDIR" 2>/dev/null || true
	[ -d "$LOCKDIR" ] && [ ! -L "$LOCKDIR" ] || return 1
	case "$(stat -c %u:%a "$LOCKDIR" 2>/dev/null)" in "$(id -u):700" | "$(id -u):2700") ;; *) return 1 ;; esac
}

fail2ban_config_write() {
	[ "$#" -eq 2 ] || die "fail2ban-config-write erwartet SSH-Adresse und Inhalt"
	case $1 in '' | *[!0-9A-Fa-f:.]*) die "ungueltige SSH-Adresse" ;; esac
	[ "${#1}" -le 45 ] && [ "${#2}" -le 65536 ] || die "Konfiguration zu lang"
	printf '%s\n' "$2" | awk -v peer="$1" '
		function complete() {
			if (jail == "") return 1
			return seen["enabled"] && (!on || (seen["bantime"] && seen["findtime"] && seen["maxretry"] && seen["ignoreip"]))
		}
		/^$/ { next }
		/^\[[A-Za-z0-9][A-Za-z0-9_.-]*\]$/ {
			if (!complete()) exit 1
			jail=substr($0, 2, length($0)-2)
			if (length(jail)>64 || used[jail]++) exit 1
			delete seen; on=0; next
		}
		/^(enabled|bantime|findtime|maxretry|ignoreip|backend) = / {
			if (jail == "" || seen[$1]++) exit 1
			key=$1; value=substr($0, length(key)+4)
			if (key == "enabled") {
				if (value != "true" && value != "false") exit 1
				on=(value == "true"); if (jail == "sshd" && on) ssh=1
			} else if (key == "backend") {
				if (jail != "sshd" || value != "systemd") exit 1
			} else if (key == "ignoreip") {
				if (value !~ /^[0-9A-Fa-f:./ ]+$/) exit 1
				n=split(value, ips, " "); found=0
				for (i=1; i<=n; i++) { if (length(ips[i])>49) exit 1; if (ips[i]==peer) found=1 }
				if (!found) exit 1
			} else {
				if (value !~ /^[0-9]+$/ || length(value)>8 || value+0<1 || value+0>(key=="maxretry" ? 1000 : 31536000)) exit 1
			}
			next
		}
		{ exit 1 }
		END { if (!ssh || !complete()) exit 1 }
	' || die "ungueltiger Inhalt"
	known=$(fail2ban_jails)
	for jail in $(printf '%s\n' "$2" | sed -n 's/^\[\(.*\)\]$/\1/p'); do
		printf '%s\n' "$known" | grep -Fxq "$jail" || die "unbekannter Jail"
	done
	dir=$ETCDIR/fail2ban/jail.d
	[ ! -L "$dir" ] && [ ! -L "$dir/nodvard.local" ] || die "Konfiguration ist ein Link"
	[ -d "$dir" ] || mkdir -p "$dir"
	lockdir_safe || die "unsicherer Sperrordner"
	exec 9> "$LOCKDIR/fail2ban-config.lock"
	flock -x 9
	target=$dir/nodvard.local
	tmp=$(mktemp "$dir/.nodvard.XXXXXX")
	backup=$(mktemp "$dir/.nodvard-backup.XXXXXX")
	had=0
	if [ -e "$target" ]; then cp -p "$target" "$backup"; had=1; fi
	committed=0
	trap 'if [ "$committed" -eq 0 ]; then if [ "$had" -eq 1 ]; then mv -f "$backup" "$target"; else rm -f "$target"; fi; fi; rm -f "$tmp" "$backup"' EXIT
	trap 'exit 1' HUP INT TERM
	printf '%s\n' "$2" > "$tmp"
	chmod 644 "$tmp"
	mv -f "$tmp" "$target"
	fail2ban-client -t
	dump=$(fail2ban-client -d)
	printf '%s\n' "$dump" | awk -v peer="$1" '
		{ gsub(/[][\047,]/, " ") }
		$1 == "add" { active[$2]=1 }
		$1 == "set" && $3 == "addignoreip" { for (i=4; i<=NF; i++) if ($i==peer) protected[$2]=1 }
		END { if (!active["sshd"]) exit 1; for (j in active) if (!protected[j]) exit 1 }
	' || { echo @@msg=fail2ban_unprotected; exit 1; }
	printf '%s\n' "$dump" | awk '
		NR == FNR {
			if ($0 ~ /^\[/) jail=substr($0, 2, length($0)-2)
			if ($1 == "enabled") enabled[jail]=($3 == "true")
			if ($1 ~ /^(bantime|findtime|maxretry)$/) wanted[jail, $1]=$3
			if ($1 == "ignoreip") for (i=3; i<=NF; i++) wanted_ignore[jail, $i]=1
			next
		}
		{ gsub(/[][\047,]/, " ") }
		$1 == "add" { active[$2]=1 }
		$1 == "set" && $3 ~ /^(bantime|findtime|maxretry)$/ { effective[$2, $3]=$4 }
		$1 == "set" && $3 == "addignoreip" { for (i=4; i<=NF; i++) effective_ignore[$2, $i]=1 }
		END {
			for (ip in wanted_ignore) if (!(ip in effective_ignore)) exit 1
			for (ip in effective_ignore) if (!(ip in wanted_ignore)) exit 1
			for (j in enabled) if (enabled[j] != (j in active)) exit 1
			for (j in active) {
				if (!enabled[j]) exit 1
				n=split("bantime findtime maxretry", keys, " ")
				for (i=1; i<=n; i++) if (effective[j, keys[i]] != wanted[j, keys[i]]) exit 1
			}
		}
	' "$target" - || { echo @@msg=fail2ban_conflict; exit 1; }
	fail2ban-client reload
	committed=1
	echo ok
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
			if [ ! -f "$ETCDIR/fail2ban/jail.d/nodvard.local" ]; then
				mkdir -p "$ETCDIR/fail2ban/jail.d"
				printf '[sshd]\nenabled = true\nbackend = systemd\n' > "$ETCDIR/fail2ban/jail.d/nodvard.local"
			fi
			fail2ban-client -t || exit 1
			systemctl enable --now fail2ban 2>/dev/null
			fail2ban-client reload || exit $?
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
			*[!A-Za-z0-9+._:~-]* | *-) die "ungueltiger Paketname" ;;
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
			if lockdir_safe &&
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
	export ODD_SCAN_OUT=$X ODD_SCAN_RC=$Y
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
		case $r:$n in
			*[!0-9:]* | : | :* | *:)
				rm -f "$L" "$X" "$Y" "$K"
				die "ungueltige Ergebnisdatei"
				;;
		esac
		T=$((T + n))
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
	[ "$1" = "${ODD_SCAN_OUT:-}" ] && [ "$2" = "${ODD_SCAN_RC:-}" ] || die "ungueltige Datei"
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

ssh_reload() {
	if [ -d "$SDMARK" ]; then
		systemctl reload sshd 2>/dev/null || systemctl reload ssh
	else
		service sshd reload 2>/dev/null || service ssh reload
	fi
}

ssh_effective() {
	sshd -T -ddd > "$ssh_state/effective" 2> "$ssh_state/trace" || return 1
	grep -q '^debug[123]:' "$ssh_state/trace" || return 1
	# Match braucht die Daten jeder Verbindung.
	if grep -qi "^debug3: checking syntax for 'Match[[:space:]]" "$ssh_state/trace"; then return 1; fi
	grep -qx 'passwordauthentication no' "$ssh_state/effective" &&
	grep -qx 'kbdinteractiveauthentication no' "$ssh_state/effective" &&
	grep -qx 'maxauthtries 3' "$ssh_state/effective" &&
	grep -qxE "permitrootlogin $ssh_root" "$ssh_state/effective"
}

# Stehen Keys fuer root in authorized_keys, bleibt root mit Key erlaubt (sonst ausgesperrt).
ssh_root_keys() {
	ssh_root_home=$(getent passwd root | cut -d: -f6)
	case $ssh_root_home in /*) ;; *) return 1 ;; esac
	for ssh_keys in "$ssh_root_home/.ssh/authorized_keys" "$ssh_root_home/.ssh/authorized_keys2"; do
		[ -f "$ssh_keys" ] || continue
		grep -Eq '^[^#]*(^|[[:space:]])(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)(-cert-v01@openssh\.com)?[[:space:]]+[A-Za-z0-9+/]' "$ssh_keys" && return 0
	done
	return 1
}

# Zwei-Faktor per PAM oder AuthenticationMethods: KbdInteractiveAuthentication no wuerde Schluessel plus Code brechen.
ssh_two_factor() {
	grep -rqsEi '^[[:space:]]*AuthenticationMethods[[:space:]].*keyboard-interactive' "$ETCDIR/ssh/sshd_config" "$ETCDIR/ssh/sshd_config.d" && return 0
	grep -qsE '^[^#]*pam_(google_authenticator|oath|duo|yubico|radius_auth)' "$ETCDIR/pam.d/sshd"
}

ssh_remove() {
	ssh_backup=
	if [ -f "$ssh_conf" ]; then
		ssh_backup=$(mktemp "$ssh_state/ssh-conf.XXXXXX") || return 1
		if ! cp -p "$ssh_conf" "$ssh_backup"; then
			rm -f "$ssh_backup"
			return 1
		fi
	fi
	if ! rm -f "$ssh_conf"; then
		[ -z "$ssh_backup" ] || rm -f "$ssh_backup"
		return 1
	fi
	if sshd -t && ssh_reload; then
		[ -z "$ssh_backup" ] || rm -f "$ssh_backup"
		return 0
	fi
	[ -z "$ssh_backup" ] || mv -f "$ssh_backup" "$ssh_conf"
	return 1
}

# Unbestaetigter Zustand ueberlebt einen Neustart: Marke unter /etc und (mit systemd) eine Einheit, die vor sshd zuruecknimmt.
ssh_pending_set() {
	printf '%s\n' "$1" > "$ssh_state/pending"
	printf '%s\n' "$1" > "$ssh_mark"
	if [ -d "$SDMARK" ]; then
		mkdir -p "$ssh_unit_dir/multi-user.target.wants" || return 0
		printf '[Unit]\nDescription=Nodvard Shield: unbestaetigte SSH-Haertung zuruecknehmen\nBefore=ssh.service sshd.service\n\n[Service]\nType=oneshot\nExecStart=%s ssh-hardening boot-revert root\n\n[Install]\nWantedBy=multi-user.target\n' "$SELF" > "$ssh_unit_dir/nodvard-ssh-revert.service"
		ln -sf "../nodvard-ssh-revert.service" "$ssh_unit_dir/multi-user.target.wants/nodvard-ssh-revert.service"
	fi
}

ssh_pending_clear() {
	rm -f "$ssh_state/pending" "$ssh_mark" "$ssh_unit_dir/nodvard-ssh-revert.service" "$ssh_unit_dir/multi-user.target.wants/nodvard-ssh-revert.service"
}

# Eigene Einheit, damit das Sitzungsende (KillUserProcesses, use_pty) den Rueckweg nicht mitnimmt.
ssh_expire_start() {
	if [ -d "$SDMARK" ] && command -v systemd-run >/dev/null 2>&1 &&
		systemd-run --unit="nodvard-ssh-expire-$1" --collect --quiet /bin/sh "$SELF" ssh-hardening expire "$ssh_user" "$1" </dev/null >/dev/null 2>&1 9>&-; then
		return 0
	fi
	if command -v setsid >/dev/null 2>&1; then
		nohup setsid "$SELF" ssh-hardening expire "$ssh_user" "$1" </dev/null >/dev/null 2>&1 9>&- &
	else
		nohup "$SELF" ssh-hardening expire "$ssh_user" "$1" </dev/null >/dev/null 2>&1 9>&- &
	fi
}

ssh_hardening() {
	[ "$#" -ge 2 ] || die "ssh-hardening: operation user"
	ssh_op=$1
	ssh_user=$2
	shift 2
	case $ssh_user in '' | -* | *[!a-zA-Z0-9_.-]*) die "invalid user" ;; esac
	[ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ] || [ "$SUDO_USER" = "$ssh_user" ] || die "wrong user"
	case $ssh_op in
		status) [ "$#" -eq 0 ] || [ "$#" -eq 2 ] || die "status: optional public key" ;;
		add-key) [ "$#" -eq 2 ] || die "add-key: public key" ;;
		apply | confirm | rollback | expire)
			[ "$#" -eq 1 ] || die "missing token"
			case $1 in *[!0-9a-f]* | '') die "invalid token" ;; esac
			[ "${#1}" -eq 32 ] || die "invalid token"
			;;
		off | boot-revert) [ "$#" -eq 0 ] || die "$ssh_op: no arguments" ;;
		*) die "invalid operation" ;;
	esac
	if [ "$ssh_op" = expire ]; then
		sleep 60
	fi
	ssh_dir=$ETCDIR/ssh/sshd_config.d
	ssh_conf=$ssh_dir/nodvard-haertung.conf
	ssh_state=$TMPRUN/nodvard-shield-ssh
	ssh_mark=$ETCDIR/ssh/nodvard-haertung.pending
	ssh_unit_dir=$ETCDIR/systemd/system
	[ ! -L "$ETCDIR/ssh" ] && [ ! -L "$ssh_dir" ] && [ ! -L "$ssh_conf" ] && [ ! -L "$ssh_state" ] || die "unsafe path"
	mkdir -p "$ssh_state"
	chmod 700 "$ssh_state"
	exec 9> "$ssh_state/lock"
	flock -w 10 9 || die "busy"
	if [ "$ssh_op" = boot-revert ]; then
		# Nur wenn /run leer ist (Neustart), nie mitten im Bestaetigungsfenster.
		if [ -f "$ssh_mark" ] && [ ! -f "$ssh_state/pending" ]; then
			rm -f "$ssh_conf"
			ssh_pending_clear
		fi
		return 0
	fi
	ssh_root_config=no
	if [ "$ssh_user" = root ]; then
		ssh_root_config=prohibit-password
	elif [ "$ssh_op" = apply ]; then
		! ssh_root_keys || ssh_root_config=prohibit-password
	elif [ -f "$ssh_conf" ] && grep -qx 'PermitRootLogin prohibit-password' "$ssh_conf"; then
		ssh_root_config=prohibit-password
	fi
	ssh_root=no
	[ "$ssh_root_config" = no ] || ssh_root='(without-password|prohibit-password)'
	if [ "$ssh_op" = status ] || [ "$ssh_op" = add-key ]; then
		if [ "$#" -eq 2 ]; then
			case $1 in ssh-ed25519 | ssh-rsa | ecdsa-sha2-nistp256 | ecdsa-sha2-nistp384 | ecdsa-sha2-nistp521) ;; *) die "invalid key type" ;; esac
			case $2 in '' | *[!A-Za-z0-9+/=]*) die "invalid public key" ;; esac
			[ "${#2}" -le 16384 ] || die "public key too long"
			printf '%s %s\n' "$1" "$2" > "$ssh_state/key"
			ssh-keygen -l -f "$ssh_state/key" >/dev/null || die "invalid public key"
			ssh_home=$(getent passwd "$ssh_user" | cut -d: -f6)
			case $ssh_home in /*) ;; *) die "missing home" ;; esac
			runuser -u "$ssh_user" -- sh -s -- "$ssh_home" "$ssh_op" "$1" "$2" "$ssh_user" <<'SHIELD_AUTHORIZED_KEYS' || die "authorized_keys failed"
				cd "$1" || exit 1
				if [ "$2" = add-key ]; then
					umask 077
					[ ! -L .ssh ] || exit 1
					[ "$5" = root ] || [ ! -L .ssh/authorized_keys ] || exit 1
					mkdir -p .ssh && chmod 700 .ssh || exit 1
					[ -e .ssh/authorized_keys ] || touch .ssh/authorized_keys || exit 1
					[ -L .ssh/authorized_keys ] || chmod 600 .ssh/authorized_keys || exit 1
				fi
				if awk -v t="$3" -v k="$4" '!/^ *#/ {for(i=1;i<NF;i++) if($i == t && $(i+1) == k) found=1} END {exit !found}' .ssh/authorized_keys 2>/dev/null; then
					echo "@@authorized=yes"
				elif [ "$2" = add-key ]; then
					printf "\n%s %s\n" "$3" "$4" >> .ssh/authorized_keys || exit 1
					echo "@@authorized=yes"
				else
					echo "@@authorized=no"
				fi
SHIELD_AUTHORIZED_KEYS
		fi
		if [ -f "$ssh_conf" ] && [ ! -f "$ssh_state/pending" ] && [ ! -f "$ssh_mark" ] && ssh_effective; then echo '@@hardened=yes'; else echo '@@hardened=no'; fi
		[ ! -f "$ssh_mark" ] || [ -f "$ssh_state/pending" ] || echo '@@unconfirmed=yes'
		[ ! -f "$ssh_conf" ] || echo '@@managed=yes'
		[ "$ssh_user" = root ] || ! ssh_root_keys || echo '@@root_keys=yes'
		! ssh_two_factor || echo '@@two_factor=yes'
		return
	fi
	case $ssh_op in
		apply)
			[ ! -e "$ssh_conf" ] && [ ! -e "$ssh_state/pending" ] || die "already configured or pending"
			[ -x "$SELF" ] && { command -v nohup >/dev/null || { [ -d "$SDMARK" ] && command -v systemd-run >/dev/null; }; } || die "rollback unavailable"
			sshd -t || { printf '%s\n' "shield-root: sshd rejects the current configuration, nothing changed" >&2; return 1; }
			! ssh_two_factor || { printf '%s\n' "shield-root: two-factor login detected (PAM or AuthenticationMethods), nothing changed" >&2; return 1; }
			mkdir -p "$ssh_dir"
			ssh_pending_set "$1"
			ssh_expire_start "$1"
			printf 'PasswordAuthentication no\nPermitRootLogin %s\nKbdInteractiveAuthentication no\nMaxAuthTries 3\n' "$ssh_root_config" > "$ssh_conf"
			if ! sshd -t || ! ssh_effective || ! ssh_reload; then
				printf '%s\n' "shield-root: hardening not active (sshd_config.d is not read, another setting takes precedence, or reload failed)" >&2
				ssh_remove && ssh_pending_clear
				return 1
			fi
			echo '@@pending=yes'
			;;
		confirm)
			[ -f "$ssh_state/pending" ] && [ "$(cat "$ssh_state/pending")" = "$1" ] || die "confirmation expired"
			[ -f "$ssh_conf" ] && ssh_effective || die "configuration changed"
			ssh_pending_clear
			echo '@@hardened=yes'
			;;
		rollback | expire)
			if [ -f "$ssh_state/pending" ] && [ "$(cat "$ssh_state/pending")" = "$1" ]; then
				ssh_remove || return 1
				ssh_pending_clear
			elif [ "$ssh_op" = expire ]; then
				return
			elif [ -e "$ssh_conf" ]; then
				die "rollback not confirmed"
			fi
			echo '@@rollback=yes'
			;;
		off)
			ssh_remove || return 1
			ssh_pending_clear
			echo '@@hardened=no'
			;;
	esac
}

[ "$#" -ge 1 ] || die "Unterbefehl fehlt"
command=$1
shift

case $command in
	ssh-hardening) ssh_hardening "$@" ;;
	version)
		[ "$#" -eq 0 ] || die "version erwartet keine Argumente"
		printf '%s\n' "$HELPER_VERSION"
		;;
	guard) guard "$@" ;;
	detect-templates) detect_templates "$@" ;;
	refresh-packages) refresh_packages "$@" ;;
	package-inventory) package_inventory "$@" ;;
	quarantine) quarantine "$@" ;;
	restore) restore "$@" ;;
	delete) delete "$@" ;;
	quarantine-state) quarantine_state "$@" ;;
	ban) fail2ban_set banip "$@" ;;
	unban) fail2ban_set unbanip "$@" ;;
	fail2ban-config-read) fail2ban_config_read "$@" ;;
	fail2ban-config-write) fail2ban_config_write "$@" ;;
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
