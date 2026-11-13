#!/bin/sh
# Lässt nur die genannten Module und das gemeinsame UI-Kit stehen; leer heißt Standardauswahl.
set -eu
modules=$1
folder=$2
if [ -z "$modules" ]; then
  rm -rf "$folder/network" "$folder/hello-world"
  exit 0
fi
case $modules in
  *[!a-z0-9,-]*)
    echo "Ungültige Modulliste: $modules (erlaubt sind Kleinbuchstaben, Ziffern, Bindestrich und Kommas)." >&2
    exit 1 ;;
esac
if [ -z "$(printf '%s' "$modules" | tr -d ',')" ]; then
  echo "Die Modulliste nennt kein Modul: $modules" >&2
  exit 1
fi
for name in $(printf '%s' "$modules" | tr ',' ' '); do
  if [ ! -f "$folder/$name/extension.toml" ]; then
    echo "Das Modul $name gibt es nicht unter $folder." >&2
    exit 1
  fi
done
for path in "$folder"/*/; do
  name=$(basename "$path")
  case ",_shared,$modules," in
    *",$name,"*) ;;
    *) rm -rf "$path" ;;
  esac
done
