#!/usr/bin/env python3
"""Print the decompiled source text from a REA inspect-android-method JSON."""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def extract(nr):
    if isinstance(nr, str):
        return nr
    if isinstance(nr, dict):
        for k in ("text", "source", "decompiled"):
            v = nr.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                t = extract(v)
                if t:
                    return t
    return ""


d = json.load(open(sys.argv[1], encoding="utf-8"))
dd = d.get("data", {})
nr = dd.get("normalized_result")
if not nr:
    print("[no decompile] code=", dd.get("code"), (dd.get("message") or "")[:160])
    sys.exit()
src = extract(nr)
if len(sys.argv) > 2:
    open(sys.argv[2], "w", encoding="utf-8").write(src)
print(src if src else "[empty source] keys=" + str(list(nr.keys())))
