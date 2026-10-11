# -*- coding: utf-8 -*-
"""全库扫描：统计 messages.db 里有多少条正文含 XML 1.0 非法字符 / 孤立代理项。

只读打开记忆库，绝不写入。用法：

    python scripts/scan_illegal.py            # 全库扫描
    python scripts/scan_illegal.py <id> [...] # 额外打印指定消息 id 的清洗前后对照

结果同时写入 exports/scan_illegal.txt（同名 .done 作为完成标记）。
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if STUDIO not in sys.path:
    sys.path.insert(0, STUDIO)

from core.config import Config          # noqa: E402
from core.exporter import clean_text, _XML_BAD_RE  # noqa: E402
from core.memory import _ro             # noqa: E402


def scan(extra_ids: list[int]) -> int:
    cfg = Config.load()
    db = os.path.join(cfg.memory_dir, "messages.db")
    out_dir = os.path.join(STUDIO, "exports")
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "scan_illegal.txt")
    done_path = log_path + ".done"

    lines: list[str] = []
    def emit(s: str) -> None:
        print(s, flush=True)
        lines.append(s)

    emit(f"记忆库：{db}")
    emit(f"扫描开始：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    if not os.path.isfile(db):
        emit(f"未找到记忆库：{db}")
        _flush(log_path, done_path, lines)
        return 2

    con = _ro(db)
    t0 = time.time()
    total = 0            # msg 表全部行
    with_text = 0        # text 非空的行
    bad_rows = 0         # 含非法字符的正文行
    bad_chars = Counter()  # 各非法字符出现次数
    bad_msgs: list[tuple] = []  # (id, chat_id, 原文片段)
    sample_limit = 40
    try:
        cur = con.execute("SELECT id, chat_id, text FROM msg")
        while True:
            rows = cur.fetchmany(20000)
            if not rows:
                break
            for mid, cid, text in rows:
                total += 1
                if not text:
                    continue
                with_text += 1
                if _XML_BAD_RE.search(text):
                    bad_rows += 1
                    for m in _XML_BAD_RE.finditer(text):
                        bad_chars[m.group(0)] += 1
                    if len(bad_msgs) < sample_limit:
                        bad_msgs.append((mid, cid, text[:80]))
            if total % 200000 == 0:
                emit(f"  …已扫描 {total:,} 行，命中 {bad_rows:,} 行  ({time.time()-t0:.0f}s)")
    finally:
        con.close()

    emit("")
    emit(f"扫描完成，用时 {time.time()-t0:.1f}s")
    emit(f"msg 表总行数        ：{total:,}")
    emit(f"text 非空行数       ：{with_text:,}")
    emit(f"含非法字符的正文行数：{bad_rows:,}")
    pct_all = (bad_rows / total * 100) if total else 0.0
    pct_txt = (bad_rows / with_text * 100) if with_text else 0.0
    emit(f"占全部消息比例      ：{pct_all:.4f}%")
    emit(f"占非空正文比例      ：{pct_txt:.4f}%")

    emit("")
    emit("非法字符分布（按出现次数）：")
    for ch, n in bad_chars.most_common():
        o = ord(ch)
        emit(f"  U+{o:04X}  出现 {n:,} 次   → 占位 {chr(0x2400+o) if o <= 0x1F else '（删除）'}")

    emit("")
    emit(f"样例（最多 {sample_limit} 条，id / chat_id / 原文片段）：")
    for mid, cid, frag in bad_msgs:
        cleaned, _ = clean_text(frag)
        emit(f"  #{mid}  {cid}  原始={frag!r}")
        emit(f"          清洗={cleaned!r}")

    for mid in extra_ids:
        try:
            r = con2 = None
            con2 = _ro(db)
            r = con2.execute("SELECT id, chat_id, text FROM msg WHERE id=?", (mid,)).fetchone()
            con2.close()
        except Exception as e:  # noqa: BLE001
            emit(f"  查询 id={mid} 失败：{e}")
            continue
        if not r:
            emit(f"  指定 id={mid} 不存在")
            continue
        cleaned, changed = clean_text(r[2] or "")
        emit(f"  指定 id={mid}  chat_id={r[1]}  是否含非法字符={changed}")
        emit(f"     原始={ (r[2] or '' )[:120]!r}")
        emit(f"     清洗={cleaned[:120]!r}")

    _flush(log_path, done_path, lines)
    return 0


def _flush(log_path: str, done_path: str, lines: list[str]) -> None:
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(done_path, "w", encoding="utf-8") as f:
        f.write("done\n")


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:] if a.isdigit()]
    raise SystemExit(scan(ids))
