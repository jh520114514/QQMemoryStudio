# -*- coding: utf-8 -*-
"""QQ(NTQQ) 数据目录/账号探测 + 计划任务读取。全部复用 pipeline/paths.py 的探测逻辑。"""
from __future__ import annotations

import os
import subprocess
from typing import Callable

from . import config as C


def _paths():
    import sys
    if C.STUDIO_DIR not in sys.path:
        sys.path.insert(0, C.STUDIO_DIR)
    from pipeline import paths  # type: ignore
    return paths


def detect_accounts(qq_data_dir: str = "") -> list[str]:
    """返回可用 QQ 号列表（纯数字目录名）。"""
    paths = _paths()
    base = qq_data_dir or ""
    if base and base.lower().endswith("nt_msg.db"):
        base = os.path.dirname(base)
    try:
        if base and os.path.isdir(base):
            acc = paths.list_accounts(base)
            if acc:
                return acc
        out = []
        for root in paths.default_qq_roots():
            if os.path.isdir(root):
                out += paths.list_accounts(root)
        # 去重保序
        seen, res = set(), []
        for a in out:
            if a not in seen:
                seen.add(a)
                res.append(a)
        return res
    except Exception:  # noqa: BLE001
        return []


def find_nt_msg(qq_data_dir: str = "", account: str = "") -> str | None:
    try:
        return _paths().find_nt_msg(qq_data_dir, account)
    except Exception:  # noqa: BLE001
        return None


def last_backup_time(memory_dir: str) -> str:
    """上次增量备份时间：取 memory\\ 下相关文件的最近 mtime。"""
    import time
    best = 0.0
    for name in ("messages.db", "state.json", "vectors.db", "vecs.f16"):
        p = os.path.join(memory_dir, name)
        if os.path.isfile(p):
            best = max(best, os.path.getmtime(p))
    if best <= 0:
        return "未知"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(best))


def next_scheduled_time(keywords: tuple = ("qq_memory", "qqmem", "qq-memory", "记忆")) -> str:
    r"""从 Windows 计划任务里找名字含关键字的任务，返回其“下次运行时间”；找不到=未知。

    只认与本项目相关的任务名（避免误匹配 \Microsoft\...\MemoryDiagnostic 之类系统任务）。
    """
    if os.name != "nt":
        return "未知"
    out = None
    for enc in ("mbcs", "utf-8", "cp936"):
        try:
            out = subprocess.run(
                ["schtasks", "/query", "/fo", "CSV", "/nh"],
                capture_output=True, text=True, encoding=enc, errors="replace", timeout=15,
                creationflags=0x08000000)
            if out.returncode == 0:
                break
        except Exception:  # noqa: BLE001
            out = None
    if out is None or out.returncode != 0:
        return "未知"
    try:
        import csv
        import io
        for row in csv.reader(io.StringIO(out.stdout or "")):
            if len(row) < 3:
                continue
            name = row[0].strip('"')
            nxt = row[2].strip('"')
            low = name.lower()
            if low.startswith("\\microsoft"):  # 系统自带任务不算
                continue
            if any(k in low for k in keywords) and nxt:
                return f"{nxt}（任务：{name}）"
    except Exception:  # noqa: BLE001
        return "未知"
    return "未知"


if __name__ == "__main__":
    print("accounts:", detect_accounts())
    print("nt_msg:", find_nt_msg())
    print("last backup:", last_backup_time(C.DEFAULT_MEMORY_DIR))
    print("next scheduled:", next_scheduled_time())
