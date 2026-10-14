#!/bin/bash
# List a class's method signatures (compact) via REA; full JSON kept under evidence/inspect-android-class/.
cd "$(dirname "$0")/.."
cls="$1"
mkdir -p evidence/inspect-android-class
out="evidence/inspect-android-class/${cls}_.json"
./tools/rea.sh inspect-android-class 'C:\dev\mifitness-re\target\mihealth-3.59.1.apk' "$cls" --format json --full-output </dev/null > "$out" 2>/dev/null
python -I -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
nr=d.get('data',d).get('normalized_result') or d.get('normalized_result') or {}
for m in nr.get('methods',[]): print(m['overload_index'], m['reported_signature'])
" "$out"
