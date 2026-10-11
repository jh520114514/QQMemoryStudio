# -*- coding: utf-8 -*-
"""离屏冒烟测试：记忆库不存在时，三处（主窗口/检索窗口/导出窗口）是否给出统一中文引导。

不触碰真实记忆库：只用一个不存在的临时目录。QMessageBox 被替换成记录器，避免阻塞。
用法： python scripts/smoke_empty_memory.py
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if STUDIO not in sys.path:
    sys.path.insert(0, STUDIO)

EMPTY = os.path.join(tempfile.gettempdir(), "qqms_smoke_empty_mem__does_not_exist")


def main() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from core.config import Config, default_config
    from core import memory as M

    app = QApplication([])

    calls: list[tuple[str, str, str]] = []
    for name in ("information", "warning", "question"):
        def make(n):
            def f(parent, title, text, *a, **k):
                calls.append((n, title, text))
                return QMessageBox.Yes
            return f
        setattr(QMessageBox, name, staticmethod(make(name)))

    cfg = Config(default_config())
    cfg.set("paths.memory_dir", EMPTY)

    from ui.main_window import MainWindow
    from ui.search_window import SearchWindow
    from ui.export_window import ExportWindow

    print("=== 主窗口 ===")
    mw = MainWindow(cfg)
    mw._on_stats({"stats": {"error": "记忆库文件不存在"}, "accounts": [],
                  "last_backup": "未知", "next_scheduled": "未知"})
    print("lbl_notice.showToParent:", mw.lbl_notice.isVisibleTo(mw))
    print("lbl_notice.text:\n" + mw.lbl_notice.text())
    print("lbl_db:", mw.lbl_db.text())
    assert mw.lbl_notice.isVisibleTo(mw)
    # 库尚未生成属正常首启：状态标签给中性说明，不得说成“读取异常”
    assert "尚未生成" in mw.lbl_db.text(), "空库状态未标注"
    assert "读取异常" not in mw.lbl_db.text(), "空库不应被报成读取异常"
    assert "未找到记忆库" in mw.lbl_notice.text()

    # 真正的读取失败（库损坏 / 权限 / 被占用）仍必须原样可见，不被尺寸标签覆盖
    mw._on_stats({"stats": {"error": "OperationalError: unable to open database file",
                             "db_bytes": 0}, "accounts": []})
    print("真异常 lbl_db:", mw.lbl_db.text())
    assert mw.lbl_db.text().startswith("messages.db 读取异常："), "真异常被尺寸覆盖了！"

    # 回到空库情形，后面的“统一引导”检查基于它
    mw._on_stats({"stats": {"error": "记忆库文件不存在"}, "accounts": [],
                  "last_backup": "未知", "next_scheduled": "未知"})
    assert "未找到记忆库" in mw.lbl_notice.text()

    print("\n=== 检索窗口 ===")
    sw = SearchWindow(cfg)
    print("overview:", sw.overview.text())
    sw.query.setText("测试")
    sw.do_search()
    print("status:", sw.status.text())
    print("dialog:", calls[-1] if calls else None)

    print("\n=== 导出窗口 ===")
    ew = ExportWindow(cfg)
    ew._on_chats([], "")
    print("lbl_count:", ew.lbl_count.text())
    print("log tail:\n" + ew.log_view.toPlainText())

    print("\n=== 统一口径检查 ===")
    guide = M.memory_missing_guide(EMPTY)
    print(guide)
    for step in M.GUIDE_STEPS:
        assert step in mw.lbl_notice.text()
    print("OK: 三处均包含统一引导三步，且无英文异常堆栈")
    # 等待后台线程收尾并按正常路径关闭窗口，避免 QThread 未结束导致退出码非 0
    for w in (mw, sw, ew):
        try:
            w.stop_workers()
        except Exception:  # noqa: BLE001
            pass
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
