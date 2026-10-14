#!/usr/bin/env python3
"""Extract string-pool entries from every classes*.dex in an APK.

Optional substring filters (case-insensitive) limit output. One string per
line, control chars escaped. Used to recover Room CREATE TABLE SQL, endpoint
paths, and other embedded constants from a large multidex APK.
"""
import struct
import sys
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def read_strings(buf: bytes):
    if buf[:4] not in (b"dex\n", b"dey\n"):
        return
    string_ids_size = struct.unpack_from("<I", buf, 0x38)[0]
    string_ids_off = struct.unpack_from("<I", buf, 0x3C)[0]

    def read_uleb(off):
        result = 0
        shift = 0
        while True:
            b = buf[off]
            off += 1
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7
        return result, off

    for i in range(string_ids_size):
        data_off = struct.unpack_from("<I", buf, string_ids_off + i * 4)[0]
        _, off = read_uleb(data_off)
        end = buf.index(b"\x00", off)
        yield buf[off:end].decode("utf-8", "replace")


def main():
    apk = sys.argv[1]
    filters = [f.lower() for f in sys.argv[2:]]
    seen = set()
    with zipfile.ZipFile(apk) as z:
        for n in z.namelist():
            if n.startswith("classes") and n.endswith(".dex"):
                for s in read_strings(z.read(n)):
                    if filters and not any(f in s.lower() for f in filters):
                        continue
                    if s in seen:
                        continue
                    seen.add(s)
                    print(s.replace("\n", "\\n").replace("\r", "\\r"))


if __name__ == "__main__":
    main()
