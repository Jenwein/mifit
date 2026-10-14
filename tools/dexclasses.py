#!/usr/bin/env python3
"""List all defined class descriptors from every classes*.dex inside an APK.

Pure-stdlib DEX class_defs reader. Outputs fully-qualified dotted class names,
one per line. Used only to build a reliable class-name index for a very large
multidex APK where the engine's own inventory call is unstable.
"""
import struct
import sys
import zipfile


def read_dex_classes(buf: bytes):
    if buf[:4] not in (b"dex\n", b"dey\n"):
        return []
    # header: string_ids off/size at 0x38/0x3C area
    string_ids_size = struct.unpack_from("<I", buf, 0x38)[0]
    string_ids_off = struct.unpack_from("<I", buf, 0x3C)[0]
    type_ids_size = struct.unpack_from("<I", buf, 0x40)[0]
    type_ids_off = struct.unpack_from("<I", buf, 0x44)[0]
    class_defs_size = struct.unpack_from("<I", buf, 0x60)[0]
    class_defs_off = struct.unpack_from("<I", buf, 0x64)[0]

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

    def string_at(idx):
        data_off = struct.unpack_from("<I", buf, string_ids_off + idx * 4)[0]
        _, off = read_uleb(data_off)  # utf16 size prefix
        end = buf.index(b"\x00", off)
        return buf[off:end].decode("utf-8", "replace")

    # type_ids[idx] -> string idx
    def type_descriptor(type_idx):
        s_idx = struct.unpack_from("<I", buf, type_ids_off + type_idx * 4)[0]
        return string_at(s_idx)

    out = []
    for i in range(class_defs_size):
        base = class_defs_off + i * 32
        class_idx = struct.unpack_from("<I", buf, base)[0]
        out.append(type_descriptor(class_idx))
    return out


def desc_to_dotted(d: str) -> str:
    if d.startswith("L") and d.endswith(";"):
        return d[1:-1].replace("/", ".")
    return d


def main():
    apk = sys.argv[1]
    names = set()
    with zipfile.ZipFile(apk) as z:
        for n in z.namelist():
            if n.startswith("classes") and n.endswith(".dex"):
                for d in read_dex_classes(z.read(n)):
                    dd = desc_to_dotted(d)
                    if dd.startswith("L"):
                        continue
                    names.add(dd)
    for n in sorted(names):
        print(n)


if __name__ == "__main__":
    main()
