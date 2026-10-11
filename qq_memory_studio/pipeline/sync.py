# -*- coding: utf-8 -*-
"""一键同步流水线（插件 / 命令行共用）。

链路：定位 nt_msg.db -> 取密钥 -> 解密(快照) -> 增量构建/刷新 messages.db 与中文索引
      ->（可选）生成向量 ->（可选）导出可读文本。

用法::

    python sync.py [--qq-data-dir DIR] [--account QQ] [--key KEY] \
                   [--memory-dir DIR] [--full] [--vectors] [--export-text] [--auto-key]

密钥来源优先级：--key > 环境变量 QQMEM_DB_KEY > 本机缓存的密钥文件 > 自动抓取(--auto-key)。

结束时打印形如::

    SYNC_OK total=<n> added_c2c=<n> added_group=<n> elapsed=<s> db=<path>
    SYNC_FAIL <原因>

供插件解析。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import paths
import refresh_memory


# ------------------------------------------------------------------ 密钥缓存
def _key_dir(work: str) -> str:
    return os.path.join(work, "keys")


def load_cached_key(work: str, memory: str, account: str) -> str:
    """从本机缓存里找 16 字符密钥。"""
    # 1) keys/<account>.key
    if account:
        p = os.path.join(_key_dir(work), f"{account}.key")
        if os.path.isfile(p):
            k = open(p, encoding="utf-8").read().strip()
            if k:
                return k
    # 2) keys_found.json（可能有多份）
    for jf in (os.path.join(_key_dir(work), "keys_found.json"),
               os.path.join(work, "keys_found.json"),
               os.path.join(memory, "keys_found.json")):
        if not os.path.isfile(jf):
            continue
        try:
            data = json.load(open(jf, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                if str(item.get("nKey")) == "16" and item.get("key"):
                    return str(item["key"])
            for item in data:
                if item.get("key"):
                    return str(item["key"])
    return ""


def save_cached_key(work: str, account: str, key: str) -> None:
    if not account or not key:
        return
    d = _key_dir(work)
    os.makedirs(d, exist_ok=True)
    try:
        with open(os.path.join(d, f"{account}.key"), "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        pass


def _force_utf8_stdio() -> None:
    """统一 stdout/stderr 编码。

    冻结成 exe 后（尤其是 --noconsole/pythonw）PYTHONUTF8 / PYTHONIOENCODING 不再生效，
    print() 会按系统 ANSI 代码页(cp936)写字节，而父进程按 UTF-8 读 → 中文全乱码。
    在源头上写死 UTF-8，父子两侧约定才稳定。
    """
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001  流可能不支持 reconfigure
            pass


def try_auto_key(work: str, account: str) -> str:
    """尝试调用 extract_key.py 现场抓取（需管理员 + QQ 登录）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(_key_dir(work), "keys_found.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    import subprocess

    # 冻结(onefile)后 sys.executable 就是本程序 exe，直接 "exe -u extract_key.py" 不会被
    # app.py 识别，会再启动一个完整 GUI。因此冻结时必须走显式内部分支。
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--studio-run-extract-key", "--out", out]
    else:
        cmd = [sys.executable, "-u", os.path.join(here, "extract_key.py"), "--out", out]
    print(f"[key] 尝试自动抓取密钥: {' '.join(cmd)}", flush=True)
    try:
        subprocess.run(cmd, timeout=1200)
    except Exception as e:  # noqa: BLE001
        print(f"[key] 自动抓取失败: {e}", flush=True)
    key = load_cached_key(work, "", account)
    # 只播报状态，不泄露密钥内容 / 内部细节
    print(f"[key] {'已获取候选密钥' if key else '未命中任何候选密钥'}", flush=True)
    return key


# ------------------------------------------------------------------ 主流程
def resolve_key(args, work: str, memory: str, account: str) -> str:
    key = args.key or os.environ.get("QQMEM_DB_KEY", "")
    if key:
        return key
    key = load_cached_key(work, memory, account)
    if key:
        print(f"[key] 使用本机缓存密钥（{len(key)} 字符）")
        return key
    if args.auto_key:
        key = try_auto_key(work, account)
        if key:
            return key
    return ""


def main() -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="QQ 长期记忆一键同步")
    ap.add_argument("--qq-data-dir", default="", help="QQ 数据根目录（Tencent Files）")
    ap.add_argument("--account", default="", help="目标账号 QQ 号")
    ap.add_argument("--key", default="", help="SQLCipher 密钥（或用 QQMEM_DB_KEY）")
    ap.add_argument("--data-dir", default="", help="插件数据目录")
    ap.add_argument("--memory-dir", default="", help="记忆库目录")
    ap.add_argument("--work-dir", default="", help="快照/缓存目录")
    ap.add_argument("--full", action="store_true", help="全量重建")
    ap.add_argument("--vectors", action="store_true", help="同步后生成/续跑语义向量")
    ap.add_argument("--export-text", action="store_true", help="同步后导出逐会话文本")
    ap.add_argument("--auto-key", action="store_true", help="缺密钥时自动抓取（需管理员+QQ登录）")
    ap.add_argument("--limit", type=int, default=0, help="向量化条数上限（调试用）")
    args = ap.parse_args()

    t0 = time.time()
    data = paths.data_dir(args.data_dir)
    memory = paths.memory_dir(args.memory_dir) if args.memory_dir else os.path.join(data, "memory")
    work = paths.work_dir(args.work_dir) if args.work_dir else data
    os.makedirs(memory, exist_ok=True)
    os.makedirs(work, exist_ok=True)

    # 1) 定位 nt_msg.db
    nt_msg = paths.find_nt_msg(args.qq_data_dir, args.account)
    if not nt_msg:
        msg = args.qq_data_dir or "自动探测"
        print(f"SYNC_FAIL 未找到 QQ 数据目录 / nt_msg.db（数据根：{msg}）")
        return 3
    account = args.account or paths.guess_account(nt_msg)
    print(f"[sync] 账号={account or '?'}  nt_msg.db={nt_msg}")

    # 2) 取密钥
    key = resolve_key(args, work, memory, account)
    if not key:
        print("SYNC_FAIL 未取到密钥（请在配置里填写 db_key，或用 --auto-key 现场抓取）")
        return 4
    save_cached_key(work, account, key)

    # 3) 增量/全量刷新
    try:
        r = refresh_memory.run(nt_msg, key, memory, work, full=args.full)
    except Exception as e:  # noqa: BLE001
        print(f"SYNC_FAIL 刷新失败：{type(e).__name__}: {e}")
        return 5
    if not r.get("ok"):
        print(f"SYNC_FAIL 刷新未完成（{r.get('reason')}）")
        return 2

    # 4) 中文索引自检
    import sqlite3
    db = r["db"]
    try:
        c = sqlite3.connect(db)
        has_cjk = bool(c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='msg_fts_cjk'").fetchone())
        n_cjk = c.execute("SELECT COUNT(*) FROM msg_fts_cjk").fetchone()[0] if has_cjk else 0
        c.close()
        if (not has_cjk or n_cjk == 0) and r["total"] > 0:
            print("[sync] 中文索引缺失/为空，重建中 ...", flush=True)
            import build_cjk_fts
            build_cjk_fts.build(db)
    except Exception as e:  # noqa: BLE001
        print(f"[sync] 中文索引自检跳过: {e}")

    # 5) 可选：向量
    if args.vectors:
        try:
            import export_embed_jsonl
            import embed_vectors
            export_embed_jsonl.run(db, memory)
            embed_vectors.run(memory, 64, args.limit)
        except Exception as e:  # noqa: BLE001
            print(f"[sync] 向量生成失败（不影响关键词检索）：{type(e).__name__}: {e}")

    # 6) 可选：导出文本
    if args.export_text:
        try:
            import export_text
            export_text.run(db, os.path.join(data, "export"))
        except Exception as e:  # noqa: BLE001
            print(f"[sync] 文本导出失败：{e}")

    added = r.get("added", {})
    print(f"SYNC_OK total={r['total']} added_c2c={added.get('c2c', 0)} "
          f"added_group={added.get('group', 0)} elapsed={time.time() - t0:.0f} db={db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
