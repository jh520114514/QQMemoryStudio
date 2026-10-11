# -*- coding: utf-8 -*-
"""为 messages.db 建立 CJK 友好的 FTS5 索引 msg_fts_cjk。

FTS5 默认 unicode61 会把一整串中文当成一个 token，导致「开心」搜不到
「我今天很开心」。这里把中日韩字符逐字用空格切开后再入索引，查询时做同样
处理并用短语匹配，即可实现任意长度中文子串检索。

用法::

    python build_cjk_fts.py [--db FILE]
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import time

import paths

CJK = re.compile(r"([\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f])")


def space_cjk(s: str) -> str:
    return CJK.sub(r" \1 ", s or "")


def build(db_path: str) -> int:
    c = sqlite3.connect(db_path)
    c.text_factory = lambda b: b.decode("utf-8", "replace")
    t0 = time.time()
    c.executescript("""
DROP TABLE IF EXISTS msg_fts_cjk;
CREATE VIRTUAL TABLE msg_fts_cjk USING fts5(text, chat_name, sender_name, content='');
""")
    n = 0
    batch = []
    for row in c.execute("SELECT id, text, chat_name, sender_name FROM msg"):
        batch.append((row[0], space_cjk(row[1]), space_cjk(row[2]), space_cjk(row[3])))
        if len(batch) >= 20000:
            c.executemany("INSERT INTO msg_fts_cjk(rowid,text,chat_name,sender_name) VALUES (?,?,?,?)", batch)
            c.commit()
            n += len(batch)
            batch.clear()
            print(f"  ... {n:,}  {time.time() - t0:.0f}s", flush=True)
    if batch:
        c.executemany("INSERT INTO msg_fts_cjk(rowid,text,chat_name,sender_name) VALUES (?,?,?,?)", batch)
        n += len(batch)
    c.commit()
    c.execute("INSERT INTO msg_fts_cjk(msg_fts_cjk) VALUES('optimize')")
    c.commit()
    print(f"完成: {n:,} 条 CJK 索引  {time.time() - t0:.0f}s")
    c.close()
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="重建中文 FTS 索引")
    ap.add_argument("--db", default="", help="messages.db 路径")
    args = ap.parse_args()
    db = args.db or paths.messages_db()
    if not os.path.isfile(db):
        raise SystemExit(f"[错误] 找不到记忆库: {db}")
    build(db)


if __name__ == "__main__":
    main()
