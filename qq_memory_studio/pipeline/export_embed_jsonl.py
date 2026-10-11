# -*- coding: utf-8 -*-
"""为语义向量索引准备输入。

- 只取有正文的消息，正文截断到 400 字
- 按正文去重（去重后条数通常只有一半），映射关系存进 vectors.db
- 发给 Embedding API 的只有正文，不含昵称/QQ 号

产出：
  <memory>/embed_input.jsonl   每行 {"text_id": i, "text": "..."}
  <memory>/vectors.db          表 vec_map(text_id, msg_id) —— 命中后回查原消息

用法::

    python export_embed_jsonl.py [--db FILE] [--memory-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time

import paths

MAXLEN = 400


def run(db: str, mem: str) -> None:
    t0 = time.time()
    vdb_path = os.path.join(mem, "vectors.db")
    out_path = os.path.join(mem, "embed_input.jsonl")
    if os.path.exists(vdb_path):
        os.remove(vdb_path)
    vdb = sqlite3.connect(vdb_path)
    vdb.execute("CREATE TABLE vec_map (text_id INTEGER, msg_id INTEGER)")
    vdb.execute("CREATE INDEX idx_vm_msg ON vec_map(msg_id)")
    vdb.execute("CREATE INDEX idx_vm_text ON vec_map(text_id)")
    src = sqlite3.connect(db, timeout=60)
    src.text_factory = lambda b: b.decode("utf-8", "replace")

    seen: dict[str, int] = {}
    batch = []
    n_msg = 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for mid, text in src.execute(
                "SELECT id, text FROM msg WHERE text IS NOT NULL AND TRIM(text) != '' ORDER BY id"):
            t = " ".join(str(text).split())[:MAXLEN]
            if not t:
                continue
            n_msg += 1
            tid = seen.get(t)
            if tid is None:
                tid = len(seen)
                seen[t] = tid
                f.write(json.dumps({"text_id": tid, "text": t}, ensure_ascii=False) + "\n")
            batch.append((tid, mid))
            if len(batch) >= 20000:
                vdb.executemany("INSERT INTO vec_map VALUES (?,?)", batch)
                vdb.commit()
                batch.clear()
                print(f"  ... 处理 {n_msg:,} 条 / 去重后 {len(seen):,}  ({time.time() - t0:.0f}s)",
                      flush=True)
        if batch:
            vdb.executemany("INSERT INTO vec_map VALUES (?,?)", batch)
    vdb.commit()

    total_unique = vdb.execute("SELECT COUNT(*) FROM vec_map").fetchone()[0]
    print(f"完成：正文消息 {n_msg:,} 条 -> 去重后 {len(seen):,} 条待向量化")
    print(f"输入: {out_path}  {os.path.getsize(out_path) / 1e6:.0f} MB")
    print(f"映射: {vdb_path}  {total_unique:,} 行")
    vdb.close()
    src.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="生成语义向量输入与映射")
    ap.add_argument("--db", default="", help="messages.db 路径")
    ap.add_argument("--memory-dir", default="", help="记忆库目录")
    args = ap.parse_args()
    db = args.db or paths.messages_db()
    mem = paths.memory_dir(args.memory_dir)
    if not os.path.isfile(db):
        raise SystemExit(f"[错误] 找不到记忆库: {db}")
    run(db, mem)


if __name__ == "__main__":
    main()
