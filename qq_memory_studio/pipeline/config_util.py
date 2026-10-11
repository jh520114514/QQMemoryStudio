# -*- coding: utf-8 -*-
"""从 AstrBot 的 cmd_config.json 读取 Embedding / Rerank 凭据。

安全约定：
  * 密钥只在本进程内存中使用，任何 helper 都不得把密钥写进日志/文件/报告。
  * 提供 redact() 用于在需要展示时把密钥替换成 <set>/<empty>。
  * 支持环境变量覆盖：
        QQMEM_EMBED_API_KEY / QQMEM_EMBED_API_BASE / QQMEM_EMBED_MODEL / QQMEM_EMBED_DIM
        QQMEM_RERANK_API_KEY / QQMEM_RERANK_API_BASE / QQMEM_RERANK_SUFFIX / QQMEM_RERANK_MODEL
        QQMEM_CMD_CONFIG    显式指定 cmd_config.json 路径
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)

EMBED_PROVIDER_ID = "openai_embedding"
RERANK_PROVIDER_ID = "vllm_rerank"


def _cmd_config_candidates() -> list[str]:
    """自动定位本实例的 cmd_config.json（不硬编码任何绝对路径）。"""
    cands: list[str] = []
    p = os.environ.get("QQMEM_CMD_CONFIG")
    if p:
        cands.append(p)
    # 从插件目录向上找 core/data/cmd_config.json
    cur = PLUGIN_DIR
    for _ in range(8):
        cands.append(os.path.join(cur, "cmd_config.json"))
        cands.append(os.path.join(cur, "data", "cmd_config.json"))
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    root = os.environ.get("ASTRBOT_ROOT", "")
    if root:
        cands.append(os.path.join(root, "data", "cmd_config.json"))
        cands.append(os.path.join(root, "cmd_config.json"))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


@dataclass
class EmbeddingConfig:
    api_key: str
    api_base: str
    model: str
    dimensions: int

    def endpoint(self) -> str:
        return self.api_base.rstrip("/") + "/embeddings"


@dataclass
class RerankConfig:
    api_key: str
    api_base: str
    suffix: str
    model: str

    def endpoint(self) -> str:
        base = self.api_base.rstrip("/")
        suffix = self.suffix if self.suffix.startswith("/") else "/" + self.suffix
        return base + suffix


def redact(value: Any) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, str):
        return "<set len=%d>" % len(value) if value else "<empty>"
    return "<set>"


def load_cmd_config(path: str = "") -> dict:
    for c in ([path] if path else _cmd_config_candidates()):
        if c and os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception:
                continue
    return {}


def _find_provider(cfg: dict, provider_id: str) -> dict:
    for item in cfg.get("provider", []) or []:
        if item.get("id") == provider_id:
            return item
    return {}


def _first_embedding_provider(cfg: dict) -> dict:
    for item in cfg.get("provider", []) or []:
        pid = str(item.get("id", ""))
        ptype = str(item.get("type", ""))
        if pid == EMBED_PROVIDER_ID or ptype in ("openai_embedding", "embedding"):
            return item
    return {}


def load_embedding_config(config_path: str = "") -> EmbeddingConfig:
    cfg = load_cmd_config(config_path)
    item = _find_provider(cfg, EMBED_PROVIDER_ID) or _first_embedding_provider(cfg)

    api_key = os.environ.get("QQMEM_EMBED_API_KEY") or item.get("embedding_api_key", "")
    api_base = os.environ.get("QQMEM_EMBED_API_BASE") or item.get(
        "embedding_api_base", "https://api.siliconflow.cn/v1"
    )
    model = os.environ.get("QQMEM_EMBED_MODEL") or item.get("embedding_model", "BAAI/bge-m3")
    dim = os.environ.get("QQMEM_EMBED_DIM") or item.get("embedding_dimensions", 1024)

    try:
        dimensions = int(dim)
    except (TypeError, ValueError):
        dimensions = 1024

    return EmbeddingConfig(
        api_key=str(api_key or ""),
        api_base=str(api_base),
        model=str(model),
        dimensions=dimensions,
    )


def load_rerank_config(config_path: str = "") -> RerankConfig:
    cfg = load_cmd_config(config_path)
    item = _find_provider(cfg, RERANK_PROVIDER_ID)

    api_key = os.environ.get("QQMEM_RERANK_API_KEY") or item.get("rerank_api_key", "")
    api_base = os.environ.get("QQMEM_RERANK_API_BASE") or item.get(
        "rerank_api_base", "https://api.siliconflow.cn/"
    )
    suffix = os.environ.get("QQMEM_RERANK_SUFFIX") or item.get("rerank_api_suffix", "/v1/rerank")
    model = os.environ.get("QQMEM_RERANK_MODEL") or item.get(
        "rerank_model", "BAAI/bge-reranker-v2-m3"
    )

    return RerankConfig(
        api_key=str(api_key or ""),
        api_base=str(api_base),
        suffix=str(suffix),
        model=str(model),
    )


if __name__ == "__main__":
    emb = load_embedding_config()
    rer = load_rerank_config()
    print("cmd_config candidates:", _cmd_config_candidates()[:4])
    print("embedding endpoint :", emb.endpoint())
    print("embedding model    :", emb.model)
    print("embedding dim      :", emb.dimensions)
    print("embedding api_key  :", redact(emb.api_key))
    print("rerank endpoint    :", rer.endpoint())
    print("rerank model       :", rer.model)
    print("rerank api_key     :", redact(rer.api_key))
