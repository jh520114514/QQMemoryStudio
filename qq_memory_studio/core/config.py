# -*- coding: utf-8 -*-
"""配置读写。

设计要点：
* 首次运行自动生成 config.json（默认指向 ../qq_memory/memory，即相对 studio 目录推导，
  不硬编码任何本机绝对路径）。
* embedding / AI 的默认凭据会尝试从本机 AstrBot 的 cmd_config.json 里“借”来作为初始值
  （只在首次生成默认配置时读取，密钥会写入 studio 自己的 config.json）。
* 提供扁平的点号读写：cfg.get("embedding.api_key") / cfg.set("embedding.model", ...)。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# ---------------------------------------------------------------- 目录推导
# 本文件: <studio>/core/config.py
#
# 便携性原则：无论是否被 PyInstaller 冻结，config.json 与默认记忆库目录都必须锚定在
# 「用户可见、可移动」的部署目录上，而不是临时解包目录（否则 onefile 退出即丢、
# onedir 落到 _internal\）：
#   * 冻结运行 (sys.frozen)：部署目录 = exe 所在目录 (sys.executable 的父目录)，
#     config.json 写在 exe 同目录；记忆库默认 <exe_dir>\qq_memory\memory。
#     代码/资源目录 STUDIO_DIR = PyInstaller 解包目录 (sys._MEIPASS)，随包 pipeline 在其中。
#   * 未冻结（源码运行）：部署目录 = 源码 studio 目录，完全保持原有行为。
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    STUDIO_DIR = getattr(sys, "_MEIPASS", os.path.dirname(CORE_DIR))  # 解包资源目录
    DEPLOY_DIR = os.path.dirname(os.path.abspath(sys.executable))     # exe 同目录
    DATA_ROOT = DEPLOY_DIR
else:
    STUDIO_DIR = os.path.dirname(CORE_DIR)
    DEPLOY_DIR = STUDIO_DIR
    DATA_ROOT = os.path.dirname(STUDIO_DIR)
WORKSPACE_ROOT = DATA_ROOT
CONFIG_PATH = os.path.join(DEPLOY_DIR, "config.json")

# 记忆库默认位置：冻结时 <exe_dir>\qq_memory\memory；源码时 <workspace>\qq_memory\memory
DEFAULT_MEMORY_DIR = os.path.join(DATA_ROOT, "qq_memory", "memory")
# 同步中间产物（快照/密钥缓存）默认位置
DEFAULT_DATA_DIR = os.path.join(DATA_ROOT, "qq_memory", "data")


# ---------------------------------------------------------------- 默认值
def _seed_from_cmd_config() -> dict:
    """尝试从本机 AstrBot cmd_config.json 抽取 embedding / AI 初始默认值。"""
    seed = {"embedding": {}, "ai": {}}
    candidates = []
    root = os.environ.get("ASTRBOT_ROOT", "")
    env_cfg = os.environ.get("QQMEM_CMD_CONFIG", "")
    if env_cfg:
        candidates.append(env_cfg)
    # 从部署目录向上找 data/cmd_config.json（冻结时=exe 同目录，源码时=studio 目录）
    cur = DEPLOY_DIR
    for _ in range(8):
        candidates.append(os.path.join(cur, "data", "cmd_config.json"))
        candidates.append(os.path.join(cur, "cmd_config.json"))
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    if root:
        candidates.append(os.path.join(root, "data", "cmd_config.json"))
    # 逐个候选都读出来（本机可能存在多个 cmd_config.json，其中有的 provider 为空）
    cfgs = []
    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8-sig") as f:
                    cfgs.append(json.load(f))
            except Exception:  # noqa: BLE001
                pass
    providers: list[dict] = []
    for cfg in cfgs:
        providers += list(cfg.get("provider", []) or [])
    cfg = {"provider": providers}
    for item in cfg.get("provider", []) or []:
        pid = str(item.get("id", ""))
        ptype = str(item.get("type", ""))
        if not seed["embedding"] and (pid == "openai_embedding" or ptype in ("openai_embedding", "embedding")):
            seed["embedding"] = {
                "base_url": item.get("embedding_api_base", "") or "",
                "api_key": item.get("embedding_api_key", "") or "",
                "model": item.get("embedding_model", "") or "",
                "dim": int(item.get("embedding_dimensions", 0) or 0),
            }
    # 选一个 chat 类型 provider 作为 AI 默认
    for item in cfg.get("provider", []) or []:
        pid = str(item.get("id", ""))
        ptype = str(item.get("type", ""))
        if pid in ("openai_embedding", "vllm_rerank"):
            continue
        if ptype in ("", "openai_chat_completion", "openai", "chat_completion"):
            key = item.get("api_key") or item.get("embedding_api_key") or ""
            if key and item.get("api_base"):
                seed["ai"] = {
                    "base_url": item.get("api_base", ""),
                    "api_key": key,
                    "model": item.get("model", "") or item.get("model_config", {}).get("model", ""),
                }
                break
    # 清理空值
    seed["embedding"] = {k: v for k, v in seed["embedding"].items() if v}
    seed["ai"] = {k: v for k, v in seed["ai"].items() if v}
    return seed


def default_config() -> dict:
    seed = _seed_from_cmd_config()
    emb = {"base_url": "https://api.siliconflow.cn/v1", "api_key": "",
           "model": "BAAI/bge-m3", "dim": 1024, "timeout": 30}
    emb.update({k: v for k, v in seed.get("embedding", {}).items() if k in emb})
    ai = {"base_url": "https://api.siliconflow.cn/v1", "api_key": "",
          "model": "deepseek-ai/DeepSeek-V3"}
    ai.update({k: v for k, v in seed.get("ai", {}).items() if k in ai})
    return {
        "version": 1,
        "qq": {"data_dir": "", "account": ""},
        "paths": {"memory_dir": DEFAULT_MEMORY_DIR, "data_dir": DEFAULT_DATA_DIR,
                  "work_dir": DEFAULT_DATA_DIR, "db_key": ""},
        "embedding": emb,
        "ai": ai,
        "features": {"auto_recall": True, "recall_top_k": 8},
        "ui": {"guide_shown": False},
    }


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """轻量配置对象；支持点号读写与原子保存。"""

    def __init__(self, data: dict | None = None, path: str = CONFIG_PATH):
        self.path = path
        self.data = data if data is not None else default_config()

    # ---- 读写 ----
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self.data
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value

    # ---- 持久化 ----
    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str = CONFIG_PATH, create: bool = True) -> "Config":
        cfg = default_config()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    user = json.load(f)
                cfg = _deep_merge(cfg, user)
            except Exception:  # noqa: BLE001
                pass
        elif create:
            obj = cls(cfg, path)
            try:
                obj.save()
            except OSError:
                pass
            return obj
        return cls(cfg, path)

    # ---- 常用便捷属性 ----
    @property
    def memory_dir(self) -> str:
        return self.get("paths.memory_dir") or DEFAULT_MEMORY_DIR

    @property
    def data_dir(self) -> str:
        return self.get("paths.data_dir") or DEFAULT_DATA_DIR

    @property
    def work_dir(self) -> str:
        return self.get("paths.work_dir") or self.data_dir

    @property
    def qq_data_dir(self) -> str:
        return self.get("qq.data_dir") or ""

    @property
    def qq_account(self) -> str:
        return str(self.get("qq.account") or "")

    @property
    def db_key(self) -> str:
        return self.get("paths.db_key") or ""


if __name__ == "__main__":
    c = Config.load()
    print("config path:", c.path)
    print(json.dumps(c.data, ensure_ascii=False, indent=2))
