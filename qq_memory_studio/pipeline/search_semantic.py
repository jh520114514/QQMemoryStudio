# -*- coding: utf-8 -*-
"""语义检索：用自然语言回忆聊天记录。

  python search_semantic.py "我答应过谁什么事" --k 8
  python search_semantic.py "约好的时间" --chat 路过的风        # 限定会话
  python search_semantic.py "关于搬家的事" --only-me           # 只看我发的
  python search_semantic.py "..." --json                       # 给插件/程序调用

原理：查询文本 -> Embedding API -> 与 <memory>/vecs.f16 里的 L2 归一化向量做
      余弦相似度，取 top-k，再用 <memory>/vectors.db 的 vec_map 回查原消息。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

import numpy as np

import paths
from config_util import load_embedding_config

DIM = 1024
CHUNK = 50000


def vec_count(vecs: str) -> int:
    return os.path.getsize(vecs) // (DIM * 2) if os.path.isfile(vecs) else 0


def embed_query(text: str) -> np.ndarray:
    import requests

    cfg = load_embedding_config()
    r = requests.post(
        cfg.endpoint(),
        json={"model": cfg.model, "input": [text], "encoding_format": "float"},
        headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
        timeout=60,
    )
    r.raise_for_status()
    v = np.asarray(r.json()["data"][0]["embedding"], dtype=np.float32)
    v /= max(1e-9, float(np.linalg.norm(v)))
    return v


def topk(vecs: str, q: np.ndarray, k: int, n: int):
    """分批点积，返回 (相似度, 行号) 的 top-k。"""
    best_s = np.empty(0, dtype=np.float32)
    best_i = np.empty(0, dtype=np.int64)
    with open(vecs, "rb") as f:
        for start in range(0, n, CHUNK):
            cnt = min(CHUNK, n - start)
            raw = np.fromfile(f, dtype=np.float16, count=cnt * DIM)
            if raw.size < cnt * DIM:
                break
            m = raw.reshape(-1, DIM).astype(np.float32)
            s = m @ q
            if best_s.size:
                s = np.concatenate([best_s, s])
                idx = np.concatenate([best_i, np.arange(start, start + cnt, dtype=np.int64)])
            else:
                idx = np.arange(start, start + cnt, dtype=np.int64)
            take = min(k, s.size)
            part = np.argpartition(-s, take - 1)[:take]
            best_s, best_i = s[part], idx[part]
    order = np.argsort(-best_s)
    return best_s[order], best_i[order]


def fetch(mem: str, text_ids: list[int], chat, only_me: bool, group_ids: tuple) -> list[dict]:
    vdb = os.path.join(mem, "vectors.db")
    msg_db = os.path.join(mem, "messages.db")
    con = sqlite3.connect(vdb, timeout=30)
    ph = ",".join("?" * len(text_ids))
    rows = con.execute(f"SELECT text_id, msg_id FROM vec_map WHERE text_id IN ({ph})",
                       text_ids).fetchall()
    con.close()
    by_text: dict[int, list[int]] = {}
    for tid, mid in rows:
        by_text.setdefault(tid, []).append(mid)

    out: list[dict] = []
    con = sqlite3.connect(msg_db, timeout=30)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    for tid in text_ids:
        for mid in by_text.get(tid, [])[:3]:
            r = con.execute(
                "SELECT ts, chat_type, chat_id, chat_name, sender_qq, sender_name, direction, text "
                "FROM msg WHERE id=?", (mid,)).fetchone()
            if not r:
                continue
            if group_ids and r[1] != "c2c" and str(r[2]) not in group_ids:
                continue
            if chat and chat not in (r[3] or "") and chat not in (str(r[2]) or ""):
                continue
            if only_me and r[6] != 2:
                continue
            out.append({"text_id": tid, "msg_id": mid, "ts": r[0],
                        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[0])) if r[0] else "",
                        "chat_type": r[1], "chat": r[3] or r[2], "sender_id": r[4],
                        "sender": r[5], "dir": r[6], "text": r[7]})
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="语义检索记忆库")
    ap.add_argument("query")
    ap.add_argument("--memory-dir", default="", help="记忆库目录")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--chat", default=None, help="会话名包含该词")
    ap.add_argument("--only-me", action="store_true", help="只要我发的")
    ap.add_argument("--group-ids", default="", help="限定群号，逗号分隔（不填=全库）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mem = paths.memory_dir(args.memory_dir)
    vecs = os.path.join(mem, "vecs.f16")
    n = vec_count(vecs)
    if n == 0:
        print("向量文件为空，请先跑 embed_vectors.py", file=sys.stderr)
        sys.exit(1)
    group_ids = tuple(x.strip() for x in args.group_ids.split(",") if x.strip())
    t0 = time.time()
    q = embed_query(args.query)
    sims, ids = topk(vecs, q, max(args.k * 3, 30), n)
    hits = fetch(mem, [int(i) for i in ids], args.chat, args.only_me, group_ids)[: args.k]
    for h, s in zip(hits, sims):
        h["score"] = float(s)
    hits.sort(key=lambda h: -h["score"])

    if args.json:
        print(json.dumps({"query": args.query, "vectors": n, "hits": hits},
                         ensure_ascii=False, indent=2))
        return
    print(f"查询：{args.query}   （向量库 {n:,} 条，耗时 {time.time() - t0:.2f}s）")
    for i, h in enumerate(hits, 1):
        who = "我" if h["dir"] == 2 else (h["sender"] or "?")
        print(f"\n{i}. [{h['score']:.3f}] {h['time']}  {h['chat']}  {who}")
        print(f"   {str(h['text'])[:200]}")


if __name__ == "__main__":
    main()
