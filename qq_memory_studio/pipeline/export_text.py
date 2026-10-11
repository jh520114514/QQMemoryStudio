# -*- coding: utf-8 -*-
"""把记忆库导出成人类可读的文本，每个会话一个文件。

输出：<export-dir>/<类型>_<会话名>_<ID>.txt

用法::

    python export_text.py [--db FILE] [--out DIR]
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import time

import paths

BAD = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]+')
PLACEHOLDER = re.compile(r"<\$[^$]{0,24}\$>")
KEEP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf .\-_()（）：·]")


def clean_name(s: str) -> str:
    s = PLACEHOLDER.sub("", s or "")
    return BAD.sub("_", KEEP.sub("", s)).strip()


def safe(s: str) -> str:
    return clean_name(s)[:60] or "未知"


def run(db: str, out: str) -> int:
    os.makedirs(out, exist_ok=True)
    c = sqlite3.connect(db)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    chats = c.execute("SELECT chat_type, chat_id, chat_name, COUNT(*) FROM msg "
                      "GROUP BY chat_type, chat_id ORDER BY 4 DESC").fetchall()
    print(f"共 {len(chats)} 个会话")
    index_lines = ["QQ 聊天记录导出（按会话拆分）", ""]
    for ctype, cid, cname, n in chats:
        label = f"{'群' if ctype == 'group' else '好友'}_{safe(cname)}_{cid}"
        path = os.path.join(out, label + ".txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {clean_name(cname) or cid}（{ctype} {cid}）共 {n} 条\n")
            f.write("# 导出时间: %s\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            for ts, sname, sender, direction, text in c.execute(
                    "SELECT ts, sender_name, sender_qq, direction, text FROM msg "
                    "WHERE chat_type=? AND chat_id=? ORDER BY ts", (ctype, cid)):
                who = "我" if direction in (1, 2) else (sname or str(sender) or "？")
                t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or 0))
                f.write(f"[{t}] {who}: {(text or '').strip()}\n")
        index_lines.append(f"{label}.txt\t{n} 条\t{clean_name(cname) or cid}")
    open(os.path.join(out, "00_目录.txt"), "w", encoding="utf-8").write("\n".join(index_lines))
    print("已导出到", out)
    c.close()
    return len(chats)


def main() -> None:
    ap = argparse.ArgumentParser(description="按会话导出可读文本")
    ap.add_argument("--db", default="", help="messages.db 路径")
    ap.add_argument("--out", default="", help="输出目录")
    args = ap.parse_args()
    db = args.db or paths.messages_db()
    out = args.out or os.path.join(paths.data_dir(), "export")
    if not os.path.isfile(db):
        raise SystemExit(f"[错误] 找不到记忆库: {db}")
    run(db, out)


if __name__ == "__main__":
    main()
