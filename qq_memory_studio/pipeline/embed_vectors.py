# -*- coding: utf-8 -*-
"""批量向量化（增量追加 + 断点续跑），面向几十万条规模。

向量用 float16 追加写进单个二进制文件，每批只落盘新增部分（O(1) 写入），
断点 = 文件长度 / 每条字节数。

用法::

    python embed_vectors.py [--memory-dir DIR] [--batch 64] [--limit N]

产出：
  <memory>/vecs.f16   L2 归一化后的 float16 向量，下标 = text_id
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import requests

import paths
from config_util import load_embedding_config, redact


def load_texts(path: str, limit: int | None = None) -> list[str]:
    out: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line)["text"])
            if limit and len(out) >= limit:
                break
    return out


def embed_batch(session, cfg, texts, batch, tries=6, verbose=True):
    """返回 L2 归一化 float16 矩阵，失败抛异常。"""
    payload = {"model": cfg.model, "input": texts, "encoding_format": "float"}
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    delay = 1.0
    last = None
    for attempt in range(1, tries + 1):
        try:
            r = session.post(cfg.endpoint(), json=payload, headers=headers, timeout=90)
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
            data = r.json()["data"]
            data = sorted(data, key=lambda d: d.get("index", 0))
            v = np.asarray([d["embedding"] for d in data], dtype=np.float32)
            if v.shape[0] != len(texts):
                raise RuntimeError(f"返回 {v.shape[0]} 条 != 请求 {len(texts)} 条")
            n = np.linalg.norm(v, axis=1, keepdims=True)
            n[n == 0] = 1.0
            return (v / n).astype(np.float16)
        except Exception as e:  # noqa: BLE001
            last = e
            if verbose:
                print(f"    重试 {attempt}/{tries}（{type(e).__name__}: {str(e)[:120]}）", flush=True)
            time.sleep(delay)
            delay = min(30.0, delay * 2)
    raise RuntimeError(f"批失败: {last}")


def run(mem: str, batch_size: int = 64, limit: int = 0) -> int:
    cfg = load_embedding_config()
    dim = cfg.dimensions
    inp = os.path.join(mem, "embed_input.jsonl")
    vecs = os.path.join(mem, "vecs.f16")
    if not os.path.isfile(inp):
        raise SystemExit(f"[错误] 找不到 {inp}（先运行 export_embed_jsonl.py）")
    texts = load_texts(inp, limit or None)
    total = len(texts)
    per = dim * 2
    done = os.path.getsize(vecs) // per if os.path.isfile(vecs) else 0
    if done > total:
        print(f"已有向量 {done} > 文本 {total}，请检查输入是否变化", flush=True)
        return done
    print(f"[vec] {cfg.model} dim={dim} key={redact(cfg.api_key)}", flush=True)
    print(f"[vec] 待处理 {total:,} 条，已完成 {done:,} 条，从 {done:,} 继续", flush=True)

    session = requests.Session()
    t0 = time.time()
    n0 = done
    with open(vecs, "ab") as f:
        while done < total:
            batch = texts[done:done + batch_size]
            v = embed_batch(session, cfg, batch, batch_size)
            f.write(v.tobytes())
            f.flush()
            os.fsync(f.fileno())
            done += len(batch)
            if (done // batch_size) % 20 == 0 or done >= total:
                sp = (done - n0) / max(1e-6, time.time() - t0)
                left = (total - done) / max(1e-6, sp)
                print(f"[vec] {done:,}/{total:,}  速度 {sp:.0f} 条/秒  "
                      f"剩余约 {left / 60:.0f} 分钟  ({time.time() - t0:.0f}s)", flush=True)
    print(f"[vec] 完成：{done:,} 条 × {dim} 维 -> {vecs}", flush=True)
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="批量向量化（增量）")
    ap.add_argument("--memory-dir", default="", help="记忆库目录")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    run(paths.memory_dir(args.memory_dir), args.batch, args.limit)


if __name__ == "__main__":
    main()
