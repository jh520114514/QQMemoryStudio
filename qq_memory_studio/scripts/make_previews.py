# -*- coding: utf-8 -*-
"""离屏（offscreen）构造三个窗口并各导出一张预览 PNG 到 previews/。

    set QT_QPA_PLATFORM=offscreen
    python scripts/make_previews.py

会真实读取 memory\\ 做只读检索来填充界面，因此预览图反映真实内容。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STUDIO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if STUDIO_DIR not in sys.path:
    sys.path.insert(0, STUDIO_DIR)

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import Config  # noqa: E402
from core.memory import MemoryStore  # noqa: E402
from core.semantic import SemanticIndex  # noqa: E402
from core import qq  # noqa: E402

PREVIEW_DIR = os.path.join(STUDIO_DIR, "previews")


def _pump(app, n: int = 8) -> None:
    for _ in range(n):
        app.processEvents()


def shot(app, widget, name: str, size: tuple[int, int]):
    widget.resize(*size)
    widget.show()
    _pump(app)
    path = os.path.join(PREVIEW_DIR, name)
    ok = widget.grab().save(path)
    print(f"  saved {path}  ok={ok}  size={os.path.getsize(path) if os.path.exists(path) else 0}")
    return path


def fill_main(win, config) -> None:
    res = {"stats": {}, "files": {}, "last_backup": "未知", "next_scheduled": "未知",
           "accounts": [], "error": ""}
    ms = MemoryStore(config.memory_dir, config.get("embedding.dim") or 1024)
    res["stats"] = ms.stats()
    res["files"] = ms.file_info()
    res["last_backup"] = qq.last_backup_time(config.memory_dir)
    res["next_scheduled"] = qq.next_scheduled_time()
    res["accounts"] = qq.detect_accounts(config.qq_data_dir) or ["(未探测到 QQ 数据目录)"]
    win._on_stats(res)
    win.log("已加载真实记忆库统计（预览模式，同步加载）")
    win.log(f"messages.db = {res['stats'].get('count', 0):,} 条 / {res['stats'].get('chats', 0)} 会话")


def fill_search(win, config, keyword: str = "开心") -> None:
    win.reload_backends()
    win.refresh_overview()
    win.query.setText(keyword)
    win.mode.setCurrentText("关键词")
    win.k.setValue(30)
    hits, dt = win.store.keyword_search(keyword, k=30)
    win.on_results(hits, dt, "")
    win.log_preview = True  # 标记（无实际用途）
    return hits


def fill_ai(win, config, hits: list[dict]) -> None:
    win.e_base.setText(config.get("ai.base_url") or "https://api.siliconflow.cn/v1")
    win.e_key.setText(config.get("ai.api_key") or "sk-****（预览占位）")
    win.e_model.setText(config.get("ai.model") or "deepseek-ai/DeepSeek-V3")
    win.intent.setText("关于一起玩游戏、约时间")
    win.btn_send.setEnabled(True)
    win.status.setText("完成 · 引用 3 条")
    win.view.clear()  # 清掉构造时的“未配置”提示，换成演示对话
    win._append("我", "（检索意图：关于一起玩游戏、约时间）我最近和谁约过一起玩游戏？")
    win._append("助手", "根据记忆库检索结果：\n"
                        "· 在「小兔建站交流群」里，有人问过“有没有一起玩游戏的”[1]；\n"
                        "· 你也说过“我找人打游戏”[2]、“打个游戏”[3]。\n"
                        "综合来看，最近围绕“一起打游戏”的邀请主要发生在该群，时间集中在 2026-05 与 2026-10。")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    win.refs.clear()
    for h in hits[:3]:
        cite = hits.index(h) + 1
        item = QListWidgetItem(
            f"[{cite}] {h.get('time')} · {h.get('chat')} · {h.get('sender')}\n"
            f"    {str(h.get('text') or '')[:70]}")
        item.setData(Qt.UserRole, h.get("id"))
        win.refs.addItem(item)


def main() -> int:
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    # offscreen 平台默认字体不含中文字形，直接加载系统字体文件，避免预览出现“豆腐块”
    from PySide6.QtGui import QFontDatabase
    loaded_fam = ""
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                 r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\Deng.ttf"):
        if os.path.isfile(path):
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            if fams:
                loaded_fam = fams[0]
                app.setFont(QFont(loaded_fam, 9))
                print("使用预览字体：", loaded_fam, "<-", path)
                break
    if not loaded_fam:
        print("警告：未加载到中文字体，预览可能显示方块")
    config = Config.load()
    ms = MemoryStore(config.memory_dir, config.get("embedding.dim") or 1024)

    print("[1/3] 主窗口 …")
    from ui.main_window import MainWindow
    win = MainWindow(config)
    fill_main(win, config)
    shot(app, win, "main.png", (980, 800))
    win.close()

    print("[2/3] 检索窗口 …")
    from ui.search_window import SearchWindow
    sw = SearchWindow(config)
    hits = fill_search(sw, config, "开心")
    # 用第一条真实结果作为“引用”给 AI 窗口
    from core.semantic import SemanticIndex  # noqa: F401
    shot(app, sw, "search.png", (1120, 700))
    sw.close()

    print("[3/3] AI 助手窗口 …")
    from ui.ai_window import AIWindow
    aw = AIWindow(config)
    fill_ai(aw, config, hits)
    shot(app, aw, "ai.png", (1120, 740))
    aw.close()

    print("预览生成完成 ->", PREVIEW_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
