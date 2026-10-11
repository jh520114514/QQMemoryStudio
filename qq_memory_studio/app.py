# -*- coding: utf-8 -*-
"""QQ Memory Studio 入口。

    python app.py            # 启动图形界面
    python app.py --studio-run-sync <同步参数>   # 内部使用：冻结成 exe 后子进程跑同步
    python app.py --studio-run-extract-key <参数>  # 内部使用：冻结成 exe 后子进程抓取密钥

本程序独立运行，不依赖、不安装进 AstrBot，也不会修改 8081 实例。
"""
from __future__ import annotations

import os
import sys

# 冻结(打包)后 __file__ 会落在 PyInstaller 解包目录；统一以「资源目录」为准，
# 保证随包的 core/、pipeline/ 等模块能被 import / runpy 到（配置/数据目录另见 core.config）。
if getattr(sys, "frozen", False):
    STUDIO_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
else:
    STUDIO_DIR = os.path.dirname(os.path.abspath(__file__))
if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)


def _force_utf8_stdio() -> None:
    """冻结(--noconsole/pythonw)后 PYTHONUTF8 / PYTHONIOENCODING 不生效，
    子进程默认按系统 ANSI 代码页(cp936)写 stdout，父进程按 UTF-8 读会乱码。
    这里显式改 stdout/stderr 编码，保证父子进程编码约定一致。"""
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001  某些流(如 None)不支持 reconfigure
            pass


def _pipeline_script(name: str) -> str:
    """返回随包 pipeline 目录下脚本的绝对路径，并确保其目录可被 import。"""
    pipeline_dir = os.path.join(STUDIO_DIR, "pipeline")
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    return os.path.join(pipeline_dir, name)


def _run_pipeline_sync(argv: list[str]) -> int:
    """把自身当作同步子进程使用（PyInstaller 冻结后由 core/sync.py 调用）。"""
    import runpy

    _force_utf8_stdio()
    sync_py = _pipeline_script("sync.py")
    sys.argv = [sync_py] + list(argv)
    try:
        runpy.run_path(sync_py, run_name="__main__")
        return 0
    except SystemExit as e:  # 透传退出码
        return int(e.code or 0)


def _run_pipeline_extract_key(argv: list[str]) -> int:
    """把自身当作「抓取密钥」子进程使用。

    冻结后 pipeline/sync.py 若直接用 ``sys.executable -u extract_key.py`` 调起本 exe，
    参数既不是 --studio-run-sync 也不匹配任何分支，就会落进 Qt 主流程再开一个 GUI。
    这里提供显式的内部分支，与该 exe 的其它子命令行为保持一致。
    """
    import runpy

    _force_utf8_stdio()
    script = _pipeline_script("extract_key.py")
    sys.argv = [script] + list(argv)
    try:
        runpy.run_path(script, run_name="__main__")
        return 0
    except SystemExit as e:  # 透传退出码
        return int(e.code or 0)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--studio-run-sync":
        return _run_pipeline_sync(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "--studio-run-extract-key":
        return _run_pipeline_extract_key(sys.argv[2:])

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    from core.config import Config, CONFIG_PATH
    from ui.main_window import MainWindow

    # 首次运行判定：启动时 config.json 不存在 = 全新部署（之后 Config.load 会生成它）。
    config_existed = os.path.isfile(CONFIG_PATH)

    app = QApplication(sys.argv)
    app.setApplicationName("QQ Memory Studio")
    # 指定系统中文字体，保证中文正常显示（先按家族名找，找不到再直接加载字体文件）
    families = set(QFontDatabase.families())
    chosen = ""
    for fam in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun"):
        if fam in families:
            chosen = fam
            break
    if not chosen and os.name == "nt":
        for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                     r"C:\Windows\Fonts\simsun.ttc"):
            if os.path.isfile(path):
                fid = QFontDatabase.addApplicationFont(path)
                fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
                if fams:
                    chosen = fams[0]
                    break
    if chosen:
        app.setFont(QFont(chosen, 9))
    config = Config.load()
    win = MainWindow(config, first_run=not config_existed)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
