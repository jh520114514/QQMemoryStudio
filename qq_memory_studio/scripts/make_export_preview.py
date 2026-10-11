# -*- coding: utf-8 -*-
"""离屏验证 + 预览：检查「窗口」菜单含新项、渲染导出窗口、并通过真实 worker 导出一个会话。

    set QT_QPA_PLATFORM=offscreen
    python scripts/make_export_preview.py
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STUDIO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)

from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import Config  # noqa: E402

PREVIEW_DIR = os.path.join(STUDIO_DIR, "previews")
EXPORT_DIR = os.path.join(STUDIO_DIR, "exports")
TARGET_CHAT_ID = "1027129163"


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _wait_until(app, cond, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        time.sleep(0.03)
        if cond():
            return True
    return False


def load_cjk_font(app) -> None:
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            if fams:
                app.setFont(QFont(fams[0], 9))
                print("预览字体：", fams[0])
                return


def save(widget, name: str) -> str:
    path = os.path.join(PREVIEW_DIR, name)
    ok = widget.grab().save(path)
    print(f"  saved {path} ok={ok} bytes={os.path.getsize(path) if os.path.exists(path) else 0}")
    return path


def main() -> int:
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    load_cjk_font(app)
    config = Config.load()

    # ---------------- 1. 主窗口 + 菜单项 ----------------
    from ui.main_window import MainWindow
    win = MainWindow(config)
    win.resize(1000, 800)
    win.show()
    _pump(app, 1.0)
    save(win, "export_main.png")

    # 找到「窗口」菜单
    menu = None
    for act in win.menuBar().actions():
        if act.text() == "窗口":
            menu = act.menu()
            break
    assert menu is not None, "未找到「窗口」菜单"
    items = [a.text() for a in menu.actions()]
    print("「窗口」菜单项：", items)
    assert any("会话导出" in t for t in items), "菜单里没有『会话导出』项！"

    # 渲染菜单为图片（离屏下把菜单置顶并 grab）
    menu.setAttribute(Qt.WA_DontShowOnScreen, True)
    menu.popup(QPoint(200, 40))
    _pump(app, 0.4)
    save(menu, "export_menu.png")
    menu.close()
    _pump(app, 0.2)

    # ---------------- 2. 导出窗口 ----------------
    from ui.export_window import ExportWindow
    ew = ExportWindow(config)
    ew.resize(1180, 720)
    ew.show()
    _pump(app, 0.5)
    ok = _wait_until(app, lambda: len(ew.chats) > 0, 60)
    print(f"会话列表加载完成={ok}，共 {len(ew.chats)} 个会话")
    assert ok, "会话列表加载超时"
    save(ew, "export_window.png")

    # ---------------- 3. 真实导出（走 UI worker）----------------
    row = -1
    for i in range(ew.table.rowCount()):
        if ew.table.item(i, 4) and ew.table.item(i, 4).text() == TARGET_CHAT_ID:
            row = i
            break
    assert row >= 0, "列表里没找到目标会话"
    ew.table.selectRow(row)
    _pump(app, 0.3)
    ew.chk_qq.setChecked(True)
    ew.chk_byday.setChecked(True)
    ew.cmb_fmt.setCurrentIndex(0)  # docx
    ew.e_out.setText(os.path.join(EXPORT_DIR, "ui_小兽太实验舱_群聊.docx"))
    ew._path_edited = True
    print("选中会话：", ew.current_chat())
    ew.start_export()
    done = _wait_until(app, lambda: not (ew._export_worker and ew._export_worker.isRunning()), 300)
    _pump(app, 0.5)
    print("导出结束：", done)
    save(ew, "export_window_done.png")
    print("进度：", ew.lbl_progress.text())
    print("日志尾部：")
    print(ew.log_view.toPlainText()[-800:])

    ew.stop_workers(); ew.close()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
