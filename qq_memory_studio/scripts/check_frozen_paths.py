# -*- coding: utf-8 -*-
"""静态检查：冻结(/未冻结)两种模式下，配置目录、资源目录、同步命令的路径推导是否自洽。
只打印路径，不执行任何同步 / 解密动作。
"""
from __future__ import annotations

import os
import sys

STUDIO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)


def report(tag: str) -> None:
    import importlib
    from core import config as C
    importlib.reload(C)
    from core import sync as S
    importlib.reload(S)
    print(f"===== {tag} =====")
    print("  frozen        =", C.FROZEN)
    print("  STUDIO_DIR    =", C.STUDIO_DIR, "  (存在:", os.path.isdir(C.STUDIO_DIR), ")")
    print("  DEPLOY_DIR    =", C.DEPLOY_DIR)
    print("  CONFIG_PATH   =", C.CONFIG_PATH)
    print("  DEFAULT_MEM   =", C.DEFAULT_MEMORY_DIR)
    print("  DEFAULT_DATA  =", C.DEFAULT_DATA_DIR)
    print("  PIPELINE_SYNC =", S.PIPELINE_SYNC, "  (存在:", os.path.isfile(S.PIPELINE_SYNC), ")")
    r = S.SyncRunner(C.DEFAULT_MEMORY_DIR)
    print("  sync.command()=", " ".join(r.command()))
    print()


def main() -> int:
    report("未冻结（源码运行）")

    # 模拟冻结：伪装成 PyInstaller 解包环境
    fake_exe = r"C:\QQMS_FreshTest\QQMemoryStudio\QQMemoryStudio.exe"
    sys.frozen = True                     # type: ignore[attr-defined]
    sys._MEIPASS = STUDIO_DIR             # type: ignore[attr-defined]  解包目录≈源码目录
    sys.executable = fake_exe
    report("模拟冻结（sys.frozen=True, exe 在 C:\\QQMS_FreshTest\\QQMemoryStudio\\）")

    # 顺带看 app.py 侧 STUDIO_DIR 推导
    import app
    print("app.STUDIO_DIR =", app.STUDIO_DIR)
    print("app.pipeline_dir exists =",
          os.path.isdir(os.path.join(app.STUDIO_DIR, "pipeline")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
