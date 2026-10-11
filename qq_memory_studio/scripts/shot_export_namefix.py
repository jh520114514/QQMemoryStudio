# -*- coding: utf-8 -*-
"""任务一验证：真实桌面上打开「会话导出」窗口，截图到 previews/。

不会触发任何同步/解密动作：只构造 MainWindow + 调用 open_export()（纯函数调用，
不模拟鼠标点击，避免误触「同步」）。

    python scripts/shot_export_namefix.py
"""
from __future__ import annotations

import os
import sys
import time

STUDIO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

PREVIEW_DIR = os.path.join(STUDIO_DIR, "previews")


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _wait(app, cond, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)
        if cond():
            return True
    return False


def load_cjk(app) -> str:
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            if fams:
                app.setFont(QFont(fams[0], 9))
                return fams[0]
    return ""


def main() -> int:
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    print("字体：", load_cjk(app))
    from core.config import Config
    config = Config.load()

    from ui.main_window import MainWindow
    win = MainWindow(config)
    win.resize(980, 780)
    win.show()
    _pump(app, 1.0)

    # 纯函数调用打开导出窗口（绝不点击任何按钮）
    win.open_export()
    ew = win.export_win
    ew.resize(1320, 760)
    ew.show(); ew.raise_(); ew.activateWindow()
    _pump(app, 0.5)

    ok = _wait(app, lambda: len(getattr(ew, "chats", [])) > 0, 180)
    print(f"会话列表加载完成={ok} 共 {len(ew.chats)} 个会话")
    _pump(app, 0.5)

    # 名称列宽度 & tooltip 自检
    print("名称列宽 =", ew.table.columnWidth(1), "px")
    for r in range(min(5, ew.table.rowCount())):
        it = ew.table.item(r, 1)
        print(f"  row{r}: 名称={it.text()!r} tooltip={it.toolTip()!r}")

    wpath = os.path.join(PREVIEW_DIR, "export_window_namefix.png")
    print("save window:", ew.grab().save(wpath), wpath)

    # 再抓一张整屏，证明窗口真实出现在桌面上
    scr = QGuiApplication.primaryScreen()
    if scr is not None:
        spath = os.path.join(PREVIEW_DIR, "export_window_namefix_screen.png")
        print("save screen:", scr.grabWindow(0).save(spath), spath)

    ew.stop_workers(); ew.close(); win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
