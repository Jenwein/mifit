#!/bin/bash
cd "$(dirname "$0")/.."
cls="$1"; m="$2"; ov="$3"
mkdir -p evidence/methods
safe=$(echo "${cls}__${m}${ov:+__$ov}" | tr -c 'A-Za-z0-9._-' '_')
out="evidence/methods/${safe}.json"; txt="evidence/methods/${safe}.txt"
args=( inspect-android-method 'C:\dev\mifitness-re\target\mihealth-3.59.1.apk' "$cls" "$m" )
[ -n "$ov" ] && args+=( --overload-index "$ov" )
./tools/rea.sh "${args[@]}" --format json --full-output </dev/null > "$out" 2>/dev/null
python -I tools/show_method.py "$out" "$txt"
