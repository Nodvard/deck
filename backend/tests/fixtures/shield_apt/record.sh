#!/bin/sh
# Nimmt die Ausgabe von updates.status_command in Containern auf: Testpakete aus einer lokalen Paketquelle, darunter
# welche mit "Phased-Update-Percentage" (auf Debian ohne das Feld, wie in den echten Debian-Quellen), eins mit neuer
# Abhängigkeit, eins gesperrt (apt-mark hold) und eins für i386. Braucht Docker, kein Netz im Container. Aufruf im Repo-Wurzelordner, mit der Python-Umgebung des Backends:
# PYTHON=.venv/bin/python sh backend/tests/fixtures/shield_apt/record.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../../.." && pwd)
work=$(mktemp -d)
chmod 755 "$work"
trap 'rm -rf "$work"' EXIT
PYTHONPATH="$root/extensions/shield/src:$root/backend/src:$root/sdk/python" "${PYTHON:-python3}" -c \
    'from nodvard_deck_ext_shield import updates; print(updates.status_command(refresh=False))' > "$work/status.sh"
cat > "$work/inner.sh" <<'INNER'
set -eu
export DEBIAN_FRONTEND=noninteractive
mkdir -p /build /repo
mk() {
    a=${4:-all}
    d=/build/$1_$2_$a
    mkdir -p "$d/DEBIAN"
    printf 'Package: %s\nVersion: %s\nArchitecture: %s\nMaintainer: Test <test@example.invalid>\nDescription: Testpaket\n' "$1" "$2" "$a" > "$d/DEBIAN/control"
    if [ -n "${3:-}" ]; then printf 'Depends: %s\n' "$3" >> "$d/DEBIAN/control"; fi
    dpkg-deb -b "$d" "/build/$1_$2_$a.deb" >/dev/null
}
entry() {
    f=/build/$1_$2_${4:-all}.deb
    dpkg-deb -f "$f" Package Version Architecture Maintainer Depends Description | grep -v '^$' >> /repo/Packages
    printf 'Filename: ./%s\nSize: %s\nSHA256: %s\n' "$(basename "$f")" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)" >> /repo/Packages
    if [ "$PHASED" = 1 ] && [ -n "${3:-}" ]; then printf 'Phased-Update-Percentage: %s\n' "$3" >> /repo/Packages; fi
    echo >> /repo/Packages
}
dpkg --add-architecture i386
plain="demo-normal demo-phased demo-phased-two demo-phased-full demo-keptback demo-phased-newdep demo-held"
for p in $plain; do mk "$p" 1.0; done
for p in demo-normal demo-phased demo-phased-two demo-phased-full demo-held; do mk "$p" 1.1; done
mk demo-keptback 1.1 demo-newdep
mk demo-phased-newdep 1.1 demo-newdep-two
mk demo-newdep 1.0
mk demo-newdep-two 1.0
mk demo-foreign 1.0 "" i386
mk demo-foreign 1.1 "" i386
for p in $plain; do dpkg -i "/build/${p}_1.0_all.deb" >/dev/null; done
dpkg -i /build/demo-foreign_1.0_i386.deb >/dev/null
apt-mark hold demo-held >/dev/null
if [ "$BROKEN" = 1 ]; then mk demo-broken 1.0 demo-missing; dpkg -i --force-depends /build/demo-broken_1.0_all.deb >/dev/null 2>&1; fi
: > /repo/Packages
entry demo-normal 1.1
entry demo-phased 1.1 0
entry demo-phased-two 1.1 0
entry demo-phased-full 1.1 100
entry demo-keptback 1.1
entry demo-phased-newdep 1.1 0
entry demo-held 1.1 0
entry demo-newdep 1.0
entry demo-newdep-two 1.0
entry demo-foreign 1.1 0 i386
rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*
echo 'deb [trusted=yes] file:/repo ./' > /etc/apt/sources.list.d/test.list
[ -s /etc/machine-id ] || echo 0123456789abcdef0123456789abcdef > /etc/machine-id
if [ "$ALWAYS" = 1 ]; then echo 'APT::Get::Always-Include-Phased-Updates "true";' > /etc/apt/apt.conf.d/99-phased; fi
apt-get update -qq -o Acquire::Languages=none >/dev/null 2>&1
useradd -m deck
su deck -s /bin/sh -c 'sh /work/status.sh'
INNER
# Kernel und Laufzeit stammen vom aufnehmenden Rechner, nicht aus dem Container: weg damit.
record() {
    docker run --rm --network none -e PHASED="$3" -e ALWAYS="$4" -e BROKEN="${5:-0}" -v "$work:/work:ro" "$1" sh /work/inner.sh > "$work/out"
    grep -qx '@@end' "$work/out" || { echo "Aufnahme $2 unvollständig" >&2; exit 1; }
    awk '/^@@(kernel|kernels|uptime)$/ { skip = 1; next } /^@@/ { skip = 0 } !skip' "$work/out" > "$here/$2.txt"
}
record ubuntu:22.04 ubuntu-22.04 1 0
record ubuntu:24.04 ubuntu-24.04 1 0
record ubuntu:24.04 ubuntu-24.04-immer-alle 1 1
record ubuntu:24.04 ubuntu-24.04-kaputt 1 0 1
record ubuntu:26.04 ubuntu-26.04 1 0
record debian:12 debian-12 0 0
record debian:13 debian-13 0 0
