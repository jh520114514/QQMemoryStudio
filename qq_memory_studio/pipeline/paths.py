# -*- coding: utf-8 -*-
"""路径与配置解析。

所有默认值都相对「插件目录」推导，**不含任何本机绝对路径**。
优先级：命令行参数 > 环境变量(QQMEM_*) > 插件自身目录。

典型布局::

    astrbot_plugin_qq_memory/          <- PLUGIN_DIR
      main.py
      pipeline/                        <- HERE
        paths.py  sync.py  ...
        tools/util/msgdb/...
      data/                            <- DATA_DIR（运行期生成，随插件走）
        memory/messages.db
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))          # <plugin>/pipeline
PLUGIN_DIR = os.path.dirname(HERE)                         # <plugin>


def env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v else default


def data_dir(arg: str = "") -> str:
    return arg or env("QQMEM_DATA_DIR", os.path.join(PLUGIN_DIR, "data"))


def memory_dir(arg: str = "") -> str:
    return arg or env("QQMEM_MEMORY_DIR", os.path.join(data_dir(), "memory"))


def messages_db(arg: str = "") -> str:
    return arg or env("QQMEM_DB", os.path.join(memory_dir(), "messages.db"))


def work_dir(arg: str = "") -> str:
    """解密 / 快照等中间产物目录（默认与 data 同级）。"""
    return arg or env("QQMEM_WORK_DIR", data_dir())


def default_qq_roots() -> list[str]:
    """自动探测 QQ(NTQQ) 数据根目录候选（Windows）。"""
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    roots = [
        os.path.join(docs, "Tencent Files"),
        os.path.join(home, "OneDrive", "Documents", "Tencent Files"),
        os.path.join(home, "OneDrive", "文档", "Tencent Files"),
        os.path.join(docs, "Tencent Files", "nt_qq"),
    ]
    extra = env("QQMEM_QQ_ROOT", "")
    if extra:
        roots.insert(0, extra)
    return roots


def list_accounts(base: str) -> list[str]:
    """列出某个 Tencent Files 根目录下的账号目录（纯数字目录名）。"""
    out: list[str] = []
    try:
        for d in os.listdir(base):
            if d.isdigit() and os.path.isdir(os.path.join(base, d)):
                out.append(d)
    except OSError:
        pass
    return out


def _candidate_paths(base: str, account: str) -> list[str]:
    """给定 base（可能是 Tencent Files 根 / 账号目录 / nt_db 目录），列出 nt_msg.db 候选。"""
    cands: list[str] = []
    rels = (
        ("nt_qq", "nt_db", "nt_msg.db"),
        ("nt_db", "nt_msg.db"),
        ("nt_msg.db",),
    )
    accounts = [account] if account else list_accounts(base)
    for rel in rels:
        cands.append(os.path.join(base, *rel))
    for acc in accounts:
        for rel in rels:
            cands.append(os.path.join(base, acc, *rel))
    return cands


def find_nt_msg(qq_data_dir: str = "", account: str = "") -> str | None:
    """定位 nt_msg.db；找不到返回 None。

    ``qq_data_dir`` 可指向 Tencent Files 根、账号目录或 nt_db 目录；留空则自动探测。
    """
    qq_data_dir = qq_data_dir or ""
    if qq_data_dir.lower().endswith("nt_msg.db") and os.path.isfile(qq_data_dir):
        return qq_data_dir
    bases = [qq_data_dir] if qq_data_dir else default_qq_roots()
    for base in bases:
        for p in _candidate_paths(base, account):
            if os.path.isfile(p):
                return p
    return None


def guess_account(nt_msg_path: str) -> str:
    """从 nt_msg.db 路径反推账号 QQ 号。"""
    parts = os.path.normpath(nt_msg_path).split(os.sep)
    for seg in reversed(parts):
        if seg.isdigit():
            return seg
    return ""
