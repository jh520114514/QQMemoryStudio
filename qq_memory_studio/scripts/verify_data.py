# -*- coding: utf-8 -*-
"""校验 memory\\ 下核心产物是否与 MANIFEST.sha256 一致（只读）。

    python scripts/verify_data.py [memory_dir]
"""
from __future__ import annotations

import hashlib
import os
import re
import sys


def parse_manifest(path: str) -> list[tuple[str, str, int]]:
    items = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([0-9a-fA-F]{64})\s+(\d+)\s+(.+)$", line)
            if m:
                items.append((m.group(3).strip(), m.group(1).lower(), int(m.group(2))))
    return items


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    mem = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "qq_memory", "memory")
    mem = os.path.abspath(mem)
    man = os.path.join(mem, "MANIFEST.sha256")
    if not os.path.isfile(man):
        print("未找到 MANIFEST.sha256：", man)
        return 2
    all_ok = True
    for name, want, size in parse_manifest(man):
        p = os.path.join(mem, name)
        if not os.path.isfile(p):
            print(f"  MISSING {name}")
            all_ok = False
            continue
        got = sha256(p)
        ok = got == want and os.path.getsize(p) == size
        all_ok &= ok
        print(f"  {'OK     ' if ok else 'CHANGED'} {name}  {os.path.getsize(p)} bytes")
    print("ALL_OK" if all_ok else "SOME_CHANGED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
