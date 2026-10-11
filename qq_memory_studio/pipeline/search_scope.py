# -*- coding: utf-8 -*-
"""按「隐私范围」检索记忆库：好友私聊 +（可选）指定的若干群。

用法::

    python search_scope.py "关键词"                         # 默认 scope=all（全库）
    python search_scope.py "关键词" --groups "群A,群B"       # 只搜 c2c + 这两个群（按名字）
    python search_scope.py "关键词" --group-ids "123,456"    # 同上（按群号）
    python search_scope.py "关键词" --k 10 --only-me --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time

import paths


def space_cjk(s: str) -> str:
    return re.sub(r"([\u4e00-\u9fff])", r" \1 ", s or "")


def build_where(groups: tuple, group_ids: tuple, only_me: bool):
    parts, args = [], []
    likes = [f"m.chat_name LIKE ?"] * len(groups)
    if group_ids:
        likes.append("m.chat_id IN (%s)" % ",".join("?" * len(group_ids)))
    if likes:
        parts.append("(m.chat_type = 'c2c' OR " + " OR ".join(likes) + ")")
        args += [f"%{g}%" for g in groups] + list(group_ids)
    if only_me:
        parts.append("m.direction = 2")
    return (" AND ".join(parts) if parts else "1=1"), args


def main() -> None:
    ap = argparse.ArgumentParser(description="按隐私范围做关键词检索")
    ap.add_argument("keyword")
    ap.add_argument("--db", default="", help="messages.db 路径")
    ap.add_argument("--groups", default="", help="限定群名，逗号分隔")
    ap.add_argument("--group-ids", default="", help="限定群号，逗号分隔")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--only-me", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    db = a.db or paths.messages_db()
    groups = tuple(x.strip() for x in a.groups.split(",") if x.strip())
    group_ids = tuple(x.strip() for x in a.group_ids.split(",") if x.strip())

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    where, wargs = build_where(groups, group_ids, a.only_me)
    expr = " ".join(space_cjk(a.keyword).split())
    sql = f"""SELECT m.ts, m.chat_name, m.chat_type, m.sender_name, m.direction, m.text,
                     bm25(msg_fts_cjk) AS s
              FROM msg_fts_cjk f JOIN msg m ON m.id = f.rowid
              WHERE msg_fts_cjk MATCH ? AND {where}
              ORDER BY s LIMIT ?"""
    t0 = time.time()
    rows = con.execute(sql, [expr] + wargs + [a.k]).fetchall()
    dt = time.time() - t0
    con.close()

    out = [{
        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[0])) if r[0] else "",
        "chat": r[1], "chat_type": r[2], "sender": "我" if r[4] == 2 else r[3],
        "text": r[5], "score": round(-r[6], 3),
    } for r in rows]
    if a.json:
        print(json.dumps({"keyword": a.keyword, "hits": out, "took": round(dt, 3)},
                         ensure_ascii=False, indent=2))
        return
    print(f"关键词「{a.keyword}」  命中 {len(out)} 条  {dt:.3f}s")
    for h in out:
        print(f"  {h['time']}  [{h['chat']}] {h['sender']}: {str(h['text'])[:70]}")


if __name__ == "__main__":
    main()
