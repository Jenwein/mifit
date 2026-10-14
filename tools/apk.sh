#!/bin/bash
# Usage: tools/apk.sh <rea-android-subcommand> <args...>   (APK path is injected)
# Saves full JSON evidence under evidence/<subcommand>/ and prints the file path.
cd "$(dirname "$0")/.."
sub="$1"; shift
mkdir -p "evidence/$sub"
name="$(echo "$*" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-150)"
out="evidence/$sub/${name}.json"
./tools/rea.sh "$sub" 'C:\dev\mifitness-re\target\mihealth-3.59.1.apk' "$@" --format json --full-output </dev/null > "$out"
echo "$out"
