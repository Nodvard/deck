#!/bin/sh
set -eu

if [ "$(id -u)" != "0" ]; then
  echo "Die Umstellung des Datenordners braucht einmalig root." >&2
  exit 1
fi
if [ ! -d /app/data ] || [ -L /app/data ]; then
  echo "Der Datenordner /app/data fehlt oder ist ein Link." >&2
  exit 1
fi
find /app/data -xdev \( ! -uid 1000 -o ! -gid 1000 \) -exec chown -h 1000:1000 {} +
