# -*- coding: utf-8 -*-
"""语义检索封装：复用 pipeline/search_semantic.py 的思路。

  query 文本 --Embedding API--> 归一化向量 --点积--> 与 <memory>/vecs.f16 比对取 top-k
  --用 <memory>/vectors.db 的 vec_map(text_id -> msg_id) 回查 messages.db 原文。

与 pipeline 版本的区别：所有路径/凭据均来自 studio 的 config.json，并支持会话/发送人/
时间范围过滤，且对“未配置 key / 向量文件缺失”给出可读提示，而不是直接崩溃。
"""
from __future__ import annotations

import os
import sqlite3
import time

import numpy as np

from .memory import _ro, fmt_ts, NULL_TEXT

CHUNK = 50000


class EmbedError(RuntimeError):
    """Embedding 调用失败（未配置 / 网络 / HTTP 错误）。"""


def embed_texts(cfg: dict, texts: list[str], timeout: int = 60) -> np.ndarray:
    """调用 OpenAI 兼容的 /embeddings，返回 L2 归一化 float32 矩阵。"""
    import requests

    base = str(cfg.get("base_url") or "").rstrip("/")
    key = str(cfg.get("api_key") or "")
    model = str(cfg.get("model") or "")
    if not base:
        raise EmbedError("未配置 embedding base_url")
    if not key:
        raise EmbedError("未配置 embedding api_key")
    if not model:
        raise EmbedError("未配置 embedding model")
    payload = {"model": model, "input": texts, "encoding_format": "float"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        r = requests.post(base + "/embeddings", json=payload, headers=headers,
                          timeout=int(cfg.get("timeout") or timeout))
    except Exception as e:  # noqa: BLE001
        raise EmbedError(f"网络错误：{type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise EmbedError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        arr = np.asarray([d["embedding"] for d in data], dtype=np.float32)
    except Exception as e:  # noqa: BLE001
        raise EmbedError(f"响应解析失败：{e}") from e
    if arr.ndim != 2 or arr.shape[0] != len(texts):
        raise EmbedError(f"返回维度异常：{arr.shape}")
    n = np.linalg.norm(arr, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return arr / n


def topk(vecs_path: str, q: np.ndarray, k: int, n: int, dim: int):
    """分批点积，返回 (相似度, 行号) 的 top-k。"""
    best_s = np.empty(0, dtype=np.float32)
    best_i = np.empty(0, dtype=np.int64)
    with open(vecs_path, "rb") as f:
        for start in range(0, n, CHUNK):
            cnt = min(CHUNK, n - start)
            raw = np.fromfile(f, dtype=np.float16, count=cnt * dim)
            if raw.size < cnt * dim:
                break
            m = raw.reshape(-1, dim).astype(np.float32)
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


class SemanticIndex:
    def __init__(self, memory_dir: str, embedding: dict, dim: int | None = None):
        self.memory_dir = memory_dir
        self.embedding = embedding or {}
        self.vecs = os.path.join(memory_dir, "vecs.f16")
        self.vectors_db = os.path.join(memory_dir, "vectors.db")
        self.db = os.path.join(memory_dir, "messages.db")
        self.dim = int(dim or self.embedding.get("dim") or 1024)

    # ------------------------------------------------------------ 可用性
    def available(self) -> tuple[bool, str]:
        if not os.path.isfile(self.vecs):
            return False, f"未找到向量文件：{self.vecs}"
        if not os.path.isfile(self.vectors_db):
            return False, f"未找到向量映射库：{self.vectors_db}"
        if os.path.getsize(self.vecs) == 0:
            return False, "向量文件为空（请先跑 embed_vectors.py）"
        if not self.embedding.get("api_key"):
            return False, "未配置 embedding API Key（可在主窗口“基础设置”里填写后重试）"
        if not self.embedding.get("base_url"):
            return False, "未配置 embedding base_url"
        return True, ""

    def overview(self) -> dict:
        vec_bytes = os.path.getsize(self.vecs) if os.path.isfile(self.vecs) else 0
        rows = 0
        if os.path.isfile(self.vectors_db):
            try:
                con = _ro(self.vectors_db)
                rows = con.execute("SELECT COUNT(*) FROM vec_map").fetchone()[0]
                con.close()
            except Exception:  # noqa: BLE001
                pass
        dim = self.dim
        return {"vecs_bytes": vec_bytes, "vec_rows": rows, "dim": dim,
                "vec_count": (vec_bytes // (2 * dim)) if dim else 0,
                "model": self.embedding.get("model", ""), "base_url": self.embedding.get("base_url", "")}

    # ------------------------------------------------------------ 连接测试
    def test_connection(self) -> tuple[bool, str]:
        if not self.embedding.get("api_key"):
            return False, "未配置 embedding API Key"
        t0 = time.time()
        try:
            v = embed_texts(self.embedding, ["连接测试"])
            return True, f"OK · {self.embedding.get('model')} · dim={v.shape[1]} · {time.time()-t0:.2f}s"
        except EmbedError as e:
            return False, str(e)

    # ------------------------------------------------------------ 检索
    def search(self, query: str, k: int = 8, chat: str = "", sender: str = "",
               ts_from: int = 0, ts_to: int = 0, only_me: bool = False,
               dim: int | None = None) -> tuple[list[dict], float]:
        ok, why = self.available()
        if not ok:
            raise EmbedError(why)
        dim = int(dim or self.dim)
        n = os.path.getsize(self.vecs) // (dim * 2)
        t0 = time.time()
        q = embed_texts(self.embedding, [query])[0]
        sims, ids = topk(self.vecs, q, max(k * 3, 30), n, dim)
        hits = self._fetch([int(i) for i in ids], chat, sender, ts_from, ts_to, only_me)
        # 相似度映射
        sim_map = {int(i): float(s) for i, s in zip(ids, sims)}
        for h in hits:
            h["score"] = round(sim_map.get(h["text_id"], 0.0), 4)
            h["source"] = "语义"
        hits.sort(key=lambda h: -h["score"])
        return hits[:k], round(time.time() - t0, 3)

    def _fetch(self, text_ids: list[int], chat: str, sender: str,
               ts_from: int, ts_to: int, only_me: bool) -> list[dict]:
        vcon = _ro(self.vectors_db)
        ph = ",".join("?" * len(text_ids))
        rows = vcon.execute(
            f"SELECT text_id, msg_id FROM vec_map WHERE text_id IN ({ph})", text_ids).fetchall()
        vcon.close()
        by_text: dict[int, list[int]] = {}
        for tid, mid in rows:
            by_text.setdefault(tid, []).append(mid)

        out: list[dict] = []
        mcon = _ro(self.db)
        for tid in text_ids:
            for mid in by_text.get(tid, [])[:3]:
                r = mcon.execute(
                    "SELECT id, ts, chat_type, chat_id, chat_name, sender_qq, sender_name, "
                    "direction, text FROM msg WHERE id=?", (mid,)).fetchone()
                if not r:
                    continue
                if chat and chat not in (r[4] or "") and chat not in str(r[3] or ""):
                    continue
                if sender and sender not in (r[6] or "") and sender not in str(r[5] or ""):
                    continue
                if ts_from and (r[1] or 0) < ts_from:
                    continue
                if ts_to and (r[1] or 0) > ts_to:
                    continue
                if only_me and r[7] != 2:
                    continue
                out.append({
                    "id": r[0], "msg_id": r[0], "ts": r[1], "time": fmt_ts(r[1]),
                    "chat": r[4] or str(r[3]), "chat_type": r[2], "chat_id": str(r[3]),
                    "sender": "我" if r[7] == 2 else (r[6] or str(r[5] or "")),
                    "sender_name": r[6], "sender_qq": r[5], "dir": r[7],
                    "text": r[8] or NULL_TEXT, "text_id": tid,
                })
        mcon.close()
        return out


if __name__ == "__main__":
    import json
    from . import config as _cfg
    c = _cfg.Config.load()
    si = SemanticIndex(c.memory_dir, c.get("embedding"), c.get("embedding.dim"))
    print(json.dumps(si.overview(), ensure_ascii=False, indent=2))
    print("available:", si.available())
    print("test:", si.test_connection())
    hits, dt = si.search("约好的时间", k=3)
    print(f"hits {len(hits)} ({dt}s)")
    for h in hits:
        print(" ", h["score"], h["time"], h["chat"], str(h["text"])[:40])
